#!/usr/bin/env python3
"""
SDN controller for the Bandwidth Meter and Traffic Analyzer project.

Responsibilities (mapped to the project rubric):
  1. Mininet + SDN Implementation
     - Installs table-miss + ARP-flood flows on every switch.
     - Installs static forwarding for the h1 -> h2 measurement flow,
       initially over the *primary* path (s1 <-> s2 direct link).
     - Falls back to plain MAC-learning for anything else (priority 1).

  2. Socket-SDN Integration
     - Runs a small alert listener. socket_app/receiver.py sends
       "LOSS:<pct>:<flow_id>" to it when it observes high
       application-level packet loss, and that message directly
       triggers a controller reroute decision -- the application event
       influencing SDN behaviour.
       NOTE ON TRANSPORT: this uses an AF_UNIX datagram socket at
       ALERT_SOCK_PATH rather than a UDP/IP socket. Mininet hosts (h1,
       h2) each get their own *network* namespace (their own "127.0.0.1"
       and IP stack), but not a separate *mount* namespace/filesystem --
       so a Unix domain socket path is reachable from both a Mininet
       host process and this controller process (which runs in the root
       namespace), with no extra NAT/routing setup. A UDP/IP socket
       would need additional plumbing (e.g. a NAT node) to cross that
       namespace boundary, which is unnecessary complexity for this
       out-of-band control signal (compare: the OpenFlow control channel
       itself is out-of-band for the same reason).

  3. Dynamic SDN Functionality
     - A background monitor thread also polls OpenFlow flow statistics
       on s1 every POLL_INTERVAL seconds, independent of the socket
       alert, and reroutes to the backup path if the *measured network
       throughput* exceeds CONGESTION_THRESHOLD_BPS. This is the
       network-side (SDN-native) half of the dynamic behaviour, and lets
       you demonstrate rerouting even if you disable the alert socket.

  4. Performance Evaluation & Comparison
     - Every poll, (timestamp, dpid, rate_bps, active_path, event) is
       appended to results/controller_stats.csv, which
       analysis/compare.py later lines up against the receiver's
       application-level throughput CSV.

  5. Static baseline (for the "compare with a baseline" criterion)
     - MODE=static disables every reroute decision while keeping polling
       and logging identical, so the same traffic can be measured with and
       without the dynamic behaviour. Switch at run time with
       `python3 experiments/ctl.py static|dynamic` (or start with
       BWM_MODE=static).

  6. Multiple senders
     - Rules for a second sender host h3 (see topology/topo.py,
       create_network(multi_client=True)); all senders share the same
       h2-bound flow entry, so the poll sees their aggregate load.

Run (from the project root, so the results/ path resolves correctly):
    osken-manager controller/sdn_controller.py

Uses os-ken (OpenStack's actively-maintained fork of Ryu -- same API,
under the os_ken.* import path instead of ryu.*) rather than the
original, now-unmaintained `ryu` package, which fails to install on any
recent setuptools (its legacy setup.py hooks into
easy_install.get_script_args, an attribute that no longer exists on
modern setuptools). Install with `pip install os-ken` -- it ships a
normal prebuilt wheel, so none of that legacy build path is involved.

NOTE ON ASSUMPTIONS -- verify these against your actual Mininet run
(topology/topo.py prints them at startup) and edit the constants below
if they differ:
    H1_MAC, H2_MAC     -- host MAC addresses
    DPID_S1/S2/S3       -- switch datapath IDs
    S1_PORT_*, S2_PORT_*, S3_PORT_* -- per-switch port numbers
"""
import csv
import os
import socket
import time
import errno

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet, ether_types
from os_ken.lib import hub

# --------------------------------------------------------------------------
# Topology-dependent constants -- keep in sync with topology/topo.py
# --------------------------------------------------------------------------
H1_MAC = '00:00:00:00:00:01'
H2_MAC = '00:00:00:00:00:02'
H3_MAC = '00:00:00:00:00:03'      # only exists when topo.create_network(multi_client=True)

DPID_S1, DPID_S2, DPID_S3 = 1, 2, 3

S1_PORT_H1, S1_PORT_S2, S1_PORT_S3, S1_PORT_H3 = 1, 2, 3, 4
S2_PORT_H2, S2_PORT_S1, S2_PORT_S3 = 1, 2, 3
S3_PORT_S1, S3_PORT_S2 = 1, 2

# --------------------------------------------------------------------------
# Behaviour tuning
# --------------------------------------------------------------------------
POLL_INTERVAL = 1.0            # seconds between flow-stats polls
CONGESTION_THRESHOLD_BPS = 4_000_000   # ~4 Mbps, primary link is capped at 5Mbps
RECOVERY_POLLS = 5              # consecutive quiet polls before reverting to primary
ALERT_SOCK_PATH = '/tmp/bwm_controller_alert.sock'   # AF_UNIX, see note above
RESULTS_DIR = 'results'

# Operating mode (used for the static-baseline comparison):
#   dynamic : the proposed approach -- reroute on congestion (poll or app alert)
#   static  : baseline -- the flow stays on the primary path no matter what.
#             Polling and CSV logging still run, so the network-side
#             measurement is identical; only the reroute decision is disabled.
# Initial mode comes from the BWM_MODE environment variable; it can be
# switched at run time, without restarting, by sending "MODE:static" or
# "MODE:dynamic" to the AF_UNIX socket (see experiments/ctl.py).
MODES = ('dynamic', 'static')
DEFAULT_MODE = os.environ.get('BWM_MODE', 'dynamic').strip().lower()
if DEFAULT_MODE not in MODES:
    DEFAULT_MODE = 'dynamic'
STATS_CSV = os.path.join(RESULTS_DIR, 'controller_stats.csv')


class BandwidthAwareController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.active_path = 'primary'          # 'primary' or 'backup'
        self.mode = DEFAULT_MODE              # 'dynamic' or 'static' (baseline)
        self._static_flagged = False          # one marker row per congestion episode
        self._prev_byte_count = None
        self._last_rate_bps = 0.0             # most recent real poll reading; used so
                                               # event-marker rows show a real rate
                                               # instead of a misleading placeholder
        self._quiet_polls = 0

        os.makedirs(RESULTS_DIR, exist_ok=True)
        self._init_csv()

        self.monitor_thread = hub.spawn(self._monitor)
        self.alert_thread = hub.spawn(self._alert_listener)

    # ------------------------------------------------------------------
    # CSV logging
    # ------------------------------------------------------------------
    def _init_csv(self):
        write_header = not os.path.exists(STATS_CSV)
        with open(STATS_CSV, 'a', newline='') as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(['timestamp', 'dpid', 'rate_bps', 'active_path', 'event'])

    def _log(self, dpid, rate_bps, event=''):
        with open(STATS_CSV, 'a', newline='') as f:
            w = csv.writer(f)
            w.writerow([f'{time.time():.3f}', dpid, f'{rate_bps:.2f}',
                        self.active_path, event])

    # ------------------------------------------------------------------
    # Switch setup: table-miss, ARP flood, static measurement-flow route
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        self.datapaths[dp.id] = dp

        # Table-miss -> controller (fallback MAC learning, priority 0)
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, 0, match, actions)

        # ARP flood on every switch (priority 100)
        match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_ARP)
        actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
        self._add_flow(dp, 100, match, actions)

        if dp.id == DPID_S1:
            self._install_s1_route(dp, out_port=S1_PORT_S2)   # start on primary
            # Return traffic to the sender hosts (h3 only exists in the
            # multi-client topology; the rule is inert otherwise).
            for mac, port in ((H1_MAC, S1_PORT_H1), (H3_MAC, S1_PORT_H3)):
                match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, eth_dst=mac)
                self._add_flow(dp, 50, match, [parser.OFPActionOutput(port)])
        elif dp.id == DPID_S2:
            match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, eth_dst=H2_MAC)
            actions = [parser.OFPActionOutput(S2_PORT_H2)]
            self._add_flow(dp, 50, match, actions)
            # Replies to every sender host go back over the primary link.
            for mac in (H1_MAC, H3_MAC):
                match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, eth_dst=mac)
                actions = [parser.OFPActionOutput(S2_PORT_S1)]
                self._add_flow(dp, 50, match, actions)
        elif dp.id == DPID_S3:
            match = parser.OFPMatch(in_port=S3_PORT_S1)
            actions = [parser.OFPActionOutput(S3_PORT_S2)]
            self._add_flow(dp, 50, match, actions)
            match = parser.OFPMatch(in_port=S3_PORT_S2)
            actions = [parser.OFPActionOutput(S3_PORT_S1)]
            self._add_flow(dp, 50, match, actions)

        self.logger.info('Switch %s connected and configured', dp.id)

    def _install_s1_route(self, dp, out_port):
        """(Re)install the h1->h2 measurement flow on s1 with the given
        egress port. Same match+priority as any previous install, so this
        doubles as the reroute mechanism (OFPFC_ADD overwrites in OVS)."""
        parser = dp.ofproto_parser
        match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, eth_dst=H2_MAC)
        actions = [parser.OFPActionOutput(out_port)]
        self._add_flow(dp, 50, match, actions)

    def _add_flow(self, dp, priority, match, actions, idle_timeout=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=dp, priority=priority, match=match,
                                 instructions=inst, idle_timeout=idle_timeout)
        dp.send_msg(mod)

    # ------------------------------------------------------------------
    # Fallback L2 learning for any traffic not covered by the static rules
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dpid = dp.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][eth.src] = in_port

        out_port = self.mac_to_port[dpid].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=eth.dst)
            self._add_flow(dp, 1, match, actions, idle_timeout=30)

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                   in_port=in_port, actions=actions, data=data)
        dp.send_msg(out)

    # ------------------------------------------------------------------
    # Dynamic SDN functionality: periodic flow-stats polling + rerouting
    # ------------------------------------------------------------------
    def _monitor(self):
        hub.sleep(2)  # let switches connect first
        while True:
            dp = self.datapaths.get(DPID_S1)
            if dp:
                parser = dp.ofproto_parser
                match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, eth_dst=H2_MAC)
                req = parser.OFPFlowStatsRequest(dp, match=match)
                dp.send_msg(req)
            hub.sleep(POLL_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        dp = ev.msg.datapath
        if dp.id != DPID_S1:
            return
        byte_count = sum(stat.byte_count for stat in ev.msg.body)

        now = time.time()
        if self._prev_byte_count is not None:
            prev_bytes, prev_time = self._prev_byte_count
            dt = now - prev_time
            rate_bps = ((byte_count - prev_bytes) * 8 / dt) if dt > 0 else 0
            self._last_rate_bps = rate_bps
            self._log(DPID_S1, rate_bps)
            self._evaluate_congestion(rate_bps)
        self._prev_byte_count = (byte_count, now)

    def _evaluate_congestion(self, rate_bps):
        if self.mode == 'static':
            # Baseline: observe, never act. Record ONE marker row per
            # congestion episode so the results show the baseline *saw* the
            # congestion and (by design) did nothing about it.
            if rate_bps > CONGESTION_THRESHOLD_BPS and not self._static_flagged:
                self._static_flagged = True
                self._log(DPID_S1, rate_bps,
                          event=f'STATIC:congestion-no-action ({rate_bps/1e6:.2f} Mbps)')
            elif rate_bps < CONGESTION_THRESHOLD_BPS * 0.5:
                self._static_flagged = False
            return
        if self.active_path == 'primary' and rate_bps > CONGESTION_THRESHOLD_BPS:
            self._reroute('backup', reason=f'poll-triggered ({rate_bps/1e6:.2f} Mbps)')
            self._quiet_polls = 0
        elif self.active_path == 'backup':
            if rate_bps < CONGESTION_THRESHOLD_BPS * 0.5:
                self._quiet_polls += 1
            else:
                self._quiet_polls = 0
            if self._quiet_polls >= RECOVERY_POLLS:
                self._reroute('primary', reason='recovered, reverting')
                self._quiet_polls = 0

    def _set_mode(self, mode):
        """Switch between 'dynamic' and 'static' at run time and ALWAYS
        re-assert the primary path, so every experiment run starts from the
        same state regardless of what the previous run left behind."""
        self.mode = mode
        self._quiet_polls = 0
        self._static_flagged = False
        dp = self.datapaths.get(DPID_S1)
        if dp is not None:
            self._install_s1_route(dp, S1_PORT_S2)
        self.active_path = 'primary'
        self.logger.info('MODE -> %s (flow reset to primary path)', mode)
        self._log(DPID_S1, self._last_rate_bps, event=f'MODE->{mode}')

    def _reroute(self, new_path, reason=''):
        if self.mode == 'static' and new_path == 'backup':
            return                      # safety net: baseline never reroutes
        dp = self.datapaths.get(DPID_S1)
        if not dp or new_path == self.active_path:
            return
        out_port = S1_PORT_S3 if new_path == 'backup' else S1_PORT_S2
        self._install_s1_route(dp, out_port)
        self.active_path = new_path
        self.logger.info('REROUTE -> %s (%s)', new_path, reason)
        self._log(DPID_S1, self._last_rate_bps, event=f'REROUTE->{new_path}:{reason}')

    # ------------------------------------------------------------------
    # Socket-SDN integration: UDP alert channel driven by the receiver app
    # ------------------------------------------------------------------
    def _alert_listener(self):
        try:
            os.unlink(ALERT_SOCK_PATH)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(ALERT_SOCK_PATH)
        os.chmod(ALERT_SOCK_PATH, 0o666)   # receiver.py runs as a different user/host process
        self.logger.info('Alert listener on unix socket %s', ALERT_SOCK_PATH)
        while True:
            data, addr = sock.recvfrom(1024)
            self._handle_control_message(data, addr)

    def _handle_control_message(self, data, addr=None):
        """Parse one datagram from the AF_UNIX channel. Two message types:
            LOSS:<pct>:<flow_id>   -- from receiver.py (application event)
            MODE:<dynamic|static>  -- from experiments/ctl.py (experiment control)
        Kept separate from the socket loop so it can be unit-tested."""
        try:
            msg = data.decode().strip() if isinstance(data, bytes) else str(data).strip()
            kind, *rest = msg.split(':')
        except (UnicodeDecodeError, ValueError):
            return

        if kind == 'MODE':
            mode = rest[0].strip().lower() if rest else ''
            if mode in MODES:
                self._set_mode(mode)
            else:
                self.logger.warning('Ignoring MODE message with unknown mode %r', mode)
            return

        if kind == 'LOSS':
            try:
                loss_pct_s, flow_id = rest
                loss_pct = float(loss_pct_s)
            except (ValueError, TypeError):
                return
            self.logger.info('Alert from %s: %s', addr, msg)
            if self.mode == 'static':
                self.logger.info('  (ignored: static baseline mode)')
                return
            if self.active_path == 'primary' and loss_pct > 0:
                self._reroute('backup', reason=f'app-triggered (loss={loss_pct:.1f}% '
                                                f'from flow {flow_id})')

