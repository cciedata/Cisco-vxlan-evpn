# The lab

Everything the lab needs is rendered by Infrahub from the same data as the configs: the containerlab topology per site
(`lab-topology`), the post-check expectations (`lab-expected`), the FRR stand-in init scripts for the firewalls, ISP
routers and carriers (`frr-lab-init`, `carrier-lab-init`) and an IOS XR variant for XRd. `lab/labctl.py` fetches them
from Infrahub, validates them against the host, deploys, pushes the full configs and carves the TCAM on the virtual Nexus.
The tools are described in [`lab/README.md`](../lab/README.md); this page says what ran where and how to repeat it.

## Images

| Image | How |
|---|---|
| Nexus 9000v `vrnetlab/cisco_n9kv:10.6.4.M-lite` | build with [vrnetlab](https://github.com/srl-labs/vrnetlab) from `nexus9300v64-lite.10.6.4.M.qcow2` (your own Cisco download; rename the file `n9kv-<version>.qcow2`) |
| FRR `quay.io/frrouting/frr:10.6.0` | `docker pull` |
| `alpine:3.22` | `docker pull` (lab hosts) |

Every node that is not a Nexus runs as an FRR stand-in rendered from the same data as its real config (FortiOS or
IOS XR): same ASNs, same sessions, same route-maps, BFD 999 × 3. The stand-ins are the lab's way to run the design
end to end without FortiGate or XRd images; the real configs stay rendered and checked.

## One Nexus on a laptop (12 GB)

Infrahub and one N9Kv do not fit together in an 8 GB WSL VM, so the laptop runs one node at a time with Infrahub
stopped. Fetch first, while Infrahub is up:
```
.venv/bin/python lab/labctl.py fetch DC1 --compare-baselines
docker compose stop
.venv/bin/python lab/labctl.py deploy DC1 --node-filter DC1-L11 --memory 6144 --reserve 512
.venv/bin/python lab/labctl.py push DC1 --nodes DC1-L11
.venv/bin/python lab/labctl.py carve DC1 --nodes DC1-L11
```
`deploy` boots the node with a minimal config (hostname, users, mgmt0, management VRF, NX-API, SSH, SCP) from
`topology.boot.yml`; `push` sends the full rendered config block by block over NX-API and re-sends refused blocks over
SSH; `carve` sets the TCAM regions the virtual Nexus needs for ARP suppression (`racl 512`, `arp-ether 256 double-wide`),
saves, reloads and re-pushes. On the laptop DC1-L11 is configured about 15 minutes after `deploy`. Afterwards:
```
.venv/bin/python lab/labctl.py destroy DC1
docker compose start
```
What ran this way: every one of the 106 NX-OS configs loaded on a single N9Kv (acceptance test), a two-node lab
(DC1-L11 plus a PROD host pinging its anycast gateway), the Day-2 push test, and the ZTP server test with a blank N9Kv.

## The DC1 lab path (about 88 GB)

The DC1 lab path is 21 nodes and 49 links: 10 N9Kv (S1, S2, L11, L12, BL1/2, SL1/2, EDGE1/2), FRR stand-ins for the
Internal FW, the Internet FW, the two ISP routers and their carriers, and five hosts (a test host on each leaf access
port, two internet hosts). It first ran on 8 Oct as 18 nodes with 9 N9Kv (before L12 joined) on an AWS m8i.8xlarge spot
instance (32 vCPU, 123 GB, nested virtualization), and ran again at full size on 25 Oct together with DC2 on one
64-core cloud VM (see below). The artifacts are fetched from Infrahub first:
```
.venv/bin/python lab/labctl.py fetch DC1 --compare-baselines
.venv/bin/python lab/labctl.py validate DC1
.venv/bin/python lab/labctl.py deploy DC1
.venv/bin/python lab/labctl.py push DC1
.venv/bin/python lab/labctl.py carve DC1
.venv-lab/bin/python lab/postcheck.py DC1
.venv-lab/bin/python lab/failtests.py DC1 --list
```
`deploy` boots the N9Kv in groups of 5 (topology order): a group is created when every N9Kv of the previous group is
healthy (`topology.staged.yml`, containerlab stages). A watchdog treats an N9Kv as hung when it is not healthy and its
vrnetlab log has not moved for 10 minutes (25 Oct: one N9Kv stopped in the vrnetlab bootstrap at `feature telnet`);
it then destroys and redeploys the site once, and stops if the hang comes back. `--group-size 0` boots all at once,
`--no-watchdog` is the plain containerlab deploy. ❓ Not yet run on a lab host.
On 8 Oct, deploy to a fully configured 18-node lab took 27 minutes with no manual step. `postcheck.py` runs the checks
in the `lab-expected` artifact over SSH with Nornir and netmiko (`show … | json`) and `docker exec` for the FRR nodes:
BGP sessions, BFD sessions, routes, pings. Every BFD check fails on the virtual Nexus, which does not run BFD. On 25 Oct,
with the BFD checks left out and lab-only test hosts added in the DEV and CITRIX networks (so their summary routes
exist), DC1 passed 124 of 124. The BFD skip was a lab-only copy of the script; it is not in this repository yet.

`failtests.py` breaks something, measures the outage from the break to the first reply to a probe sent after it,
restores and runs the post-check again. 14 scenarios are documented with before, during and after captures: leaf–spine
link down (1.2 s), vPC peer-link and keepalive down (0.0 s), spine down (39–41 s, no BFD on the platform), both
carriers down (the ISP default is a graceful-restart stale path for 15 s, then the Internet FW withdraws 0/0), leaf
reload, firewall and ISP failures, filter tests. Open and accepted: one Internal FW bundle member down stops traffic
on the stand-in; the vPC peer-link outage is measured later than the break.

## The DC2 lab path (about 88 GB)

The same lab for DC2: 21 nodes and 49 links (10 N9Kv, the same FRR stand-ins, five hosts). It ran on 25 Oct together
with DC1. With the BFD checks left out and the lab-only DEV and CITRIX hosts, DC2 passed 150 of 150. One lab-only
change: the BGP password was removed from one Internal FW session in DC2, whose MD5 session would not come up (cause
still open). Same commands with `DC2`:
```
.venv/bin/python lab/labctl.py fetch DC2 --compare-baselines
.venv/bin/python lab/labctl.py validate DC2
.venv/bin/python lab/labctl.py deploy DC2
.venv/bin/python lab/labctl.py push DC2
.venv/bin/python lab/labctl.py carve DC2
```

## DC1 and DC2 together (about 176 GB)

The two lab paths can run side by side on one host: separate labs (`vamd-dc1-p3`, `vamd-dc2-p3`), separate mgmt networks
(10.250.0.0/24, 10.250.16.0/24), nothing shared. They are not connected: neither lab path has the BGWs or the DCI
switches, so there is no Multi-Site traffic between them. Run the five commands above for `DC1`, then for `DC2`. This ran on
25 Oct on one 64-core cloud VM with nested virtualization. How to run it there, with the cost: [LAB-GCP.md](LAB-GCP.md).

## The entire DC1 site (about 290 GB, not run)

`lab-topology-full` renders every DC1 production device (34 Nexus, 8 FortiGates and 4 Cisco 8201 as FRR stand-ins),
DC2's DCI and BGW switches as control-plane stand-ins (Multi-Site), and the outside nodes: carriers, internet hosts, the
PROD host, one SD-WAN branch, the VPN partner and one cloud exchange per cloud router (AWS and Azure peers). It is on
`main`, merged through the proposed change "Option 5: entire-site lab topology and FRR stand-ins", rendered and checked offline (`labctl validate`, an FRR-only smoke test); it needs about 290 GB to run. The site argument
`DC1-full` selects it:
```
.venv/bin/python lab/labctl.py fetch DC1-full --compare-baselines
.venv/bin/python lab/labctl.py validate DC1-full
.venv/bin/python lab/labctl.py deploy DC1-full
.venv/bin/python lab/labctl.py push DC1-full
.venv/bin/python lab/labctl.py carve DC1-full
```
The decisions behind it (which units run, what stands in for IPsec and the clouds, lab-only addresses) are listed in
the diagrams for option 5.

## Diagrams of every lab

PDFs in [`docs/lab-diagrams/`](lab-diagrams/), one per lab: every device and cable with ports and addresses, BGP and
VRFs, a packet walk with the headers on every cable, the first ARP, the lab cables against the production cable
schedule, and a run card (memory, commands, logins).

| Lab | PDF |
|---|---|
| One switch (DC1-L11) on a 12 GB laptop | [Option1-one-switch.pdf](lab-diagrams/Option1-one-switch.pdf) |
| DC1 lab path | [DC1-lab-path.pdf](lab-diagrams/DC1-lab-path.pdf) |
| DC2 lab path | [DC2-lab-path.pdf](lab-diagrams/DC2-lab-path.pdf) |
| DC1 and DC2 together | [Option4-DC1-and-DC2.pdf](lab-diagrams/Option4-DC1-and-DC2.pdf) |
| The entire DC1 site | [Option5-entire-DC1.pdf](lab-diagrams/Option5-entire-DC1.pdf) |

## ZTP and Day-2

- `ztp/`: a blank switch gets its address from Kea DHCP, runs `poap.py`, and the ZTP server serves the device's
  `startup-config` artifact live by serial from Infrahub, then verifies the reloaded switch and writes `ztp_state`.
  Ran end to end to `complete` in 233 s. The `ciscoadmin` secret (`ztp/make-secret.sh`) must exist before ZTP, or the
  verification has no login. Details and the state table: [`ztp/README.md`](../ztp/README.md).
- `lab/day2/`: `nornir_push.py --check` copies the device's artifact to bootflash and runs the NX-OS config-replace
  pre-check; without `--check` it runs `configure replace`, saves, and the next `--check` must print
  "Rollback Patch is Empty". The file keeps the device's TCAM carve lines (they are not in Infrahub), and every template
  states `no ip domain-lookup`, so the replace is clean on a carved switch. An Ansible playbook does the same push.
  Details: [`lab/day2/README.md`](../lab/day2/README.md).

## Memory and sizing

| What | Memory |
|---|---|
| Infrahub stack (8 containers) | about 3.7 GiB |
| one N9Kv 10.6(4)M lite | 6 GB (`--memory 6144`) plus the host reserve |
| the DC1 lab path (10 N9Kv + 11 FRR / hosts) | about 88 GB; ran on a 123 GB AWS host with 9 N9Kv (8 Oct) |
| the DC2 lab path (10 N9Kv + 11 FRR / hosts) | about 88 GB |
| DC1 and DC2 lab paths together | about 176 GB; ran on a 64-core cloud VM (25 Oct) |
| the entire DC1 site (34 N9Kv + FRR stand-ins) | about 290 GB; not run |

## Known limits

- BFD is not on the Nexus 9000v: every BFD check fails there and spine failover takes about 40 s instead of 3 s.
- `containerlab start --node` does not bring a stopped Nexus 9000v back (19 Oct, containerlab 0.79); FRR stand-ins and hosts
  come back in seconds. Details: [LAB-GUI.md](LAB-GUI.md).
- `docker pause` on an FRR stand-in does not stop kernel forwarding; the failure tests use `ip link set down` for those.
- MACsec (DCI switches) and `evpn esi multihoming` (ESI leaves) are rejected by the virtual image.
- `labctl push` imports netmiko for its SSH fallback: install `lab/requirements-lab.txt` into the environment that runs
  labctl, or run it from `.venv-lab`.
