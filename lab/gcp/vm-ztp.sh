#!/usr/bin/env bash
# vm-ztp.sh <prep|start|status|stop>: runs ON the lab VM in $REPO. ZTP for one device (DC1-L11) with the ztp/ tools
# against the VM's own Infrahub (localhost:8000), on a separate br-ztp network.
set -u
REPO=${REPO:-$HOME/Cisco-vxlan-evpn}
cd "$REPO" || exit 1
export PATH=$HOME/.local/bin:$PATH INFRAHUB_ADDRESS=http://localhost:8000
export INFRAHUB_API_TOKEN="$(grep '^INFRAHUB_INITIAL_ADMIN_TOKEN=' .env | cut -d= -f2-)"
export VENV=$PWD/.venv-lab
case "${1:-}" in
prep)
  echo "== ztp-callback account (setup_account.py)"
  if grep -q '^ZTP_CALLBACK_TOKEN=' ~/.vamd/secrets.env; then echo "already in secrets.env"; else .venv/bin/python ztp/setup_account.py 2>&1 | tail -15; fi
  cut -d= -f1 ~/.vamd/secrets.env
  echo "== run-ztp.sh prep"; bash ztp/run-ztp.sh prep 2>&1 | tail -12
  echo ZTPPREP_END ;;
start)  bash ztp/run-ztp.sh start DC1-L11 2>&1 | tail -20; echo ZTPSTART_END ;;
status) bash ztp/run-ztp.sh status ${2:-20} 2>&1; echo "== load"; uptime ;;
stop)   bash ztp/run-ztp.sh stop ;;
*) echo "usage: vm-ztp.sh prep|start|status|stop"; exit 2;;
esac
