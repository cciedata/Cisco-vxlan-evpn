#!/bin/sh
# DC1-VPNFW2: lab stand-in for the VPN-partner FortiGate (FRR in a Linux container). Rendered by Infrahub, do not edit.
# Mirrors the FortiOS routing config. Not mirrored: HA (unit 2 runs with its data ports down), mgmt1 (the container's eth0),
# firewall policies. Lab-only: GRE instead of IPsec per partner, iptables SNAT to each translated /28, 0/0 via 10.19.14.2.
set -eu
sysctl -qw net.ipv4.ip_forward=1
sysctl -qw net.ipv4.tcp_l3mdev_accept=1
ip route del default dev eth0 2>/dev/null || true
# --- VRF (FortiOS VRF 16 -> vrf16, table 1016); VRF 0 is the main table
ip link add vrf16 type vrf table 1016 2>/dev/null || true
ip link set vrf16 up
# --- aggregates (members that exist in the lab; the others stay absent)
ip link add agg-inside type bond mode 802.3ad 2>/dev/null || true
if [ -e /sys/class/net/port37 ]; then ip link set port37 down; ip link set port37 mtu 9216; ip link set port37 master agg-inside; ip link set port37 up; fi
if [ -e /sys/class/net/port38 ]; then ip link set port38 down; ip link set port38 mtu 9216; ip link set port38 master agg-inside; ip link set port38 up; fi
ip link set agg-inside mtu 9216 up
ip link add agg-outside type bond mode 802.3ad 2>/dev/null || true
if [ -e /sys/class/net/port39 ]; then ip link set port39 down; ip link set port39 mtu 9216; ip link set port39 master agg-outside; ip link set port39 up; fi
if [ -e /sys/class/net/port40 ]; then ip link set port40 down; ip link set port40 mtu 9216; ip link set port40 master agg-outside; ip link set port40 up; fi
ip link set agg-outside mtu 9216 up
# --- inside leg (VRF 16), outside leg and WAN loopback (main table)
ip link add link agg-inside name PARTNER-3300 type vlan id 3300 2>/dev/null || true
ip link set PARTNER-3300 master vrf16
ip addr replace 10.19.9.3/29 dev PARTNER-3300
ip link set PARTNER-3300 mtu 9216 up
ip link add link agg-outside name OUTSIDE-3200 type vlan id 3200 2>/dev/null || true
ip addr replace 10.19.14.12/24 dev OUTSIDE-3200
ip link set OUTSIDE-3200 mtu 9216 up
ip link add LO-WAN type dummy 2>/dev/null || true
ip addr replace 198.51.100.129/32 dev LO-WAN
ip link set LO-WAN up
# --- one tunnel per partner (lab-only GRE; FortiOS: route-based IKEv2 on LO-WAN) in VRF 16, SNAT to its /28
command -v iptables >/dev/null || apk add --no-cache iptables >/dev/null 2>&1 || echo "WARNING: iptables not available (no package mirror): partner source NAT not set"
ip tunnel add PARTNER01 mode gre local 198.51.100.129 remote 198.18.100.1 ttl 64 2>/dev/null || true
ip link set PARTNER01 master vrf16
ip link set PARTNER01 up
if command -v iptables >/dev/null; then iptables -t nat -C POSTROUTING -o PARTNER01 -j SNAT --to-source 10.16.160.1-10.16.160.14 2>/dev/null || iptables -t nat -A POSTROUTING -o PARTNER01 -j SNAT --to-source 10.16.160.1-10.16.160.14; fi
# --- routing daemons (watchfrr starts zebra and staticd; bgpd and bfdd are started here)
pgrep -x bfdd >/dev/null || /usr/lib/frr/bfdd -d -A 127.0.0.1
pgrep -x bgpd >/dev/null || /usr/lib/frr/bgpd -d -A 127.0.0.1
sleep 2
cat > /etc/frr/frr.conf <<'FRREOF'
hostname DC1-VPNFW2
service integrated-vtysh-config
!
ip route 0.0.0.0/0 10.19.14.2
vrf vrf16
 ip route 192.168.100.0/24 PARTNER01
 ip route 10.16.160.0/28 blackhole
exit-vrf
!
ip prefix-list PL-DEFAULT seq 10 permit 0.0.0.0/0
ip prefix-list PL-PARTNER-NAT seq 10 permit 10.16.160.0/28
!
route-map RM-PARTNER-FROM-LEAF permit 10
 match ip address prefix-list PL-DEFAULT
route-map RM-PARTNER-TO-LEAF permit 10
 match ip address prefix-list PL-PARTNER-NAT
!
bfd
 profile FW-BFD
  transmit-interval 999
  receive-interval 999
  detect-multiplier 3
  no echo-mode
 exit
exit
!
router bgp 65130 vrf vrf16
 bgp router-id 10.19.9.3
 bgp graceful-restart
 neighbor 10.19.9.1 remote-as 65100
 neighbor 10.19.9.1 description DC1-BL1
 neighbor 10.19.9.1 password cisco123
 neighbor 10.19.9.1 update-source PARTNER-3300
 neighbor 10.19.9.1 bfd profile FW-BFD
 neighbor 10.19.9.2 remote-as 65100
 neighbor 10.19.9.2 description DC1-BL2
 neighbor 10.19.9.2 password cisco123
 neighbor 10.19.9.2 update-source PARTNER-3300
 neighbor 10.19.9.2 bfd profile FW-BFD
 address-family ipv4 unicast
  network 10.16.160.0/28
  neighbor 10.19.9.1 route-map RM-PARTNER-FROM-LEAF in
  neighbor 10.19.9.1 route-map RM-PARTNER-TO-LEAF out
  neighbor 10.19.9.2 route-map RM-PARTNER-FROM-LEAF in
  neighbor 10.19.9.2 route-map RM-PARTNER-TO-LEAF out
 exit-address-family
exit
FRREOF
vtysh -f /etc/frr/frr.conf
echo "DC1-VPNFW2 stand-in ready"
