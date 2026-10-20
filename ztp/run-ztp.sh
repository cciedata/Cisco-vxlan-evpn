#!/bin/bash
# vamd ZTP on the lab host (Phase 3b; A27/A30 lab values): run-ztp.sh prep | start <device> | status [n] | stop
#   prep            qemu packages, the N9Kv base disk + OVMF out of the vrnetlab image, the Kea image (no VM yet)
#   start <device>  bridge br-ztp 10.250.99.1/24 + tap ztp0, Kea DHCP, the ZTP server, a blank N9Kv on ztp0, console logger.
#                   <device> (for example DC1-L11): its mgmt0 gateway from its rendered config is added to br-ztp so the server
#                   can verify the switch after the POAP reload (skipped, with a warning, when that subnet is already in use here).
#   status [n]      last n events, POAP console lines, Kea log
#   stop            stop everything started here
# Needs: Infrahub reachable at INFRAHUB_ADDRESS (lab host: the laptop's reverse SSH tunnel, 127.0.0.1:18000), ~/.vamd/secrets.env
# (ZTP_CALLBACK_TOKEN; VAMDADMIN_HASH/PASSWORD), the lab venv with netmiko (VENV, default <repo>/.venv-lab).
set -u
Z=$(cd "$(dirname "$0")" && pwd); REPO=$(dirname "$Z"); VENV=${VENV:-$REPO/.venv-lab}
W=/var/lib/vamd-ztp; IMG=vrnetlab/cisco_n9kv:10.6.4.M-lite; KEA=docker.cloudsmith.io/isc/docker/kea-dhcp4:2.6.3
export INFRAHUB_ADDRESS=${INFRAHUB_ADDRESS:-http://127.0.0.1:18000}
case "${1:-}" in
prep)
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q qemu-system-x86 qemu-utils >/dev/null && echo "qemu: $(qemu-system-x86_64 --version | head -1)"
  sudo mkdir -p $W && sudo chown "$(id -un)" $W
  if [ ! -f $W/base.qcow2 ]; then
    cid=$(sudo docker create $IMG); sudo docker cp $cid:/n9kv-9300-lite-10.6.4.M.qcow2 $W/base.qcow2; sudo docker cp $cid:/OVMF.fd $W/OVMF.fd; sudo docker rm $cid >/dev/null
    sudo chown "$(id -un)" $W/*
  fi
  ls -la $W; sudo docker pull -q $KEA | tail -1
  ;;
start)
  DEV=${2:?usage: run-ztp.sh start <device>}
  sudo ip link add br-ztp type bridge 2>/dev/null; sudo ip addr replace 10.250.99.1/24 dev br-ztp; sudo ip link set br-ztp up
  sudo ip tuntap add dev ztp0 mode tap user "$(id -un)" 2>/dev/null; sudo ip link set ztp0 master br-ztp; sudo ip link set ztp0 up
  sudo iptables -I INPUT -i br-ztp -j ACCEPT; sudo iptables -I DOCKER-USER -i br-ztp -j ACCEPT 2>/dev/null; true
  GW=$(cd "$REPO/lab" && $VENV/bin/python -c "
import re, sys, labctl
cfg, _ = labctl.Infrahub('main').artifact('startup-config', '$DEV')
gw = re.search(r'^vrf context management\n(?:  .*\n)*?  ip route 0\.0\.0\.0/0 (\S+)', cfg, re.M)
ip = re.search(r'^interface mgmt0\n(?:  .*\n)*?  ip address \S+/(\d+)', cfg, re.M)
print(f'{gw.group(1)}/{ip.group(1)}' if gw and ip else '')")
  if [ -z "$GW" ]; then echo "WARNING: no mgmt0 gateway in $DEV's config: verification cannot reach the switch"
  elif ip -4 route show | grep -v br-ztp | grep -q "^$($VENV/bin/python -c "import ipaddress; print(ipaddress.ip_interface('$GW').network)") "; then
    echo "WARNING: $GW's subnet is already routed on this host (lab mgmt network?): verification will not reach $DEV, ztp_state ends failed after 30 min"
  else sudo ip addr replace "$GW" dev br-ztp; echo "br-ztp: + $GW (mgmt0 gateway of $DEV)"; fi
  sudo docker rm -f vamd-kea >/dev/null 2>&1
  sudo docker run -d --name vamd-kea --network host --cap-add NET_ADMIN --cap-add NET_RAW -v $Z/kea-dhcp4.json:/etc/kea/kea-dhcp4.conf:ro $KEA >/dev/null && echo "kea started"
  curl -s -o /dev/null -w "Infrahub at $INFRAHUB_ADDRESS: http %{http_code}\n" $INFRAHUB_ADDRESS/api/config
  pkill -f ztp_server.py; nohup $VENV/bin/python $Z/ztp_server.py 10.250.99.1 8080 > $W/server.log 2>&1 &
  sleep 2; curl -s http://10.250.99.1:8080/poap.py | head -3
  rm -f $W/ztp.qcow2; qemu-img create -q -f qcow2 -F qcow2 -b $W/base.qcow2 $W/ztp.qcow2
  sudo nohup qemu-system-x86_64 -enable-kvm -display none -machine pc -cpu host -smp 3 -m 8192 \
    -monitor tcp:127.0.0.1:4099,server,nowait -serial telnet:127.0.0.1:5099,server,nowait \
    -drive file=$W/ztp.qcow2,if=none,id=drive-sata-disk0,format=qcow2 -device ahci,id=ahci0 -device ide-hd,drive=drive-sata-disk0,bus=ahci0.0,id=sata-disk0 \
    -bios $W/OVMF.fd -boot c \
    -netdev tap,id=mgmt,ifname=ztp0,script=no,downscript=no -device e1000,netdev=mgmt,mac=0c:00:5a:7e:99:01 \
    > $W/qemu.log 2>&1 &
  sleep 3; pgrep -af 'qemu-system.*ztp.qcow2' | cut -c1-120
  pkill -f ztp_console.py; nohup $VENV/bin/python $Z/ztp_console.py > $W/console-logger.log 2>&1 &
  echo "started $(date -u +%T)"
  ;;
status)
  echo "== events"; tail -n ${2:-15} $Z/ztp-events.jsonl 2>/dev/null
  echo "== console (POAP lines)"; grep -aE 'POAP|poap|DHCP|Abort|serial|scheduled|login:|vamd|Reboot|reboot|hostname|loader' $W/console.log 2>/dev/null | tail -n ${2:-25} | cut -c1-200
  echo "== kea"; sudo docker logs --tail 5 vamd-kea 2>&1 | cut -c1-200
  ;;
stop)
  sudo pkill -f 'qemu-system.*ztp.qcow2'; pkill -f ztp_server.py; pkill -f ztp_console.py; sudo docker rm -f vamd-kea >/dev/null 2>&1
  echo stopped ;;
*) sed -n '2,12p' "$0"; exit 1 ;;
esac
