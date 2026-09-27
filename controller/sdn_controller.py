#!/usr/bin/env python3
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

H1_MAC = '00:00:00:00:00:01'
H2_MAC = '00:00:00:00:00:02'

DPID_S1, DPID_S2, DPID_S3 = 1, 2, 3
S1_PORT_H1, S1_PORT_S2, S1_PORT_S3 = 1, 2, 3
S2_PORT_H2, S2_PORT_S1, S2_PORT_S3 = 1, 2, 3
S3_PORT_S1, S3_PORT_S2 = 1, 2

POLL_INTERVAL = 1.0
CONGESTION_THRESHOLD_BPS = 4_000_000
RECOVERY_POLLS = 5
ALERT_SOCK_PATH = '/tmp/bwm_controller_alert.sock'
RESULTS_DIR = 'results'
STATS_CSV = os.path.join(RESULTS_DIR, 'controller_stats.csv')

class BandwidthAwareController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.active_path = 'primary'
        self._prev_byte_count = None
        self._last_rate_bps = 0.0
        self._quiet_polls = 0

        os.makedirs(RESULTS_DIR, exist_ok=True)
        self._init_csv()

        self.monitor_thread = hub.spawn(self._monitor)
        self.alert_thread = hub.spawn(self._alert_listener)

    def _init_csv(self):
        write_header = not os.path.exists(STATS_CSV)
        with open(STATS_CSV, 'a', newline='') as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(['timestamp', 'dpid', 'rate_bps', 'active_path', 'event'])

    def _log(self, dpid, rate_bps, event=''):
        with open(STATS_CSV, 'a', newline='') as f:
            w = csv.writer(f)
            w.writerow([f'{time.time():.3f}', dpid, f'{rate_bps:.2f}', self.active_path, event])

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        self.datapaths[dp.id] = dp

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, 0, match, actions)

        match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_ARP)
        actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
        self._add_flow(dp, 100, match, actions)

        if dp.id == DPID_S1:
            self._install_s1_route(dp, out_port=S1_PORT_S2)
        elif dp.id == DPID_S2:
            match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, eth_dst=H2_MAC)
            actions = [parser.OFPActionOutput(S2_PORT_H2)]
            self._add_flow(dp, 50, match, actions)
            match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, eth_dst=H1_MAC)
            actions = [parser.OFPActionOutput(S2_PORT_S1)]
            self._add_flow(dp, 50, match, actions)
        elif dp.id == DPID_S3:
            match = parser.OFPMatch(in_port=S3_PORT_S1)
            actions = [parser.OFPActionOutput(S3_PORT_S2)]
            self._add_flow(dp, 50, match, actions)
            match = parser.OFPMatch(in_port=S3_PORT_S2)
            actions = [parser.OFPActionOutput(S3_PORT_S1)]
            self._add_flow(dp, 50, match, actions)

    def _install_s1_route(self, dp, out_port):
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

    def _monitor(self):
        hub.sleep(2)
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

    def _reroute(self, new_path, reason=''):
        dp = self.datapaths.get(DPID_S1)
        if not dp or new_path == self.active_path:
            return
        out_port = S1_PORT_S3 if new_path == 'backup' else S1_PORT_S2
        self._install_s1_route(dp, out_port)
        self.active_path = new_path
        self._log(DPID_S1, self._last_rate_bps, event=f'REROUTE->{new_path}:{reason}')

    def _alert_listener(self):
        try:
            os.unlink(ALERT_SOCK_PATH)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(ALERT_SOCK_PATH)
        os.chmod(ALERT_SOCK_PATH, 0o666)
        while True:
            data, addr = sock.recvfrom(1024)
            try:
                msg = data.decode().strip()
                _, loss_pct_s, flow_id = msg.split(':')
                loss_pct = float(loss_pct_s)
            except (ValueError, IndexError):
                continue
            if self.active_path == 'primary' and loss_pct > 0:
                self._reroute('backup', reason=f'app-triggered (loss={loss_pct:.1f}% from flow {flow_id})')