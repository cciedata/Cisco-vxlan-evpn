# Build both sites from this repository alone

From an empty machine with Docker to an Infrahub that holds DC1 and DC2 with every device, interface, pair, cable and
address, and renders the 134 configs and 58 lab artifacts equal to `tests/expected/` and `tests/expected-lab/`.

Commands marked READ ONLY change nothing; RUN changes something. Every step ends with the check that proves it.

Status, step by step (so you know what to expect):
- ✅ verified on an empty Infrahub: steps 3–8 at the Phase 0 commit (26 Sep 2026: schema, objects 00–06, pools,
  `fabric_site` for both sites; every count equal to the records, 4 of 4 spine renders equal). Every later file, pool
  and generator was loaded the same way on its own branch of a running instance before it was merged, with its
  plan-only run reviewed and a full rerun that kept every object id.
- ❓ not yet rerun as one sequence from an empty instance at the current commit (the order of steps 6, 7 and 9 below
  follows the order the pieces were built in).

## 1. Prerequisites

| Item | Version | Where it is pinned / from |
|---|---|---|
| Infrahub (server, task-manager, task-worker) | 1.11.2 | `docker-compose.yml` (`registry.opsmill.io/opsmill/infrahub:1.11.2`) |
| Neo4j / RabbitMQ / Redis / PostgreSQL | 2026.05.0-community / 4.2.1-management / 8.4.0 / 18-alpine | `docker-compose.yml` |
| infrahub-sdk (with `infrahubctl`) | 1.23.2 | `uv.lock` (`pyproject.toml` asks for `infrahub-sdk[all]`) |
| Python | 3.11 – 3.13 | `pyproject.toml` |
| uv | any current | astral.sh/uv |
| Docker Engine + Compose v2, `docker` usable without sudo | tested with docker.io 29.1.3 and docker-compose-v2 2.40 on Ubuntu 24.04 | `apt-get install docker.io docker-compose-v2` |
| Lab only: containerlab ≥ 0.79, vrnetlab, a Nexus 9000v 10.6(4)M lite qcow2, FRR 10.6.0 | see docs/LAB.md | the Nexus image is your own Cisco download |

Memory: the Infrahub stack uses about 3.7 GiB. A build needs nothing else. The lab is sized in docs/LAB.md.

## 2. Get the code and the environment

RUN
```
git clone https://github.com/cciedata/Cisco-vxlan-evpn.git
cd Cisco-vxlan-evpn
uv sync
uv run python -m pytest -q tests/test_repo_consistency.py
```
Success: `uv run infrahubctl --help` answers; pytest ends with `passed` and no `failed`.

For the lab tools (Nornir, netmiko, Ansible) a second environment keeps their pins apart from the SDK:
```
uv venv .venv-lab && uv pip install --python .venv-lab/bin/python -r lab/requirements-lab.txt
```

## 3. Start an empty Infrahub

RUN: `.env` with fresh secrets, then the stack (compose project `cisco-vxlan-evpn`)
```
cp .env.example .env
python3 - <<'EOF'
import pathlib, secrets, uuid
p = pathlib.Path(".env"); out = []
for line in p.read_text().splitlines():
    k, sep, v = line.partition("=")
    if sep and v == "CHANGE_ME":
        v = str(uuid.uuid4()) if k.endswith(("_TOKEN", "_SECRET_KEY")) else secrets.token_urlsafe(24)
    out.append(k + sep + v)
p.write_text("\n".join(out) + "\n")
EOF
chmod 600 .env
docker compose up -d
```
Another Infrahub already on 8000 / 2004 / 6362 / 15692: set `EVPN_INFRAHUB_PORT=8001`, `EVPN_NEO4J_METRICS_PORT=2005`,
`EVPN_NEO4J_BACKUP_PORT=6363`, `EVPN_RABBITMQ_METRICS_PORT=15693` in `.env` before `up`. Do not export `INFRAHUB_ADDRESS`
in the terminal that runs `docker compose` (compose would hand it to the containers).

READ ONLY
```
docker compose ps
curl -sf -o /dev/null http://localhost:8000/api/schema/summary && echo OK
```
Success: six services `healthy`, two `task-worker` replicas running, `OK`.

Client terminal for every later step (not the one you use for `docker compose`):
```
export INFRAHUB_ADDRESS=http://localhost:8000
export INFRAHUB_API_TOKEN="$(grep '^INFRAHUB_INITIAL_ADMIN_TOKEN=' .env | cut -d= -f2-)"
source .venv/bin/activate
```

## 4. Schema ✅

RUN
```
infrahubctl schema check schemas --branch main
infrahubctl schema load schemas --branch main --wait 120
```
`schemas/base-schemas/` are the Marketplace base schemas (dcim, ipam, location, organization) with our role choices
added; `schemas/locations.yml` is Country → Metro → Site; `schemas/fabric.yml` holds the fabric kinds (ASNs, VRFs,
segments, switch pairs and vPC domains / ESI pairs, attachments, L2 VLANs, port-channels, branches, partners, ZTP
state); `schemas/oob.yml` the OOB kinds (cables, racks, PDUs, console ports). Success: the load ends with the schema
synced on every worker; a second `schema check` prints no diff. A `500` right after a fresh start means the server is
still converging: wait a minute and run it again.

## 5. Objects, in this order ✅

RUN: one file at a time (every reference points to an object loaded before)
```
for f in objects/00-groups.yml objects/01-countries.yml objects/02-sites.yml objects/03-ip-plan.yml \
         objects/04-hardware.yml objects/05-providers.yml objects/06-fabric.yml objects/07-segments.yml \
         objects/08-racks.yml objects/09-pdu-types.yml; do
  infrahubctl object load "$f" --branch main || break
done
```
| File | What it loads |
|---|---|
| 00-groups.yml | the generator target group `fabric-sites` and the artifact target groups the generators fill |
| 01-countries.yml | United States, the metros Rockledge and Ashburn |
| 02-sites.yml | DC1 and DC2 with their intent: `vpc_leaf_pairs` 1, `esi_leaf_pairs` 4, `single_leaves` 10 |
| 03-ip-plan.yml | every block of the IP plan (docs/DESIGN.md §3), the mgmt0 gateways |
| 04-hardware.yml | Cisco and Fortinet platforms and device types |
| 05-providers.yml | ISP-A, ISP-B, the cloud and SD-WAN providers and their ASNs |
| 06-fabric.yml | the ASNs (fabric 65100 / 65200 and every role ASN), the 8 VRFs with their summaries |
| 07-segments.yml | the 62 segments and their /24s |
| 08-racks.yml, 09-pdu-types.yml | the racks per site and the PDU types (for `site_physical`) |

Success: every load ends without an error and the loop does not `break`.

## 6. Pools ✅

RUN: the IP pools (loopback0, loopback1 VTEP, anycast VTEP, vPC backup and keepalive, fabric p2p, mgmt0, OOB) per site
```
python scripts/create_pools.py
```
Success: one line per pool; a rerun prints the same pools and creates nothing.

## 7. The fabric generator, plan first ✅

READ ONLY: the plan tables, nothing written
```
FABRIC_DRY_RUN=1 infrahubctl generator fabric_site --branch main site=DC1
FABRIC_DRY_RUN=1 infrahubctl generator fabric_site --branch main site=DC2
```
RUN: write, first in report mode (the reconciler prints what it would delete, deletes nothing), then plain
```
for s in DC1 DC2; do FABRIC_RECONCILE=report infrahubctl generator fabric_site --branch main site=$s; done
for s in DC1 DC2; do infrahubctl generator fabric_site --branch main site=$s; done
```
`fabric_site` builds, per site, the 34 switches and 12 firewalls, routers and hubs with their interfaces, loopbacks,
mgmt0, the 92 spine–leaf /31 links, the vPC domains and ESI pairs, the firewall and router attachments (one /29 per
device per VRF), the edge and DMZ VLANs, the carrier /30s and the artifact groups. Success: no `WOULD DELETE` line in
report mode, no `DELETED` line in the plain run, and the second run changes nothing (the idempotency standard of this
project is a snapshot of every generator-owned id before and after a full rerun, all equal).

## 8. The remaining data files ✅

RUN: the remaining object files (`10-lab-host-ports.yml` references a generated device, so these come after step 7)
```
for f in objects/10-branches.yml objects/10-lab-host-ports.yml objects/11-partners.yml; do
  infrahubctl object load "$f" --branch main || break
done
```
| File | What it loads |
|---|---|
| 10-branches.yml | two sample SD-WAN branches (LAN /24s from 10.64.0.0/12) homed on both hubs |
| 10-lab-host-ports.yml | the lab PROD host access port: DC1-L11 Ethernet1/1 → segment PROD-1010 |
| 11-partners.yml | one sample VPN partner with its translated /28 per site (documentation values) |

## 9. The OOB and physical generators ✅

READ ONLY then RUN, per site
```
for s in DC1 DC2; do FABRIC_RECONCILE=report infrahubctl generator oob_site --branch main site=$s; done
for s in DC1 DC2; do infrahubctl generator oob_site --branch main site=$s; done
for s in DC1 DC2; do SITE_PHYSICAL_DRY_RUN=1 infrahubctl generator site_physical --branch main site=$s; done
for s in DC1 DC2; do infrahubctl generator site_physical --branch main site=$s; done
```
`oob_site` builds the OOB network of a site (2 FortiGates, 2 aggregation, 2 network-access, 15 BMC switches, 2 console
servers, a console port on every production device, 179 cables) and `site_physical` the rack placements (69), PDUs (58)
and fabric cables (167) per site. Success: `oob_site` report mode prints no `WOULD DELETE`; the `site_physical` plan
prints its placement and cable tables; the write runs end with `WROTE …` and a rerun changes nothing.

## 10. Render everything and compare with the baselines ✅

READ ONLY
```
bash scripts/render_all.sh lab/configs main
```
Success: `configs: 134 of 134 equal tests/expected`, `lab artifacts: 58 of 58 equal tests/expected-lab`,
`VERIFIED 134 configs and 58 lab artifacts on branch main`. A difference means a template, object file or allocation
differs from the reviewed state; never edit `tests/expected*` to make it pass.

The four diagram views per site render with `infrahubctl transform site_diagrams site=DC1 --branch main` (and
`site_ports`, `site_tables`, `site_cable_schedule`); `docs/diagrams/` holds the rendered files.

## 11. Check ✅

READ ONLY: every device in a site's fabric ASN has exactly one loopback0 with one address
```
infrahubctl check loopback0_check
```
Success: `PASSED` for DC1 and DC2.

## 12. Connect the repository (optional, for the proposed-change pipeline)

In Infrahub, add a `CoreRepository` (read-write) pointing at your fork with a token that has Contents: Read and write.
Every later change then goes: Git branch → push (Infrahub creates the matching branch) → load schema and objects on the
branch, run the generators there → proposed change → about 30 validators → merge through the PC → step 10 on `main`.
The stored artifacts on `main` can lag a merge by a few minutes; the render comparison is the authority.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `docker compose up`: `set INFRAHUB_... in .env` | a credential is missing in `.env` (step 3) |
| `schema check` or `load` answers `500 Internal Server Error` right after a fresh start | wait a minute and run it again |
| `object load` error naming a missing object | an earlier file was skipped; load in the step 5 / step 8 order |
| generator: `refuses to run` on empty intent | `02-sites.yml` not loaded, or a site without `vpc_leaf_pairs` / `esi_leaf_pairs` / `single_leaves` |
| generator: a `GUARD` / `STOP` line | it refused to move an address held by another device or to save an object another writer owns; nothing was written |
| a rerun deletes or renumbers something | a real idempotency problem: keep the output, do not continue |
| render differs from the baseline | `diff lab/configs/<device>.cfg tests/expected/<device>.cfg`; a template or data change must explain every changed line |
| `labctl push`: `No module named 'netmiko'` | run labctl from `.venv-lab`, or install `lab/requirements-lab.txt` into `.venv` |

## Known limits

- The whole sequence has not been rerun from an empty instance at the current commit (see the status at the top).
- `infrahubctl schema load --wait` behaviour on an empty instance and the pool allocation order of a fresh instance (which
  decides whether the baselines match byte for byte) were verified at the Phase 0 commit, not since.
- The unused schema choices `pop_circuit` / `pop_router` exist in a live schema that saw the Denver POP; a fresh load from
  this repository does not have them.
