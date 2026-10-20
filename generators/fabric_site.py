import ipaddress
import os

from infrahub_sdk.generator import InfrahubGenerator

# Approved constants (Day 7)
PLATFORM = "Cisco NX-OS"                      # every switch
OS_VERSION = "10.6(1)F"                       # NX-OS target release, spines included
STATUS = "provisioning"
DCI_ASN_OFFSET = 60
MAX_SERVER_LEAVES = 20
DEVICE_TYPE = {
    "spine": "N9K-C9364D-GX2A",
    "leaf": "N9K-C93180YC-FX3",
    "border_leaf": "N9K-C9332D-GX2B",
    "service_leaf": "N9K-C9332D-GX2B",
    "bgw": "N9K-C9332D-GX2B",
    "edge": "N9K-C9332D-GX2B",
    "dci": "N9K-C9332D-GX2B",
    # Phase 2 (decided)
    "dmz": "N9K-C93180YC-FX3",
    "internet_fw": "FG-3800G",
    "internal_fw": "FG-3800G",
    "isp_router": "8201-24H8FH",
    "cloud_router": "8201-24H8FH",               # Phase 4 PC2 (row 52 B)
}

# Approved constants (Phase 2): platforms, releases, management ports, ASN rules
PLATFORM_BY_ROLE = {"internet_fw": "Fortinet FortiOS", "internal_fw": "Fortinet FortiOS", "isp_router": "Cisco IOS XR",
                    "cloud_router": "Cisco IOS XR"}   # cloud routers: IOS XR 25.2.1 (row 52 B)
OS_VERSION_BY_PLATFORM = {"Cisco NX-OS": OS_VERSION, "Cisco IOS XR": "25.2.1", "Fortinet FortiOS": "7.6.7"}  # FortiOS decided PC3
MGMT_IF_BY_PLATFORM = {"Cisco NX-OS": "mgmt0", "Cisco IOS XR": "MgmtEth0/RP0/CPU0/0", "Fortinet FortiOS": "mgmt1"}  # mgmt1: HA reserved management (PC3)
NO_MGMT_ROLES: set = set()   # PC3: the FortiGates get mgmt1 from the site MGMT0 pool like every other device
INTERNAL_FW_ASN_OFFSET = 10                      # fabric ASN + 10 (design: AS numbers)
INTERNET_FW_ASN_OFFSET = 40                      # fabric ASN + 40
ISP_ROUTER_ASN = 65050                           # shared by all four ISP routers

# Approved constants (Day 8): interfaces and addresses
INTERFACE_STATUS = "provisioning"          # Day 6 value (loopback0, mgmt0)
MGMT0_ADDRESS_STATUS = "active"            # Day 6 value (mgmt0)
LOOPBACK0_DESCRIPTION = "Router ID, BGP peering"
LOOPBACK1_DESCRIPTION = "VTEP source (NVE)"
MGMT0_DESCRIPTION = "OOB management"
MGMT0_ROLE = "management"
NO_LOOPBACK0_ROLES = {"edge", "dmz", "internet_fw", "internal_fw"}   # edge/dmz: L2 only; FW: PC3 (dci: fixed loopback0 since Phase 4)
LOOPBACK1_ROLES = {"leaf", "border_leaf", "service_leaf", "bgw"}           # VTEPs only (also the pairs that get a backup SVI)
# ISP routers: loopback0 is fixed from <infra>.15.16/28 (decided): ISP1 .17, ISP2 .18
ISP_LOOPBACK0_HOST = {1: 17, 2: 18}

# Approved constants (Day 8): fabric links (Day 6 values, Day 7 port maps)
FABRIC_ROLE = "core"
FABRIC_MTU = 9216
LINK_STATUS = "active"                     # prefixes and addresses (Day 6 value)
SERVER_LEAF_UPLINKS = {"S1": ("Ethernet1/49", "Ethernet1/50"), "S2": ("Ethernet1/51", "Ethernet1/52")}
FIXED_LEAF_UPLINKS = {"S1": "Ethernet1/1", "S2": "Ethernet1/2"}
SPINE_FIXED_PORTS = (("BL1", 41), ("BL2", 42), ("SL1", 43), ("SL2", 44), ("BGW1", 45), ("BGW2", 46))

# Approved constants (Day 8, Phase 2): switch pairs
VPC_DOMAIN_BASE = {"DC1": 100, "DC2": 200}
# Fixed slots: 1 = first vPC server pair, 2 BL, 3 SL, 4 EDGE, 5 DMZ (Phase 2), further vPC server pairs from 6.
VPC_FIXED_SLOTS = (("BL", 2), ("SL", 3), ("EDGE", 4), ("DMZ", 5))
FIRST_EXTRA_SERVER_SLOT = 6

# Approved constants (Phase 1, Phase 2): vPC plumbing per pair role
VPC_PEER_LINK = "port-channel1000"                     # same number on every vPC pair (decided Phase 2)
VPC_KEEPALIVE_VRF_NOTE = "VRF VPC-KEEPALIVE (template side)"
VPC_BACKUP_SVI = "Vlan3900"                                 # L-19: VLAN 3999 is reserved on NX-OS
VPC_MTU = 9216
VPC_PORTS = {
    # role: peer-link members, keepalive port, backup SVI (only pairs with NVE, decided Phase 2)
    "leaf":         {"peer_link": ("Ethernet1/53", "Ethernet1/54"), "keepalive": "Ethernet1/48", "backup": True},
    "dmz":          {"peer_link": ("Ethernet1/53", "Ethernet1/54"), "keepalive": "Ethernet1/48", "backup": False},
    "border_leaf":  {"peer_link": ("Ethernet1/3", "Ethernet1/4", "Ethernet1/5", "Ethernet1/6"), "keepalive": "Ethernet1/34", "backup": True},
    "service_leaf": {"peer_link": ("Ethernet1/3", "Ethernet1/4", "Ethernet1/5", "Ethernet1/6"), "keepalive": "Ethernet1/34", "backup": True},
    "edge":         {"peer_link": ("Ethernet1/1", "Ethernet1/2", "Ethernet1/3", "Ethernet1/4"), "keepalive": "Ethernet1/34", "backup": False},
}
ROLE_PEER_LINK = "vpc_peer_link"
ROLE_KEEPALIVE = "vpc_keepalive"
ROLE_BACKUP = "vpc_backup"
VPC_BACKUP_DESCRIPTION = "vPC backup underlay"

# Approved constants (Phase 2): switch ports toward attached devices; vPC number = port-channel number
# (switch role: (port, attached device short name, port-channel number)).
# Reserved, not created (decided): BL 1/9-1/12 SD-WAN + VPN FWs, 1/15-1/16 DCI1/2 (Phase 4; BL 1/13-1/14 cloud routers: created, PC2);
# SL 1/9-1/12 replication arrays; EDGE 1/9-1/12 SD-WAN + VPN FW outside (Phase 4); DMZ 1/1-1/4 load balancers (last).
ATTACH_PORTS = {
    "border_leaf":  (("Ethernet1/7", "INETFW1", 11), ("Ethernet1/8", "INETFW2", 12),
                     ("Ethernet1/13", "CLOUD1", 31), ("Ethernet1/14", "CLOUD2", 32)),   # Phase 4 PC2 (row 60 B; P4A-105/106)
    "service_leaf": (("Ethernet1/7", "INTFW1", 11), ("Ethernet1/8", "INTFW2", 12)),
    "edge":         (("Ethernet1/5", "ISP1", 21), ("Ethernet1/6", "ISP2", 22),
                     ("Ethernet1/7", "INETFW1", 11), ("Ethernet1/8", "INETFW2", 12)),
    "dmz":          (("Ethernet1/49", "INETFW1", 11), ("Ethernet1/50", "INETFW2", 12)),
}
ROLE_PEERING = "peering"       # ports, port-channels, bundles, SVIs and sub-interfaces toward attached devices

# Approved constants (Phase 2 PC3): FortiGate side of every attach link. Switch role -> (FW aggregate, FW port toward
# switch 1, FW port toward switch 2). FG-3800G: port19-22 400G, port15-18 200G (Fortinet hardware guide).
FW_PORTS = {
    "border_leaf":  ("agg-inside", ("port19", "port20")),
    "service_leaf": ("agg-inside", ("port19", "port20")),
    "edge":         ("agg-outside", ("port21", "port22")),
    "dmz":          ("agg-dmz", ("port15", "port16")),
}
FW_HA_PORTS = ("ha3", "ha4")                     # heartbeat (decided), role core (internal link between the pair)
FW_NPU_LINKS = ("npu0_vlink0", "npu0_vlink1")    # tenant side / TRANSIT side of every VRF leak link (Internal FW)
FW_VRF_ID = {"TRANSIT": 1, "PROD": 10, "DEV": 11, "DC2-PROD": 12, "DC2-DEV": 13, "CITRIX": 15, "PARTNER": 16}  # FortiOS VRF ids (decided)
FW_LEAK_THIRD, FW_LEAK_BASE = 8, 128             # vlink /30s: <infra>.8.(128 + 4 x Internal FW slot) (decided)
FW_OUTSIDE_SUBIF = "OUTSIDE-{vlan}"              # sub-interface names <VRF or role>-<VLAN> (decided)
FW_DMZ_SUBIF = "DMZ-{vlan}"
FW_DMZ_GW_HOST = 1                               # Internet FW is .1 of every DMZ /24 (decided)
ROLE_UPSTREAM = "upstream"     # carrier ports
EXT_MTU = 9216                 # every FW, ISP router and carrier link (decided)

# Approved constants (Phase 2): attachments (one per attached pair and VRF, on a vPC leaf pair)
# /29 by fixed slot: .1 lower member, .2 upper member, .3 the attached pair (shared by both units)
SITE_INFRA = {"DC1": "10.19", "DC2": "10.23"}
TRANSIT_ATTACHMENTS = (("INTFW", "SL", 0), ("INETFW", "BL", 1))     # slot in <infra>.16.0/24; slots 2-5 are Phase 4
CLOUD_ATTACHMENTS = (("CLOUD1", "BL", 3), ("CLOUD2", "BL", 4))     # design (TRANSIT table): one router per slot, .3 = the router (PC2)
INTFW_VRF_SLOT = {"PROD": 0, "DEV": 1, "DC2-PROD": 2, "DC2-DEV": 3, "CITRIX": 5, "PARTNER": 6}  # slot in <infra>.8.0/25; 4 retired (DMZ)
TENANT_EXCLUDED = {"REPLICATION", "TRANSIT"}     # never on the Internal FW
TRANSIT_VLAN_BASE = 3000
TENANT_VLAN_BASE = 3100
ATTACHMENT_NAME = "{site}-{vrf}-{pair}"          # option B (decided)

# Approved constants (Phase 2): ISP routers (Cisco 8201-24H8FH, IOS XR)
# ❓ 400G interface numbering: xrdocs lists FH0/0/0/0,2,..,14; Cisco's 8200 install guide says QSFP-DD at positions 0-7.
#    Used as decided (flagged); confirmed before PC3 renders; renamed by this generator if wrong.
ISP_EDGE_PORTS = (("FourHundredGigE0/0/0/0", "EDGE1"), ("FourHundredGigE0/0/0/2", "EDGE2"))
ISP_CROSSLINK_PORT = "FourHundredGigE0/0/0/4"
ISP_CARRIER_PORT = "FourHundredGigE0/0/0/6"
ISP_BUNDLE = "Bundle-Ether21"                    # same number on both routers (decided); edge side Po21 / Po22
EDGE_VLAN = 3200
ISP_EDGE_SUBIF = f"{ISP_BUNDLE}.{EDGE_VLAN}"     # tagged (decided)
EDGE_VLAN_HOST = {"VIP": 1, "ISP1": 2, "ISP2": 3, "INETFW": 10, "SDWANFW": 11, "VPNFW": 12}   # Phase 4: .11 SD-WAN FW, .12 VPN-partner FW

# Approved constants (Phase 4 PC3: SD-WAN hub FW; decisions.xlsx Open-Phase4 rows 37-46, 71; auto_approved.md P4A-3xx)
# BL 1/9-1/10 and EDGE 1/9-1/10 (reserved in Phase 2) are created from here on.
DEVICE_TYPE["sdwan_fw"] = "FG-1800F"                       # row 37 B
PLATFORM_BY_ROLE["sdwan_fw"] = "Fortinet FortiOS"          # row 37 B; release 7.6.7 from OS_VERSION_BY_PLATFORM
NO_LOOPBACK0_ROLES.add("sdwan_fw")                         # FortiGate: no loopback0 (same rule as the Phase 2 FWs)
SDWAN_FW_ASN_OFFSET = 20                                   # fabric ASN + 20 = 65120 / 65220 (design: AS numbers); local-as, row 38 B
FW_ROLES = {"internal_fw", "internet_fw", "sdwan_fw"}      # roles build_fw() builds
WAN_FW_ROLES = {"sdwan_fw"}                                # FWs with an outside leg on the edge VLAN + a public WAN loopback (static routing)
# FG-1800F: port37-40 = 4 x 100G QSFP28, ha1/ha2 = 10G SFP+, mgmt1/mgmt2 (Fortinet hardware acceleration guide, 1800F)
FW_PORTS_BY_ROLE = {
    "sdwan_fw": {"border_leaf": ("agg-inside", ("port37", "port38")),     # row 45 B; ports P4A-302
                 "edge": ("agg-outside", ("port39", "port40"))},          # row 46 B
}
FW_HA_PORTS_BY_ROLE = {"sdwan_fw": ("ha1", "ha2")}         # P4A-303
ATTACH_PORTS["border_leaf"] += (("Ethernet1/9", "SDWANFW1", 13), ("Ethernet1/10", "SDWANFW2", 14))   # row 45 B; Po/vPC 13/14 P4A-304
ATTACH_PORTS["edge"] += (("Ethernet1/9", "SDWANFW1", 13), ("Ethernet1/10", "SDWANFW2", 14))          # row 46 B
TRANSIT_ATTACHMENTS += (("SDWANFW", "BL", 2),)             # design: TRANSIT slot 2 = SD-WAN FW, <infra>.16.16/29
FW_WAN_LOOPBACK = "LO-WAN"                                 # P4A-305: public WAN endpoint loopback (IKE binds to it)
FW_WAN_HOST = 1                                            # P4A-305: first host of the role's public /28
FW_WAN_ENDPOINT = {                                        # design: public /24 carving; the IP plan owns the /28s
    ("DC1", "sdwan_fw"): "198.51.100.144/28", ("DC2", "sdwan_fw"): "203.0.113.144/28",
}
# decisions.xlsx row 71 B (7 Oct): one overlay /21 per hub. This is the only place to change.
SDWAN_OVERLAY = {"DC1": "10.79.0.0/21", "DC2": "10.79.8.0/21"}
SDWAN_TUNNEL = "ADVPN-HUB"                                 # P4A-306: hub phase1-interface (tunnel interface) name
SDWAN_HUB_HOST = 1                                         # P4A-307: hub tunnel address = first host of the site's overlay /21
# end Phase 4 PC3 constants

# Approved constants (Phase 4 PC4: VPN-partner FW; decisions.xlsx Open-Phase4 rows 37, 47-51; auto_approved.md P4A-4xx)
# BL 1/11-1/12 and EDGE 1/11-1/12 (reserved in Phase 2) are created from here on.
DEVICE_TYPE["vpn_fw"] = "FG-1800F"                         # row 37 B (SD-WAN and VPN-partner FWs)
PLATFORM_BY_ROLE["vpn_fw"] = "Fortinet FortiOS"            # row 37 B; release 7.6.7 from OS_VERSION_BY_PLATFORM
NO_LOOPBACK0_ROLES.add("vpn_fw")
VPN_FW_ASN_OFFSET = 30                                     # fabric ASN + 30 = 65130 / 65230 (design: AS numbers)
FW_ROLES.add("vpn_fw")
WAN_FW_ROLES.add("vpn_fw")
FW_PORTS_BY_ROLE["vpn_fw"] = FW_PORTS_BY_ROLE["sdwan_fw"]   # row 51 B: same pattern as the SD-WAN FW (port37/38 inside, port39/40 outside)
FW_HA_PORTS_BY_ROLE["vpn_fw"] = FW_HA_PORTS_BY_ROLE["sdwan_fw"]   # P4A-403 (= P4A-303)
ATTACH_PORTS["border_leaf"] += (("Ethernet1/11", "VPNFW1", 15), ("Ethernet1/12", "VPNFW2", 16))   # row 51 B; Po/vPC 15/16 P4A-404
ATTACH_PORTS["edge"] += (("Ethernet1/11", "VPNFW1", 15), ("Ethernet1/12", "VPNFW2", 16))          # row 51 B
FW_WAN_ENDPOINT.update({("DC1", "vpn_fw"): "198.51.100.128/28", ("DC2", "vpn_fw"): "203.0.113.128/28"})   # design: public /24
VPN_ATTACHMENTS = (("VPNFW", "BL", "PARTNER", 0),)          # design: <infra>.9.0/26 = VPN-partner FW block, slot 0 = .0/29 PARTNER
VPN_BLOCK_THIRD = 9
VPN_VLAN_BASE = 3300                                       # P4A-405: attachment VLAN = 3300 + slot
# end Phase 4 PC4 constants

# Approved constants (Phase 4 PC1: Multi-Site + DCI; decisions.xlsx Open-Phase4 rows 2, 14-30 and Auto-approved-P4)
MS_VIP_IF = "loopback2"                          # row 15 C
MS_VIP_DESCRIPTION = "Multi-Site VIP (anycast BGW pair)"
MS_VIP_HOST = 129                                # <infra>.2.129/32 (design: Multi-Site VIP inside the site block); on BGW1 (shared-address rule)
DCI_LOOPBACK0_HOST = {1: 1, 2: 2}                # <infra>.12.0/28 (row 2 B): DCI1 .1, DCI2 .2
DCI_PAIR_LINK = {"po": "port-channel23", "members": ("Ethernet1/23", "Ethernet1/24"), "host": 16}   # row 26 B; <infra>.12.16/31 (row 2 B), DCI1 lower
BGW_DCI_LINKS = ((1, "Ethernet1/3", "Ethernet1/1", 32), (2, "Ethernet1/3", "Ethernet1/1", 34))     # rows 16 C, 18 B, 19 B: BGWn <-> DCIn; <infra>.12.<host>/31, DCI lower (P4A-03)
DCI_CIRCUIT_PORT = "Ethernet1/25"                # row 27 B (first MACsec port)
DCI_CIRCUIT = {1: "10.0.255.0/31", 2: "10.0.255.2/31"}   # design: DCI-1, DCI-2; owned by the IP plan (objects/03-ip-plan.yml)
DCI_CIRCUIT_HOST = {"DC1": 0, "DC2": 1}          # design: lower address on the lower-numbered site
OTHER_SITE = {"DC1": "DC2", "DC2": "DC1"}
ROLE_DCI_CIRCUIT = "dci_circuit"                 # new interface role (schema): not a prefix owner, the IP plan owns the /31

# Approved constants (Phase 4 PC2: cloud routers; decisions.xlsx Open-Phase4 rows 52-60 and Auto-approved-P4 P4A-101..130)
CLOUD_ASN_OFFSET = 70                            # design: cloud routers = fabric ASN + 70 (65170 / 65270)
CLOUD_LOOPBACK0_HOST = {1: 1, 2: 2}              # <infra>.13.0/28 (design); CLOUD1 .1, CLOUD2 .2 (P4A-109)
CLOUD_XLINK_HOST = 16                            # <infra>.13.16/31 (design); CLOUD1 lower (P4A-110)
# ❓ 8201-24H8FH port numbering: same convention and flag as the ISP routers (ISP_EDGE_PORTS); renamed here if wrong.
CLOUD_FABRIC_PORTS = (("FourHundredGigE0/0/0/0", "BL1"), ("FourHundredGigE0/0/0/2", "BL2"))   # row 60 B; P4A-102
CLOUD_XLINK_PORT = "FourHundredGigE0/0/0/4"      # P4A-103
CLOUD_XC_PORT = "HundredGigE0/0/0/16"            # cloud-exchange port, 100G (row 53 C); P4A-104
CLOUD_BUNDLE = "Bundle-Ether31"                  # same number on both routers; BL side port-channel31 / 32 (P4A-105)
CLOUD_AWS_VLAN = {"DC1": {1: 100, 2: 101}, "DC2": {1: 110, 2: 111}}       # row 55 B: VIF1 on CLOUD1, VIF2 on CLOUD2
CLOUD_ER_VLAN = {"DC1": 200, "DC2": 210}                                    # row 56 B: same VLAN on primary and secondary
CLOUD_ER_NET = {"DC1": {1: "10.98.0.0/30", 2: "10.98.0.4/30"},             # row 57 B: primary on CLOUD1, secondary on CLOUD2
                "DC2": {1: "10.98.0.8/30", 2: "10.98.0.12/30"}}            # owned by the IP plan (objects/03-ip-plan.yml)
CLOUD_AWS_NET = {"DC1": {1: "169.254.100.0/30", 2: "169.254.101.0/30"},    # placeholders until AWS generates them (P4A-112)
                 "DC2": {1: "169.254.110.0/30", 2: "169.254.111.0/30"}}    # owned by the IP plan
ER_CUSTOMER_HOST, ER_MICROSOFT_HOST = 1, 2       # row 57 B: we take the first usable, Microsoft the second
AWS_PEER_HOST, AWS_CUSTOMER_HOST = 1, 2          # P4A-112 (placeholder convention)
ER_LEG = {1: "primary", 2: "secondary"}
CLOUD_PROVIDER = {"aws": "AWS", "azure": "Microsoft"}   # OrganizationProvider names (objects/05-providers.yml); descriptions start with them
CLOUD_XC_MTU = 9216                              # P4A-114: physical port carries the tagged handoffs
CLOUD_HANDOFF_MTU = 1500                         # P4A-114: IP MTU of the AWS and ExpressRoute sub-interfaces
ROLE_CLOUD = "cloud_handoff"                     # new interface role (schema): not a prefix owner, the IP plan owns the /30s

# Approved constants (Phase 2 PC2): L2-only VLANs (FabricL2Vlan) on the EDGE and DMZ pairs, one set per site
SITE_TENANT = {"DC1": "10.16", "DC2": "10.20"}   # tenant /16 per site; DMZ blocks 10.16.128.0/20 and 10.20.128.0/20 (decided Phase 1)
EDGE_VLAN_NAME = "{site}-EDGE-OUTSIDE"           # decided PC2
DMZ_VLAN_BASE, DMZ_THIRD_OCTET_BASE = 1400, 128  # VLAN 1400 -> <tenant>.128.0/24 ... 1415 -> <tenant>.143.0/24 (carving decided)
DMZ_VLAN_NAMES = {1400: "LB-VIP", 1401: "LB-SELF", **{v: f"SRV-{v - DMZ_VLAN_BASE:02d}" for v in range(1402, 1416)}}  # <site>-DMZ-<name>
DMZ_VLAN_PURPOSE = {1400: "load balancer virtual servers", 1401: "load balancer self IPs and SNAT"}  # 1402-1415: DMZ servers
ISP_CROSSLINK_HOST = {1: 0, 2: 1}                # <infra>.15.0/31: ISP1 lower
CARRIER_BLOCK = "192.0.2"                        # /30s: DC1 ISP-A .0, DC1 ISP-B .4, DC2 ISP-A .8, DC2 ISP-B .12
SITE_CARRIER_BASE = {"DC1": 0, "DC2": 2}         # index of the site's first /30
CARRIER = {1: "ISP-A", 2: "ISP-B"}               # router n peers with carrier n
CARRIER_SIDE_HOST, OUR_SIDE_HOST = 1, 2          # carrier .1, our router .2 (decided)

# Approved constants (Phase 1, Phase 2): artifact target groups, filled by the generator (add only)
ARTIFACT_GROUPS = {
    "spine": "fabric-spines", "leaf": "fabric-server-leaves",
    "border_leaf": "fabric-border-leaves", "service_leaf": "fabric-service-leaves",
    "edge": "fabric-edge-switches", "dmz": "fabric-dmz-switches",
    "internet_fw": "fabric-internet-fws", "internal_fw": "fabric-internal-fws", "isp_router": "fabric-isp-routers",
    "bgw": "fabric-bgws", "dci": "fabric-dci-switches",          # Phase 4 PC1
    "cloud_router": "fabric-cloud-routers",                       # Phase 4 PC2
}

ARTIFACT_GROUPS["sdwan_fw"] = "fabric-sdwan-fws"            # Phase 4 PC3
ARTIFACT_GROUPS["vpn_fw"] = "fabric-vpn-fws"                # Phase 4 PC4

# Approved constants (Day 8): reconciliation (scope Day 8, extended Phase 1 and Phase 2)
# Plan-only mode (decided Phase 2): FABRIC_DRY_RUN=1 prints every object the run would write and exits before any write.
DRY_RUN = os.environ.get("FABRIC_DRY_RUN", "0") == "1"
# Descriptions (decided Phase 2): far-end device and port where the far-end port is known now; device only toward the
# FortiGates until their port names are decided (PC3). Server-leaf pairs keep the Phase 1 rule (peer name) so the 40
# reviewed renders stay byte-identical.
PEER_PORT_ROLES = {"border_leaf", "service_leaf", "edge", "dmz"}
RECONCILE = os.environ.get("FABRIC_RECONCILE", "delete")
if RECONCILE not in ("delete", "report"):
    raise ValueError(f"FABRIC_RECONCILE must be 'delete' or 'report', got {RECONCILE!r}")
OWNED_VIRTUAL = {"loopback0", "loopback1", MS_VIP_IF}
OWNED_VIRTUAL_ROLES = {ROLE_BACKUP, ROLE_PEERING, ROLE_CLOUD}                                         # Vlan3900, attachment SVIs, bundle sub-interfaces
OWNED_PHYSICAL_ROLES = {MGMT0_ROLE, FABRIC_ROLE, ROLE_KEEPALIVE, ROLE_PEER_LINK, ROLE_PEERING, ROLE_UPSTREAM, ROLE_DCI_CIRCUIT,
                        ROLE_CLOUD}
OWNED_PORTCHANNEL_ROLES = {ROLE_PEER_LINK, ROLE_PEERING, FABRIC_ROLE}   # FABRIC_ROLE: DCI pair link port-channel23 (Phase 4)
PREFIX_ROLES = {FABRIC_ROLE, ROLE_KEEPALIVE, ROLE_BACKUP, ROLE_UPSTREAM}   # interfaces whose link prefix the generator owns
# Not reconciled on their own (documented): standalone addresses inside object-file prefixes (edge VLAN VIP and .10).
# Attachment /29s and carrier /30s go with their attachment or interface.

# Roles that always come in a pair of 2 per site (design: "Devices per datacenter"; Phase 2 adds DMZ, FWs, ISP routers).
# (name prefix, role, description with {n} and {carrier}, asn key)
FIXED_PAIRS = (
    ("BL", "border_leaf", "Border leaf {n}", "fabric"),
    ("SL", "service_leaf", "Service leaf {n}", "fabric"),
    ("BGW", "bgw", "Border gateway {n}, Multi-Site", "fabric"),
    ("EDGE", "edge", "Edge switch {n}, untrusted side, L2 only", None),
    ("DCI", "dci", "DCI switch {n}", "dci"),
    ("DMZ", "dmz", "DMZ switch {n}, L2 only, behind the Internet firewall", None),
    ("INETFW", "internet_fw", "Internet firewall unit {n} (active/passive pair)", "internet_fw"),
    ("INTFW", "internal_fw", "Internal firewall unit {n} (active/passive pair)", "internal_fw"),
    ("SDWANFW", "sdwan_fw", "SD-WAN hub firewall unit {n} (active/passive pair)", "sdwan_fw"),          # Phase 4 PC3
    ("VPNFW", "vpn_fw", "VPN-partner firewall unit {n} (active/passive pair)", "vpn_fw"),               # Phase 4 PC4
    ("ISP", "isp_router", "ISP router {n}, peers with {carrier}", "isp"),
    ("CLOUD", "cloud_router", "Cloud router {n}", "cloud"),          # Phase 4 PC2 (P4A-101)
)


def build_plan(site: str, vpc_pairs: int, esi_pairs: int, single: int) -> list[dict]:
    """One dict per device: name, role, description, asn key, platform.

    Order: S1-S2, L1..L(2*vpc) vPC, next 2*esi ESI, next `single` single-homed, then the fixed pairs in
    FIXED_PAIRS order. The order also decides which new device gets which pool address (Day 8).
    """
    server_leaves = 2 * vpc_pairs + 2 * esi_pairs + single
    if server_leaves > MAX_SERVER_LEAVES:
        raise ValueError(f"{site}: {server_leaves} server leaves exceed the spine port map ({MAX_SERVER_LEAVES})")

    plan: list[dict] = []

    def add(short: str, role: str, description: str, asn: str | None) -> None:
        plan.append({"name": f"{site}-{short}", "short": short, "role": role, "description": description,
                     "asn": asn, "platform": PLATFORM_BY_ROLE.get(role, PLATFORM)})

    for n in (1, 2):
        add(f"S{n}", "spine", f"Spine {n}, EVPN route reflector", "fabric")

    n = 1
    for _ in range(2 * vpc_pairs):
        add(f"L{n}", "leaf", f"Server leaf {n}, vPC pair", "fabric")
        n += 1
    for _ in range(2 * esi_pairs):
        add(f"L{n}", "leaf", f"Server leaf {n}, ESI pair", "fabric")
        n += 1
    for _ in range(single):
        add(f"L{n}", "leaf", f"Server leaf {n}, single-homed", "fabric")
        n += 1

    for prefix, role, description, asn in FIXED_PAIRS:
        for n in (1, 2):
            add(f"{prefix}{n}", role, description.format(n=n, carrier=CARRIER[n]), asn)

    return plan


def build_links(site: str, plan: list[dict]) -> list[dict]:
    """One dict per spine-leaf link: spine, spine_port, leaf, leaf_port (order decides the /31, Day 8)."""
    spines = [r["name"] for r in plan if r["role"] == "spine"]
    server_leaves = [r["name"] for r in plan if r["role"] == "leaf"]
    names = {r["name"] for r in plan}

    links: list[dict] = []
    for spine in spines:
        short = spine.removeprefix(f"{site}-")
        for n, leaf in enumerate(server_leaves, start=1):
            for i, leaf_port in enumerate(SERVER_LEAF_UPLINKS[short]):
                links.append({"spine": spine, "spine_port": f"Ethernet1/{2 * n - 1 + i}",
                              "leaf": leaf, "leaf_port": leaf_port})
        for leaf_short, port in SPINE_FIXED_PORTS:
            links.append({"spine": spine, "spine_port": f"Ethernet1/{port}",
                          "leaf": f"{site}-{leaf_short}", "leaf_port": FIXED_LEAF_UPLINKS[short]})

    for link in links:
        if link["leaf"] not in names:
            raise ValueError(f"{site}: link to unknown device {link['leaf']}")
    return links


def build_pairs(site: str, plan: list[dict], vpc_pairs: int, esi_pairs: int) -> list[dict]:
    """One dict per switch pair: kind, name, members, domain_id (vPC only), role, nve."""
    if site not in VPC_DOMAIN_BASE:
        raise ValueError(f"{site}: no vPC domain base in VPC_DOMAIN_BASE")
    base = VPC_DOMAIN_BASE[site]
    roles = {r["name"]: r["role"] for r in plan}
    leaves = [r["name"] for r in plan if r["role"] == "leaf"]
    vpc_leaves = leaves[: 2 * vpc_pairs]
    esi_leaves = leaves[2 * vpc_pairs : 2 * vpc_pairs + 2 * esi_pairs]

    pairs: list[dict] = []

    def add(kind: str, m1: str, m2: str, domain_id: int | None) -> None:
        tag = "VPC" if kind == "FabricVpcDomain" else "ESI"
        s1, s2 = m1.removeprefix(f"{site}-"), m2.removeprefix(f"{site}-")
        role = roles.get(m1)
        pairs.append({"kind": kind, "name": f"{site}-{tag}-{s1}-{s2}", "members": (m1, m2),
                      "domain_id": domain_id, "role": role, "nve": role in LOOPBACK1_ROLES})

    for k in range(vpc_pairs):
        slot = 1 if k == 0 else FIRST_EXTRA_SERVER_SLOT + k - 1
        add("FabricVpcDomain", vpc_leaves[2 * k], vpc_leaves[2 * k + 1], base + slot)
    for prefix, slot in VPC_FIXED_SLOTS:
        add("FabricVpcDomain", f"{site}-{prefix}1", f"{site}-{prefix}2", base + slot)
    for k in range(esi_pairs):
        add("FabricEsiPair", esi_leaves[2 * k], esi_leaves[2 * k + 1], None)

    seen_devices, seen_ids = set(), set()
    for pair in pairs:
        for member in pair["members"]:
            if member not in roles:
                raise ValueError(f"{pair['name']}: unknown member {member}")
            if member in seen_devices:
                raise ValueError(f"{member} is in two pairs")
            seen_devices.add(member)
        if roles[pair["members"][0]] != roles[pair["members"][1]]:
            raise ValueError(f"{pair['name']}: members have different roles")
        did = pair["domain_id"]
        if did is not None:
            if not (base + 1 <= did <= base + 99 and 1 <= did <= 1000):
                raise ValueError(f"{pair['name']}: domain ID {did} out of range")
            if did in seen_ids:
                raise ValueError(f"{pair['name']}: domain ID {did} used twice")
            seen_ids.add(did)
    return pairs


def vpc_domains(pairs: list[dict]) -> list[dict]:
    """Every vPC domain (server, BL, SL, EDGE, DMZ): gets keepalive and peer-link; backup SVI only with NVE."""
    out = []
    for p in pairs:
        if p["kind"] != "FabricVpcDomain":
            continue
        if p["role"] not in VPC_PORTS:
            raise ValueError(f"{p['name']}: no vPC port map for role {p['role']}")
        ports = VPC_PORTS[p["role"]]
        if ports["backup"] != p["nve"]:
            raise ValueError(f"{p['name']}: backup SVI rule disagrees with NVE rule")
        out.append({**p, "peer_link": ports["peer_link"], "keepalive": ports["keepalive"], "backup": ports["backup"]})
    return out


def build_attach_ports(site: str, plan: list[dict]) -> list[dict]:
    """Switch-side ports toward attached devices: device, port, far (device), po (port-channel name), po_number,
    far_port_desc / far_po_desc (descriptions: device + port where the far port is known, decided Phase 2)."""
    names = {r["name"] for r in plan}
    # ISP router n: EDGE1 <-> first ISP edge port, EDGE2 <-> second (ISP_EDGE_PORTS order)
    isp_far_port = {f"{site}-EDGE{k}": port for k, (port, _) in enumerate(ISP_EDGE_PORTS, start=1)}
    # Cloud router: BL n <-> the router's n-th fabric port (CLOUD_FABRIC_PORTS order), Phase 4 PC2
    cloud_far_port = {f"{site}-{bl}": port for port, bl in CLOUD_FABRIC_PORTS}
    out = []
    for r in plan:
        for port, far_short, po in ATTACH_PORTS.get(r["role"], ()):
            far = f"{site}-{far_short}"
            if far not in names:
                raise ValueError(f"{site}: attach port to unknown device {far}")
            if far_short.startswith("ISP"):
                port_desc, po_desc = f"{far} {isp_far_port[r['name']]}", f"{far} {ISP_BUNDLE}"
            elif far_short.startswith("CLOUD"):
                port_desc, po_desc = f"{far} {cloud_far_port[r['name']]}", f"{far} {CLOUD_BUNDLE}"
            else:                                      # FortiGate side (PC3): port and aggregate by switch role
                far_role = next(x["role"] for x in plan if x["name"] == far)
                agg, fw_ports = FW_PORTS_BY_ROLE.get(far_role, FW_PORTS)[r["role"]]
                port_desc, po_desc = f"{far} {fw_ports[int(r['name'][-1]) - 1]}", f"{far} {agg}"
            out.append({"device": r["name"], "port": port, "far": far, "po": f"port-channel{po}", "po_number": po,
                        "far_port_desc": port_desc, "far_po_desc": po_desc})
    return out


def build_attachments(site: str, plan: list[dict], site_vrfs: list[str]) -> list[dict]:
    """One dict per attachment: name, vrf, pair (vPC domain name), members, devices (2 FW units), slot, vlan, prefix, addrs."""
    if site not in SITE_INFRA:
        raise ValueError(f"{site}: no infrastructure block in SITE_INFRA")
    infra = SITE_INFRA[site]
    roles = {r["name"]: r["role"] for r in plan}
    out = []

    def add(pair_short: str, leaf_short: str, vrf: str, slot: int, block_third: int, vlan: int, devices=None) -> None:
        members = (f"{site}-{leaf_short}1", f"{site}-{leaf_short}2")
        router = devices is not None             # Phase 4 PC2: one independent router, .3 is its own interface
        devices = devices or (f"{site}-{pair_short}1", f"{site}-{pair_short}2")
        for d in members + devices:
            if d not in roles:
                raise ValueError(f"{site}: attachment to unknown device {d}")
        net = ipaddress.ip_network(f"{infra}.{block_third}.{8 * slot}/29")
        out.append({"name": ATTACHMENT_NAME.format(site=site, vrf=vrf, pair=pair_short), "vrf": vrf,
                    "pair": f"{site}-VPC-{leaf_short}1-{leaf_short}2", "members": members, "devices": devices,
                    "slot": slot, "vlan": vlan, "prefix": str(net), "router": router,
                    "addrs": {members[0]: str(net[1]), members[1]: str(net[2]), "fw": str(net[3])}})

    for pair_short, leaf_short, slot in TRANSIT_ATTACHMENTS:
        add(pair_short, leaf_short, "TRANSIT", slot, 16, TRANSIT_VLAN_BASE + slot)
    for dev_short, leaf_short, slot in CLOUD_ATTACHMENTS:
        add(dev_short, leaf_short, "TRANSIT", slot, 16, TRANSIT_VLAN_BASE + slot, devices=(f"{site}-{dev_short}",))
    for vrf in sorted(v for v in site_vrfs if v not in TENANT_EXCLUDED):
        if vrf not in INTFW_VRF_SLOT:
            raise ValueError(f"{site}: VRF {vrf} is in the site but has no Internal FW slot (INTFW_VRF_SLOT); decide it first")
        slot = INTFW_VRF_SLOT[vrf]
        add("INTFW", "SL", vrf, slot, 8, TENANT_VLAN_BASE + slot)
    for pair_short, leaf_short, vrf, slot in VPN_ATTACHMENTS:          # Phase 4 PC4: VPN-partner FW on the border leaves
        if vrf not in site_vrfs:
            raise ValueError(f"{site}: VRF {vrf} is not in the site; the VPN-partner FW attachment needs it")
        add(pair_short, leaf_short, vrf, slot, VPN_BLOCK_THIRD, VPN_VLAN_BASE + slot)

    seen_vlan, seen_prefix, seen_name = set(), set(), set()
    for a in out:
        key = (a["pair"], a["vlan"])
        if key in seen_vlan or a["prefix"] in seen_prefix or a["name"] in seen_name:
            raise ValueError(f"{a['name']}: duplicate VLAN, prefix or name")
        seen_vlan.add(key); seen_prefix.add(a["prefix"]); seen_name.add(a["name"])
    return out


def build_isp(site: str, plan: list[dict]) -> list[dict]:
    """One dict per ISP router: name, n, loopback0, edge_ports, bundle, subif_addr, crosslink, carrier, plus the site's edge addresses."""
    infra = SITE_INFRA[site]
    roles = {r["name"]: r["role"] for r in plan}
    edge_ports = {far_short: port for port, far_short, _ in ATTACH_PORTS["edge"] if far_short.startswith("ISP")}
    out = []
    for n in (1, 2):
        name = f"{site}-ISP{n}"
        if roles.get(name) != "isp_router":
            raise ValueError(f"{site}: {name} missing from the plan")
        idx = SITE_CARRIER_BASE[site] + (n - 1)
        carrier_net = ipaddress.ip_network(f"{CARRIER_BLOCK}.{4 * idx}/30")
        out.append({
            "name": name, "n": n,
            "loopback0": f"{infra}.15.{ISP_LOOPBACK0_HOST[n]}",
            "edge_ports": [(port, f"{site}-{edge_short}", edge_ports[f"ISP{n}"]) for port, edge_short in ISP_EDGE_PORTS],
            "bundle": ISP_BUNDLE, "subif": ISP_EDGE_SUBIF,
            "subif_addr": f"{infra}.14.{EDGE_VLAN_HOST[f'ISP{n}']}",
            "crosslink": {"port": ISP_CROSSLINK_PORT, "prefix": f"{infra}.15.0/31", "addr": f"{infra}.15.{ISP_CROSSLINK_HOST[n]}",
                          "peer": f"{site}-ISP{3 - n}"},
            "carrier": {"port": ISP_CARRIER_PORT, "prefix": str(carrier_net), "ours": str(carrier_net[OUR_SIDE_HOST]),
                        "theirs": str(carrier_net[CARRIER_SIDE_HOST]), "provider": CARRIER[n]},
        })
    return out


def edge_vlan_addresses(site: str) -> dict:
    infra = SITE_INFRA[site]
    return {"vip": f"{infra}.14.{EDGE_VLAN_HOST['VIP']}", "inetfw": f"{infra}.14.{EDGE_VLAN_HOST['INETFW']}"}


def build_l2_vlans(site: str) -> list[dict]:
    """L2-only VLANs (decided PC2): the edge VLAN on the EDGE pair, the DMZ VLANs on the DMZ pair.
    Every /24 is owned by the IP plan (objects/03-ip-plan.yml); the generator only links to it (job 056b)."""
    if site not in SITE_TENANT:
        raise ValueError(f"{site}: no tenant block in SITE_TENANT")
    out = [{"name": EDGE_VLAN_NAME.format(site=site), "vlan": EDGE_VLAN, "pair": f"{site}-VPC-EDGE1-EDGE2",
            "prefix": f"{SITE_INFRA[site]}.14.0/24",
            "description": "Edge VLAN: VRRP VIP, ISP routers, Internet FW outside leg"}]
    for vlan, suffix in DMZ_VLAN_NAMES.items():
        out.append({"name": f"{site}-DMZ-{suffix}", "vlan": vlan, "pair": f"{site}-VPC-DMZ1-DMZ2",
                    "prefix": f"{SITE_TENANT[site]}.{DMZ_THIRD_OCTET_BASE + vlan - DMZ_VLAN_BASE}.0/24",
                    "description": f"DMZ VLAN: {DMZ_VLAN_PURPOSE.get(vlan, 'DMZ servers')}"})
    return out


def build_fw(site: str, plan: list[dict], attach_ports: list[dict], attachments: list[dict], l2vlans: list[dict]) -> list[dict]:
    """FortiGate interfaces (PC3), one dict per FW unit: ports toward the switches, aggregates, HA ports, VLAN
    sub-interfaces (A/P units share every address: it sits on unit 1's interface, decided data rule) and, on the
    Internal FW, the NPU vlink pairs to TRANSIT with their /30s."""
    infra = SITE_INFRA[site]
    names = {r["name"] for r in plan}
    out = []
    for r in plan:
        if r["role"] not in FW_ROLES:
            continue
        unit, n = r["name"], int(r["name"][-1])
        peer = f"{unit[:-1]}{2 if n == 1 else 1}"
        if peer not in names:
            raise ValueError(f"{unit}: HA peer {peer} not in plan")
        fw = {"name": unit, "unit": n, "role": r["role"], "ports": [], "aggs": {}, "subifs": [], "npu": [],
              "ha": [(p, f"{peer} {p}") for p in FW_HA_PORTS_BY_ROLE.get(r["role"], FW_HA_PORTS)], "virtuals": []}
        for ap in attach_ports:
            if ap["far"] != unit:
                continue
            sw_role = next(x["role"] for x in plan if x["name"] == ap["device"])
            agg, fw_ports = FW_PORTS_BY_ROLE.get(r["role"], FW_PORTS)[sw_role]
            fw_port = fw_ports[int(ap["device"][-1]) - 1]
            fw["ports"].append((fw_port, f"{ap['device']} {ap['port']}", agg))
            sw_short = ap["device"].removeprefix(f"{site}-")[:-1]
            fw["aggs"][agg] = f"{site}-{sw_short}1/{sw_short}2 {ap['po']}"
        for a in attachments:
            if unit in a["devices"]:
                fw["subifs"].append({"name": f"{a['vrf']}-{a['vlan']}", "parent": "agg-inside", "desc": a["name"],
                                     "addr": (a["addrs"]["fw"], 29) if n == 1 else None, "new_addr": False})
                if r["role"] == "internal_fw" and a["vrf"] != "TRANSIT":
                    net = ipaddress.ip_network(f"{infra}.{FW_LEAK_THIRD}.{FW_LEAK_BASE + 4 * INTFW_VRF_SLOT[a['vrf']]}/30")
                    fw["npu"].append({"vrf": a["vrf"], "prefix": str(net),
                                      "tenant": {"name": f"LK-{a['vrf']}", "parent": FW_NPU_LINKS[0], "addr": str(net[1])},
                                      "transit": {"name": f"LK-T-{a['vrf']}", "parent": FW_NPU_LINKS[1], "addr": str(net[2])}})
        if r["role"] == "internet_fw":
            edge = edge_vlan_addresses(site)
            fw["subifs"].append({"name": FW_OUTSIDE_SUBIF.format(vlan=EDGE_VLAN), "parent": "agg-outside", "desc": f"Edge VLAN {EDGE_VLAN}",
                                 "addr": (edge["inetfw"], 24) if n == 1 else None, "new_addr": False})
            for v in l2vlans:
                if v["pair"].endswith("DMZ1-DMZ2"):
                    gw = str(ipaddress.ip_network(v["prefix"])[FW_DMZ_GW_HOST])
                    fw["subifs"].append({"name": FW_DMZ_SUBIF.format(vlan=v["vlan"]), "parent": "agg-dmz", "desc": v["name"],
                                         "addr": (gw, 24) if n == 1 else None, "new_addr": True})
        if r["role"] in WAN_FW_ROLES:      # Phase 4: outside leg on the edge VLAN, public WAN loopback, SD-WAN hub tunnel
            short = unit.removeprefix(f"{site}-")[:-1]
            fw["subifs"].append({"name": FW_OUTSIDE_SUBIF.format(vlan=EDGE_VLAN), "parent": "agg-outside", "desc": f"Edge VLAN {EDGE_VLAN}",
                                 "addr": (f"{infra}.14.{EDGE_VLAN_HOST[short]}", 24) if n == 1 else None, "new_addr": True})
            wan = ipaddress.ip_network(FW_WAN_ENDPOINT[(site, r["role"])])
            fw["virtuals"].append({"name": FW_WAN_LOOPBACK, "desc": f"WAN endpoint (public {wan})",
                                   "addr": (str(wan[FW_WAN_HOST]), wan.prefixlen) if n == 1 else None,
                                   "plan_prefix": str(wan), "own_prefix": None})
            if r["role"] == "sdwan_fw":
                ov = ipaddress.ip_network(SDWAN_OVERLAY[site])
                fw["virtuals"].append({"name": SDWAN_TUNNEL, "desc": f"ADVPN 2.0 hub tunnel, overlay {ov}",
                                       "addr": (str(ov[SDWAN_HUB_HOST]), ov.prefixlen) if n == 1 else None,
                                       "plan_prefix": None, "own_prefix": str(ov),
                                       "own_prefix_desc": f"{site} SD-WAN overlay (ADVPN hub tunnel, row 71 B)"})
        for x in fw["virtuals"]:
            if len(x["name"]) > 15:
                raise ValueError(f"{unit}: FortiOS interface name {x['name']} longer than 15 characters")
        for sub in fw["subifs"]:
            if len(sub["name"]) > 15:
                raise ValueError(f"{unit}: FortiOS interface name {sub['name']} longer than 15 characters")
        out.append(fw)
    return out


def build_dci(site: str, plan: list[dict]) -> dict:
    """Phase 4 PC1: BGW <-> DCI links, the DCI pair link, the two inter-site circuits, DCI loopback0, the Multi-Site VIP."""
    infra = SITE_INFRA[site]
    names = {r["name"] for r in plan}
    for n in (1, 2):
        for d in (f"{site}-DCI{n}", f"{site}-BGW{n}"):
            if d not in names:
                raise ValueError(f"{site}: {d} missing from the plan")
    links = []
    for n, bgw_port, dci_port, host in BGW_DCI_LINKS:
        net = ipaddress.ip_network(f"{infra}.12.{host}/31")
        links.append({"bgw": f"{site}-BGW{n}", "bgw_port": bgw_port, "dci": f"{site}-DCI{n}", "dci_port": dci_port,
                      "prefix": str(net), "dci_addr": str(net[0]), "bgw_addr": str(net[1])})
    pl = DCI_PAIR_LINK
    pnet = ipaddress.ip_network(f"{infra}.12.{pl['host']}/31")
    pair = {"po": pl["po"], "members": pl["members"], "prefix": str(pnet),
            "addrs": {f"{site}-DCI1": str(pnet[0]), f"{site}-DCI2": str(pnet[1])}}
    circuits = []
    for n in (1, 2):
        net = ipaddress.ip_network(DCI_CIRCUIT[n])
        circuits.append({"dci": f"{site}-DCI{n}", "port": DCI_CIRCUIT_PORT, "prefix": str(net), "name": f"DCI-{n}",
                         "addr": str(net[DCI_CIRCUIT_HOST[site]]), "far": f"{OTHER_SITE[site]}-DCI{n} {DCI_CIRCUIT_PORT}"})
    return {"links": links, "pair": pair, "circuits": circuits, "vip": f"{infra}.2.{MS_VIP_HOST}",
            "loopback0": {f"{site}-DCI{n}": f"{infra}.12.{DCI_LOOPBACK0_HOST[n]}" for n in (1, 2)}}


def build_cloud(site: str, plan: list[dict], attachments: list[dict]) -> list[dict]:
    """Phase 4 PC2, one dict per cloud router: loopback0, fabric bundle + members toward the BL vPC port-channel, the TRANSIT
    sub-interface (.3 of its attachment /29), iBGP cross-link /31, the cloud-exchange port and its AWS / ExpressRoute handoffs."""
    infra = SITE_INFRA[site]
    roles = {r["name"]: r["role"] for r in plan}
    bl_side = {far_short: (port, po) for port, far_short, po in ATTACH_PORTS["border_leaf"] if far_short.startswith("CLOUD")}
    out = []
    for n in (1, 2):
        name = f"{site}-CLOUD{n}"
        if roles.get(name) != "cloud_router":
            raise ValueError(f"{site}: {name} missing from the plan")
        att = [a for a in attachments if a["devices"] == (name,)]
        if len(att) != 1:
            raise ValueError(f"{name}: expected 1 TRANSIT attachment, got {len(att)}")
        a = att[0]
        bl_port, po = bl_side[f"CLOUD{n}"]
        xnet = ipaddress.ip_network(f"{infra}.13.{CLOUD_XLINK_HOST}/31")
        aws_net, er_net = ipaddress.ip_network(CLOUD_AWS_NET[site][n]), ipaddress.ip_network(CLOUD_ER_NET[site][n])
        aws_vlan, er_vlan = CLOUD_AWS_VLAN[site][n], CLOUD_ER_VLAN[site]
        out.append({
            "name": name, "n": n, "loopback0": f"{infra}.13.{CLOUD_LOOPBACK0_HOST[n]}",
            "fabric_ports": [(port, f"{site}-{bl}", bl_port) for port, bl in CLOUD_FABRIC_PORTS],
            "bundle": CLOUD_BUNDLE, "bl_po": f"port-channel{po}",
            "subif": f"{CLOUD_BUNDLE}.{a['vlan']}", "subif_addr": a["addrs"]["fw"], "attachment": a["name"],
            "crosslink": {"port": CLOUD_XLINK_PORT, "prefix": str(xnet), "addr": str(xnet[n - 1]), "peer": f"{site}-CLOUD{3 - n}"},
            "xc": {"port": CLOUD_XC_PORT, "desc": f"Cloud exchange port {n} (AWS VIF {n}, ExpressRoute {ER_LEG[n]})"},
            "handoffs": [
                {"provider": CLOUD_PROVIDER["aws"], "subif": f"{CLOUD_XC_PORT}.{aws_vlan}", "vlan": aws_vlan, "prefix": str(aws_net),
                 "ours": str(aws_net[AWS_CUSTOMER_HOST]), "theirs": str(aws_net[AWS_PEER_HOST]),
                 "desc": f"{CLOUD_PROVIDER['aws']} private VIF {n} to the Direct Connect Gateway, VLAN {aws_vlan}"},
                {"provider": CLOUD_PROVIDER["azure"], "subif": f"{CLOUD_XC_PORT}.{er_vlan}", "vlan": er_vlan, "prefix": str(er_net),
                 "ours": str(er_net[ER_CUSTOMER_HOST]), "theirs": str(er_net[ER_MICROSOFT_HOST]),
                 "desc": f"{CLOUD_PROVIDER['azure']} ExpressRoute {ER_LEG[n]} private peering, VLAN {er_vlan}"},
            ],
        })
    return out


def expected_interfaces(plan, links, domains, attach_ports, attachments, isp, fws=(), dci=None, cloud=()) -> dict:
    """device -> set of interface names the generator owns. Guard: no physical port is used twice on a device."""
    exp = {}
    used: list[tuple] = []          # (device, physical port) from every source, for the guard

    def port(device: str, name: str) -> None:
        used.append((device, name)); exp[device].add(name)

    for r in plan:
        names = set()
        mgmt = MGMT_IF_BY_PLATFORM.get(r["platform"])
        if r["role"] not in NO_MGMT_ROLES and mgmt:
            names.add(mgmt)
        if r["role"] not in NO_LOOPBACK0_ROLES:
            names.add("loopback0")
        if r["role"] in LOOPBACK1_ROLES:
            names.add("loopback1")
        if r["role"] == "bgw":
            names.add(MS_VIP_IF)
        exp[r["name"]] = names
    for link in links:
        port(link["spine"], link["spine_port"]); port(link["leaf"], link["leaf_port"])
    for d in domains:
        for m in d["members"]:
            exp[m].add(VPC_PEER_LINK)
            port(m, d["keepalive"])
            for p in d["peer_link"]:
                port(m, p)
            if d["backup"]:
                exp[m].add(VPC_BACKUP_SVI)
    po_far: dict = {}
    for ap in attach_ports:
        port(ap["device"], ap["port"]); exp[ap["device"]].add(ap["po"])
        key = (ap["device"], ap["po"])
        if ap["po"] == VPC_PEER_LINK or po_far.setdefault(key, ap["far"]) != ap["far"]:
            raise ValueError(f"{ap['device']}: {ap['po']} toward both {po_far.get(key)} and {ap['far']} (or the vPC peer-link)")
    for a in attachments:
        for m in a["members"]:
            exp[m].add(f"Vlan{a['vlan']}")
    for r in isp:
        for p, _, _ in r["edge_ports"]:
            port(r["name"], p)
        port(r["name"], r["crosslink"]["port"]); port(r["name"], r["carrier"]["port"])
        exp[r["name"]] |= {r["bundle"], r["subif"]}
    for fw in fws:
        for p, _, _ in fw["ports"]:
            port(fw["name"], p)
        for p, _ in fw["ha"]:
            port(fw["name"], p)
        exp[fw["name"]] |= set(fw["aggs"]) | {s["name"] for s in fw["subifs"]} | {x["name"] for x in fw.get("virtuals", ())}
        if fw["npu"]:
            exp[fw["name"]] |= set(FW_NPU_LINKS) | {x[side]["name"] for x in fw["npu"] for side in ("tenant", "transit")}
    if dci:
        for l in dci["links"]:
            port(l["bgw"], l["bgw_port"]); port(l["dci"], l["dci_port"])
        for dev in dci["pair"]["addrs"]:
            for m in dci["pair"]["members"]:
                port(dev, m)
            exp[dev].add(dci["pair"]["po"])
        for c in dci["circuits"]:
            port(c["dci"], c["port"])
    for r in cloud:
        for p, _, _ in r["fabric_ports"]:
            port(r["name"], p)
        port(r["name"], r["crosslink"]["port"]); port(r["name"], r["xc"]["port"])
        exp[r["name"]] |= {r["bundle"], r["subif"]} | {h["subif"] for h in r["handoffs"]}
    dupes = {u for u in used if used.count(u) > 1}
    if dupes:
        raise ValueError(f"port used twice: {sorted(dupes)}")
    return exp


def print_plan(site, fabric_asn, plan, links, pairs, domains, attach_ports, attachments, isp, expected, site_vrfs, l2vlans, fws=(), dci=None, cloud=()) -> None:
    """Every object a write run creates or updates for this site, as tables (plan-only mode)."""
    mgmt_pool = f"{site}-MGMT0"
    print(f"=== PLAN {site}: fabric ASN {fabric_asn}; site VRFs {sorted(site_vrfs)}")
    print(f"--- devices ({len(plan)}): name | role | platform | os_version | asn rule | device type | mgmt interface (pool) | loopbacks")
    for r in plan:
        mgmt = MGMT_IF_BY_PLATFORM.get(r["platform"]) if r["role"] not in NO_MGMT_ROLES else None
        lo = []
        if r["role"] == "isp_router":
            lo.append(f"loopback0={SITE_INFRA[site]}.15.{ISP_LOOPBACK0_HOST[int(r['name'][-1])]}/32 (fixed)")
        elif r["role"] == "dci":
            lo.append(f"loopback0={SITE_INFRA[site]}.12.{DCI_LOOPBACK0_HOST[int(r['name'][-1])]}/32 (fixed)")
        elif r["role"] == "cloud_router":
            lo.append(f"loopback0={SITE_INFRA[site]}.13.{CLOUD_LOOPBACK0_HOST[int(r['name'][-1])]}/32 (fixed)")
        else:
            if r["role"] not in NO_LOOPBACK0_ROLES:
                lo.append(f"loopback0 (pool {site}-LOOPBACK0)")
            if r["role"] in LOOPBACK1_ROLES:
                lo.append(f"loopback1 (pool {site}-LOOPBACK1-VTEP)")
            if r["role"] == "bgw":
                lo.append(f"{MS_VIP_IF}={SITE_INFRA[site]}.2.{MS_VIP_HOST}/32 (fixed, on BGW1)" if r["name"].endswith("1") else f"{MS_VIP_IF} (VIP read from BGW1)")
        asn = {"fabric": fabric_asn, "dci": fabric_asn + DCI_ASN_OFFSET, "internal_fw": fabric_asn + INTERNAL_FW_ASN_OFFSET,
               "internet_fw": fabric_asn + INTERNET_FW_ASN_OFFSET,
               "sdwan_fw": fabric_asn + SDWAN_FW_ASN_OFFSET, "vpn_fw": fabric_asn + VPN_FW_ASN_OFFSET, "isp": ISP_ROUTER_ASN, "cloud": fabric_asn + CLOUD_ASN_OFFSET,
               None: "-"}[r["asn"]]
        print(f"  {r['name']:<12} {r['role']:<13} {r['platform']:<17} {str(OS_VERSION_BY_PLATFORM.get(r['platform'])):<9} {str(asn):<6} "
              f"{DEVICE_TYPE[r['role']]:<17} {str(mgmt) + (' (' + mgmt_pool + ')' if mgmt else ''):<40} {', '.join(lo)}")
    print(f"--- spine-leaf links: {len(links)} (/31 from pool {site}-FABRIC-P2P, identifier <spine>-<spine port>)")
    print(f"--- switch pairs ({len(pairs)}): name | kind | domain id | members | anycast VTEP")
    for p in pairs:
        print(f"  {p['name']:<24} {p['kind']:<16} {str(p['domain_id']):<5} {p['members'][0]} + {p['members'][1]}  "
              f"anycast={'pool ' + site + '-ANYCAST-VTEP' if p['nve'] and p['domain_id'] else '-'}")
    print(f"--- vPC plumbing ({len(domains)} domains): peer-link {VPC_PEER_LINK} members | keepalive (/31 pool {site}-VPC-KEEPALIVE) | backup SVI (/31 pool {site}-VPC-BACKUP)")
    for d in domains:
        print(f"  {d['name']:<24} {', '.join(d['peer_link']):<52} {d['keepalive']:<13} {VPC_BACKUP_SVI if d['backup'] else 'none'}")
    print(f"--- switch ports toward attached devices ({len(attach_ports)}): device | port | port-channel | description")
    for ap in attach_ports:
        print(f"  {ap['device']:<10} {ap['port']:<13} {ap['po']:<16} {ap['far_port_desc']}")
    print(f"--- attachments ({len(attachments)}): name | VLAN | /29 | leaf pair | .1 | .2 | .3 (FW, no interface) | FW units")
    for a in attachments:
        m1, m2 = a["members"]
        print(f"  {a['name']:<22} {a['vlan']} {a['prefix']:<16} {a['pair']:<20} {m1} Vlan{a['vlan']}={a['addrs'][m1]} "
              f"{m2} Vlan{a['vlan']}={a['addrs'][m2]} fw={a['addrs']['fw']} {'+'.join(a['devices'])}")
    e = edge_vlan_addresses(site)
    print(f"--- edge VLAN {EDGE_VLAN} standalone addresses: VRRP VIP {e['vip']}/24, INETFW outside {e['inetfw']}/24 (no interface until PC3)")
    print(f"--- ISP routers ({len(isp)}): loopback0 | bundle + members -> edge | sub-interface address | cross-link | carrier /30 (ours / theirs, provider)")
    for r in isp:
        edge = ", ".join(f"{p}->{far} {fp}" for p, far, fp in r["edge_ports"])
        x, c = r["crosslink"], r["carrier"]
        print(f"  {r['name']:<9} lo0={r['loopback0']}/32 | {r['bundle']}: {edge} | {r['subif']}={r['subif_addr']}/24 | "
              f"{x['port']}={x['addr']}/31 <-> {x['peer']} | {c['port']}={c['ours']}/30 ({c['theirs']} {c['provider']})")
    print(f"--- L2-only VLANs ({len(l2vlans)}): name | VLAN | pair | prefix (from the IP plan, must exist)")
    for v in l2vlans:
        print(f"  {v['name']:<22} {v['vlan']:<5} {v['pair']:<22} {v['prefix']}")
    print(f"--- FortiGate interfaces (PC3): unit | ports | aggregates | HA | sub-interfaces (address on unit 1) | vlinks")
    for fw in fws:
        ports = ", ".join(f"{p}->{d}" for p, d, _ in fw["ports"])
        subs = ", ".join(f"{x['name']}={x['addr'][0] + '/' + str(x['addr'][1]) if x['addr'] else '-'}" for x in fw["subifs"])
        npu = ", ".join(f"{x['vrf']} {x['prefix']}" for x in fw["npu"]) or "-"
        print(f"  {fw['name']:<12} {ports} | {', '.join(fw['aggs'])} | {'/'.join(p for p, _ in fw['ha'])} | {subs} | {npu}")
        for x in fw.get("virtuals", ()):
            print(f"  {'':<12} + {x['name']}={x['addr'][0] + '/' + str(x['addr'][1]) if x['addr'] else '-'}"
                  f"{' (prefix ' + x['plan_prefix'] + ' from the IP plan)' if x['plan_prefix'] else ''}"
                  f"{' (prefix ' + x['own_prefix'] + ' owned by the generator)' if x['own_prefix'] and x['addr'] else ''}")
    if dci:
        print(f"--- Multi-Site + DCI (Phase 4 PC1): VIP {dci['vip']}/32 | BGW <-> DCI links | DCI pair link | circuits (IP plan)")
        for l in dci["links"]:
            print(f"  {l['bgw']} {l['bgw_port']}={l['bgw_addr']}/31 (upstream) <-> {l['dci']} {l['dci_port']}={l['dci_addr']}/31 (core)  {l['prefix']}")
        p = dci["pair"]
        print(f"  {p['po']} [{', '.join(p['members'])}] " + " ".join(f"{d}={a}/31" for d, a in p["addrs"].items()) + f"  {p['prefix']}")
        for c in dci["circuits"]:
            print(f"  {c['dci']} {c['port']}={c['addr']}/31 ({c['name']}, {ROLE_DCI_CIRCUIT}) -> {c['far']}  {c['prefix']}")
    if cloud:
        print(f"--- cloud routers (Phase 4 PC2) ({len(cloud)}): loopback0 | bundle + members -> BL | TRANSIT sub-interface | cross-link | "
              f"cloud-exchange port: handoffs (ours, provider side; /30 from the IP plan)")
        for r in cloud:
            fab = ", ".join(f"{p}->{far} {fp}" for p, far, fp in r["fabric_ports"])
            x = r["crosslink"]
            hs = ", ".join(f"{h['subif']}={h['ours']}/30 ({h['provider']} {h['theirs']})" for h in r["handoffs"])
            print(f"  {r['name']:<11} lo0={r['loopback0']}/32 | {r['bundle']}: {fab} | {r['subif']}={r['subif_addr']}/29 ({r['attachment']}) | "
                  f"{x['port']}={x['addr']}/31 <-> {x['peer']} | {r['xc']['port']}: {hs}")
    total_if = sum(len(v) for v in expected.values())
    print(f"--- artifact groups: " + ", ".join(f"{role}->{g}" for role, g in ARTIFACT_GROUPS.items()))
    print(f"--- totals: devices {len(plan)}, interfaces owned {total_if}, pairs {len(pairs)}, attachments {len(attachments)}, "
          f"fixed prefixes {len(attachments) + 1 + len(isp)} (/29s, cross-link /31, carrier /30s), "
          f"L2-only VLANs {len(l2vlans)}")


class FabricSiteGenerator(InfrahubGenerator):
    async def allocate(self, pool, identifier: str, data: dict):
        """Same identifier -> same address on every run (Day 3, Day 6)."""
        address = await self.client.allocate_next_ip_address(resource_pool=pool, identifier=identifier, data=data)
        if address is None:
            raise ValueError(f"{identifier}: pool {pool.name.value} returned nothing")
        return address

    async def allocate_p31(self, pool, identifier: str, description: str, site):
        """Same identifier -> same /31 on every run; `site` set after allocation (Day 6 fix)."""
        prefix = await self.client.allocate_next_ip_prefix(
            resource_pool=pool, identifier=identifier, data={"description": description, "status": LINK_STATUS})
        if prefix is None:
            raise ValueError(f"{identifier}: pool {pool.name.value} returned nothing")
        prefix.site = site
        await prefix.save()
        network = ipaddress.ip_network(str(prefix.prefix.value))
        if network.prefixlen != 31:
            raise ValueError(f"{identifier}: expected a /31, got {network}")
        return network

    async def upsert(self, kind: str, data: dict):
        obj = await self.client.create(kind=kind, data=data)
        await obj.save(allow_upsert=True)
        return obj

    async def address(self, ip: str, plen: int, description: str, namespace):
        """An address by value (upsert): links, attachments, edge VLAN, carriers, ISP loopbacks."""
        return await self.upsert("IpamIPAddress", {
            "address": f"{ip}/{plen}", "description": description, "status": LINK_STATUS, "ip_namespace": namespace.id})

    async def p31_address(self, ip, device_name: str, ifname: str, namespace):
        return await self.address(str(ip), 31, f"{device_name} {ifname}", namespace)

    async def fixed_prefix(self, prefix: str, description: str, site, namespace, role: str | None = None):
        """A prefix by value (Phase 2: fixed-slot /29s, cross-link /31s, carrier /30s), no pool (decided).

        Get-or-create (not upsert): the same prefix keeps its id on every run (Day 6 rule for scripts).
        An existing prefix is saved on every run, changed or not: save() registers it in the generator's
        tracking group; an unsaved prefix is treated as unused and deleted at the end of the run (job 032).
        """
        existing = await self.client.get(kind="IpamPrefix", prefix__value=prefix, raise_when_missing=False)
        if existing is not None:
            for attr, value in (("description", description), ("status", LINK_STATUS), ("member_type", "address"), ("role", role)):
                if value is not None and getattr(existing, attr).value != value:
                    getattr(existing, attr).value = value
            if existing.site.id != site.id:
                existing.site = site
            await existing.save()  # always: keeps it in the tracking group even when nothing changed
            return existing
        data = {"prefix": prefix, "description": description, "status": LINK_STATUS, "member_type": "address",
                "ip_namespace": namespace.id, "site": site.id}
        if role:
            data["role"] = role
        obj = await self.client.create(kind="IpamPrefix", data=data)
        await obj.save()
        return obj

    async def physical(self, device, name: str, description: str, role: str, mtu: int | None = None,
                       addresses=None, l2_mode: str | None = None, port_channel=None):
        data = {"name": name, "device": device.id, "description": description, "role": role, "status": INTERFACE_STATUS}
        if mtu:
            data["mtu"] = mtu
        if addresses:
            data["ip_addresses"] = [a.id for a in addresses]
        if l2_mode:
            data["l2_mode"] = l2_mode
        if port_channel is not None:
            data["port_channel"] = port_channel.id
        return await self.upsert("InterfacePhysical", data)

    async def virtual(self, device, name: str, description: str, role: str | None = None, mtu: int | None = None,
                      addresses=None, parent=None):
        data = {"name": name, "device": device.id, "description": description, "status": INTERFACE_STATUS}
        if role:
            data["role"] = role
        if mtu:
            data["mtu"] = mtu
        if addresses:
            data["ip_addresses"] = [a.id for a in addresses]
        if parent is not None:
            data["parent_interface"] = parent.id
        return await self.upsert("InterfaceVirtual", data)

    async def port_channel(self, device, name: str, description: str, role: str, mtu: int, members, l2_mode: str | None = None,
                           addresses=None):
        data = {"name": name, "device": device.id, "description": description, "role": role, "mtu": mtu,
                "status": INTERFACE_STATUS, "members": [m.id for m in members]}
        if addresses:
            data["ip_addresses"] = [a.id for a in addresses]
        if l2_mode:
            data["l2_mode"] = l2_mode
        return await self.upsert("InterfacePortChannel", data)

    # ---------------------------------------------------------------- reconciliation
    async def owned_interfaces(self, device) -> list[tuple]:
        """(interface, owns_prefix) for the interfaces this generator owns on a device; nothing else."""
        owned = []
        for intf in await self.client.filters(kind="InterfaceVirtual", device__ids=[device.id], include=["ip_addresses"]):
            if intf.name.value in OWNED_VIRTUAL or intf.role.value in OWNED_VIRTUAL_ROLES:
                owned.append((intf, intf.role.value in PREFIX_ROLES))
        for intf in await self.client.filters(kind="InterfacePhysical", device__ids=[device.id], include=["ip_addresses"]):
            if intf.role.value in OWNED_PHYSICAL_ROLES:
                owned.append((intf, intf.role.value in PREFIX_ROLES))
        for intf in await self.client.filters(kind="InterfacePortChannel", device__ids=[device.id], include=["ip_addresses"]):
            if intf.role.value in OWNED_PORTCHANNEL_ROLES:
                owned.append((intf, False))
        return owned

    async def remove(self, label: str, node) -> None:
        if RECONCILE == "delete":
            await node.delete()
            print(f"DELETED       {label}")
        else:
            print(f"WOULD DELETE  {label}")

    async def remove_prefix_with_addresses(self, network: str, done_prefixes: set) -> None:
        """A link prefix and every address in it (both ends, carrier side), once per prefix."""
        if network in done_prefixes:
            return
        done_prefixes.add(network)
        prefix = await self.client.get(kind="IpamPrefix", prefix__value=network, raise_when_missing=False, include=["ip_addresses"])
        if prefix is None:
            return
        for peer in prefix.ip_addresses.peers:
            addr = await self.client.get(kind="IpamIPAddress", id=peer.id, raise_when_missing=False)
            if addr is not None:
                await self.remove(f"IpamIPAddress {addr.address.value} (in {network})", addr)
        await self.remove(f"IpamPrefix    {network}", prefix)

    async def remove_interface(self, device_name: str, intf, owns_prefix: bool, done_prefixes: set) -> None:
        """The interface, its addresses and, for a link, its prefix with every address in it."""
        addresses, networks = [], []
        for peer in intf.ip_addresses.peers:
            address = await self.client.get(kind="IpamIPAddress", id=peer.id, raise_when_missing=False)
            if address is None:
                continue
            addresses.append(address)
            if owns_prefix:
                networks.append(str(ipaddress.ip_interface(str(address.address.value)).network))
        await self.remove(f"Interface     {device_name} {intf.name.value}", intf)
        for address in addresses:
            await self.remove(f"IpamIPAddress {address.address.value} ({device_name} {intf.name.value})", address)
        for network in networks:
            await self.remove_prefix_with_addresses(network, done_prefixes)

    async def reconcile(self, site_name: str, plan, links, pairs, domains, attach_ports, attachments, isp, fws=(), dci=None, cloud=()) -> None:
        """Delete what exists in the generator's scope but is not in the plan (Day 8 scope, Phase 1 vPC, Phase 2 attachments)."""
        expected_devices = {r["name"] for r in plan}
        expected_if = expected_interfaces(plan, links, domains, attach_ports, attachments, isp, fws, dci, cloud)
        expected_pairs = {p["name"] for p in pairs}
        expected_attachments = {a["name"] for a in attachments}
        done_prefixes: set = set()

        # 1. Pairs first: a pair must never point at a missing member; a removed vPC domain takes its anycast VTEP.
        for kind in ("FabricVpcDomain", "FabricEsiPair"):
            for pair in await self.client.filters(kind=kind):
                n = pair.name.value
                if n.startswith((f"{site_name}-VPC-", f"{site_name}-ESI-")) and n not in expected_pairs:
                    anycast = None
                    if kind == "FabricVpcDomain" and pair.anycast_vtep.id:
                        anycast = await self.client.get(kind="IpamIPAddress", id=pair.anycast_vtep.id)
                    await self.remove(f"Pair          {n}", pair)
                    if anycast is not None:
                        await self.remove(f"IpamIPAddress {anycast.address.value} ({n} anycast VTEP)", anycast)

        devices = [d for d in await self.client.filters(kind="DcimDevice", location__name__value=site_name)
                   if d.role.value in DEVICE_TYPE]

        # 2. Orphan interfaces on devices that stay.
        for d in devices:
            name = d.name.value
            if name in expected_devices:
                for intf, owns_prefix in await self.owned_interfaces(d):
                    if intf.name.value not in expected_if[name]:
                        await self.remove_interface(name, intf, owns_prefix, done_prefixes)

        # 3. Orphan devices, with their owned interfaces removed explicitly first.
        for d in devices:
            name = d.name.value
            if name not in expected_devices:
                for intf, owns_prefix in await self.owned_interfaces(d):
                    await self.remove_interface(name, intf, owns_prefix, done_prefixes)
                await self.remove(f"Device        {name}", d)

        # 4. Orphan attachments of this site: the object, then its /29 with every address in it (leaf SVIs went in step 2).
        for att in await self.client.filters(kind="FabricAttachment", site__name__value=site_name, include=["prefix"]):
            n = att.name.value
            if n not in expected_attachments:
                network = None
                if att.prefix.id:
                    pfx = await self.client.get(kind="IpamPrefix", id=att.prefix.id, raise_when_missing=False)
                    network = str(pfx.prefix.value) if pfx is not None else None
                await self.remove(f"Attachment    {n}", att)
                if network:
                    await self.remove_prefix_with_addresses(network, done_prefixes)

        print(f"reconcile ({RECONCILE}) done")

    # ---------------------------------------------------------------- generation
    async def site_vrfs(self, site_name: str) -> dict:
        """VRF name -> id for every VRF that lists this site (GraphQL, one request)."""
        r = await self.client.execute_graphql(
            query="query { FabricVRF { edges { node { id name { value } sites { edges { node { name { value } } } } } } } }")
        out = {}
        for e in r["FabricVRF"]["edges"]:
            n = e["node"]
            if site_name in [s["node"]["name"]["value"] for s in n["sites"]["edges"]]:
                out[n["name"]["value"]] = n["id"]
        return out

    async def generate(self, data: dict) -> None:
        edges = data["LocationSite"]["edges"]
        if len(edges) != 1:
            raise ValueError(f"expected exactly 1 site, got {len(edges)}")
        node = edges[0]["node"]
        site_name = node["name"]["value"]

        counts = {k: node[k]["value"] for k in ("vpc_leaf_pairs", "esi_leaf_pairs", "single_leaves")}
        if any(v is None for v in counts.values()):
            raise ValueError(f"{site_name}: leaf counts not set: {counts}")
        if node["fabric_asn"]["node"] is None:
            raise ValueError(f"{site_name}: fabric_asn not set")
        fabric_asn = node["fabric_asn"]["node"]["asn"]["value"]

        plan = build_plan(site_name, counts["vpc_leaf_pairs"], counts["esi_leaf_pairs"], counts["single_leaves"])
        vrfs = await self.site_vrfs(site_name)

        # Resolve every reference once; get() raises if the object is missing.
        site = await self.client.get(kind="LocationSite", name__value=site_name)
        platforms = {name: await self.client.get(kind="DcimPlatform", name__value=name)
                     for name in {r["platform"] for r in plan}}
        types = {name: await self.client.get(kind="DcimDeviceType", name__value=name) for name in set(DEVICE_TYPE.values())}
        asns = {
            "fabric": await self.client.get(kind="FabricAutonomousSystem", asn__value=fabric_asn),
            "dci": await self.client.get(kind="FabricAutonomousSystem", asn__value=fabric_asn + DCI_ASN_OFFSET),
            "internal_fw": await self.client.get(kind="FabricAutonomousSystem", asn__value=fabric_asn + INTERNAL_FW_ASN_OFFSET),
            "internet_fw": await self.client.get(kind="FabricAutonomousSystem", asn__value=fabric_asn + INTERNET_FW_ASN_OFFSET),
            "sdwan_fw": await self.client.get(kind="FabricAutonomousSystem", asn__value=fabric_asn + SDWAN_FW_ASN_OFFSET),
            "vpn_fw": await self.client.get(kind="FabricAutonomousSystem", asn__value=fabric_asn + VPN_FW_ASN_OFFSET),
            "isp": await self.client.get(kind="FabricAutonomousSystem", asn__value=ISP_ROUTER_ASN),
            "cloud": await self.client.get(kind="FabricAutonomousSystem", asn__value=fabric_asn + CLOUD_ASN_OFFSET),
        }
        pools = {
            "loopback0": await self.client.get(kind="CoreIPAddressPool", name__value=f"{site_name}-LOOPBACK0"),
            "loopback1": await self.client.get(kind="CoreIPAddressPool", name__value=f"{site_name}-LOOPBACK1-VTEP"),
            "mgmt0": await self.client.get(kind="CoreIPAddressPool", name__value=f"{site_name}-MGMT0"),
            "anycast": await self.client.get(kind="CoreIPAddressPool", name__value=f"{site_name}-ANYCAST-VTEP"),
        }
        p2p_pool = await self.client.get(kind="CoreIPPrefixPool", name__value=f"{site_name}-FABRIC-P2P")
        keepalive_pool = await self.client.get(kind="CoreIPPrefixPool", name__value=f"{site_name}-VPC-KEEPALIVE")
        backup_pool = await self.client.get(kind="CoreIPPrefixPool", name__value=f"{site_name}-VPC-BACKUP")
        namespace = await self.client.get(kind="IpamNamespace", name__value="default")
        groups = {role: await self.client.get(kind="CoreStandardGroup", name__value=name) for role, name in ARTIFACT_GROUPS.items()}

        # Plans and guards before anything is written.
        links = build_links(site_name, plan)
        pairs = build_pairs(site_name, plan, counts["vpc_leaf_pairs"], counts["esi_leaf_pairs"])
        domains = vpc_domains(pairs)
        attach_ports = build_attach_ports(site_name, plan)
        attachments = build_attachments(site_name, plan, list(vrfs))
        isp = build_isp(site_name, plan)
        l2vlans = build_l2_vlans(site_name)
        fws = build_fw(site_name, plan, attach_ports, attachments, l2vlans)
        dci = build_dci(site_name, plan)
        cloud = build_cloud(site_name, plan, attachments)
        expected = expected_interfaces(plan, links, domains, attach_ports, attachments, isp, fws, dci, cloud)
        infra = SITE_INFRA[site_name]

        if DRY_RUN:
            print_plan(site_name, fabric_asn, plan, links, pairs, domains, attach_ports, attachments, isp, expected, list(vrfs), l2vlans, fws, dci, cloud)
            print("DRY RUN: nothing written")
            return

        # ---- devices, management, loopbacks
        devices = {}
        for row in plan:
            name, role, platform_name = row["name"], row["role"], row["platform"]
            payload = {"name": name, "description": row["description"], "status": STATUS, "role": role,
                       "location": site.id, "platform": platforms[platform_name].id, "device_type": types[DEVICE_TYPE[role]].id}
            if OS_VERSION_BY_PLATFORM.get(platform_name):
                payload["os_version"] = OS_VERSION_BY_PLATFORM[platform_name]
            if row["asn"]:
                payload["asn"] = asns[row["asn"]].id
            mgmt_address = None
            mgmt_if = MGMT_IF_BY_PLATFORM.get(platform_name)
            if role not in NO_MGMT_ROLES and mgmt_if:
                mgmt_address = await self.allocate(pools["mgmt0"], f"{name}-{mgmt_if}",
                                                   {"description": f"{name} {mgmt_if}", "status": MGMT0_ADDRESS_STATUS})
                payload["primary_address"] = mgmt_address.id
            device = await self.upsert("DcimDevice", payload)
            devices[name] = device
            report = []
            if mgmt_address is not None:
                await self.physical(device, mgmt_if, MGMT0_DESCRIPTION, MGMT0_ROLE, addresses=[mgmt_address])
                report.append(f"{mgmt_if}={mgmt_address.address.value}")

            if role in ("isp_router", "dci", "cloud_router"):
                n = int(name[-1])
                if role == "isp_router":
                    lo_ip = f"{infra}.15.{ISP_LOOPBACK0_HOST[n]}"
                elif role == "dci":
                    lo_ip = dci["loopback0"][name]
                else:
                    lo_ip = f"{infra}.13.{CLOUD_LOOPBACK0_HOST[n]}"
                lo = await self.address(lo_ip, 32, f"{name} loopback0", namespace)
                await self.virtual(device, "loopback0", LOOPBACK0_DESCRIPTION, addresses=[lo])
                report.append(f"loopback0={lo.address.value}")
            else:
                loopbacks = []
                if role not in NO_LOOPBACK0_ROLES:
                    loopbacks.append(("loopback0", LOOPBACK0_DESCRIPTION))
                if role in LOOPBACK1_ROLES:
                    loopbacks.append(("loopback1", LOOPBACK1_DESCRIPTION))
                for ifname, description in loopbacks:
                    address = await self.allocate(pools[ifname], f"{name}-{ifname}", {"description": f"{name} {ifname}"})
                    await self.virtual(device, ifname, description, addresses=[address])
                    report.append(f"{ifname}={address.address.value}")
            if role == "bgw":
                vip = [await self.address(dci["vip"], 32, f"{site_name} Multi-Site VIP (anycast BGW pair, address on {name})", namespace)] \
                    if name.endswith("1") else []
                await self.virtual(device, MS_VIP_IF, MS_VIP_DESCRIPTION, addresses=vip)
                report.append(f"{MS_VIP_IF}={dci['vip'] + '/32' if vip else '(VIP on BGW1)'}")
            print(f"{name:<12} {role:<13} asn={str(row['asn']):<12} " + " ".join(report))

        # ---- artifact target groups: add this site's devices; other members are kept (fetch, then add)
        for role, group in groups.items():
            await group.members.fetch()
            have = {peer.id for peer in group.members.peers}
            new = [devices[r["name"]].id for r in plan if r["role"] == role and devices[r["name"]].id not in have]
            for node_id in new:
                group.members.add(node_id)
            if new:
                await group.save()
            print(f"group {group.name.value:<24} +{len(new)} (members now {len(have) + len(new)})")

        # ---- spine-leaf links (both ends exist now)
        for link in links:
            network = await self.allocate_p31(p2p_pool, f"{link['spine']}-{link['spine_port']}",
                                              f"{link['spine']} {link['spine_port']} link", site)
            ends = ((link["spine"], link["spine_port"], link["leaf"], network[0]),
                    (link["leaf"], link["leaf_port"], link["spine"], network[1]))
            for device_name, ifname, peer, ip in ends:
                address = await self.p31_address(ip, device_name, ifname, namespace)
                await self.physical(devices[device_name], ifname, peer, FABRIC_ROLE, mtu=FABRIC_MTU, addresses=[address])
            print(f"{link['spine']:<8} {link['spine_port']:<13} <-> {link['leaf']:<9} {link['leaf_port']:<13} {network}")

        # ---- switch pairs (the device side fills itself); NVE vPC domains get an anycast VTEP
        pair_objs = {}
        for pair in pairs:
            m1, m2 = pair["members"]
            data = {"name": pair["name"], "members": [devices[m1].id, devices[m2].id]}
            anycast = "-"
            if pair["domain_id"] is not None:
                data["domain_id"] = pair["domain_id"]
                if pair["nve"]:
                    address = await self.allocate(pools["anycast"], pair["name"], {"description": f"{pair['name']} anycast VTEP"})
                    data["anycast_vtep"] = address.id
                    anycast = str(address.address.value)
            pair_objs[pair["name"]] = await self.upsert(pair["kind"], data)
            print(f"{pair['name']:<24} {pair['kind']:<16} id={str(pair['domain_id']):<5} {m1} + {m2}  anycast={anycast}")

        # ---- vPC plumbing on every vPC domain: keepalive, peer-link, backup SVI (NVE pairs only)
        for d in domains:
            m1, m2 = d["members"]
            ka_net = await self.allocate_p31(keepalive_pool, f"{m1}-{d['keepalive']}", f"{d['name']} vPC peer-keepalive", site)
            bk_net = await self.allocate_p31(backup_pool, f"{m1}-{VPC_BACKUP_SVI}", f"{d['name']} vPC backup underlay", site) if d["backup"] else None
            for device_name, peer, i in ((m1, m2, 0), (m2, m1, 1)):
                device = devices[device_name]
                far = (lambda port: f"{peer} {port}") if d["role"] in PEER_PORT_ROLES else (lambda port: peer)
                ka_addr = await self.p31_address(ka_net[i], device_name, d["keepalive"], namespace)
                await self.physical(device, d["keepalive"], far(d["keepalive"]), ROLE_KEEPALIVE, mtu=VPC_MTU, addresses=[ka_addr])
                if bk_net is not None:
                    bk_addr = await self.p31_address(bk_net[i], device_name, VPC_BACKUP_SVI, namespace)
                    await self.virtual(device, VPC_BACKUP_SVI, VPC_BACKUP_DESCRIPTION, role=ROLE_BACKUP, mtu=VPC_MTU, addresses=[bk_addr])
                members = [await self.physical(device, ifname, far(ifname), ROLE_PEER_LINK, mtu=VPC_MTU) for ifname in d["peer_link"]]
                await self.port_channel(device, VPC_PEER_LINK, far(VPC_PEER_LINK), ROLE_PEER_LINK, VPC_MTU, members)
                print(f"{device_name:<10} {d['keepalive']}={ka_net[i]}/31 "
                      f"{VPC_BACKUP_SVI + '=' + str(bk_net[i]) + '/31' if bk_net is not None else 'no backup SVI':<24} "
                      f"{VPC_PEER_LINK}=[{', '.join(d['peer_link'])}] peer={peer}")

        # ---- switch ports toward attached devices: one member port + its port-channel (vPC number = Po number)
        for ap in attach_ports:
            device = devices[ap["device"]]
            member = await self.physical(device, ap["port"], ap["far_port_desc"], ROLE_PEERING, mtu=EXT_MTU, l2_mode="trunk")
            await self.port_channel(device, ap["po"], ap["far_po_desc"], ROLE_PEERING, EXT_MTU, [member], l2_mode="trunk")
            print(f"{ap['device']:<10} {ap['port']:<13} -> {ap['far']:<12} {ap['po']}")

        # ---- attachments: /29 by slot, leaf SVIs (.1 lower member, .2 upper), FW-side .3 without interface (PC3)
        for a in attachments:
            pfx = await self.fixed_prefix(a["prefix"], f"{a['name']} attachment /29 (VLAN {a['vlan']})", site, namespace, role="link")
            for m in a["members"]:
                addr = await self.address(a["addrs"][m], 29, f"{m} Vlan{a['vlan']} ({a['name']})", namespace)
                await self.virtual(devices[m], f"Vlan{a['vlan']}", a["name"], role=ROLE_PEERING, mtu=EXT_MTU, addresses=[addr])
            if not a["router"]:
                await self.address(a["addrs"]["fw"], 29, f"{a['devices'][0][:-1]} {a['vrf']} (shared by both units; interface in PC3)", namespace)
            await self.upsert("FabricAttachment", {
                "name": a["name"], "slot": a["slot"], "vlan_id": a["vlan"],
                "description": f"{' + '.join(a['devices'])} in {a['vrf']} on {a['pair']}" if a["router"]
                               else f"{a['devices'][0][:-1]} pair in {a['vrf']} on {a['pair']}",
                "vrf": vrfs[a["vrf"]], "prefix": pfx.id, "switch_pair": pair_objs[a["pair"]].id,
                "devices": [devices[d].id for d in a["devices"]], "site": site.id})
            print(f"{a['name']:<24} VLAN {a['vlan']} {a['prefix']:<16} {a['members'][0]}={a['addrs'][a['members'][0]]} "
                  f"{a['members'][1]}={a['addrs'][a['members'][1]]} fw={a['addrs']['fw']}")

        # ---- edge VLAN standalone addresses: VRRP VIP and the Internet FW outside leg (interface in PC3)
        edge = edge_vlan_addresses(site_name)
        await self.address(edge["vip"], 24, f"{site_name} edge VLAN {EDGE_VLAN} VRRP VIP (VRID 1)", namespace)
        await self.address(edge["inetfw"], 24, f"{site_name}-INETFW outside leg, edge VLAN {EDGE_VLAN} (shared by both units; interface in PC3)", namespace)

        # ---- L2-only VLANs (PC2): edge VLAN on the EDGE pair, DMZ VLANs on the DMZ pair
        for v in l2vlans:
            # The /24 belongs to the IP plan: fetched, never saved, so it never joins this generator's tracking group
            pfx = await self.client.get(kind="IpamPrefix", prefix__value=v["prefix"])   # raises if missing
            await self.upsert("FabricL2Vlan", {"name": v["name"], "vlan_id": v["vlan"], "description": v["description"],
                                               "switch_pair": pair_objs[v["pair"]].id, "site": site.id, "prefix": pfx.id})
            print(f"{v['name']:<22} VLAN {v['vlan']} {v['prefix']} on {v['pair']}")

        # ---- ISP routers: edge bundle + tagged sub-interface, iBGP cross-link /31, carrier /30
        for r in isp:
            device = devices[r["name"]]
            members = [await self.physical(device, port, f"{far} {far_port}", ROLE_PEERING, mtu=EXT_MTU) for port, far, far_port in r["edge_ports"]]
            po = f"port-channel{ATTACH_PORTS['edge'][r['n'] - 1][2]}"
            bundle = await self.port_channel(device, r["bundle"], f"{site_name}-EDGE1/EDGE2 {po}", ROLE_PEERING, EXT_MTU, members)
            sub_addr = await self.address(r["subif_addr"], 24, f"{r['name']} {r['subif']}", namespace)
            await self.virtual(device, r["subif"], f"Edge VLAN {EDGE_VLAN}", role=ROLE_PEERING, mtu=EXT_MTU, addresses=[sub_addr], parent=bundle)
            x = r["crosslink"]
            await self.fixed_prefix(x["prefix"], f"{site_name} ISP router iBGP cross-link", site, namespace, role="link")
            x_addr = await self.address(x["addr"], 31, f"{r['name']} {x['port']}", namespace)
            await self.physical(device, x["port"], f"{x['peer']} {x['port']}", FABRIC_ROLE, mtu=EXT_MTU, addresses=[x_addr])
            c = r["carrier"]
            await self.fixed_prefix(c["prefix"], f"{r['name']} <-> {c['provider']} handoff", site, namespace, role="link")
            ours = await self.address(c["ours"], 30, f"{r['name']} {c['port']}", namespace)
            await self.address(c["theirs"], 30, f"{c['provider']} side of the {r['name']} handoff", namespace)
            await self.physical(device, c["port"], f"{c['provider']} carrier handoff", ROLE_UPSTREAM, mtu=EXT_MTU, addresses=[ours])
            print(f"{r['name']:<9} lo0={r['loopback0']} {r['subif']}={r['subif_addr']} xlink={x['addr']}/31 "
                  f"carrier={c['ours']}/30 ({c['provider']} {c['theirs']})")

        # ---- FortiGate interfaces (PC3): ports, aggregates, HA ports, VLAN sub-interfaces, NPU vlinks (Internal FW)
        for fw in fws:
            device = devices[fw["name"]]
            unit_stem = fw["name"][:-1]
            members: dict = {}
            for p, desc, agg in fw["ports"]:
                members.setdefault(agg, []).append(await self.physical(device, p, desc, ROLE_PEERING, mtu=EXT_MTU))
            aggs = {agg: await self.port_channel(device, agg, desc, ROLE_PEERING, EXT_MTU, members[agg]) for agg, desc in fw["aggs"].items()}
            for p, desc in fw["ha"]:
                await self.physical(device, p, desc, FABRIC_ROLE)
            for sub in fw["subifs"]:
                addrs = []
                if sub["addr"]:
                    addrs = [await self.address(sub["addr"][0], sub["addr"][1], f"{unit_stem} {sub['name']} (shared by both units)", namespace)]
                await self.virtual(device, sub["name"], sub["desc"], role=ROLE_PEERING, mtu=EXT_MTU, addresses=addrs, parent=aggs[sub["parent"]])
            if fw["npu"]:
                npu = {link: await self.physical(device, link, "NPU inter-VDOM link (VRF leaking)", FABRIC_ROLE) for link in FW_NPU_LINKS}
                for x in fw["npu"]:
                    if fw["unit"] == 1:
                        await self.fixed_prefix(x["prefix"], f"{unit_stem} vlink {x['vrf']} <-> TRANSIT", site, namespace, role="link")
                    for side in ("tenant", "transit"):
                        e = x[side]
                        addrs = [await self.address(e["addr"], 30, f"{unit_stem} {e['name']} (shared by both units)", namespace)] if fw["unit"] == 1 else []
                        await self.virtual(device, e["name"], f"vlink {x['vrf']} <-> TRANSIT", role=FABRIC_ROLE, addresses=addrs, parent=npu[e["parent"]])
            for x in fw.get("virtuals", ()):
                if x["plan_prefix"]:
                    # owned by the IP plan: fetched (raises if missing), never saved
                    await self.client.get(kind="IpamPrefix", prefix__value=x["plan_prefix"])
                if x["own_prefix"] and fw["unit"] == 1:
                    await self.fixed_prefix(x["own_prefix"], x["own_prefix_desc"], site, namespace)
                addrs = []
                if x["addr"]:
                    addrs = [await self.address(x["addr"][0], x["addr"][1], f"{unit_stem} {x['name']} (shared by both units)", namespace)]
                await self.virtual(device, x["name"], x["desc"], role=ROLE_PEERING, addresses=addrs)
            print(f"{fw['name']:<12} ports {len(fw['ports'])} aggs {len(fw['aggs'])} subifs {len(fw['subifs'])} vlinks {len(fw['npu'])}"
                  f" virtuals {len(fw.get('virtuals', ()))}")

        # ---- Phase 4 PC1: BGW <-> DCI links (/31 fixed by rule), DCI pair link (routed port-channel), inter-site circuits
        for l in dci["links"]:
            await self.fixed_prefix(l["prefix"], f"{l['bgw']} <-> {l['dci']} link", site, namespace, role="link")
            a = await self.address(l["dci_addr"], 31, f"{l['dci']} {l['dci_port']}", namespace)
            await self.physical(devices[l["dci"]], l["dci_port"], f"{l['bgw']} {l['bgw_port']}", FABRIC_ROLE, mtu=FABRIC_MTU, addresses=[a])
            b = await self.address(l["bgw_addr"], 31, f"{l['bgw']} {l['bgw_port']}", namespace)
            await self.physical(devices[l["bgw"]], l["bgw_port"], f"{l['dci']} {l['dci_port']}", ROLE_UPSTREAM, mtu=FABRIC_MTU, addresses=[b])
            print(f"{l['bgw']:<9} {l['bgw_port']}={l['bgw_addr']}/31 <-> {l['dci']} {l['dci_port']}={l['dci_addr']}/31")
        p = dci["pair"]
        await self.fixed_prefix(p["prefix"], f"{site_name} DCI switch iBGP link ({p['po']})", site, namespace, role="link")
        dci_names = list(p["addrs"])
        for dev in dci_names:
            peer = next(x for x in dci_names if x != dev)
            members = [await self.physical(devices[dev], m, f"{peer} {m}", FABRIC_ROLE, mtu=FABRIC_MTU) for m in p["members"]]
            a = await self.address(p["addrs"][dev], 31, f"{dev} {p['po']}", namespace)
            await self.port_channel(devices[dev], p["po"], f"{peer} {p['po']}", FABRIC_ROLE, FABRIC_MTU, members, addresses=[a])
            print(f"{dev:<9} {p['po']}=[{', '.join(p['members'])}] {p['addrs'][dev]}/31 <-> {peer}")
        for c in dci["circuits"]:
            await self.client.get(kind="IpamPrefix", prefix__value=c["prefix"])   # owned by the IP plan; raises if missing
            a = await self.address(c["addr"], 31, f"{c['dci']} {c['port']} ({c['name']})", namespace)
            await self.physical(devices[c["dci"]], c["port"], c["far"], ROLE_DCI_CIRCUIT, mtu=EXT_MTU, addresses=[a])
            print(f"{c['dci']:<9} {c['port']}={c['addr']}/31 {c['name']} -> {c['far']}")
        # ---- Phase 4 PC2: cloud routers: fabric bundle + TRANSIT sub-interface, iBGP cross-link /31, cloud-exchange handoffs
        for r in cloud:
            device = devices[r["name"]]
            members = [await self.physical(device, port, f"{far} {far_port}", ROLE_PEERING, mtu=FABRIC_MTU) for port, far, far_port in r["fabric_ports"]]
            bundle = await self.port_channel(device, r["bundle"], f"{site_name}-BL1/BL2 {r['bl_po']}", ROLE_PEERING, FABRIC_MTU, members)
            sub_addr = await self.address(r["subif_addr"], 29, f"{r['name']} {r['subif']} ({r['attachment']})", namespace)
            await self.virtual(device, r["subif"], r["attachment"], role=ROLE_PEERING, mtu=FABRIC_MTU, addresses=[sub_addr], parent=bundle)
            x = r["crosslink"]
            await self.fixed_prefix(x["prefix"], f"{site_name} cloud router iBGP link", site, namespace, role="link")
            x_addr = await self.address(x["addr"], 31, f"{r['name']} {x['port']}", namespace)
            await self.physical(device, x["port"], f"{x['peer']} {x['port']}", FABRIC_ROLE, mtu=FABRIC_MTU, addresses=[x_addr])
            xc = await self.physical(device, r["xc"]["port"], r["xc"]["desc"], ROLE_CLOUD, mtu=CLOUD_XC_MTU)
            for h in r["handoffs"]:
                await self.client.get(kind="IpamPrefix", prefix__value=h["prefix"])   # owned by the IP plan; raises if missing
                ours = await self.address(h["ours"], 30, f"{r['name']} {h['subif']}", namespace)
                await self.address(h["theirs"], 30, f"{h['provider']} side of the {r['name']} {h['subif']} handoff", namespace)
                await self.virtual(device, h["subif"], h["desc"], role=ROLE_CLOUD, mtu=CLOUD_HANDOFF_MTU, addresses=[ours], parent=xc)
            print(f"{r['name']:<11} lo0={r['loopback0']} {r['subif']}={r['subif_addr']} xlink={x['addr']}/31 "
                  + " ".join(f"{h['subif']}={h['ours']}/30" for h in r["handoffs"]))

        await self.reconcile(site_name, plan, links, pairs, domains, attach_ports, attachments, isp, fws, dci, cloud)

