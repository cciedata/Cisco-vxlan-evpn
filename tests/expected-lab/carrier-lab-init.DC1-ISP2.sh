#!/bin/sh
# DC1-ISP-B: lab stand-in for carrier ISP-B (AS 64497) facing DC1-ISP2 FourHundredGigE0/0/0/6.
# Rendered by Infrahub from DC1-ISP2's data, do not edit. Interfaces: "isp" toward DC1-ISP2, "host" toward the internet test host.
set -eu
sysctl -qw net.ipv4.ip_forward=1
ip link set isp mtu 9216 up
ip addr replace 192.0.2.5/30 dev isp
ip link set host up
ip addr replace 198.18.2.1/24 dev host
if [ -e /sys/class/net/wan ]; then ip link set wan mtu 9216 up; ip addr replace 198.18.3.254/24 dev wan; fi
pgrep -x bfdd >/dev/null || /usr/lib/frr/bfdd -d -A 127.0.0.1
pgrep -x bgpd >/dev/null || /usr/lib/frr/bgpd -d -A 127.0.0.1
sleep 2
cat > /etc/frr/frr.conf <<'FRREOF'
hostname DC1-ISP-B
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
router bgp 64497
 bgp router-id 192.0.2.5
 no bgp ebgp-requires-policy
 neighbor 192.0.2.6 remote-as 65050
 neighbor 192.0.2.6 description DC1-ISP2
 neighbor 192.0.2.6 password cisco123
 neighbor 192.0.2.6 bfd profile CARRIER-BFD
 address-family ipv4 unicast
  network 198.18.2.0/24
  network 198.18.3.0/24
  neighbor 192.0.2.6 default-originate
 exit-address-family
exit
FRREOF
vtysh -f /etc/frr/frr.conf
echo "DC1-ISP-B stand-in ready"
