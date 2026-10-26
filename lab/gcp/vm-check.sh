#!/usr/bin/env bash
# vm-check.sh: runs ON the lab VM after vm-build.sh. Changes nothing. Prints docs/BUILD.md section 11, compares the lab
# topology and IOS XR lab files with their expected copies (address-only differences?), the switch-secret file state and
# the repositories in Infrahub.
REPO=${REPO:-$HOME/Cisco-vxlan-evpn}
cd "$REPO"; export PATH=$HOME/.local/bin:$PATH; source .venv/bin/activate
export INFRAHUB_ADDRESS=http://localhost:8000 INFRAHUB_API_TOKEN="$(grep '^INFRAHUB_INITIAL_ADMIN_TOKEN=' .env | cut -d= -f2-)"
git log -1 --format='repo HEAD %h %s'
echo "== BUILD.md section 11"; awk '/^## 11\./{f=1;next} f&&/^## /{exit} f' docs/BUILD.md
echo "== lab artifact diffs, address-masked"
for f in iosxr-lab-config.DC1-ISP1.cfg iosxr-lab-config.DC1-ISP2.cfg iosxr-lab-config.DC2-ISP1.cfg iosxr-lab-config.DC2-ISP2.cfg lab-topology.DC1.yml lab-topology.DC2.yml lab-topology-full.DC1.yml lab-topology-full.DC2.yml; do
  a=$(find tests/expected-lab -name "$f" | head -1); b=$(find lab/configs -name "$f" | head -1)
  [ -n "$a" ] && [ -n "$b" ] || { echo "$f: file not found (expected: $a, rendered: $b)"; continue; }
  diff $a $b | grep -E '^[<>]' > /tmp/la.txt
  n=$(wc -l < /tmp/la.txt); o=$(sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}(\/[0-9]+)?/<ip>/g; s/^[<>] //' /tmp/la.txt | sort | uniq -c | awk '$1%2==1' | wc -l)
  echo "$f: $n differing lines, $o not address-only"; sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}(\/[0-9]+)?/<ip>/g; s/^[<>] //' /tmp/la.txt | sort | uniq -c | head -4
done
echo "== switch secret (QUICKSTART 6): file state only"; ls -l ~/.vamd/ 2>&1
echo "== repositories in Infrahub"; infrahubctl repository list 2>&1 | tail -8
echo CHECK_END
