# Cisco-vxlan-evpn

[![checks](https://github.com/cciedata/Cisco-vxlan-evpn/actions/workflows/checks.yml/badge.svg)](https://github.com/cciedata/Cisco-vxlan-evpn/actions/workflows/checks.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

I built this to run two datacenters as code. DC1 and DC2 are Cisco Nexus VXLAN EVPN fabrics joined by EVPN Multi-Site,
with FortiGate firewalls, Cisco 8000 ISP and cloud routers, an SD-WAN hub, VPN partners, a DMZ pair and a full
out-of-band network around them.

Everything starts in Infrahub. Two generators turn a few intent values per site into devices, interfaces, pairs, cables
and IP addresses, and Jinja2 templates turn that into the configs: NX-OS for the switches, FortiOS for the firewalls,
IOS XR for the routers. I don't edit configs by hand: if something has to change, it changes in the data, goes through a
proposed change with about 30 validators, and every rendered config is compared byte for byte with a reviewed baseline.
The lab (containerlab, Nexus 9000v plus FRR stand-ins for everything that is not a Nexus) is rendered from the same data.

## Where it stands

134 configs render on `main` and equal their baselines: 106 NX-OS, 20 FortiOS, 8 IOS XR. Every Nexus config has loaded
on a real NX-OS 10.6(4) image; the only rejected lines are two limits of the virtual image (MACsec, ESI multihoming). The full DC1 site (18 nodes, 43 links) has run as a lab on AWS end to
end with the merged design and no lab-only changes: PROD host to the internet through the leaf, the service leaf, the
Internal FW, TRANSIT, the border leaf and the Internet FW. 14 failure scenarios are documented with before/during/after
evidence. ZTP (POAP) provisions a blank switch from Infrahub to `complete`, and the Day-2 push (NX-OS config replace from
the Infrahub artifact) applies on a real switch with an empty rollback patch afterwards.

Not done: the F5 load balancers, the FortiOS and IOS XR configs on real devices (FRR stand-ins speak the same BGP in the
lab), BFD (not on the virtual Nexus), a DC2 lab run, and the server-facing ports beyond one lab access port.

## What's in it

- Two sites, 46 production devices each: 2 spines, 20 server leaves (one vPC pair, 4 EVPN ESI pairs, 10 single-homed),
  border, service, edge, DMZ, BGW and DCI pairs, 2 Internal and 2 Internet FortiGates, 2 ISP routers, 2 cloud routers,
  2 SD-WAN hubs, 2 VPN-partner FortiGates. OSPF underlay, iBGP EVPN with the spines as route reflectors, eBGP at every
  boundary, 8 tenant VRFs, 62 segments, anycast gateway.
- A full out-of-band network per site: 2 OOB FortiGates, 2 aggregation switches, 2 network-access and 15 BMC switches,
  2 console servers, a console port on every production device, 179 cables per site.
- Rack placement and cabling as data: 69 placements, 58 PDUs and 167 fabric cables per site, with four rendered views per
  site (site diagram, port view, tables, cable schedule).
- The lab: topology, post-check expectations, FRR stand-in init scripts and the IOS XR lab variants are Infrahub artifacts.
  `lab/labctl.py` fetches, validates, deploys, pushes and carves; `lab/postcheck.py` runs 164 checks; `lab/failtests.py`
  runs the failure tests; `lab/day2/` pushes configs with Nornir or Ansible.
- ZTP: Kea DHCP, a POAP script, a ZTP server that renders the config live by serial and records `ztp_state` in Infrahub.
- The rendered config of every device, in [`tests/expected/`](tests/expected), and every lab artifact in
  [`tests/expected-lab/`](tests/expected-lab).

## Try it

[docs/QUICKSTART.md](docs/QUICKSTART.md) takes you from an empty Ubuntu machine (Linux, or Windows with WSL2) to an
Infrahub that holds both sites and renders all 134 configs. One Nexus 9000v fits a 12 GB laptop with Infrahub stopped;
the full DC1 lab needs a host with about 80 GB (it ran on an AWS m8i.8xlarge). More detail is in
[docs/BUILD.md](docs/BUILD.md) (every build step) and [docs/DESIGN.md](docs/DESIGN.md) (the design as built).

## Repository map

| Path | What it holds |
|---|---|
| `docs/QUICKSTART.md` | from an empty Ubuntu (Linux or WSL2) to Infrahub with both sites and 134 rendered configs; then the lab |
| `docs/DESIGN.md` | the design as built: sites, hardware, numbering, underlay, vPC and ESI, overlay, tenants, firewalls, OOB, pipeline |
| `docs/BUILD.md` | the build, step by step: Infrahub, schema, objects, pools, generators, renders, checks against the baselines |
| `docs/LAB.md` | the lab: images, single Nexus on a laptop, full DC1 site on a big host, post-checks, failure tests, Day-2 push |
| `docs/LAB-GUI.md` | turn a single device off and on, from the command line or by clicking in VS Code (containerlab extension) |
| `docs/lab-diagrams/` | one PDF per lab (one switch, DC1 lab path, DC2 lab path, both, the entire DC1 site): cables, addresses, BGP, packet walk, first ARP, run card |
| `docs/diagrams/` | the four rendered views per site (draw.io diagrams, port views, tables, cable schedule) |
| `ztp/` | zero-touch provisioning of the Nexus switches from Infrahub (`ztp/README.md`) |
| `lab/` | `labctl.py`, `postcheck.py`, `failtests.py`, the Day-2 push (`lab/day2/`), the lab requirements (`lab/README.md`) |
| `docker-compose.yml`, `.env.example` | the Infrahub 1.11.2 stack (compose project `cisco-vxlan-evpn`, no built-in secrets) |
| `pyproject.toml`, `uv.lock`, `infrahubctl.toml.example` | the uv environment (infrahub-sdk 1.23.2), infrahubctl settings |
| `schemas/` | the Infrahub schema: base schemas, locations, `fabric.yml` (ASNs, VRFs, segments, switch pairs, attachments), `oob.yml` |
| `objects/` | the data that is not generated, loaded in file-name order: groups, countries, sites, IP plan, hardware, providers, ASNs and VRFs, segments, branches, partners, lab host ports |
| `generators/` | `fabric_site` (the production fabric of a site from 4 intent values), `oob_site` (the OOB network and cables), `site_physical` (racks, PDUs, placements, fabric cables) |
| `queries/`, `templates/`, `transforms/`, `.infrahub.yml` | GraphQL queries, 28 Jinja2 templates, the lab transforms, the artifact and generator definitions |
| `checks/` | `loopback0_check`: every device in a site's fabric ASN has exactly one loopback0 with one address |
| `scripts/` | `create_pools.py` (the IP pools), schema and query helpers |
| `tests/expected/`, `tests/expected-lab/` | the 134 reference configs and 58 reference lab artifacts |

## License

MIT, see LICENSE.
