#!/bin/sh
# DC1-CLOUD2: lab stand-in for the IOS XR cloud router (FRR in a Linux container). Rendered by Infrahub, do not edit.
# Mirrors the IOS XR routing config. Not mirrored: the management VRF (the container's eth0; its default route is removed).
# Port names: FourHundredGigE0/0/0/0 -> Gi0-0-0-0, FourHundredGigE0/0/0/2 -> Gi0-0-0-1, FourHundredGigE0/0/0/4 -> Gi0-0-0-2, HundredGigE0/0/0/16 -> Hu0-0-0-16, Bundle-Ether31 -> BE31.
set -eu
sysctl -qw net.ipv4.ip_forward=1
ip route del default dev eth0 2>/dev/null || true
ip addr replace 10.19.13.2/32 dev lo
# --- Bundle-Ether31 (LACP; members that exist in the lab) and its sub-interface Bundle-Ether31.3004
ip link add BE31 type bond mode 802.3ad 2>/dev/null || true
if [ -e /sys/class/net/Gi0-0-0-0 ]; then ip link set Gi0-0-0-0 down; ip link set Gi0-0-0-0 mtu 9216; ip link set Gi0-0-0-0 master BE31; ip link set Gi0-0-0-0 up; fi
if [ -e /sys/class/net/Gi0-0-0-1 ]; then ip link set Gi0-0-0-1 down; ip link set Gi0-0-0-1 mtu 9216; ip link set Gi0-0-0-1 master BE31; ip link set Gi0-0-0-1 up; fi
ip link set BE31 mtu 9216 up
ip link add link BE31 name BE31.3004 type vlan id 3004 2>/dev/null || true
ip addr replace 10.19.16.35/29 dev BE31.3004
ip link set BE31.3004 mtu 9216 up
# --- cross-link FourHundredGigE0/0/0/4
if [ -e /sys/class/net/Gi0-0-0-2 ]; then ip link set Gi0-0-0-2 mtu 9216; ip addr replace 10.19.13.17/31 dev Gi0-0-0-2; ip link set Gi0-0-0-2 up; fi
# --- cloud-exchange port HundredGigE0/0/0/16 and its handoffs
if [ -e /sys/class/net/Hu0-0-0-16 ]; then
ip link set Hu0-0-0-16 mtu 9216 up
ip link add link Hu0-0-0-16 name Hu0-0-0-16.101 type vlan id 101 2>/dev/null || true
ip addr replace 169.254.101.2/30 dev Hu0-0-0-16.101
ip link set Hu0-0-0-16.101 mtu 1500 up
ip link add link Hu0-0-0-16 name Hu0-0-0-16.200 type vlan id 200 2>/dev/null || true
ip addr replace 10.98.0.5/30 dev Hu0-0-0-16.200
ip link set Hu0-0-0-16.200 mtu 1500 up
fi
# --- routing daemons (watchfrr starts zebra and staticd; bgpd and bfdd are started here)
pgrep -x bfdd >/dev/null || /usr/lib/frr/bfdd -d -A 127.0.0.1
pgrep -x bgpd >/dev/null || /usr/lib/frr/bgpd -d -A 127.0.0.1
sleep 2
cat > /etc/frr/frr.conf <<'FRREOF'
hostname DC1-CLOUD2
service integrated-vtysh-config
!
ip prefix-list PS-GUARDRAIL seq 10 permit 10.19.0.0/16 le 32
ip prefix-list PS-GUARDRAIL seq 20 permit 10.23.0.0/16 le 32
ip prefix-list PS-GUARDRAIL seq 30 permit 10.0.0.0/16 le 32
ip prefix-list PS-DEFAULT seq 10 permit 0.0.0.0/0
ip prefix-list PS-AWS seq 10 permit 172.16.0.0/13 le 24
ip prefix-list PS-AZURE seq 10 permit 172.30.0.0/15 le 24
ip prefix-list PS-TO-CLOUD seq 10 permit 0.0.0.0/0
ip prefix-list PS-TO-CLOUD seq 20 permit 10.16.0.0/16
ip prefix-list PS-TO-CLOUD seq 30 permit 10.16.160.0/21
ip prefix-list PS-FROM-FABRIC seq 10 permit 0.0.0.0/0
ip prefix-list PS-FROM-FABRIC seq 20 permit 10.16.0.0/16 le 24
!
route-map RP-AWS-IN deny 10
 match ip address prefix-list PS-GUARDRAIL
route-map RP-AWS-IN permit 20
 match ip address prefix-list PS-AWS
route-map RP-AZURE-IN deny 10
 match ip address prefix-list PS-GUARDRAIL
route-map RP-AZURE-IN permit 20
 match ip address prefix-list PS-AZURE
route-map RP-CLOUD-OUT permit 10
 match ip address prefix-list PS-TO-CLOUD
route-map RP-FABRIC-IN permit 10
 match ip address prefix-list PS-FROM-FABRIC
route-map RP-FABRIC-OUT permit 10
 match ip address prefix-list PS-AWS
route-map RP-FABRIC-OUT permit 20
 match ip address prefix-list PS-AZURE
!
bfd
 profile CR-BFD
  transmit-interval 999
  receive-interval 999
  detect-multiplier 3
  no echo-mode
 exit
 profile CLOUD-BFD
  transmit-interval 300
  receive-interval 300
  detect-multiplier 3
  no echo-mode
 exit
exit
!
router bgp 65170
 bgp router-id 10.19.13.2
 neighbor 169.254.101.1 remote-as 65500
 neighbor 169.254.101.1 description AWS private VIF 2 to the Direct Connect Gateway, VLAN 101
 neighbor 169.254.101.1 password cisco123
 neighbor 169.254.101.1 bfd profile CLOUD-BFD
 neighbor 10.98.0.6 remote-as 12076
 neighbor 10.98.0.6 description Microsoft ExpressRoute secondary private peering, VLAN 200
 neighbor 10.98.0.6 password cisco123
 neighbor 10.98.0.6 bfd profile CLOUD-BFD
 neighbor 10.19.13.16 remote-as 65170
 neighbor 10.19.13.16 description DC1-CLOUD1
 neighbor 10.19.13.16 password cisco123
 neighbor 10.19.13.16 bfd profile CR-BFD
 neighbor 10.19.16.33 remote-as 65100
 neighbor 10.19.16.33 description DC1-BL1 (DC1-TRANSIT-CLOUD2)
 neighbor 10.19.16.33 password cisco123
 neighbor 10.19.16.33 bfd profile CR-BFD
 neighbor 10.19.16.34 remote-as 65100
 neighbor 10.19.16.34 description DC1-BL2 (DC1-TRANSIT-CLOUD2)
 neighbor 10.19.16.34 password cisco123
 neighbor 10.19.16.34 bfd profile CR-BFD
 address-family ipv4 unicast
  aggregate-address 10.16.0.0/16
  neighbor 169.254.101.1 route-map RP-AWS-IN in
  neighbor 169.254.101.1 maximum-prefix 100 90 warning-only
  neighbor 169.254.101.1 route-map RP-CLOUD-OUT out
  neighbor 10.98.0.6 route-map RP-AZURE-IN in
  neighbor 10.98.0.6 maximum-prefix 100 90 warning-only
  neighbor 10.98.0.6 route-map RP-CLOUD-OUT out
  neighbor 10.19.13.16 next-hop-self
  neighbor 10.19.16.33 route-map RP-FABRIC-IN in
  neighbor 10.19.16.33 route-map RP-FABRIC-OUT out
  neighbor 10.19.16.34 route-map RP-FABRIC-IN in
  neighbor 10.19.16.34 route-map RP-FABRIC-OUT out
 exit-address-family
exit
FRREOF
vtysh -f /etc/frr/frr.conf
echo "DC1-CLOUD2 stand-in ready"
