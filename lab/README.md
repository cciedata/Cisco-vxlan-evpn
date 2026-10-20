# Lab tooling (Phase 3)

Builds and runs a site's lab from what Infrahub rendered. Nothing here holds device values: the node list, kinds,
links, configs and expectations are Infrahub artifacts (`lab-topology`, `lab-expected`, `startup-config`,
`iosxr-lab-config`, `frr-lab-init`, `carrier-lab-init`).

```
.venv/bin/python lab/labctl.py fetch DC1 --compare-baselines   # artifacts -> lab/build/DC1/, diffed with tests/expected*
.venv/bin/python lab/labctl.py validate DC1                     # files, links, mgmt IPs, images, host memory
.venv/bin/python lab/labctl.py deploy DC1                       # containerlab deploy
.venv/bin/python lab/labctl.py carve DC1                        # ARP-suppression TCAM carve on the VTEP nodes (N9Kv)
.venv-lab/bin/python lab/postcheck.py DC1 --plan                    # list every check
.venv-lab/bin/python lab/postcheck.py DC1                           # run them, report in lab/build/DC1/reports/
.venv-lab/bin/python lab/failtests.py DC1 --list                  # the 15 failure tests and what each expects
.venv-lab/bin/python lab/failtests.py DC1 --run 3,5                 # run some (default all); report in lab/build/DC1/reports/
.venv/bin/python lab/labctl.py destroy DC1
```

Partial lab (laptop, one N9Kv): `deploy DC1 --node-filter DC1-L11,DC1-HOST-PROD --memory 6144 --reserve 512`, then
`postcheck.py DC1 --nodes DC1-L11,DC1-HOST-PROD`. The memory override goes to `topology.override.yml`; the artifact is untouched.

Lab-only additions, applied at fetch or deploy time and recorded in `lab/build/<site>/MANIFEST.json`:
`feature nxapi` and `feature scp-server` (Day-2 push, FU-16) on every N9Kv config (P3-47) and the TCAM carve (L-12). The production templates are unchanged.
`lab/build/` is not tracked in Git.

Post-checks and failure tests run on Nornir + netmiko (NX-OS over SSH, `show ... | json`) in their own environment:
`uv venv .venv-lab && uv pip install -p .venv-lab -r lab/requirements-lab.txt`. Day-2 push (Nornir and Ansible,
inventory live from Infrahub, NX-OS config replace): `lab/day2/README.md`.

## The entire site (option 5)

`lab-topology-full` (transform `LabTopologyFull` in `transforms/lab.py`) renders every production device of a site (OOB
excluded), the other site's DCI and BGW switches as FRR stand-ins (Multi-Site control plane), and the lab-only outside
nodes: carriers and internet hosts, the PROD host, one SD-WAN branch (GRE to the hub), the VPN partner (GRE to the VPN
FW) and one cloud exchange per cloud router (AWS and Microsoft peers in two VRFs). Unit 2 of each FortiGate pair runs
with its data ports down (A/P HA shares the addresses). Use the site name with `-full`: `labctl.py fetch DC1-full`,
`validate DC1-full`, `deploy DC1-full`, `push DC1-full`, `carve DC1-full`. Lab-only nodes carry the artifact that
builds them as containerlab labels (`vamd.artifact`, `vamd.target`). DC1-full is 59 nodes (34 N9Kv) and about 290 GB:
rendered and checked offline, not run. Decisions: `claude/option5-decisions.md` in the project records.
