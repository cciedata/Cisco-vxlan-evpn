#!/usr/bin/env bash
# vm-lab.sh <prep|deploy>: runs ON the lab VM in $REPO (default ~/Cisco-vxlan-evpn). DC1 and DC2 together, as in
# docs/LAB.md: per site fetch --compare-baselines, validate, deploy, push, carve.
set -u
STAGE=${1:-}
REPO=${REPO:-$HOME/Cisco-vxlan-evpn}
cd "$REPO" || exit 1
export PATH=$HOME/.local/bin:$PATH
export INFRAHUB_ADDRESS=http://localhost:8000
export INFRAHUB_API_TOKEN="$(grep '^INFRAHUB_INITIAL_ADMIN_TOKEN=' .env | cut -d= -f2-)"
t(){ echo; echo "== $(date +%T) $*"; }
die(){ echo "STOP at: $*"; exit 1; }
L=.venv/bin/python
case "$STAGE" in
prep)
  t "containerlab without root: login user in clab_admins (containerlab 0.79 sudo-less mode)"
  getent group clab_admins >/dev/null && sudo usermod -aG clab_admins "$USER"; id -nG | tr ' ' '\n' | grep -E '^(docker|clab_admins)$' || echo "(group applies at next login)"
  t ".venv-lab (lab/README.md)"
  [ -x .venv-lab/bin/python ] || { uv venv -q .venv-lab && uv pip install -q -p .venv-lab -r lab/requirements-lab.txt; } || die ".venv-lab"
  .venv-lab/bin/python -c "import nornir, netmiko; print('nornir', nornir.__version__ if hasattr(nornir,'__version__') else 'ok', 'netmiko', netmiko.__version__)"
  t "stored artifacts: wait until none Pending and the count is stable"
  prev=-1
  for i in $(seq 1 60); do
    s=$(curl -s -H "Content-Type: application/json" -H "X-INFRAHUB-KEY: $INFRAHUB_API_TOKEN" -d '{"query":"{ CoreArtifact { count edges { node { status { value } } } } }"}' $INFRAHUB_ADDRESS/graphql/main | python3 -c 'import json,sys,collections; d=json.load(sys.stdin)["data"]["CoreArtifact"]; c=collections.Counter(e["node"]["status"]["value"] for e in d["edges"]); print(d["count"], c.get("Ready",0), sum(v for k,v in c.items() if k!="Ready"), dict(c))')
    set -- $s; echo "$(date +%T) count $1 ready $2 other $3"
    [ "$3" = 0 ] && [ "$1" = "$prev" ] && break; prev=$1; sleep 30
  done
  [ "$3" = 0 ] || die "artifacts not all Ready after 30 min: $s"
  for S in DC1 DC2; do
    t "$S fetch --compare-baselines"; $L lab/labctl.py fetch $S --compare-baselines 2>&1 | tail -25
    t "$S validate"; $L lab/labctl.py validate $S 2>&1 | tail -25; [ ${PIPESTATUS[0]} = 0 ] || die "$S validate"
  done
  echo PREP_END ;;
deploy)
  for S in DC1 DC2; do t "$S deploy"; $L lab/labctl.py deploy $S 2>&1 | tail -30; [ ${PIPESTATUS[0]} = 0 ] || die "$S deploy"; done
  for S in DC1 DC2; do t "$S push"; .venv-lab/bin/python lab/labctl.py push $S 2>&1 | tail -30; [ ${PIPESTATUS[0]} = 0 ] || die "$S push"; done
  for S in DC1 DC2; do t "$S carve"; $L lab/labctl.py carve $S 2>&1 | tail -30; [ ${PIPESTATUS[0]} = 0 ] || die "$S carve"; done
  for S in DC1 DC2; do t "$S status"; $L lab/labctl.py status $S 2>&1 | tail -30; done
  echo DEPLOY_END ;;
*) echo "usage: vm-lab.sh prep|deploy"; exit 2;;
esac
