#!/usr/bin/env bash
# Render every device config and every lab artifact from a running Infrahub and compare them with the baselines in
# tests/expected/ and tests/expected-lab/. READ ONLY: it writes only into the output folder.
#
#   bash scripts/render_all.sh [output folder] [branch]        default: lab/configs  main
#
# The transform for a device follows its name (the same map the proposed-change checks use):
#   spines spine_config · server leaves leaf_config · border/service leaves border_service_leaf_config · edge, DMZ, BGW,
#   DCI their own templates · FortiGates internal_fw / internet_fw / sdwan_fw / vpn_fw / oob_fw _config · ISP and cloud
#   routers isp_router_config / cloud_router_config · OOB switches oob_switch_config.
# Exit code 0 only when every render equals its baseline.
set -u
OUT=${1:-lab/configs}; BR=${2:-main}
CTL=${INFRAHUBCTL:-.venv/bin/infrahubctl}
mkdir -p "$OUT"
tmpl() { case $1 in *-OOBFW*) echo oob_fw_config;; *-OOBAGG*|*-OOBNET*|*-OOBBMC*) echo oob_switch_config;; *-VPNFW*) echo vpn_fw_config;;
  *-SDWANFW*) echo sdwan_fw_config;; *-CLOUD*) echo cloud_router_config;; *-BGW*) echo bgw_config;; *-DCI*) echo dci_config;;
  *-INTFW*) echo internal_fw_config;; *-INETFW*) echo internet_fw_config;; *-ISP*) echo isp_router_config;; *-BL*|*-SL*) echo border_service_leaf_config;;
  *-EDGE*) echo edge_config;; *-DMZ*) echo dmz_config;; *-S[0-9]*) echo spine_config;; *) echo leaf_config;; esac; }

ok=0; bad=""; n=0
for f in tests/expected/*.cfg; do
  d=$(basename "$f" .cfg); n=$((n+1))
  if COLUMNS=100000 "$CTL" render "$(tmpl "$d")" device="$d" --branch "$BR" > "$OUT/$d.cfg" 2>/dev/null && cmp -s "$OUT/$d.cfg" "$f"; then ok=$((ok+1)); else bad="$bad $d"; fi
done
echo "configs: $ok of $n equal tests/expected${bad:+; DIFF:$bad}"

lok=0; lbad=""; ln=0
render_lab() { ln=$((ln+1)); local name=$1; shift
  if COLUMNS=100000 "$CTL" "$@" --branch "$BR" > "$OUT/$name" 2>/dev/null && cmp -s "$OUT/$name" "tests/expected-lab/$name"; then lok=$((lok+1)); else lbad="$lbad $name"; fi; }
for d in DC1-INTFW1 DC1-INTFW2 DC2-INTFW1 DC2-INTFW2; do render_lab "frr-lab-init.$d.sh" render internal_fw_lab device=$d; done
for d in DC1-INETFW1 DC1-INETFW2 DC2-INETFW1 DC2-INETFW2; do render_lab "frr-lab-init.$d.sh" render internet_fw_lab device=$d; done
for d in DC1-ISP1 DC1-ISP2 DC2-ISP1 DC2-ISP2; do
  render_lab "carrier-lab-init.$d.sh" render carrier_lab device=$d
  render_lab "iosxr-lab-config.$d.cfg" render isp_router_lab_config device=$d
  render_lab "frr-lab-init.$d.sh" render isp_router_frr_lab device=$d
done
for s in DC1 DC2; do
  render_lab "lab-topology.$s.yml" transform lab_topology site=$s
  render_lab "lab-expected.$s.json" transform lab_expected site=$s
done
# the entire-site lab (lab-topology-full and the stand-ins outside the lab path), when its baselines are in the repository
if [ -e tests/expected-lab/lab-topology-full.DC1.yml ]; then
  for s in DC1 DC2; do for u in 1 2; do
    for x in "sdwan_fw_lab SDWANFW frr-lab-init" "sdwan_branch_lab SDWANFW branch-lab-init" "vpn_fw_lab VPNFW frr-lab-init" \
             "vpn_partner_lab VPNFW partner-lab-init" "cloud_router_lab CLOUD frr-lab-init" "cloud_exchange_lab CLOUD cx-lab-init" \
             "dci_lab DCI frr-lab-init" "bgw_lab BGW frr-lab-init"; do
      set -- $x; render_lab "$3.$s-$2$u.sh" render $1 device=$s-$2$u
    done; done
    render_lab "lab-topology-full.$s.yml" transform lab_topology_full site=$s
  done
fi
echo "lab artifacts: $lok of $ln equal tests/expected-lab${lbad:+; DIFF:$lbad}"
[ -z "$bad" ] && [ -z "$lbad" ] && { echo "VERIFIED $ok configs and $lok lab artifacts on branch $BR"; exit 0; }
exit 1
