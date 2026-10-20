#!/bin/sh
# DC1-DCI1: lab stand-in for the DCI switch (FRR in a Linux container), used as the other site's DCI. Rendered by Infrahub, do not edit.
set -eu
sysctl -qw net.ipv4.ip_forward=1
ip route del default dev eth0 2>/dev/null || true
ip addr replace 10.19.12.1/32 dev lo
ip link add po23 type bond mode 802.3ad 2>/dev/null || true
if [ -e /sys/class/net/eth23 ]; then ip link set eth23 down; ip link set eth23 mtu 9216; ip link set eth23 master po23; ip link set eth23 up; fi
if [ -e /sys/class/net/eth24 ]; then ip link set eth24 down; ip link set eth24 mtu 9216; ip link set eth24 master po23; ip link set eth24 up; fi
ip link set po23 mtu 9216 up
if [ -e /sys/class/net/eth1 ]; then ip link set eth1 mtu 9216; ip addr replace 10.19.12.32/31 dev eth1; ip link set eth1 up; fi
if [ -e /sys/class/net/eth25 ]; then ip link set eth25 mtu 9216; ip addr replace 10.0.255.0/31 dev eth25; ip link set eth25 up; fi
if [ -e /sys/class/net/po23 ]; then ip link set po23 mtu 9216; ip addr replace 10.19.12.16/31 dev po23; ip link set po23 up; fi
pgrep -x bfdd >/dev/null || /usr/lib/frr/bfdd -d -A 127.0.0.1
pgrep -x bgpd >/dev/null || /usr/lib/frr/bgpd -d -A 127.0.0.1
sleep 2
cat > /etc/frr/frr.conf <<'FRREOF'
hostname DC1-DCI1
service integrated-vtysh-config
!
ip prefix-list PL-LOOPBACK0 seq 10 permit 10.19.12.1/32
route-map RM-LOOPBACK0 permit 10
 match ip address prefix-list PL-LOOPBACK0
!
router bgp 65160
 bgp router-id 10.19.12.1
 no bgp ebgp-requires-policy
 neighbor 10.19.12.33 remote-as 65100
 neighbor 10.19.12.33 description DC1-BGW1
 neighbor 10.19.12.33 password cisco123
 neighbor 10.0.255.1 remote-as 65260
 neighbor 10.0.255.1 description DC2-DCI1
 neighbor 10.0.255.1 password cisco123
 neighbor 10.19.12.17 remote-as 65160
 neighbor 10.19.12.17 description DC1-DCI2
 neighbor 10.19.12.17 password cisco123
 address-family ipv4 unicast
  redistribute connected route-map RM-LOOPBACK0
  neighbor 10.19.12.17 next-hop-self
 exit-address-family
exit
FRREOF
vtysh -f /etc/frr/frr.conf
echo "DC1-DCI1 stand-in ready"
