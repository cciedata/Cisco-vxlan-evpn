# Cisco-vxlan-evpn

[![checks](https://github.com/cciedata/Cisco-vxlan-evpn/actions/workflows/checks.yml/badge.svg)](https://github.com/cciedata/Cisco-vxlan-evpn/actions/workflows/checks.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

This is two datacenters built as code. DC1 and DC2 are Cisco Nexus VXLAN EVPN fabrics connected with EVPN Multi-Site.
Around them sit FortiGate firewalls, Cisco 8000 routers for the ISPs and the clouds, an SD-WAN hub, partner VPNs, a DMZ
and a separate out-of-band network.

Infrahub holds everything. The design rules (IP numbering, port maps, timers, routing policy) are stored there as data,
in `design/fabric-design.yml`, not hard-coded in scripts. From those rules and a few numbers per site (how many leaf
pairs, how many single leaves), the generators create every device, interface, cable and IP address. Templates then
produce the configs: NX-OS for the switches, FortiOS for the firewalls and IOS XR for the routers.

I never edit a config by hand. A change goes into the data, runs through an Infrahub proposed change with its checks, and
every config it produces is compared line by line with a reviewed copy before it is merged. The lab is built from the
same data.

## Status

- 134 configs: 106 NX-OS, 20 FortiOS and 8 IOS XR. All of them match their reviewed copies.
- A fresh install, following [docs/QUICKSTART.md](docs/QUICKSTART.md) on an empty machine, works from start to finish.
- Every Nexus config loads on a real NX-OS 10.6(4) image. The only lines it refuses are MACsec and ESI multihoming,
  which the virtual Nexus doesn't support.
- Both sites have run together as a lab: 10 virtual Nexus per site, with FRR containers standing in for the firewalls
  and routers. All BGP, route and ping checks passed on both sites. Two things were changed only in the lab: extra test
  hosts for the DEV and CITRIX networks, and no BGP password on one firewall session in DC2 (still looking at why).
- DC1 has also run on its own with no lab changes at all: a server reaches the internet through the leaf, the service
  leaf, the internal firewall, the border leaf and the internet firewall. The failure tests are written up with
  before, during and after captures.
- Zero-touch provisioning takes a blank switch to a working config straight from Infrahub. Day-2 changes are pushed with
  NX-OS config replace, and the pre-check catches anything changed by hand on a switch.

Not done yet: the F5 load balancers, running the FortiOS and IOS XR configs on real hardware (the lab uses FRR for
those), BFD (the virtual Nexus doesn't run it), and server ports beyond the lab test ports.

## What's in it

- Two sites with 46 production devices each: 2 spines, 20 server leaves (one vPC pair, 4 ESI pairs, 10 single-homed),
  border, service, edge, DMZ, border gateway and DCI pairs, internal and internet firewalls, ISP and cloud routers,
  SD-WAN hubs and partner VPN firewalls. OSPF underlay, iBGP EVPN with the spines as route reflectors, eBGP at every
  boundary, 8 tenant VRFs and 62 networks.
- An out-of-band network per site: its own firewalls and switches, console servers, and a console cable to every
  production device.
- Racks, power and cabling as data, with a site diagram, port view and cable schedule generated for each site.
- The lab: Infrahub produces the containerlab topology, the expected results and the FRR configs. `lab/labctl.py` sets
  the lab up and loads the configs, `lab/postcheck.py` checks it, `lab/failtests.py` breaks things on purpose and checks
  again, and `lab/day2/` pushes changes with Nornir or Ansible.
- Zero-touch provisioning: DHCP, a POAP script and a small server that builds each switch's config on request and
  records the result in Infrahub.
- Four checks that run on every change, for example that every fabric switch has exactly one loopback0 address and that
  no two address blocks overlap.
- The expected config of every device in [`tests/expected/`](tests/expected) and of every lab file in
  [`tests/expected-lab/`](tests/expected-lab).

## Try it

Start with [docs/QUICKSTART.md](docs/QUICKSTART.md). It takes an empty Ubuntu machine (Linux, or Windows with WSL2) to an
Infrahub with both sites and all 134 configs. One virtual Nexus runs on a 12 GB laptop if Infrahub is stopped. A full
site lab needs about 88 GB of RAM; both sites together ran on a 64-core cloud VM. [docs/BUILD.md](docs/BUILD.md) goes
through every build step, [docs/DESIGN.md](docs/DESIGN.md) explains the design and [docs/LAB.md](docs/LAB.md) the lab.

## Repository map

| Path | Contents |
|---|---|
| `docs/QUICKSTART.md` | Install guide, from an empty Ubuntu machine to Infrahub with all 134 configs, then the lab |
| `docs/DESIGN.md` | The design: sites, hardware, IP numbering, underlay, vPC and ESI, overlay, tenants, firewalls, OOB |
| `docs/BUILD.md` | Every build step in order, with the checks against the expected configs |
| `docs/LAB.md` | Running the lab, from one switch on a laptop to both sites on a large host |
| `docs/LAB-GUI.md` | Shutting down or starting one lab device, from the CLI or VS Code |
| `docs/LAB-GCP.md` | Running both lab paths on one Google Cloud VM, with the cost |
| `docs/lab-diagrams/` | A PDF per lab with cabling, addressing, BGP and a packet walk |
| `docs/diagrams/` | Site diagrams, port views, tables and cable schedules |
| `design/fabric-design.yml` | The design rules and values that everything else reads |
| `schemas/` | The Infrahub schema |
| `objects/` | Data that is entered rather than generated: sites, IP plan, hardware, VRFs, networks |
| `generators/` | Code that builds the devices, interfaces, cables and addresses for each site |
| `templates/`, `queries/`, `transforms/` | Config templates, the queries that feed them, and the lab file builders |
| `checks/` | The four checks that run on every change |
| `lab/` | Lab tools: setup, checks, failure tests and Day-2 push |
| `lab/gcp/` | Scripts that create, start and stop the Google Cloud lab VM and run the lab on it |
| `ztp/` | Zero-touch provisioning for the Nexus switches |
| `scripts/` | Helpers for loading the IP pools and the design rules |
| `tests/` | Offline tests and the expected output of every device and lab file |
| `docker-compose.yml`, `.env.example` | The Infrahub 1.11.2 stack; passwords come from your own `.env` |
| `pyproject.toml`, `uv.lock` | The Python environment |

## License

MIT, see LICENSE.
