#!/bin/sh
# DC2-PARTNER01: lab stand-in for VPN partner PARTNER01 (Sample partner (documentation values)) of DC2-VPNFW (FRR container).
# Rendered by Infrahub from DC2-VPNFW1's data, do not edit. Interface "wan" toward carrier A. Lab-only: GRE instead of IKEv2.
set -eu
sysctl -qw net.ipv4.ip_forward=1
ip route del default dev eth0 2>/dev/null || true
ip link set wan mtu 9216 up
ip addr replace 198.18.100.1/24 dev wan
ip route replace default via 198.18.100.254 dev wan
ip link add PROTECTED type dummy 2>/dev/null || true
ip addr replace 192.168.100.1/24 dev PROTECTED
ip link set PROTECTED up
ip tunnel add PARTNER01 mode gre local 198.18.100.1 remote 203.0.113.129 ttl 64 2>/dev/null || true
ip link set PARTNER01 up
ip route replace 10.20.160.0/28 dev PARTNER01
echo "DC2-PARTNER01 stand-in ready"
