# Design, as built

Two datacenters, DC1 and DC2, each a Cisco Nexus VXLAN EVPN fabric, joined by EVPN Multi-Site over two 100G circuits.
Everything around the fabric (firewalls, ISP and cloud routers, SD-WAN hub, VPN partners, DMZ, out-of-band) is modeled as
data too, and every device's config renders from Infrahub. This page describes what is built; the full decision log with
every alternative considered lives outside this repository.

Diagrams: `docs/diagrams/` (one draw.io site diagram and one port view per site, plus tables and a cable schedule, all
rendered by the `site_diagrams`, `site_ports`, `site_tables` and `site_cable_schedule` transforms).

## 1. Scope

| Item | Value |
|---|---|
| Sites | DC1 (United States → Rockledge, MD), DC2 (United States → Ashburn, VA); location tree Country → Metro → Site |
| Switches per site | 34: 2 spines, 20 server leaves, border-leaf pair, service-leaf pair, border-gateway pair, edge pair, DCI pair, DMZ pair |
| Server leaves | vPC pair L1–L2, EVPN ESI pairs L3–L4 … L9–L10, single-homed L11–L20 |
| Firewalls per site | FortiGate active/passive pairs: Internet FW, Internal FW (FG-3800G), SD-WAN hub, VPN-partner FW (FG-1800F), OOB FW (FG-120G) |
| Routers per site | 2 ISP routers and 2 cloud routers, Cisco 8201-24H8FH, IOS XR 25.2.1 |
| Out-of-band per site | 2 aggregation switches (93180YC-FX3), 2 network-access and 15 BMC-access switches (9348GC-FX3), 2 console servers |
| Not built | F5 load balancers (DMZ ports reserved), server-facing ports beyond one lab access port, Ethernet Segments per server |
| Software | NX-OS 10.6(1)F intended (10.6(4)M lite in the lab), FortiOS 7.6.7, IOS XR 25.2.1, FRR 10.6.0 for the lab stand-ins |
| Data and code | Infrahub 1.11.2, infrahub-sdk 1.23.2; branch `main` |

## 2. Hardware and roles

| Role | Devices | Model | Routing |
|---|---|---|---|
| Spine | `<site>-S1`, `-S2` | N9K-C9364D-GX2A | OSPF underlay, iBGP EVPN route reflector for every leaf and BGW |
| Server leaf | `<site>-L1` … `-L20` | N9K-C93180YC-FX3 | VTEP; anycast gateway; vPC, ESI or single-homed |
| Border leaf (vPC) | `<site>-BL1`, `-BL2` | N9K-C9332D-GX2B | VTEP; TRANSIT and PARTNER eBGP to the Internet FW, SD-WAN hub, VPN FW and cloud routers |
| Service leaf (vPC) | `<site>-SL1`, `-SL2` | N9K-C9332D-GX2B | VTEP; one eBGP session per tenant VRF and TRANSIT to the Internal FW |
| Border gateway | `<site>-BGW1`, `-BGW2` | N9K-C9332D-GX2B | EVPN Multi-Site anycast BGW, VIP on loopback2, eBGP to the DCI switches |
| DCI switch | `<site>-DCI1`, `-DCI2` | N9K-C9332D-GX2B | one 100G circuit each, eBGP across it with BFD, iBGP pair |
| Edge pair (vPC) | `<site>-EDGE1`, `-EDGE2` | N9K-C9332D-GX2B | L2 only: ISP routers and the outside legs of the Internet, SD-WAN and VPN firewalls |
| DMZ pair (vPC) | `<site>-DMZ1`, `-DMZ2` | N9K-C93180YC-FX3 | L2 only, behind the Internet FW (the gateway at .1 of every DMZ /24) |

Interface names are the hardware names (`Ethernet1/n`, `mgmt0`, `port-channelN`, `VlanN`); descriptions name the far end.
The leaf-side spine ports are Eth1/49–52 on the 93180YC-FX3 (two per spine) and Eth1/1–2 on the 9332D-GX2B.

## 3. Numbering

### Addresses

| Block | Use |
|---|---|
| 10.16.0.0/14 | DC1: tenants 10.16.0.0/16, infrastructure 10.19.0.0/19 |
| 10.20.0.0/14 | DC2: tenants 10.20.0.0/16, infrastructure 10.23.0.0/19 (same third octet means the same thing in both sites) |
| 10.x.0.0/24 | loopback0 pool (/32); 10.x.1.0/24 loopback1 VTEP pool; 10.x.2.0/25 vPC anycast VTEPs; 10.x.2.128/25 Multi-Site VIPs |
| 10.x.3.0/24 | vPC backup SVI /31s (Vlan3900); 10.x.4.0/22 spine–leaf /31s (92 links per site); 10.x.17.0/24 vPC keepalive /31s |
| 10.x.8.0/22 | firewall attachments (one /29 per firewall per VRF: .1 leaf A, .2 leaf B, .3 firewall); 10.x.8.128/25 Internal FW inter-VDOM /30s |
| 10.x.12.0/24, 10.x.13.0/24 | DCI switches; cloud routers (loopbacks, iBGP links) |
| 10.x.14.0/24 | edge VLAN 3200: VRRP VIP .1, ISP1 .2, ISP2 .3, Internet FW .10 |
| 10.x.15.0/24 | ISP routers (cross-link /31, loopbacks) |
| 10.x.16.0/24 | TRANSIT attachments, one /29 per slot: 0 Internal FW, 1 Internet FW, 2 SD-WAN hub, 3–4 cloud routers |
| 10.0.255.0/24 | inter-site links (DCI-1 10.0.255.0/31, DCI-2 .2/31), owned by neither site |
| 10.250.0.0/20, 10.250.16.0/20 | OOB: network-device mgmt /24 (mgmt0, gateway .1 on the OOB FW), OOB infrastructure /24, server BMCs /22 |
| 10.64.0.0/12 | SD-WAN branches |
| 172.16.0.0/13, 172.30.0.0/15 | AWS, Azure |
| 198.51.100.0/24, 203.0.113.0/24 | public space DC1, DC2 (documentation ranges): /25 NAT pool, /28 VPN endpoints, /28 SD-WAN WAN, /27 published services |
| 192.0.2.0/24 | carrier /30s (documentation range) |

Source: `objects/03-ip-plan.yml` and `scripts/create_pools.py`. Pools allocate by identifier (`<device>-<interface>`),
so a rerun gets the same addresses.

### ASNs

| Device(s) | DC1 | DC2 |
|---|---|---|
| Fabric (spines, leaves, BGWs; iBGP EVPN) | 65100 | 65200 |
| Internal FW / SD-WAN hub / VPN-partner FW / Internet FW | 65110 / 65120 / 65130 / 65140 | 65210 / 65220 / 65230 / 65240 |
| DCI switches / cloud routers | 65160 / 65170 | 65260 / 65270 |
| ISP routers (shared) | 65050 | 65050 |
| SD-WAN overlay / AWS / Azure / carriers ISP-A, ISP-B | 65400 / 65500 / 12076 / 64496, 64497 | same |

Rule: site block + role offset (fabric +00, Internal +10, SD-WAN +20, VPN +30, Internet +40, DCI +60, cloud +70). eBGP at
every administrative or physical boundary; iBGP only inside one site's fabric or pair.

### Names and VLANs

Devices `<site>-<role><n>`. Pairs `<site>-VPC-<m1>-<m2>` (domain ID = site base 100/200 + fixed slot: 01 L1–L2, 02 BL,
03 SL, 04 EDGE, 05 DMZ) and `<site>-ESI-<m1>-<m2>`. Server VLANs 1000–1599 with L2 VNI = 10000 + VLAN; L3 VNI VLANs
2500 + (VNI − 50000), TRANSIT 2599; attachment VLANs 3000 + TRANSIT slot and 3100 + Internal FW slot; edge VLAN 3200,
PARTNER 3300, vPC backup underlay 3900, DMZ 1400–1415.

## 4. Underlay

- OSPF `UNDERLAY` area 0 inside each site, numbered /31 fabric links, point-to-point, MTU 9216 everywhere.
- loopback0 = router ID and BGP peering (pool `<site>-LOOPBACK0`); loopback1 = VTEP source on leaves and BGWs, with the
  vPC anycast VTEP as secondary; loopback2 = Multi-Site VIP on the BGWs.
- Spine–leaf iBGP EVPN with BFD multihop 999 ms × 3 (the range the device accepts; detection longer than OSPF
  reconvergence). BGP password a constant in the templates.
- Every leaf uses `maximum-paths ibgp 4`; ESI leaves `evpn esi multihoming`.

## 5. vPC and ESI

| Item | Value |
|---|---|
| vPC pairs | L1–L2, BL, SL, EDGE, DMZ per site |
| Peer-link | port-channel1000, LACP active, `spanning-tree port type network`, no MTU line (the device keeps 9216) |
| Peer-keepalive | Eth1/48 (93180YC-FX3) or Eth1/34 (9332D-GX2B), VRF `VPC-KEEPALIVE`, /31, timers 1000 / 5 / 3 |
| Backup underlay | Vlan3900 `VPC-BACKUP-UNDERLAY`, /31, OSPF point-to-point, `system nve infra-vlans 3900` |
| Domain | peer-gateway, `layer3 peer-router` (VTEP pairs), ARP synchronize, delay restore 360, `auto-recovery reload-delay 360`, role priority 10/20, peer-switch, STP priority 8192 |
| Anycast VTEP | loopback1 secondary from `<site>-ANYCAST-VTEP`, `advertise-pip` + `advertise virtual-rmac` |
| ESI pairs | L3–L4 … L9–L10: `evpn esi multihoming`; Ethernet Segments per server deferred |
| Attachments | one vPC port-channel per attached unit: Po11/Po12 firewall unit 1/2, Po21/Po22 ISP1/ISP2, BPDU guard, `port type edge trunk` |

## 6. Overlay and Multi-Site

- iBGP L2VPN EVPN per site, spines as route reflectors (`retain route-target all`), ingress replication for BUM, ARP
  suppression on every VNI (`global suppress-arp`; the virtual Nexus needs a TCAM carve for it, `labctl carve`).
- Anycast gateway .1 of every segment, MAC 0200.0000.00aa; symmetric IRB with one L3 VNI VLAN per VRF.
- Route targets `auto` with `rewrite-evpn-rt-asn` on the BGWs, so 65100 and 65200 import each other's stretched VRFs.
- EVPN Multi-Site: anycast BGW pair per site (VIP on loopback2, dci-tracking / fabric-tracking, storm control 1 %), eBGP
  BGW ↔ DCI switch, eBGP across each DCI circuit with BFD and a placeholder MACsec key.

## 7. Tenants and routing policy

| VRF | L3 VNI / VLAN | Scope |
|---|---|---|
| PROD, DEV | 50001 / 2501, 50002 / 2502 | stretched, Multi-Site, homed in DC1 |
| DC2-PROD, DC2-DEV | 50011 / 2511, 50012 / 2512 | DC2 only |
| CITRIX | 50022 / 2522 | one per site, never on the BGWs (its VLANs and VNIs are reused in both sites) |
| PARTNER | 50031 / 2531 | both sites, per-site NAT /21 |
| REPLICATION | 50041 / 2541 | stretched, isolated: no leaks, no default, no firewall |
| TRANSIT | 50100 / 2599 | both sites, no server subnets: firewall and router attachments only |

Source: `objects/06-fabric.yml` and the 62 segments in `objects/07-segments.yml`.

Traffic paths:
- Tenant VRFs leave the fabric only through the Internal FW on the service leaves: each tenant VRF sends its summaries and
  accepts only 0/0. In TRANSIT the service leaves send the Internal FW only 0/0 and accept the tenant summaries. The
  Internal FW leaks between its VRFs over inter-VDOM links and rewrites the AS path toward the leaves
  (`set-aspath-action replace`, so the fabric AS never appears in a path it receives). DC2's Internal FW prepends 3×
  on the stretched VRFs.
- The border leaves carry TRANSIT to the Internet FW (accept 0/0 only, send the tenant summaries), the SD-WAN hub, the
  cloud routers, and PARTNER to the VPN-partner FW. The ISP routers originate 0/0 toward the Internet FW only while a
  carrier default exists (`rib-has-route`), send only the site's own /24 to the carriers, and run VRRPv3 on the edge VLAN.
- Published services: Internet FW DNAT from the public /27 to the DMZ; branches and clouds reach the DCs through TRANSIT
  and the Internal FW, never through the Internet FW.

## 8. Management, ZTP and Day-2

- `mgmt0` in VRF `management` on the OOB network, gateway .1 of the site's network-device /24 (the OOB FortiGate), one
  console port per device to the console servers; pairs split across the two OOB access switches and console servers.
- Local user `vamdadmin` (network-admin) on every NX-OS device: its hash is a placeholder in every render and baseline
  and is filled at delivery time from `~/.vamd/secrets.env` (`ztp/make-secret.sh`, `lab/vamd_secrets.py`).
- ZTP: Kea DHCP points a blank switch at `ztp/poap.py`; the ZTP server finds the device by `serial_number`, serves its
  `startup-config` artifact live, and after the reload verifies the switch over SSH. `ztp_state` (pending / served /
  complete / failed) and `ztp_updated` are written on `main` by the `ztp-callback` account. See `ztp/README.md`.
- Day-2: `lab/day2/nornir_push.py` (or the Ansible playbook) copies the device's artifact to bootflash and runs NX-OS
  `configure replace`; the file keeps the device's `hardware access-list tcam region` lines, every template states
  `no ip domain-lookup`, and a failed replace keeps the switch's config-replace log in the report. See `lab/day2/README.md`.
- Left out on purpose: NTP, AAA servers, SNMP, logging, banners (not decided yet).

## 9. Data model and pipeline

```
objects/*.yml ──► Infrahub (branch main) ──► generator fabric_site ──► 46 production devices per site, interfaces,
      ▲             pools (scripts/create_pools.py)     oob_site      ──► OOB devices, console ports, 179 cables per site
      │                                                site_physical ──► racks, PDUs, placements, 167 fabric cables per site
      │                                                        │
 queries/*.gql ◄──────── Jinja2 templates/*.j2 and transforms/lab.py ──► 134 configs + 58 lab artifacts (+ 8 diagram views)
                                                                        │
                        lab/ (labctl deploy / push / carve, postcheck, failtests, day2) ◄┘
```

- Schema: Marketplace base schemas plus `schemas/locations.yml`, `schemas/fabric.yml` (`FabricAutonomousSystem`,
  `FabricVRF`, `FabricSegment`, `FabricSwitchPair` → `FabricVpcDomain` / `FabricEsiPair`, `FabricAttachment`,
  `FabricL2Vlan`, `FabricBranch`, `FabricPartner`, `InterfacePortChannel`, device roles, `ztp_state`) and
  `schemas/oob.yml` (`DcimCable`, racks, PDUs, console ports). A device can be in at most one pair.
- Generators have a plan-only mode (`FABRIC_DRY_RUN=1`, `SITE_PHYSICAL_DRY_RUN=1`) and a reconciler that removes what
  the plan no longer contains (`FABRIC_RECONCILE=report` prints instead). Objects owned by a file are linked, never
  saved, by a generator. Idempotency standard: every generator-owned id is snapshotted before and after a full rerun.
- Every change goes through a Git branch, an Infrahub proposed change (about 30 validators: schema, data, repository,
  tests, one artifact validator per artifact definition, one generator validator per generator, `loopback0_check`), a
  merge through the PC, then a check of `main`: object counts, stored artifacts equal to the baselines, every config
  rendered and compared byte for byte with `tests/expected/`.

## 10. Lab and verification

| Item | Status |
|---|---|
| Every NX-OS config (106) on one Nexus 9000v 10.6(4)M lite | loaded, no config error that needs a change; rejected only MACsec (DCI) and `evpn esi multihoming` (ESI leaves), both limits of the virtual image |
| Full DC1 site on AWS (m8i.8xlarge, 18 nodes, 43 links: 9 N9Kv + FRR stand-ins for the firewalls, ISP routers, carriers, 3 hosts) | PROD host → internet 5/5 through the designed path with no lab-only changes; post-check 108 of 164 (the 56 failures: 44 BFD, absent on the virtual Nexus, and 12 summary routes with no lab host in DEV/CITRIX) |
| Failure tests | 14 documented with before/during/after captures; open: one Internet FW bundle member down stops traffic, vPC peer-link outage measured late (accepted for go-live) |
| ZTP | blank N9Kv → `complete` on main in 233 s with the merged code |
| Day-2 push | `configure replace` from the artifact completed on a carved N9Kv; the second check prints "Rollback Patch is Empty" |
| Laptop | one N9Kv at a time (6 GB), Infrahub stopped while it runs; `labctl deploy DC1 --node-filter DC1-L11 --memory 6144 --reserve 512` |

## 11. Known gaps

1. F5 load balancers: ports reserved on the DMZ pair, nothing rendered.
2. FortiOS and IOS XR configs are rendered and checked, not run on real devices; FRR stand-ins speak the same BGP.
3. BFD never observed (not on the virtual Nexus). DC2 never run in a lab.
4. The unused schema choices `pop_circuit` / `pop_router` remain in a live schema that was loaded before the Denver POP was removed.
5. `labctl push` needs netmiko in the same environment as infrahub-sdk for its SSH fallback (`lab/requirements-lab.txt`).
