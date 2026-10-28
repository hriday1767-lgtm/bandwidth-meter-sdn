#!/usr/bin/env python3
r"""
Mininet topology for the Bandwidth Meter and Traffic Analyzer project.

                     s1 ===primary(5Mbit)=== s2
                    /                          \
                  h1                            h2
                    \                          /
                     s1 --backup(20Mbit)-- s3 -- s2

Two switch-disjoint paths exist between h1 and h2:
  * primary : s1 <-> s2 direct link, deliberately narrow (5 Mbit) so it
              can be congested by the traffic generator.
  * backup  : s1 <-> s3 <-> s2, generous bandwidth (20 Mbit).

Fixed port numbering (the controller hard-codes these, so do not
reorder the addLink() calls without updating controller/sdn_controller.py):
  s1: port 1 -> h1 , port 2 -> s2 (primary) , port 3 -> s3 (backup)
  s2: port 1 -> h2 , port 2 -> s1 (primary) , port 3 -> s3 (backup)
  s3: port 1 -> s1 , port 2 -> s2

With autoSetMacs=True, Mininet assigns host MACs in creation order:
  h1 -> 00:00:00:00:00:01
  h2 -> 00:00:00:00:00:02
(Verify with `h1 ifconfig` inside the CLI if your Mininet version differs,
and update H1_MAC / H2_MAC at the top of controller/sdn_controller.py.)

Optional second sender (multi-client test):  h3 -- s1 (port 4), 10.0.0.3,
MAC 00:00:00:00:00:03.  Enable with create_network(multi_client=True) or
`sudo python3 topo.py --multi-client`.

Standalone use:
    sudo python3 topo.py
Requires an OpenFlow 1.3 controller (Ryu) already listening on
127.0.0.1:6653 -- start controller/sdn_controller.py first.
"""
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

PRIMARY_BW = 5      # Mbit/s -- intentionally narrow so it can be congested
BACKUP_BW = 20       # Mbit/s -- generous backup path
LINK_DELAY = '2ms'


def create_network(controller_ip='127.0.0.1', controller_port=6653, multi_client=False):
    """Build (but do not start) the Mininet network. Returns the Mininet
    object so callers (topo.py CLI, experiments/run_experiment.py) can
    start/stop it themselves.

    multi_client=True adds a second sender, h3 (10.0.0.3, MAC
    00:00:00:00:00:03), attached to s1 on port 4. Its link is added LAST so
    the port numbers of every existing link are unchanged."""
    net = Mininet(controller=RemoteController, switch=OVSSwitch,
                  link=TCLink, autoSetMacs=True, build=False)

    net.addController('c0', controller=RemoteController,
                       ip=controller_ip, port=controller_port)

    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')

    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')

    # Order matters -- see port-numbering comment above.
    net.addLink(h1, s1)                                       # s1 port 1
    net.addLink(h2, s2)                                       # s2 port 1
    net.addLink(s1, s2, bw=PRIMARY_BW, delay=LINK_DELAY)       # s1 port 2 <-> s2 port 2
    net.addLink(s1, s3, bw=BACKUP_BW, delay=LINK_DELAY)        # s1 port 3 <-> s3 port 1
    net.addLink(s3, s2, bw=BACKUP_BW, delay=LINK_DELAY)        # s3 port 2 <-> s2 port 3

    if multi_client:
        h3 = net.addHost('h3', ip='10.0.0.3/24')
        net.addLink(h3, s1)                                    # s1 port 4

    net.build()
    return net


def main():
    import sys
    multi = '--multi-client' in sys.argv
    setLogLevel('info')
    net = create_network(multi_client=multi)
    net.start()

    info('*** Port mapping actually assigned by Mininet (sanity-check this) ***\n')
    for name in ('s1', 's2', 's3'):
        sw = net.get(name)
        info(f'{name}: {sw.intfNames()}\n')
    for name in (('h1', 'h2', 'h3') if multi else ('h1', 'h2')):
        h = net.get(name)
        info(f'{name}: MAC={h.MAC()} IP={h.IP()}\n')

    CLI(net)
    net.stop()


if __name__ == '__main__':
    main()

