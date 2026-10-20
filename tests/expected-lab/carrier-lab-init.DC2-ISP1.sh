#!/bin/sh
# DC2-ISP-A: lab stand-in for carrier ISP-A (AS 64496) facing DC2-ISP1 FourHundredGigE0/0/0/6.
# Rendered by Infrahub from DC2-ISP1's data, do not edit. Interfaces: "isp" toward DC2-ISP1, "host" toward the internet test host.
set -eu
sysctl -qw net.ipv4.ip_forward=1
ip link set isp mtu 9216 up
ip addr replace 192.0.2.9/30 dev isp
ip link set host up
ip addr replace 198.18.1.1/24 dev host
if [ -e /sys/class/net/wan ]; then ip link set wan mtu 9216 up; ip addr replace 198.18.100.254/24 dev wan; fi
pgrep -x bfdd >/dev/null || /usr/lib/frr/bfdd -d -A 127.0.0.1
pgrep -x bgpd >/dev/null || /usr/lib/frr/bgpd -d -A 127.0.0.1
sleep 2
cat > /etc/frr/frr.conf <<'FRREOF'
hostname DC2-ISP-A
service integrated-vtysh-config
!
bfd
 profile CARRIER-BFD
  transmit-interval 999
  receive-interval 999
  detect-multiplier 3
  no echo-mode
 exit
exit
!
router bgp 64496
 bgp router-id 192.0.2.9
 no bgp ebgp-requires-policy
 neighbor 192.0.2.10 remote-as 65050
 neighbor 192.0.2.10 description DC2-ISP1
 neighbor 192.0.2.10 password cisco123
 neighbor 192.0.2.10 bfd profile CARRIER-BFD
 address-family ipv4 unicast
  network 198.18.1.0/24
  network 198.18.100.0/24
  neighbor 192.0.2.10 default-originate
 exit-address-family
exit
FRREOF
vtysh -f /etc/frr/frr.conf
echo "DC2-ISP-A stand-in ready"
