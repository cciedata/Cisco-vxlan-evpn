#!/bin/sh
# DC2-ISP1: lab stand-in for the IOS XR ISP router (FRR in a Linux container). Rendered by Infrahub, do not edit.
# Mirrors the IOS XR routing config. Not mirrored: the management VRF (the container's eth0; its default route is removed so
# 0.0.0.0/0 in the routing table comes only from BGP), VRRP (FRR vrrpd has no preempt delay or interface tracking; no lab node uses the VIP).
# Port names: FourHundredGigE0/0/0/0 -> Gi0-0-0-0, FourHundredGigE0/0/0/2 -> Gi0-0-0-1, FourHundredGigE0/0/0/4 -> Gi0-0-0-2, FourHundredGigE0/0/0/6 -> Gi0-0-0-3, Bundle-Ether21 -> BE21.
set -eu
sysctl -qw net.ipv4.ip_forward=1
ip route del default dev eth0 2>/dev/null || true
# --- Loopback0
ip addr replace 10.23.15.17/32 dev lo
# --- Bundle-Ether21 (LACP; members that exist in the lab) and its sub-interface Bundle-Ether21.3200
ip link add BE21 type bond mode 802.3ad 2>/dev/null || true
if [ -e /sys/class/net/Gi0-0-0-0 ]; then ip link set Gi0-0-0-0 down; ip link set Gi0-0-0-0 mtu 9216; ip link set Gi0-0-0-0 master BE21; ip link set Gi0-0-0-0 up; fi
if [ -e /sys/class/net/Gi0-0-0-1 ]; then ip link set Gi0-0-0-1 down; ip link set Gi0-0-0-1 mtu 9216; ip link set Gi0-0-0-1 master BE21; ip link set Gi0-0-0-1 up; fi
ip link set BE21 mtu 9216 up
ip link add link BE21 name BE21.3200 type vlan id 3200 2>/dev/null || true
ip addr replace 10.23.14.2/24 dev BE21.3200
ip link set BE21.3200 mtu 9216 up
# --- cross-link FourHundredGigE0/0/0/4 and carrier link FourHundredGigE0/0/0/6
if [ -e /sys/class/net/Gi0-0-0-2 ]; then ip link set Gi0-0-0-2 mtu 9216; ip addr replace 10.23.15.0/31 dev Gi0-0-0-2; ip link set Gi0-0-0-2 up; fi
if [ -e /sys/class/net/Gi0-0-0-3 ]; then ip link set Gi0-0-0-3 mtu 9216; ip addr replace 192.0.2.10/30 dev Gi0-0-0-3; ip link set Gi0-0-0-3 up; fi
# --- routing daemons (watchfrr starts zebra and staticd; bgpd and bfdd are started here)
pgrep -x bfdd >/dev/null || /usr/lib/frr/bfdd -d -A 127.0.0.1
pgrep -x bgpd >/dev/null || /usr/lib/frr/bgpd -d -A 127.0.0.1
sleep 2
cat > /etc/frr/frr.conf <<'FRREOF'
hostname DC2-ISP1
service integrated-vtysh-config
!
ip route 203.0.113.0/24 blackhole
ip route 203.0.113.0/25 10.23.14.10
ip route 203.0.113.160/27 10.23.14.10
ip route 203.0.113.144/28 10.23.14.11
ip route 203.0.113.128/28 10.23.14.12
!
ip prefix-list PL-OWN-PUBLIC seq 10 permit 203.0.113.0/24
ip prefix-list PL-DEFAULT seq 10 permit 0.0.0.0/0
!
bgp as-path access-list AS-LOCAL seq 10 permit ^$
!
route-map RM-CARRIER-IN permit 10
route-map RM-CARRIER-OUT permit 10
 match ip address prefix-list PL-OWN-PUBLIC
 match as-path AS-LOCAL
route-map RM-INETFW-IN permit 10
 match ip address prefix-list PL-OWN-PUBLIC
route-map RM-INETFW-OUT deny 10
route-map RM-COND-DEFAULT permit 10
 match ip address prefix-list PL-DEFAULT
!
bfd
 profile ISP-BFD
  transmit-interval 999
  receive-interval 999
  detect-multiplier 3
  no echo-mode
 exit
exit
!
router bgp 65050
 bgp router-id 10.23.15.17
 bgp graceful-restart
 neighbor 192.0.2.9 remote-as 64496
 neighbor 192.0.2.9 description ISP-A
 neighbor 192.0.2.9 password cisco123
 neighbor 192.0.2.9 bfd profile ISP-BFD
 neighbor 10.23.15.1 remote-as 65050
 neighbor 10.23.15.1 description DC2-ISP2
 neighbor 10.23.15.1 bfd profile ISP-BFD
 neighbor 10.23.14.10 remote-as 65240
 neighbor 10.23.14.10 description DC2-INETFW
 neighbor 10.23.14.10 password cisco123
 neighbor 10.23.14.10 bfd profile ISP-BFD
 address-family ipv4 unicast
  network 203.0.113.0/24
  neighbor 192.0.2.9 route-map RM-CARRIER-IN in
  neighbor 192.0.2.9 maximum-prefix 1200000 90 warning-only
  neighbor 192.0.2.9 route-map RM-CARRIER-OUT out
  neighbor 10.23.15.1 next-hop-self
  neighbor 10.23.14.10 route-map RM-INETFW-IN in
  neighbor 10.23.14.10 default-originate route-map RM-COND-DEFAULT
  neighbor 10.23.14.10 route-map RM-INETFW-OUT out
 exit-address-family
exit
FRREOF
vtysh -f /etc/frr/frr.conf
echo "DC2-ISP1 stand-in ready"
