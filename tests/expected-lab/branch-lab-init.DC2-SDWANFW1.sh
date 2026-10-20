#!/bin/sh
# DC2-BRANCH1: lab stand-in for an SD-WAN branch of hub DC2-SDWANFW (FRR in a Linux container). Rendered by Infrahub
# from DC2-SDWANFW1's data, do not edit. Interface "wan" toward carrier B. Lab-only: GRE instead of ADVPN IPsec.
set -eu
sysctl -qw net.ipv4.ip_forward=1
ip route del default dev eth0 2>/dev/null || true
ip link set wan mtu 9216 up
ip addr replace 198.18.3.10/24 dev wan
ip route replace default via 198.18.3.254 dev wan
ip link add LAN type dummy 2>/dev/null || true
ip addr replace 10.64.1.1/24 dev LAN
ip link set LAN up
ip tunnel add ADVPN-HUB mode gre local 198.18.3.10 remote 203.0.113.145 ttl 64 2>/dev/null || true
ip addr replace 10.79.8.2/21 dev ADVPN-HUB
ip link set ADVPN-HUB up
pgrep -x bgpd >/dev/null || /usr/lib/frr/bgpd -d -A 127.0.0.1
sleep 2
cat > /etc/frr/frr.conf <<'FRREOF'
hostname DC2-BRANCH1
service integrated-vtysh-config
!
router bgp 65400
 bgp router-id 10.79.8.2
 no bgp ebgp-requires-policy
 neighbor 10.79.8.1 remote-as 65400
 neighbor 10.79.8.1 description DC2-SDWANFW hub
 neighbor 10.79.8.1 password cisco123
 address-family ipv4 unicast
  network 10.64.1.0/24
 exit-address-family
exit
FRREOF
vtysh -f /etc/frr/frr.conf
echo "DC2-BRANCH1 stand-in ready"
