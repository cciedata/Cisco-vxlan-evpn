#!/bin/sh
# DC2-SDWANFW2: lab stand-in for the SD-WAN hub FortiGate (FRR in a Linux container). Rendered by Infrahub, do not edit.
# Mirrors the FortiOS routing config. Not mirrored: HA (unit 2 runs with its data ports down), mgmt1 (the container's eth0),
# firewall policies. Lab-only: GRE instead of the ADVPN IPsec hub (ADVPN-HUB to 198.18.3.10); 0/0 via 10.23.14.2 instead of the VRRP VIP.
set -eu
sysctl -qw net.ipv4.ip_forward=1
sysctl -qw net.ipv4.tcp_l3mdev_accept=1
ip route del default dev eth0 2>/dev/null || true
# --- VRF (FortiOS VRF 1 -> vrf1, table 1001); VRF 0 is the main table
ip link add vrf1 type vrf table 1001 2>/dev/null || true
ip link set vrf1 up
# --- aggregates (members that exist in the lab; the others stay absent)
ip link add agg-inside type bond mode 802.3ad 2>/dev/null || true
if [ -e /sys/class/net/port37 ]; then ip link set port37 down; ip link set port37 mtu 9216; ip link set port37 master agg-inside; ip link set port37 up; fi
if [ -e /sys/class/net/port38 ]; then ip link set port38 down; ip link set port38 mtu 9216; ip link set port38 master agg-inside; ip link set port38 up; fi
ip link set agg-inside mtu 9216 up
ip link add agg-outside type bond mode 802.3ad 2>/dev/null || true
if [ -e /sys/class/net/port39 ]; then ip link set port39 down; ip link set port39 mtu 9216; ip link set port39 master agg-outside; ip link set port39 up; fi
if [ -e /sys/class/net/port40 ]; then ip link set port40 down; ip link set port40 mtu 9216; ip link set port40 master agg-outside; ip link set port40 up; fi
ip link set agg-outside mtu 9216 up
# --- inside leg (VRF 1), outside leg and WAN loopback (main table)
ip link add link agg-inside name TRANSIT-3002 type vlan id 3002 2>/dev/null || true
ip link set TRANSIT-3002 master vrf1
ip addr replace 10.23.16.19/29 dev TRANSIT-3002
ip link set TRANSIT-3002 mtu 9216 up
ip link add link agg-outside name OUTSIDE-3200 type vlan id 3200 2>/dev/null || true
ip addr replace 10.23.14.11/24 dev OUTSIDE-3200
ip link set OUTSIDE-3200 mtu 9216 up
ip link add LO-WAN type dummy 2>/dev/null || true
ip addr replace 203.0.113.145/32 dev LO-WAN
ip link set LO-WAN up
# --- hub tunnel (lab-only GRE; FortiOS: ADVPN 2.0 dynamic IPsec on LO-WAN), overlay 10.79.8.0/21 in VRF 1
ip tunnel add ADVPN-HUB mode gre local 203.0.113.145 remote 198.18.3.10 ttl 64 2>/dev/null || true
ip link set ADVPN-HUB master vrf1
ip addr replace 10.79.8.1/21 dev ADVPN-HUB
ip link set ADVPN-HUB up
# --- routing daemons (watchfrr starts zebra and staticd; bgpd and bfdd are started here)
pgrep -x bfdd >/dev/null || /usr/lib/frr/bfdd -d -A 127.0.0.1
pgrep -x bgpd >/dev/null || /usr/lib/frr/bgpd -d -A 127.0.0.1
sleep 2
cat > /etc/frr/frr.conf <<'FRREOF'
hostname DC2-SDWANFW2
service integrated-vtysh-config
!
ip route 0.0.0.0/0 10.23.14.2
!
ip prefix-list PL-SDWAN-FROM-LEAF seq 10 permit 10.20.144.0/20
ip prefix-list PL-SDWAN-FROM-LEAF seq 20 permit 10.20.64.0/18
ip prefix-list PL-SDWAN-FROM-LEAF seq 30 permit 10.20.0.0/18
ip prefix-list PL-SDWAN-FROM-LEAF seq 40 permit 10.16.64.0/18
ip prefix-list PL-SDWAN-FROM-LEAF seq 50 permit 10.16.0.0/18
ip prefix-list PL-SDWAN-FROM-LEAF seq 60 permit 172.16.0.0/13 le 24
ip prefix-list PL-SDWAN-FROM-LEAF seq 70 permit 172.30.0.0/15 le 24
ip prefix-list PL-SDWAN-ALLOW seq 10 permit 10.20.144.0/20
ip prefix-list PL-SDWAN-ALLOW seq 20 permit 10.20.64.0/18
ip prefix-list PL-SDWAN-ALLOW seq 30 permit 10.20.0.0/18
ip prefix-list PL-SDWAN-ALLOW seq 40 permit 10.16.64.0/18
ip prefix-list PL-SDWAN-ALLOW seq 50 permit 10.16.0.0/18
ip prefix-list PL-SDWAN-ALLOW seq 60 permit 172.16.0.0/13
ip prefix-list PL-SDWAN-ALLOW seq 70 permit 172.30.0.0/15
ip prefix-list PL-BRANCH-LAN seq 10 permit 10.64.0.0/12 le 24
ip prefix-list PL-BRANCH-SUMMARY seq 10 permit 10.64.0.0/12
!
route-map RM-TRANSIT-FROM-LEAF permit 10
 match ip address prefix-list PL-SDWAN-FROM-LEAF
route-map RM-TRANSIT-TO-LEAF permit 10
 match ip address prefix-list PL-BRANCH-SUMMARY
route-map RM-FROM-BRANCH permit 10
 match ip address prefix-list PL-BRANCH-LAN
route-map RM-TO-BRANCH permit 10
 match ip address prefix-list PL-SDWAN-ALLOW
route-map RM-TO-BRANCH permit 20
 match ip address prefix-list PL-BRANCH-LAN
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
router bgp 65400 vrf vrf1
 bgp router-id 10.23.16.19
 bgp graceful-restart
 neighbor BRANCHES peer-group
 neighbor BRANCHES remote-as 65400
 neighbor BRANCHES password cisco123
 bgp listen range 10.79.8.0/21 peer-group BRANCHES
 neighbor 10.23.16.17 remote-as 65200
 neighbor 10.23.16.17 local-as 65220 no-prepend replace-as
 neighbor 10.23.16.17 description DC2-BL1
 neighbor 10.23.16.17 password cisco123
 neighbor 10.23.16.17 update-source TRANSIT-3002
 neighbor 10.23.16.17 bfd profile FW-BFD
 neighbor 10.23.16.18 remote-as 65200
 neighbor 10.23.16.18 local-as 65220 no-prepend replace-as
 neighbor 10.23.16.18 description DC2-BL2
 neighbor 10.23.16.18 password cisco123
 neighbor 10.23.16.18 update-source TRANSIT-3002
 neighbor 10.23.16.18 bfd profile FW-BFD
 address-family ipv4 unicast
  aggregate-address 10.64.0.0/12
  aggregate-address 172.16.0.0/13 summary-only
  aggregate-address 172.30.0.0/15 summary-only
  neighbor 10.23.16.17 route-map RM-TRANSIT-FROM-LEAF in
  neighbor 10.23.16.17 route-map RM-TRANSIT-TO-LEAF out
  neighbor 10.23.16.18 route-map RM-TRANSIT-FROM-LEAF in
  neighbor 10.23.16.18 route-map RM-TRANSIT-TO-LEAF out
  neighbor BRANCHES next-hop-self
  neighbor BRANCHES route-reflector-client
  neighbor BRANCHES route-map RM-FROM-BRANCH in
  neighbor BRANCHES route-map RM-TO-BRANCH out
 exit-address-family
exit
FRREOF
vtysh -f /etc/frr/frr.conf
echo "DC2-SDWANFW2 stand-in ready"
