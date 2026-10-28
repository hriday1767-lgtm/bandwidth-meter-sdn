#!/usr/bin/env python3
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

PRIMARY_BW = 5
BACKUP_BW = 20
LINK_DELAY = '2ms'

def create_network(controller_ip='127.0.0.1', controller_port=6653):
    net = Mininet(controller=RemoteController, switch=OVSSwitch,
                  link=TCLink, autoSetMacs=True, build=False)

    net.addController('c0', controller=RemoteController,
                       ip=controller_ip, port=controller_port)

    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')

    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')

    net.addLink(h1, s1)
    net.addLink(h2, s2)
    net.addLink(s1, s2, bw=PRIMARY_BW, delay=LINK_DELAY)
    net.addLink(s1, s3, bw=BACKUP_BW, delay=LINK_DELAY)
    net.addLink(s3, s2, bw=BACKUP_BW, delay=LINK_DELAY)

    net.build()
    return net

def main():
    setLogLevel('info')
    net = create_network()
    net.start()
    CLI(net)
    net.stop()

if __name__ == '__main__':
    main()