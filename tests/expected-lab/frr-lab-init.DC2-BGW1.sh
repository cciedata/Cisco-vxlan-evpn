#!/bin/sh
# DC2-BGW1: lab stand-in for the border gateway (FRR in a Linux container), used as the other site's BGW: control plane only.
# Rendered by Infrahub, do not edit.
set -eu
sysctl -qw net.ipv4.ip_forward=1
ip route del default dev eth0 2>/dev/null || true
ip addr replace 10.23.0.27/32 dev lo
ip addr replace 10.23.1.25/32 dev lo
ip addr replace 10.23.2.129/32 dev lo
if [ -e /sys/class/net/eth3 ]; then ip link set eth3 mtu 9216; ip addr replace 10.23.12.33/31 dev eth3; ip link set eth3 up; fi
pgrep -x bgpd >/dev/null || /usr/lib/frr/bgpd -d -A 127.0.0.1
sleep 2
cat > /etc/frr/frr.conf <<'FRREOF'
hostname DC2-BGW1
service integrated-vtysh-config
!
ip prefix-list PL-LOOPBACKS seq 10 permit 10.23.0.27/32
ip prefix-list PL-LOOPBACKS seq 20 permit 10.23.1.25/32
ip prefix-list PL-LOOPBACKS seq 30 permit 10.23.2.129/32
route-map RM-UNDERLAY-TO-DCI permit 10
 match ip address prefix-list PL-LOOPBACKS
!
router bgp 65200
 bgp router-id 10.23.0.27
 no bgp ebgp-requires-policy
 neighbor 10.23.12.32 remote-as 65260
 neighbor 10.23.12.32 description DC2-DCI1
 neighbor 10.23.12.32 password cisco123
 neighbor 10.19.0.27 remote-as 65100
 neighbor 10.19.0.27 description DC1-BGW1
 neighbor 10.19.0.27 password cisco123
 neighbor 10.19.0.27 update-source 10.23.0.27
 neighbor 10.19.0.27 ebgp-multihop 64
 neighbor 10.19.0.28 remote-as 65100
 neighbor 10.19.0.28 description DC1-BGW2
 neighbor 10.19.0.28 password cisco123
 neighbor 10.19.0.28 update-source 10.23.0.27
 neighbor 10.19.0.28 ebgp-multihop 64
 address-family ipv4 unicast
  redistribute connected route-map RM-UNDERLAY-TO-DCI
  no neighbor 10.19.0.27 activate
  no neighbor 10.19.0.28 activate
 exit-address-family
 address-family l2vpn evpn
  neighbor 10.19.0.27 activate
  neighbor 10.19.0.28 activate
 exit-address-family
exit
FRREOF
vtysh -f /etc/frr/frr.conf
echo "DC2-BGW1 stand-in ready"
