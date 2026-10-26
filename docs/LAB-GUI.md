# Turn a single device off and on

Every device in the lab is a container, so you can stop and start one device while the rest of the lab keeps running,
from the command line or by clicking in VS Code. Bring the lab up first as in [docs/LAB.md](LAB.md) (`labctl deploy`,
`push`, `carve`). The commands below use the DC1 lab path; for another lab use its name and folder (table at the end).

## Find the lab, its devices and its topology file

READ ONLY
```
.venv/bin/python lab/labctl.py status DC1
docker inspect -f '{{index .Config.Labels "clab-topo-file"}}' clab-vamd-dc1-p3-DC1-L11
```
The lab is `vamd-dc1-p3`; each device runs in a container named `clab-vamd-dc1-p3-<device>`, for example
`clab-vamd-dc1-p3-DC1-L11`. The second command prints the topology file the lab was deployed from:
`lab/build/DC1/topology.boot.yml` after a normal `labctl deploy`, `lab/build/DC1/topology.override.yml` after
`labctl deploy --memory` (laptop). Use that file in every `containerlab` command below.

## Which devices can be stopped and started on their own

| Device kind | Devices | Stop on its own | Start on its own | Tested |
|---|---|---|---|---|
| FRR stand-ins | firewalls (INTFW1, INETFW1), ISP routers (ISP1, ISP2), carriers (ISP-A, ISP-B) | yes | yes, back in seconds with the same routing | laptop, 19 Oct 2026: config, VRFs, addresses and routes the same after start |
| Test hosts | HOST-PROD, HOST-ISP-A, HOST-ISP-B | yes | yes, back in seconds with the same address and route | laptop, 19 Oct 2026 |
| Nexus 9000v | S1, S2, L11, BL1/2, SL1/2, EDGE1/2 | yes | **no**: the switch did not come back within 40 minutes | laptop, 19 Oct 2026 (containerlab 0.79, vrnetlab N9Kv 10.6(4)M lite) |

To take a Nexus out of service for a while, shut its ports instead (next section) or destroy and redeploy the lab.

## FRR stand-ins and test hosts: stop and start

Step 1, stop (RUN):
```
containerlab stop -t lab/build/DC1/topology.boot.yml --node DC1-INTFW1
```
Step 2, start (RUN):
```
containerlab start -t lab/build/DC1/topology.boot.yml --node DC1-INTFW1
```
containerlab keeps the device's links while it is stopped. In the 19 Oct test the stand-in came back with the same FRR
config, VRFs, addresses and routes, with nothing to do by hand. Check it (READ ONLY):
```
docker exec -it clab-vamd-dc1-p3-DC1-INTFW1 vtysh -c 'show bgp vrf all summary'
```
The same two commands work for every FRR node and host: replace `DC1-INTFW1` with the device name.

## Nexus 9000v: take it out without stopping it

Shut its ports from the switch CLI (`ssh ciscoadmin@<mgmt address>`, password from `ztp/make-secret.sh`):
```
configure terminal
interface Ethernet1/49-52
  shutdown
```
`no shutdown` brings it back. The mgmt address of every switch is on the lab diagrams (page 1 and page 2).

`containerlab stop --node DC1-L11` does stop the switch, but `containerlab start --node DC1-L11` did not bring it back
(the vrnetlab QEMU guest stayed unhealthy for 40 minutes). To get a stopped Nexus back, rebuild the lab:
```
.venv/bin/python lab/labctl.py destroy DC1
.venv/bin/python lab/labctl.py deploy DC1
.venv/bin/python lab/labctl.py push DC1
.venv/bin/python lab/labctl.py carve DC1
```

## The same by clicking: VS Code

1. Install VS Code with the extensions **WSL** (Windows only) and **Containerlab** (publisher srl-labs).
2. Open the repo folder on the machine that runs the lab (Windows: "WSL: Connect to WSL", then open the folder).
3. Containerlab icon in the left bar → **Running Labs** → `vamd-dc1-p3` → right-click a device → **Stop** or **Start**
   (FRR stand-ins and hosts, as in the table above). **Attach shell** opens a shell in the container (`vtysh` on an FRR
   node); **Connect (SSH)** opens a Nexus CLI: log in as `ciscoadmin` with the password from `ztp/make-secret.sh`.

## The whole lab

Stop everything: `.venv/bin/python lab/labctl.py destroy DC1`. Bring it back: `deploy`, `push`, `carve` as in
[docs/LAB.md](LAB.md), about 27 minutes for the DC1 lab path.

| Lab | Lab name / containers | Folder and topology file |
|---|---|---|
| DC1 lab path | `vamd-dc1-p3` / `clab-vamd-dc1-p3-<device>` | `lab/build/DC1/topology.boot.yml` |
| DC2 lab path | `vamd-dc2-p3` / `clab-vamd-dc2-p3-<device>` | `lab/build/DC2/topology.boot.yml` |
| One switch on a laptop | `vamd-dc1-p3` | `lab/build/DC1/topology.override.yml` |

Diagrams of every lab, with each device's ports, addresses and login: [docs/lab-diagrams/](lab-diagrams/).
