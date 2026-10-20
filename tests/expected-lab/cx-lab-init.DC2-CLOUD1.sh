#!/bin/sh
# DC2-CX1: lab stand-in for the cloud exchange behind DC2-CLOUD1 (FRR in a Linux container). Rendered by Infrahub from DC2-CLOUD1's
# data, do not edit. Port "cr" toward DC2-CLOUD1; VRF aws = AWS (AS 65500), VRF azure = Microsoft (AS 12076).
set -eu
sysctl -qw net.ipv4.ip_forward=1
sysctl -qw net.ipv4.tcp_l3mdev_accept=1
ip route del default dev eth0 2>/dev/null || true
ip link set cr mtu 9216 up
ip link add aws type vrf table 1001 2>/dev/null || true
ip link set aws up
ip link add link cr name cr.110 type vlan id 110 2>/dev/null || true
ip link set cr.110 master aws
ip addr replace 169.254.110.1/30 dev cr.110
ip link set cr.110 up
ip link add aws-net type dummy 2>/dev/null || true
ip link set aws-net master aws
ip addr replace 172.16.1.1/24 dev aws-net
ip link set aws-net up
ip link add azure type vrf table 1002 2>/dev/null || true
ip link set azure up
ip link add link cr name cr.210 type vlan id 210 2>/dev/null || true
ip link set cr.210 master azure
ip addr replace 10.98.0.10/30 dev cr.210
ip link set cr.210 up
ip link add azure-net type dummy 2>/dev/null || true
ip link set azure-net master azure
ip addr replace 172.30.1.1/24 dev azure-net
ip link set azure-net up
pgrep -x bfdd >/dev/null || /usr/lib/frr/bfdd -d -A 127.0.0.1
pgrep -x bgpd >/dev/null || /usr/lib/frr/bgpd -d -A 127.0.0.1
sleep 2
cat > /etc/frr/frr.conf <<'FRREOF'
hostname DC2-CX1
service integrated-vtysh-config
!
bfd
 profile CLOUD-BFD
  transmit-interval 300
  receive-interval 300
  detect-multiplier 3
  no echo-mode
 exit
exit
!
router bgp 65500 vrf aws
 bgp router-id 169.254.110.1
 no bgp ebgp-requires-policy
 neighbor 169.254.110.2 remote-as 65270
 neighbor 169.254.110.2 description DC2-CLOUD1 HundredGigE0/0/0/16.110
 neighbor 169.254.110.2 password cisco123
 neighbor 169.254.110.2 bfd profile CLOUD-BFD
 address-family ipv4 unicast
  network 172.16.1.0/24
 exit-address-family
exit
!
router bgp 12076 vrf azure
 bgp router-id 10.98.0.10
 no bgp ebgp-requires-policy
 neighbor 10.98.0.9 remote-as 65270
 neighbor 10.98.0.9 description DC2-CLOUD1 HundredGigE0/0/0/16.210
 neighbor 10.98.0.9 password cisco123
 neighbor 10.98.0.9 bfd profile CLOUD-BFD
 address-family ipv4 unicast
  network 172.30.1.0/24
 exit-address-family
exit
!
FRREOF
vtysh -f /etc/frr/frr.conf
echo "DC2-CX1 stand-in ready"
