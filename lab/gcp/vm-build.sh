#!/usr/bin/env bash
# vm-build.sh: runs ON the lab VM. Build from an empty Infrahub following docs/QUICKSTART.md steps 2-5 as written, in the
# clone at $REPO (default ~/Cisco-vxlan-evpn). Stops at the first failing step and names it.
set -u
export PATH=$HOME/.local/bin:$PATH
REPO=${REPO:-$HOME/Cisco-vxlan-evpn}
cd "$REPO" || { echo "STOP: no repo at $REPO"; exit 1; }
t(){ echo; echo "== $(date +%T) $*"; }
die(){ echo "STOP at: $*"; exit 1; }
t "repo"; git log -1 --format='%h %s'
t "QS2 uv sync + consistency test"
uv sync -q || die "QS2 uv sync"
uv run python -m pytest -q tests/test_repo_consistency.py 2>&1 | tail -3 | tee /tmp/qs2.txt
grep -q passed /tmp/qs2.txt && ! grep -q failed /tmp/qs2.txt || die "QS2 pytest"
t "QS3 .env + docker compose up"
if [ ! -f .env ]; then
  cp .env.example .env
  python3 - <<'PY'
import pathlib, secrets, uuid
p = pathlib.Path(".env"); out = []
for line in p.read_text().splitlines():
    k, sep, v = line.partition("=")
    if sep and v == "CHANGE_ME":
        v = str(uuid.uuid4()) if k.endswith(("_TOKEN", "_SECRET_KEY")) else secrets.token_urlsafe(24)
    out.append(k + sep + v)
p.write_text("\n".join(out) + "\n")
PY
  chmod 600 .env
else echo ".env exists (rerun)"; fi
docker compose up -d 2>&1 | tail -3
ok=""; for i in $(seq 1 60); do curl -sf -o /dev/null http://localhost:8000/api/schema/summary && { ok=1; break; }; sleep 10; done
[ -n "$ok" ] || { docker compose ps; die "QS3 Infrahub not answering after 10 min"; }
docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' | sort | uniq -c
echo "OK (schema summary answers)"
t "QS3 client settings"
export INFRAHUB_ADDRESS=http://localhost:8000
export INFRAHUB_API_TOKEN="$(grep '^INFRAHUB_INITIAL_ADMIN_TOKEN=' .env | cut -d= -f2-)"
source .venv/bin/activate
infrahubctl --version 2>/dev/null || pip show infrahub-sdk 2>/dev/null | grep -i ^version
t "QS4 schema load"
loaded=""; for a in 1 2 3; do infrahubctl schema load schemas --branch main --wait 120 > /tmp/qs4s.txt 2>&1 && { loaded=1; break; }; echo "schema load attempt $a failed:"; tail -3 /tmp/qs4s.txt; sleep 60; done
tail -3 /tmp/qs4s.txt; [ -n "$loaded" ] || die "QS4 schema load"
t "QS4 objects 0x"
for f in objects/0[0-9]-*.yml; do echo "-- $f"; infrahubctl object load "$f" --branch main > /tmp/qs4o.txt 2>&1 || { tail -5 /tmp/qs4o.txt; die "QS4 object load $f"; }; tail -1 /tmp/qs4o.txt; done
t "QS4 load_design"; infrahubctl run scripts/load_design.py --branch main > /tmp/qs4d.txt 2>&1 || { tail -5 /tmp/qs4d.txt; die "QS4 load_design"; }; tail -2 /tmp/qs4d.txt
t "QS4 create_pools"; infrahubctl run scripts/create_pools.py --branch main > /tmp/qs4p.txt 2>&1 || { tail -5 /tmp/qs4p.txt; die "QS4 create_pools"; }; wc -l < /tmp/qs4p.txt | sed 's/^/pool lines: /'
for s in DC1 DC2; do
  t "QS4 fabric_site $s plan-only"; FABRIC_DRY_RUN=1 infrahubctl generator fabric_site --branch main site=$s > /tmp/qs4f-plan-$s.txt 2>&1 || { tail -8 /tmp/qs4f-plan-$s.txt; die "QS4 fabric_site plan $s"; }; tail -4 /tmp/qs4f-plan-$s.txt
  t "QS4 fabric_site $s write"; infrahubctl generator fabric_site --branch main site=$s > /tmp/qs4f-$s.txt 2>&1 || { tail -8 /tmp/qs4f-$s.txt; die "QS4 fabric_site write $s"; }; tail -4 /tmp/qs4f-$s.txt
  grep -q DELETED /tmp/qs4f-$s.txt && die "QS4 fabric_site $s deleted something"
done
t "QS4 objects 1x"
for f in objects/1[0-9]-*.yml; do echo "-- $f"; infrahubctl object load "$f" --branch main > /tmp/qs4o.txt 2>&1 || { tail -5 /tmp/qs4o.txt; die "QS4 object load $f"; }; tail -1 /tmp/qs4o.txt; done
for s in DC1 DC2; do
  t "QS4 oob_site $s"; infrahubctl generator oob_site --branch main site=$s > /tmp/qs4b-$s.txt 2>&1 || { tail -8 /tmp/qs4b-$s.txt; die "QS4 oob_site $s"; }; tail -3 /tmp/qs4b-$s.txt
  t "QS4 site_physical $s plan-only"; SITE_PHYSICAL_DRY_RUN=1 infrahubctl generator site_physical --branch main site=$s > /tmp/qs4y-plan-$s.txt 2>&1 || { tail -8 /tmp/qs4y-plan-$s.txt; die "QS4 site_physical plan $s"; }; tail -3 /tmp/qs4y-plan-$s.txt
  t "QS4 site_physical $s write"; infrahubctl generator site_physical --branch main site=$s > /tmp/qs4y-$s.txt 2>&1 || { tail -8 /tmp/qs4y-$s.txt; die "QS4 site_physical write $s"; }; tail -3 /tmp/qs4y-$s.txt
done
t "QS5 render all + compare with baselines"
bash scripts/render_all.sh lab/configs main > /tmp/qs5.txt 2>&1; rc=$?
grep -E "configs:|lab artifacts:|VERIFIED|differ" /tmp/qs5.txt | tail -8
if [ $rc -ne 0 ] || ! grep -q '^VERIFIED' /tmp/qs5.txt; then
  echo "-- render not fully equal: every differing line, classified"
  for f in $(cd lab/configs && ls); do [ -f tests/expected/$f ] && ! cmp -s lab/configs/$f tests/expected/$f && echo "$f"; done > /tmp/qs5-diff-files.txt
  echo "differing config files: $(wc -l < /tmp/qs5-diff-files.txt)"
  while read f; do diff tests/expected/$f lab/configs/$f | grep -E '^[<>]'; done < /tmp/qs5-diff-files.txt > /tmp/qs5-lines.txt
  echo "differing lines: $(wc -l < /tmp/qs5-lines.txt)"
  echo "lines that are NOT an IPv4 address change (must be 0 for 'allocation order only'):"
  sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}(\/[0-9]+)?/<ip>/g' /tmp/qs5-lines.txt | sed -E 's/^[<>] //' | sort | uniq -c | awk '$1%2==1' | head -20
  echo "address-line patterns (line text with addresses masked, count):"
  sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}(\/[0-9]+)?/<ip>/g; s/^[<>] //' /tmp/qs5-lines.txt | sort | uniq -c | sort -rn | head -15
  echo "BUILD_END (render differs)"; exit 3
fi
echo "BUILD_END (VERIFIED)"
