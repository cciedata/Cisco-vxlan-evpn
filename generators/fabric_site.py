import ipaddress
import os
import re

from infrahub_sdk.generator import InfrahubGenerator

# Design values: read from Infrahub at the start of generate(), never defaulted in code.
# FabricDesign (one object) holds the global and per-site values, DcimDeviceType the hardware port maps (model
# default), DcimPlatform the OS release, FabricVRF the Internal FW slot and attachment flag. configure() /
# configure_hardware() / configure_vrfs() fill the module-level names below; the build functions read them as before.
# Global values are the same for every site; per-site values go into site-keyed dicts (no clash between sites).

# Roles this generator builds (identifiers, F). Order = the order of the former DEVICE_TYPE / ARTIFACT_GROUPS dicts.
ROLES = ("spine", "leaf", "border_leaf", "service_leaf", "bgw", "edge", "dci", "dmz", "internet_fw", "internal_fw",
         "isp_router", "cloud_router", "sdwan_fw", "vpn_fw")
ARTIFACT_GROUP_ROLES = ("spine", "leaf", "border_leaf", "service_leaf", "edge", "dmz", "internet_fw", "internal_fw",
                        "isp_router", "bgw", "dci", "cloud_router", "sdwan_fw", "vpn_fw")


async def load_design(client):
    nodes = await client.all(kind="FabricDesign")
    if len(nodes) != 1:
        raise ValueError(f"FabricDesign: expected exactly 1 object, found {len(nodes)}; fix it in Infrahub")
    return nodes[0]


def need(design, field):
    attr = getattr(design, field, None)          # a site without its dc<n>_ field (e.g. DC4) lands here too
    value = None if attr is None else attr.value
    if value is None:
        raise ValueError(f"FabricDesign.{field} is empty; fix it in Infrahub")
    return value


def units(design, field: str, count: int) -> dict:
    """Per-unit JSON field {"1": x, "2": y} -> {1: x, 2: y}; every unit 1..count must be present."""
    out = {int(k): v for k, v in need(design, field).items()}
    missing = [n for n in range(1, count + 1) if n not in out]
    if missing:
        raise ValueError(f"FabricDesign.{field} has no entry for unit(s) {missing} ({count} needed); fix it in Infrahub")
    return out


def octets(design, field: str, count: int) -> str:
    """The first `count` octets of an IPNetwork field (the code builds addresses as <octets>.<third>.<host>)."""
    net = ipaddress.ip_network(str(need(design, field)))
    if net.prefixlen != 8 * count:
        raise ValueError(f"FabricDesign.{field} must be a /{8 * count} (got {net}); fix it in Infrahub")
    return ".".join(str(net.network_address).split(".")[:count])


DEVICE_TYPE_OBJ: dict = {}        # device type name -> DcimDeviceType node (configure_hardware)


def type_need(model: str, field: str):
    obj = DEVICE_TYPE_OBJ.get(model)
    attr = getattr(obj, field, None) if obj is not None else None
    value = None if attr is None else attr.value
    if value is None:
        raise ValueError(f"DcimDeviceType {model}.{field} is empty; fix it in Infrahub")
    return value


NO_MGMT_ROLES: set = set()   # the FortiGates get mgmt1 from the site MGMT0 pool like every other device

# Constants: interfaces and addresses
LOOPBACK0_DESCRIPTION = "Router ID, BGP peering"
LOOPBACK1_DESCRIPTION = "VTEP source (NVE)"
MGMT0_DESCRIPTION = "OOB management"
MGMT0_ROLE = "management"

# Constants: fabric links
FABRIC_ROLE = "core"

# Constants: vPC plumbing per pair role
VPC_KEEPALIVE_VRF_NOTE = "VRF VPC-KEEPALIVE (template side)"
ROLE_PEER_LINK = "vpc_peer_link"
ROLE_KEEPALIVE = "vpc_keepalive"
ROLE_BACKUP = "vpc_backup"
VPC_BACKUP_DESCRIPTION = "vPC backup underlay"
ROLE_PEERING = "peering"       # ports, port-channels, bundles, SVIs and sub-interfaces toward attached devices

# FortiGate side of every attach link: switch role -> (FW aggregate, side key in DcimDeviceType.fw_attach_ports),
# from FabricDesign.fw_side_by_role (configure)
FW_OUTSIDE_SUBIF = "OUTSIDE-{vlan}"              # sub-interface names <VRF or role>-<VLAN>
FW_DMZ_SUBIF = "DMZ-{vlan}"
ROLE_UPSTREAM = "upstream"     # carrier ports
ATTACHMENT_NAME = "{site}-{vrf}-{pair}"

# Constants: SD-WAN hub FW, VPN-partner FW
FW_ROLES = {"internal_fw", "internet_fw", "sdwan_fw"}      # roles build_fw() builds
FW_WAN_LOOPBACK = "LO-WAN"                                 # public WAN endpoint loopback (IKE binds to it)
SDWAN_TUNNEL = "ADVPN-HUB"                                 # hub phase1-interface (tunnel interface) name
FW_ROLES.add("vpn_fw")
# WAN_FW_ROLES (FWs with an outside leg on the edge VLAN + a public WAN loopback): FabricDesign.wan_fw_roles (configure)

# Constants: Multi-Site + DCI
MS_VIP_IF = "loopback2"
MS_VIP_DESCRIPTION = "Multi-Site VIP (anycast BGW pair)"
# OTHER_SITE (far-end site of the DCI circuits): FabricDesign.dci_peer_site (configure)
ROLE_DCI_CIRCUIT = "dci_circuit"                 # new interface role (schema): not a prefix owner, the IP plan owns the /31

# Constants: cloud routers
ER_LEG = {1: "primary", 2: "secondary"}
CLOUD_PROVIDER = {"aws": "AWS", "azure": "Microsoft"}   # OrganizationProvider names (objects/05-providers.yml); descriptions start with them
ROLE_CLOUD = "cloud_handoff"                     # new interface role (schema): not a prefix owner, the IP plan owns the /30s

# Constants: L2-only VLANs (FabricL2Vlan) on the EDGE and DMZ pairs, one set per site
EDGE_VLAN_NAME = "{site}-EDGE-OUTSIDE"
# Keyed by the VLAN name from FabricDesign.dmz_vlan_names (the VLAN ids live only there); unnamed VLANs: DMZ servers
DMZ_VLAN_PURPOSE = {"LB-VIP": "load balancer virtual servers", "LB-SELF": "load balancer self IPs and SNAT"}

# Per-site values (FabricDesign dc1_/dc2_ fields), keyed by site, filled by configure()
VPC_DOMAIN_BASE: dict = {}
SITE_INFRA: dict = {}             # first two octets of dc<n>_infra_block
SITE_TENANT: dict = {}            # first two octets of dc<n>_tenant_block
SITE_CARRIER_BASE: dict = {}      # index of the site's first carrier /30
DCI_CIRCUIT_HOST: dict = {}
FW_WAN_ENDPOINT: dict = {}        # (site, role) -> public /28
SDWAN_OVERLAY: dict = {}
CLOUD_AWS_VLAN: dict = {}         # site -> {router n: VLAN}
CLOUD_ER_VLAN: dict = {}
CLOUD_ER_NET: dict = {}           # site -> {router n: /30}
CLOUD_AWS_NET: dict = {}          # site -> {router n: /30}

# Per-VRF values (FabricVRF), filled by configure_vrfs()
INTFW_VRF_SLOT: dict = {}         # FabricVRF.internal_fw_slot
INTFW_ATTACHED: set = set()       # FabricVRF.internal_fw_attached = true


def configure(design, site: str) -> None:
    """Global and per-site design values from FabricDesign."""
    global STATUS, DCI_ASN_OFFSET, MAX_SERVER_LEAVES, DEVICE_TYPE, PLATFORM_BY_ROLE, MGMT_IF_BY_ROLE
    global INTERNAL_FW_ASN_OFFSET, INTERNET_FW_ASN_OFFSET, SDWAN_FW_ASN_OFFSET, VPN_FW_ASN_OFFSET, CLOUD_ASN_OFFSET, ISP_ROUTER_ASN
    global INTERFACE_STATUS, MGMT0_ADDRESS_STATUS, LINK_STATUS, NO_LOOPBACK0_ROLES, LOOPBACK1_ROLES
    global VPC_FIXED_SLOTS, VPC_FIRST_SERVER_SLOT, FIRST_EXTRA_SERVER_SLOT, VPC_DOMAIN_SLOT_SPAN
    global VPC_PEER_LINK, VPC_BACKUP_SVI, VPC_PEER_LINK_OVERRIDE, ATTACH_PORTS, ATTACH_L2_MODE
    global FW_LEAK_THIRD, FW_LEAK_BASE, FW_DMZ_GW_HOST, INTFW_BLOCK_THIRD, TRANSIT_BLOCK_THIRD
    global ATTACHMENT_PLEN, ATTACHMENT_LEAF_HOSTS, ATTACHMENT_DEVICE_HOST
    global TRANSIT_ATTACHMENTS, CLOUD_ATTACHMENTS, VPN_ATTACHMENTS, TRANSIT_VLAN_BASE, TENANT_VLAN_BASE, VPN_VLAN_BASE, VPN_BLOCK_THIRD
    global ISP_BUNDLE, EDGE_VLAN, ISP_EDGE_SUBIF, EDGE_VLAN_HOST, EDGE_VLAN_THIRD, EDGE_VLAN_PLEN, ISP_BLOCK_THIRD, P2P_PLEN
    global ISP_LOOPBACK0_HOST, ISP_CROSSLINK_HOST, CARRIER_BLOCK, CARRIER_PLEN, CARRIER, CARRIER_SIDE_HOST, OUR_SIDE_HOST
    global FW_WAN_HOST, SDWAN_HUB_HOST
    global MS_VIP_HOST, MS_VIP_THIRD, DCI_BLOCK_THIRD, DCI_LOOPBACK0_HOST, DCI_PAIR_LINK, BGW_DCI_LINKS, DCI_CIRCUIT
    global CLOUD_BLOCK_THIRD, CLOUD_LOOPBACK0_HOST, CLOUD_XLINK_HOST, CLOUD_BUNDLE
    global ER_CUSTOMER_HOST, ER_MICROSOFT_HOST, AWS_PEER_HOST, AWS_CUSTOMER_HOST
    global DMZ_VLAN_BASE, DMZ_THIRD_OCTET_BASE, DMZ_VLAN_NAMES, ARTIFACT_GROUPS, ROLE_COUNT, SPINE_COUNT
    global ROLE_ASN_GROUP, LOOPBACK_PLEN, FW_SIDE, OTHER_SITE, SPINE_FIRST_DOWNLINK_PORT, BGW_DCI_END_HOST, DCI_PAIR_MEMBER_HOST
    global CLOUD_XLINK_MEMBER_HOST, FABRIC_LINK_END_HOST, VPC_KEEPALIVE_MEMBER_HOST, VPC_BACKUP_MEMBER_HOST, WAN_FW_ROLES
    global MGMT0_RULE_FIRST_HOST, VPC_BACKUP_RULE_FIRST_HOST
    global TENANT_ATTACHMENT_PAIR, ISP_XLINK_NETWORK_HOST, FIXED_LOOPBACK0_ROLES, MTU, EDGE_VRRP_GROUP_ID, TRANSIT_VRF, DMZ_VLAN_PLEN
    d = design
    s = site.lower()

    # roles: counts (pair logic), device type, platform, management interface, artifact group
    ROLE_COUNT = {}
    for _, role, _ in FIXED_PAIRS:
        ROLE_COUNT[role] = need(d, f"{role}_count")
        if ROLE_COUNT[role] != 2:
            raise ValueError(f"{role}_count must be 2 (pair logic)")
    ROLE_ASN_GROUP = dict(need(d, "role_asn_group"))      # role -> asn key (generate(): asns) or None (no ASN)
    for _, role, _ in FIXED_PAIRS:
        if role not in ROLE_ASN_GROUP:
            raise ValueError(f"FabricDesign.role_asn_group has no entry for role {role}; fix it in Infrahub")
        if ROLE_ASN_GROUP[role] not in ASN_GROUPS:
            raise ValueError(f"FabricDesign.role_asn_group: {role} -> {ROLE_ASN_GROUP[role]!r} is not one of {sorted(ASN_GROUPS)} or null")
    SPINE_COUNT = need(d, "spine_count")
    DEVICE_TYPE = {role: need(d, f"{role}_device_type") for role in ROLES}
    PLATFORM_BY_ROLE = {role: need(d, f"{role}_platform") for role in ROLES}
    MGMT_IF_BY_ROLE = {role: need(d, f"{role}_mgmt_interface") for role in ROLES}
    ARTIFACT_GROUPS = {role: need(d, f"{role}_artifact_group") for role in ARTIFACT_GROUP_ROLES}
    NO_LOOPBACK0_ROLES = set(need(d, "roles_without_loopback0"))
    FIXED_LOOPBACK0_ROLES = set(need(d, "roles_with_fixed_loopback0"))
    if FIXED_LOOPBACK0_ROLES - set(FIXED_LOOPBACK0_RULES):
        raise ValueError(f"FabricDesign.roles_with_fixed_loopback0: {sorted(FIXED_LOOPBACK0_ROLES - set(FIXED_LOOPBACK0_RULES))} "
                         f"have no fixed-address rule (rules exist for {list(FIXED_LOOPBACK0_RULES)}); fix it in Infrahub")
    LOOPBACK_PLEN = need(d, "loopback_prefix_length")
    LOOPBACK1_ROLES = set(need(d, "vtep_roles"))          # VTEPs only (also the pairs that get a backup SVI)
    MAX_SERVER_LEAVES = need(d, "max_server_leaves")

    # statuses, AS numbers, MTUs
    STATUS = need(d, "status_device")
    INTERFACE_STATUS = need(d, "status_interface")
    MGMT0_ADDRESS_STATUS = need(d, "status_address")
    LINK_STATUS = need(d, "status_link")
    DCI_ASN_OFFSET = need(d, "asn_offset_dci")
    INTERNAL_FW_ASN_OFFSET = need(d, "asn_offset_internal_fw")
    INTERNET_FW_ASN_OFFSET = need(d, "asn_offset_internet_fw")
    SDWAN_FW_ASN_OFFSET = need(d, "asn_offset_sdwan_fw")
    VPN_FW_ASN_OFFSET = need(d, "asn_offset_vpn_fw")
    CLOUD_ASN_OFFSET = need(d, "asn_offset_cloud_router")
    ISP_ROUTER_ASN = need(d, "isp_router_asn")
    # MTU per link kind: FabricDesign.interface_mtu_class names the mtu_* field of every kind (vPC peer-link: none)
    mtu_class = need(d, "interface_mtu_class")
    missing = [k for k in MTU_KINDS if k not in mtu_class]
    if missing:
        raise ValueError(f"FabricDesign.interface_mtu_class has no entry for {missing}; fix it in Infrahub")
    MTU = {k: need(d, mtu_class[k]) for k in MTU_KINDS}

    # switch pairs: vPC domain ids, peer-link, backup SVI
    VPC_DOMAIN_BASE[site] = need(d, f"{s}_vpc_domain_base")
    VPC_FIXED_SLOTS = tuple(sorted(need(d, "vpc_fixed_slots").items(), key=lambda kv: kv[1]))
    VPC_FIRST_SERVER_SLOT = need(d, "vpc_first_server_slot")
    FIRST_EXTRA_SERVER_SLOT = need(d, "vpc_first_extra_server_slot")
    VPC_DOMAIN_SLOT_SPAN = need(d, "vpc_domain_slot_span")
    VPC_PEER_LINK = f"port-channel{need(d, 'vpc_peer_link_port_channel')}"
    VPC_BACKUP_SVI = f"Vlan{need(d, 'vpc_backup_vlan')}"
    VPC_PEER_LINK_OVERRIDE = {"edge": tuple(need(d, "edge_vpc_peer_link_ports"))}   # role override
    VPC_KEEPALIVE_MEMBER_HOST = units(d, "vpc_keepalive_member_hosts", 2)   # vPC member 1, 2 -> host in the /31
    VPC_BACKUP_MEMBER_HOST = units(d, "vpc_backup_member_hosts", 2)
    # Addresses whose pool order differed between a phased and a from-empty build are computed by rule
    MGMT0_RULE_FIRST_HOST = {k: int(v) for k, v in need(d, "mgmt0_rule_first_host").items()}
    VPC_BACKUP_RULE_FIRST_HOST = {k: int(v) for k, v in need(d, "vpc_backup_rule_first_host").items()}
    for field, roles in (("mgmt0_rule_first_host", MGMT0_RULE_FIRST_HOST), ("vpc_backup_rule_first_host", VPC_BACKUP_RULE_FIRST_HOST)):
        unknown = sorted(set(roles) - set(ROLES))
        if unknown:
            raise ValueError(f"FabricDesign.{field} names unknown roles {unknown}; fix it in Infrahub")
    for role in MGMT0_RULE_FIRST_HOST:
        if role in NO_MGMT_ROLES or not MGMT_IF_BY_ROLE[role]:
            raise ValueError(f"FabricDesign.mgmt0_rule_first_host: role {role} has no management interface; fix it in Infrahub")
    FABRIC_LINK_END_HOST = dict(need(d, "fabric_link_end_hosts"))
    for end in ("spine", "leaf"):
        if end not in FABRIC_LINK_END_HOST:
            raise ValueError(f"FabricDesign.fabric_link_end_hosts has no '{end}' entry; fix it in Infrahub")
    SPINE_FIRST_DOWNLINK_PORT = need(d, "spine_first_downlink_port")

    # switch ports toward attached devices: (port, attached device short name, port-channel number)
    ATTACH_PORTS = {role: tuple(tuple(x) for x in need(d, f"{role}_attach_ports"))
                    for role in ("border_leaf", "service_leaf", "edge", "dmz")}
    ATTACH_L2_MODE = need(d, "attachment_port_l2_mode")
    FW_SIDE = {role: tuple(v) for role, v in need(d, "fw_side_by_role").items()}   # switch role -> (FW aggregate, side key)
    for role in ATTACH_PORTS:
        if role not in FW_SIDE:
            raise ValueError(f"FabricDesign.fw_side_by_role has no entry for role {role}; fix it in Infrahub")
    WAN_FW_ROLES = set(need(d, "wan_fw_roles"))

    # address blocks inside the site infrastructure /16
    SITE_INFRA[site] = octets(d, f"{s}_infra_block", 2)
    SITE_TENANT[site] = octets(d, f"{s}_tenant_block", 2)
    INTFW_BLOCK_THIRD = need(d, "internal_fw_block_third_octet")
    FW_LEAK_THIRD, FW_LEAK_BASE = INTFW_BLOCK_THIRD, need(d, "fw_leak_fourth_octet_base")
    FW_DMZ_GW_HOST = need(d, "dmz_gateway_host")
    TRANSIT_BLOCK_THIRD = need(d, "transit_block_third_octet")
    ATTACHMENT_PLEN = need(d, "attachment_prefix_length")
    ATTACHMENT_LEAF_HOSTS = tuple(need(d, "attachment_leaf_hosts"))
    if len(ATTACHMENT_LEAF_HOSTS) != 2:
        raise ValueError("FabricDesign.attachment_leaf_hosts must have 2 entries (pair logic); fix it in Infrahub")
    ATTACHMENT_DEVICE_HOST = need(d, "attachment_device_host")
    TRANSIT_ATTACHMENTS = tuple((k, v[0], v[1]) for k, v in sorted(need(d, "transit_attachment_slots").items(), key=lambda kv: kv[1][1]))
    CLOUD_ATTACHMENTS = tuple((k, v[0], v[1]) for k, v in sorted(need(d, "cloud_attachment_slots").items(), key=lambda kv: kv[1][1]))
    VPN_ATTACHMENTS = tuple((k, v[0], v[1], v[2]) for k, v in sorted(need(d, "vpn_attachment_slots").items(), key=lambda kv: kv[1][2]))
    TRANSIT_VLAN_BASE = need(d, "vlan_base_transit")
    TENANT_VLAN_BASE = need(d, "vlan_base_tenant_attachment")
    VPN_VLAN_BASE = need(d, "vlan_base_vpn")
    VPN_BLOCK_THIRD = need(d, "vpn_block_third_octet")
    TRANSIT_VRF = need(d, "transit_vrf")
    TENANT_ATTACHMENT_PAIR = tuple(need(d, "tenant_attachment_pair"))      # (attached pair prefix, leaf pair prefix)
    if len(TENANT_ATTACHMENT_PAIR) != 2:
        raise ValueError("FabricDesign.tenant_attachment_pair must have 2 entries (attached pair, leaf pair); fix it in Infrahub")

    # ISP routers, edge VLAN, carriers
    ISP_BUNDLE = f"Bundle-Ether{need(d, 'isp_bundle_id')}"
    EDGE_VLAN = need(d, "edge_vlan")
    ISP_EDGE_SUBIF = f"{ISP_BUNDLE}.{EDGE_VLAN}"     # tagged
    EDGE_VLAN_HOST = need(d, "edge_vlan_hosts")
    EDGE_VLAN_THIRD = need(d, "edge_vlan_block_third_octet")
    EDGE_VLAN_PLEN = need(d, "edge_vlan_prefix_length")
    ISP_BLOCK_THIRD = need(d, "isp_block_third_octet")
    P2P_PLEN = need(d, "p2p_link_prefix_length")
    ISP_LOOPBACK0_HOST = units(d, "isp_loopback0_hosts", ROLE_COUNT["isp_router"])
    ISP_CROSSLINK_HOST = units(d, "isp_crosslink_hosts", ROLE_COUNT["isp_router"])
    ISP_XLINK_NETWORK_HOST = need(d, "isp_crosslink_network_host")
    EDGE_VRRP_GROUP_ID = need(d, "edge_vrrp_group_id")
    CARRIER_BLOCK = octets(d, "carrier_block", 3)
    CARRIER_PLEN = need(d, "carrier_link_prefix_length")
    SITE_CARRIER_BASE[site] = need(d, f"{s}_carrier_index_base")
    CARRIER = units(d, "carrier_names", ROLE_COUNT["isp_router"])      # router n peers with carrier n
    CARRIER_SIDE_HOST, OUR_SIDE_HOST = need(d, "carrier_side_host"), need(d, "our_side_host")

    # SD-WAN hub FW and VPN-partner FW: public WAN endpoint, overlay
    FW_WAN_HOST = need(d, "fw_wan_endpoint_host")
    for role in ("sdwan_fw", "vpn_fw"):
        FW_WAN_ENDPOINT[(site, role)] = str(need(d, f"{s}_{role}_wan_endpoint"))
    SDWAN_OVERLAY[site] = str(need(d, f"{s}_sdwan_overlay"))
    SDWAN_HUB_HOST = need(d, "sdwan_hub_host")

    # Multi-Site + DCI
    MS_VIP_HOST = need(d, "inter_site_vip_host")
    MS_VIP_THIRD = need(d, "inter_site_vip_third_octet")
    DCI_BLOCK_THIRD = need(d, "dci_block_third_octet")
    DCI_LOOPBACK0_HOST = units(d, "dci_loopback0_hosts", ROLE_COUNT["dci"])
    DCI_PAIR_LINK = {"po": f"port-channel{need(d, 'dci_pair_port_channel')}", "members": tuple(need(d, "dci_pair_link_ports")),
                     "host": need(d, "dci_pair_link_host")}
    bgw_dci_ports, bgw_dci_hosts = need(d, "bgw_dci_link_ports"), units(d, "bgw_dci_link_hosts", ROLE_COUNT["bgw"])
    BGW_DCI_LINKS = tuple((n, bgw_dci_ports["bgw"], bgw_dci_ports["dci"], bgw_dci_hosts[n]) for n in range(1, ROLE_COUNT["bgw"] + 1))
    BGW_DCI_END_HOST = dict(need(d, "bgw_dci_link_end_hosts"))
    for end in ("dci", "bgw"):
        if end not in BGW_DCI_END_HOST:
            raise ValueError(f"FabricDesign.bgw_dci_link_end_hosts has no '{end}' entry; fix it in Infrahub")
    DCI_PAIR_MEMBER_HOST = units(d, "dci_pair_link_member_hosts", ROLE_COUNT["dci"])
    DCI_CIRCUIT = units(d, "dci_circuits", ROLE_COUNT["dci"])
    DCI_CIRCUIT_HOST[site] = need(d, f"{s}_dci_circuit_host")
    OTHER_SITE = dict(need(d, "dci_peer_site"))
    if site not in OTHER_SITE:
        raise ValueError(f"FabricDesign.dci_peer_site has no entry for {site}; fix it in Infrahub")

    # cloud routers
    CLOUD_BLOCK_THIRD = need(d, "cloud_block_third_octet")
    CLOUD_LOOPBACK0_HOST = units(d, "cloud_loopback0_hosts", ROLE_COUNT["cloud_router"])
    CLOUD_XLINK_HOST = need(d, "cloud_crosslink_host")
    CLOUD_XLINK_MEMBER_HOST = units(d, "cloud_crosslink_member_hosts", ROLE_COUNT["cloud_router"])
    CLOUD_BUNDLE = f"Bundle-Ether{need(d, 'cloud_bundle_id')}"
    cloud_units = range(1, ROLE_COUNT["cloud_router"] + 1)
    CLOUD_AWS_VLAN[site] = {n: need(d, f"{s}_cloud_aws_vlan_{n}") for n in cloud_units}
    CLOUD_ER_VLAN[site] = need(d, f"{s}_cloud_er_vlan")
    CLOUD_ER_NET[site] = {n: str(need(d, f"{s}_cloud_er_net_{n}")) for n in cloud_units}
    CLOUD_AWS_NET[site] = {n: str(need(d, f"{s}_cloud_aws_net_{n}")) for n in cloud_units}
    ER_CUSTOMER_HOST, ER_MICROSOFT_HOST = need(d, "er_customer_host"), need(d, "er_provider_host")
    AWS_PEER_HOST, AWS_CUSTOMER_HOST = need(d, "aws_peer_host"), need(d, "aws_customer_host")

    # L2-only VLANs: VLAN base -> <tenant>.<third base + offset>.0/24; names <site>-DMZ-<name>, unnamed ones SRV-<nn>
    DMZ_VLAN_BASE, DMZ_THIRD_OCTET_BASE = need(d, "vlan_base_dmz"), need(d, "dmz_third_octet_base")
    DMZ_VLAN_PLEN = need(d, "dmz_vlan_prefix_length")
    dmz_names = {int(k): v for k, v in need(d, "dmz_vlan_names").items()}
    DMZ_VLAN_NAMES = {v: dmz_names.get(v, f"SRV-{v - DMZ_VLAN_BASE:02d}")
                      for v in range(DMZ_VLAN_BASE, DMZ_VLAN_BASE + need(d, "dmz_vlan_count"))}


def configure_hardware(types: dict, platforms: dict) -> None:
    """Port maps from DcimDeviceType (model default) and OS releases from DcimPlatform.os_version."""
    global OS_VERSION_BY_PLATFORM, ISP_EDGE_PORTS, ISP_CROSSLINK_PORT, ISP_CARRIER_PORT
    global CLOUD_FABRIC_PORTS, CLOUD_XLINK_PORT, CLOUD_XC_PORT, DCI_CIRCUIT_PORT, FW_NPU_LINKS
    DEVICE_TYPE_OBJ.update(types)
    OS_VERSION_BY_PLATFORM = {}
    for name, platform in platforms.items():
        attr = getattr(platform, "os_version", None)
        if attr is None or attr.value is None:
            raise ValueError(f"DcimPlatform {name}.os_version is empty; fix it in Infrahub")
        OS_VERSION_BY_PLATFORM[name] = attr.value
    isp, cloud = DEVICE_TYPE["isp_router"], DEVICE_TYPE["cloud_router"]
    # ISP router: n-th fabric-facing port <-> EDGE n; cloud router: n-th fabric-facing port <-> BL n
    ISP_EDGE_PORTS = tuple((port, f"EDGE{k}") for k, port in enumerate(type_need(isp, "fabric_facing_ports"), start=1))
    if len(ISP_EDGE_PORTS) != ROLE_COUNT["edge"]:
        raise ValueError(f"DcimDeviceType {isp}.fabric_facing_ports must have {ROLE_COUNT['edge']} entries (one per edge switch)")
    ISP_CROSSLINK_PORT = type_need(isp, "crosslink_port")
    ISP_CARRIER_PORT = type_need(isp, "carrier_port")
    CLOUD_FABRIC_PORTS = tuple((port, f"BL{k}") for k, port in enumerate(type_need(cloud, "fabric_facing_ports"), start=1))
    if len(CLOUD_FABRIC_PORTS) != ROLE_COUNT["border_leaf"]:
        raise ValueError(f"DcimDeviceType {cloud}.fabric_facing_ports must have {ROLE_COUNT['border_leaf']} entries (one per border leaf)")
    CLOUD_XLINK_PORT = type_need(cloud, "crosslink_port")
    CLOUD_XC_PORT = type_need(cloud, "cloud_exchange_port")
    DCI_CIRCUIT_PORT = type_need(DEVICE_TYPE["dci"], "dci_circuit_port")
    FW_NPU_LINKS = tuple(type_need(DEVICE_TYPE["internal_fw"], "vrf_leak_link_ports"))   # tenant side / TRANSIT side


def configure_vrfs(vrf_nodes: dict) -> None:
    """Internal FW slot and attachment flag per VRF (FabricVRF, Q4)."""
    for name, n in vrf_nodes.items():
        attached = n["internal_fw_attached"]["value"]
        if attached is None:
            raise ValueError(f"FabricVRF {name}.internal_fw_attached is empty; fix it in Infrahub")
        if attached:   # keyed by VRF name (site-specific names), so DC1 and DC2 runs in one worker never collide
            INTFW_ATTACHED.add(name)
        else:
            INTFW_ATTACHED.discard(name)
        if n["internal_fw_slot"]["value"] is not None:
            INTFW_VRF_SLOT[name] = n["internal_fw_slot"]["value"]


def fw_ports(fw_role: str, sw_role: str) -> tuple:
    """(FW aggregate, (FW port toward switch 1, toward switch 2)) from DcimDeviceType.fw_attach_ports of the FW model."""
    agg, side = FW_SIDE[sw_role]
    model = DEVICE_TYPE[fw_role]
    ports = type_need(model, "fw_attach_ports")
    if side not in ports:
        raise ValueError(f"DcimDeviceType {model}.fw_attach_ports has no '{side}' ports; fix it in Infrahub")
    return agg, tuple(ports[side])


def fw_ha_ports(fw_role: str) -> tuple:
    return tuple(type_need(DEVICE_TYPE[fw_role], "ha_ports"))


def vpc_peer_link_ports(role: str) -> tuple:
    """Role override from FabricDesign where one exists, else the model default from DcimDeviceType."""
    if role in VPC_PEER_LINK_OVERRIDE:
        return VPC_PEER_LINK_OVERRIDE[role]
    return tuple(type_need(DEVICE_TYPE[role], "vpc_peer_link_ports"))


# Constants: reconciliation
# Plan-only mode: FABRIC_DRY_RUN=1 prints every object the run would write and exits before any write.
DRY_RUN = os.environ.get("FABRIC_DRY_RUN", "0") == "1"
# Descriptions: far-end device and port where the far-end port is known now; device only toward the
# FortiGates until their port names are known. Server-leaf pairs keep the peer-name rule so the 40
# reviewed renders stay byte-identical.
PEER_PORT_ROLES = {"border_leaf", "service_leaf", "edge", "dmz"}
RECONCILE = os.environ.get("FABRIC_RECONCILE", "delete")
if RECONCILE not in ("delete", "report"):
    raise ValueError(f"FABRIC_RECONCILE must be 'delete' or 'report', got {RECONCILE!r}")
OWNED_VIRTUAL = {"loopback0", "loopback1", MS_VIP_IF}
OWNED_VIRTUAL_ROLES = {ROLE_BACKUP, ROLE_PEERING, ROLE_CLOUD}                                         # backup SVI (vpc_backup_vlan), attachment SVIs, bundle sub-interfaces
OWNED_PHYSICAL_ROLES = {MGMT0_ROLE, FABRIC_ROLE, ROLE_KEEPALIVE, ROLE_PEER_LINK, ROLE_PEERING, ROLE_UPSTREAM, ROLE_DCI_CIRCUIT,
                        ROLE_CLOUD}
OWNED_PORTCHANNEL_ROLES = {ROLE_PEER_LINK, ROLE_PEERING, FABRIC_ROLE}   # FABRIC_ROLE: DCI pair link port-channel (dci_pair_port_channel)
PREFIX_ROLES = {FABRIC_ROLE, ROLE_KEEPALIVE, ROLE_BACKUP, ROLE_UPSTREAM}   # interfaces whose link prefix the generator owns
# Not reconciled on their own (documented): standalone addresses inside object-file prefixes (edge VLAN VIP and .10).
# Attachment /29s and carrier /30s go with their attachment or interface.

# Roles that come in a pair per site (count from FabricDesign <role>_count, must be 2: pair logic).
# (name prefix, role, description with {n} and {carrier}); the asn key per role is FabricDesign.role_asn_group
FIXED_PAIRS = (
    ("BL", "border_leaf", "Border leaf {n}"),
    ("SL", "service_leaf", "Service leaf {n}"),
    ("BGW", "bgw", "Border gateway {n}, Multi-Site"),
    ("EDGE", "edge", "Edge switch {n}, untrusted side, L2 only"),
    ("DCI", "dci", "DCI switch {n}"),
    ("DMZ", "dmz", "DMZ switch {n}, L2 only, behind the Internet firewall"),
    ("INETFW", "internet_fw", "Internet firewall unit {n} (active/passive pair)"),
    ("INTFW", "internal_fw", "Internal firewall unit {n} (active/passive pair)"),
    ("SDWANFW", "sdwan_fw", "SD-WAN hub firewall unit {n} (active/passive pair)"),
    ("VPNFW", "vpn_fw", "VPN-partner firewall unit {n} (active/passive pair)"),
    ("ISP", "isp_router", "ISP router {n}, peers with {carrier}"),
    ("CLOUD", "cloud_router", "Cloud router {n}"),
)
# Roles with a fixed loopback0 address rule in fixed_loopback0(); FabricDesign.roles_with_fixed_loopback0 picks which use it
FIXED_LOOPBACK0_RULES = ("isp_router", "dci", "cloud_router")
# asn keys (identifiers): the FabricAutonomousSystem objects generate() resolves; None = no ASN
ASN_GROUPS = {"fabric", "dci", "internal_fw", "internet_fw", "sdwan_fw", "vpn_fw", "isp", "cloud", None}
# Link kinds with an MTU (identifiers); FabricDesign.interface_mtu_class maps each to its mtu_* field
MTU_KINDS = ("spine_leaf", "bgw_dci", "dci_pair", "cloud_bundle", "cloud_crosslink", "vpc_keepalive", "vpc_backup",
             "attachment", "isp_edge", "isp_crosslink", "carrier", "fw_attach", "dci_circuit", "cloud_exchange", "cloud_handoff")


def build_plan(site: str, vpc_pairs: int, esi_pairs: int, single: int) -> list[dict]:
    """One dict per device: name, role, description, asn key, platform.

    Order: S1-S2, L1..L(2*vpc) vPC, next 2*esi ESI, next `single` single-homed, then the fixed pairs in
    FIXED_PAIRS order. The order also decides which new device gets which pool address.
    """
    server_leaves = 2 * vpc_pairs + 2 * esi_pairs + single
    if server_leaves > MAX_SERVER_LEAVES:
        raise ValueError(f"{site}: {server_leaves} server leaves exceed the spine port map ({MAX_SERVER_LEAVES})")

    plan: list[dict] = []

    def add(short: str, role: str, description: str, asn: str | None) -> None:
        plan.append({"name": f"{site}-{short}", "short": short, "role": role, "description": description,
                     "asn": asn, "platform": PLATFORM_BY_ROLE[role]})

    for n in range(1, SPINE_COUNT + 1):
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

    for prefix, role, description in FIXED_PAIRS:
        for n in range(1, ROLE_COUNT[role] + 1):
            add(f"{prefix}{n}", role, description.format(n=n, carrier=CARRIER[n]), ROLE_ASN_GROUP[role])

    return plan


def build_links(site: str, plan: list[dict]) -> list[dict]:
    """One dict per spine-leaf link: spine, spine_port, leaf, leaf_port (order decides the /31)."""
    spines = [r["name"] for r in plan if r["role"] == "spine"]
    server_leaves = [r["name"] for r in plan if r["role"] == "leaf"]
    names = {r["name"] for r in plan}
    roles = {r["short"]: r["role"] for r in plan}

    # Port maps from DcimDeviceType (model default): spine downlinks, leaf uplinks per spine (S1/S2 keys)
    spine_model = DEVICE_TYPE["spine"]
    port_format = type_need(spine_model, "port_name_format")
    per_leaf = type_need(spine_model, "downlink_ports_per_leaf")
    fixed_ports = type_need(spine_model, "fixed_leaf_downlink_ports")    # fixed leaf short name -> spine port
    for leaf_short in fixed_ports:
        if leaf_short not in roles:
            raise ValueError(f"{site}: link to unknown device {site}-{leaf_short}")
    fixed_leaves = [r["short"] for r in plan if r["short"] in fixed_ports]   # plan order (= BL1, BL2, SL1, SL2, BGW1, BGW2)

    def uplinks(role: str, spine_short: str):
        ports = type_need(DEVICE_TYPE[role], "uplink_ports")
        if spine_short not in ports:
            raise ValueError(f"DcimDeviceType {DEVICE_TYPE[role]}.uplink_ports has no entry for {spine_short}; fix it in Infrahub")
        return ports[spine_short]

    links: list[dict] = []
    for spine in spines:
        short = spine.removeprefix(f"{site}-")
        for n, leaf in enumerate(server_leaves, start=1):
            for i, leaf_port in enumerate(uplinks("leaf", short)):
                links.append({"spine": spine, "spine_port": port_format.format(n=per_leaf * (n - 1) + SPINE_FIRST_DOWNLINK_PORT + i),
                              "leaf": leaf, "leaf_port": leaf_port})
        for leaf_short in fixed_leaves:
            links.append({"spine": spine, "spine_port": fixed_ports[leaf_short],
                          "leaf": f"{site}-{leaf_short}", "leaf_port": uplinks(roles[leaf_short], short)})

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
        slot = VPC_FIRST_SERVER_SLOT if k == 0 else FIRST_EXTRA_SERVER_SLOT + k - 1
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
            if not (base + 1 <= did <= base + VPC_DOMAIN_SLOT_SPAN and 1 <= did <= 1000):
                raise ValueError(f"{pair['name']}: domain ID {did} out of range")
            if did in seen_ids:
                raise ValueError(f"{pair['name']}: domain ID {did} used twice")
            seen_ids.add(did)
    return pairs


def unit_number(name: str) -> int:
    """The unit number at the end of a device name (DC1-ISP2 -> 2, DC1-L11 -> 11)."""
    m = re.search(r"(\d+)$", name)
    if not m:
        raise ValueError(f"{name}: no unit number at the end of the device name")
    return int(m.group(1))


def rule_mgmt_addresses(plan: list[dict], block) -> dict:
    """Device name -> mgmt address for the roles in FabricDesign.mgmt0_rule_first_host.

    Unit n of a role (the number in its device name, not its place in any build) takes host first + n - 1 of the site's
    MGMT0 block. A phased build and a build from empty therefore give the same address.
    """
    out, seen = {}, {}
    for role, first in MGMT0_RULE_FIRST_HOST.items():
        for row in (r for r in plan if r["role"] == role):
            ip = block.network_address + first + unit_number(row["name"]) - 1
            if ip not in block or ip in (block.network_address, block.broadcast_address):
                raise ValueError(f"{row['name']}: mgmt rule address {ip} is outside the usable hosts of {block}; fix FabricDesign.mgmt0_rule_first_host")
            if ip in seen:
                raise ValueError(f"{row['name']}: mgmt rule address {ip} already given to {seen[ip]}; fix FabricDesign.mgmt0_rule_first_host")
            seen[ip] = row["name"]
            out[row["name"]] = str(ip)
    return out


def rule_backup_networks(domains: list[dict], block) -> dict:
    """vPC domain name -> backup SVI /31 for every domain with a backup SVI.

    Pair k of a role (k from member 1's unit number: units 1-2 -> 1, 3-4 -> 2) takes the /31 whose network is host
    first + 2(k - 1) of the site's VPC-BACKUP block (FabricDesign.vpc_backup_rule_first_host). Every backup domain must
    have a rule.
    """
    out, seen = {}, {}
    for d in domains:
        if not d["backup"]:
            continue
        role = d["role"]
        if role not in VPC_BACKUP_RULE_FIRST_HOST:
            raise ValueError(f"{d['name']}: role {role} has a backup SVI but no entry in FabricDesign.vpc_backup_rule_first_host; fix it in Infrahub")
        k = (unit_number(d["members"][0]) + 1) // 2
        start = block.network_address + VPC_BACKUP_RULE_FIRST_HOST[role] + 2 * (k - 1)
        net = ipaddress.ip_network(f"{start}/{P2P_PLEN}")             # strict: a misaligned host raises here
        if not net.subnet_of(block):
            raise ValueError(f"{d['name']}: backup rule /{P2P_PLEN} {net} is outside {block}; fix FabricDesign.vpc_backup_rule_first_host")
        if net in seen:
            raise ValueError(f"{d['name']}: backup rule /{P2P_PLEN} {net} already given to {seen[net]}; fix FabricDesign.vpc_backup_rule_first_host")
        seen[net] = d["name"]
        out[d["name"]] = net
    return out


def vpc_domains(pairs: list[dict]) -> list[dict]:
    """Every vPC domain (server, BL, SL, EDGE, DMZ): gets keepalive and peer-link; backup SVI only with NVE."""
    out = []
    for p in pairs:
        if p["kind"] != "FabricVpcDomain":
            continue
        # peer-link: role override or model default; keepalive: model default; backup SVI: VTEP pairs only
        ports = {"peer_link": vpc_peer_link_ports(p["role"]),
                 "keepalive": type_need(DEVICE_TYPE[p["role"]], "vpc_keepalive_port"),
                 "backup": p["role"] in LOOPBACK1_ROLES}
        if ports["backup"] != p["nve"]:
            raise ValueError(f"{p['name']}: backup SVI rule disagrees with NVE rule")
        out.append({**p, "peer_link": ports["peer_link"], "keepalive": ports["keepalive"], "backup": ports["backup"]})
    return out


def build_attach_ports(site: str, plan: list[dict]) -> list[dict]:
    """Switch-side ports toward attached devices: device, port, far (device), po (port-channel name), po_number,
    far_port_desc / far_po_desc (descriptions: device + port where the far port is known)."""
    names = {r["name"] for r in plan}
    # ISP router n: EDGE1 <-> first ISP edge port, EDGE2 <-> second (ISP_EDGE_PORTS order)
    isp_far_port = {f"{site}-EDGE{k}": port for k, (port, _) in enumerate(ISP_EDGE_PORTS, start=1)}
    # Cloud router: BL n <-> the router's n-th fabric port (CLOUD_FABRIC_PORTS order)
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
            else:                                      # FortiGate side: port and aggregate by switch role
                far_role = next(x["role"] for x in plan if x["name"] == far)
                agg, far_ports = fw_ports(far_role, r["role"])
                port_desc, po_desc = f"{far} {far_ports[int(r['name'][-1]) - 1]}", f"{far} {agg}"
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
        router = devices is not None             # one independent router, attachment_device_host is its own interface
        devices = devices or (f"{site}-{pair_short}1", f"{site}-{pair_short}2")
        for d in members + devices:
            if d not in roles:
                raise ValueError(f"{site}: attachment to unknown device {d}")
        size = 2 ** (32 - ATTACHMENT_PLEN)
        net = ipaddress.ip_network(f"{infra}.{block_third}.{size * slot}/{ATTACHMENT_PLEN}")
        lower, upper = ATTACHMENT_LEAF_HOSTS
        out.append({"name": ATTACHMENT_NAME.format(site=site, vrf=vrf, pair=pair_short), "vrf": vrf,
                    "pair": f"{site}-VPC-{leaf_short}1-{leaf_short}2", "members": members, "devices": devices,
                    "slot": slot, "vlan": vlan, "prefix": str(net), "router": router,
                    "addrs": {members[0]: str(net[lower]), members[1]: str(net[upper]), "fw": str(net[ATTACHMENT_DEVICE_HOST])}})

    for pair_short, leaf_short, slot in TRANSIT_ATTACHMENTS:
        add(pair_short, leaf_short, TRANSIT_VRF, slot, TRANSIT_BLOCK_THIRD, TRANSIT_VLAN_BASE + slot)
    for dev_short, leaf_short, slot in CLOUD_ATTACHMENTS:
        add(dev_short, leaf_short, TRANSIT_VRF, slot, TRANSIT_BLOCK_THIRD, TRANSIT_VLAN_BASE + slot, devices=(f"{site}-{dev_short}",))
    for vrf in sorted(v for v in site_vrfs if v in INTFW_ATTACHED):
        if vrf not in INTFW_VRF_SLOT:
            raise ValueError(f"{site}: VRF {vrf} is in the site but has no Internal FW slot (FabricVRF.internal_fw_slot); decide it first")
        slot = INTFW_VRF_SLOT[vrf]
        add(TENANT_ATTACHMENT_PAIR[0], TENANT_ATTACHMENT_PAIR[1], vrf, slot, INTFW_BLOCK_THIRD, TENANT_VLAN_BASE + slot)
    for pair_short, leaf_short, vrf, slot in VPN_ATTACHMENTS:          # VPN-partner FW on the border leaves
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
    for n in range(1, ROLE_COUNT["isp_router"] + 1):
        name = f"{site}-ISP{n}"
        if roles.get(name) != "isp_router":
            raise ValueError(f"{site}: {name} missing from the plan")
        idx = SITE_CARRIER_BASE[site] + (n - 1)
        carrier_net = ipaddress.ip_network(f"{CARRIER_BLOCK}.{2 ** (32 - CARRIER_PLEN) * idx}/{CARRIER_PLEN}")
        out.append({
            "name": name, "n": n,
            "loopback0": f"{infra}.{ISP_BLOCK_THIRD}.{ISP_LOOPBACK0_HOST[n]}",
            "edge_ports": [(port, f"{site}-{edge_short}", edge_ports[f"ISP{n}"]) for port, edge_short in ISP_EDGE_PORTS],
            "bundle": ISP_BUNDLE, "subif": ISP_EDGE_SUBIF,
            "subif_addr": f"{infra}.{EDGE_VLAN_THIRD}.{EDGE_VLAN_HOST[f'ISP{n}']}",
            "crosslink": {"port": ISP_CROSSLINK_PORT, "prefix": f"{infra}.{ISP_BLOCK_THIRD}.{ISP_XLINK_NETWORK_HOST}/{P2P_PLEN}",
                          "addr": f"{infra}.{ISP_BLOCK_THIRD}.{ISP_CROSSLINK_HOST[n]}",
                          "peer": f"{site}-ISP{3 - n}"},
            "carrier": {"port": ISP_CARRIER_PORT, "prefix": str(carrier_net), "ours": str(carrier_net[OUR_SIDE_HOST]),
                        "theirs": str(carrier_net[CARRIER_SIDE_HOST]), "provider": CARRIER[n]},
        })
    return out


def fixed_loopback0(site: str, name: str, role: str) -> str:
    """Fixed loopback0 address of a role in FabricDesign.roles_with_fixed_loopback0 (unit number = last digit of the name)."""
    infra, n = SITE_INFRA[site], int(name[-1])
    if role == "isp_router":
        return f"{infra}.{ISP_BLOCK_THIRD}.{ISP_LOOPBACK0_HOST[n]}"
    if role == "dci":
        return f"{infra}.{DCI_BLOCK_THIRD}.{DCI_LOOPBACK0_HOST[n]}"
    if role == "cloud_router":
        return f"{infra}.{CLOUD_BLOCK_THIRD}.{CLOUD_LOOPBACK0_HOST[n]}"
    raise ValueError(f"{name}: role {role} has no fixed loopback0 rule")


def edge_vlan_addresses(site: str) -> dict:
    infra = SITE_INFRA[site]
    return {"vip": f"{infra}.{EDGE_VLAN_THIRD}.{EDGE_VLAN_HOST['VIP']}", "inetfw": f"{infra}.{EDGE_VLAN_THIRD}.{EDGE_VLAN_HOST['INETFW']}"}


def build_l2_vlans(site: str) -> list[dict]:
    """L2-only VLANs: the edge VLAN on the EDGE pair, the DMZ VLANs on the DMZ pair.
    Every /24 is owned by the IP plan (objects/03-ip-plan.yml); the generator only links to it, never saves it."""
    if site not in SITE_TENANT:
        raise ValueError(f"{site}: no tenant block in SITE_TENANT")
    out = [{"name": EDGE_VLAN_NAME.format(site=site), "vlan": EDGE_VLAN, "pair": f"{site}-VPC-EDGE1-EDGE2",
            "prefix": f"{SITE_INFRA[site]}.{EDGE_VLAN_THIRD}.0/{EDGE_VLAN_PLEN}",
            "description": "Edge VLAN: VRRP VIP, ISP routers, Internet FW outside leg"}]
    for vlan, suffix in DMZ_VLAN_NAMES.items():
        out.append({"name": f"{site}-DMZ-{suffix}", "vlan": vlan, "pair": f"{site}-VPC-DMZ1-DMZ2",
                    "prefix": f"{SITE_TENANT[site]}.{DMZ_THIRD_OCTET_BASE + vlan - DMZ_VLAN_BASE}.0/{DMZ_VLAN_PLEN}",
                    "description": f"DMZ VLAN: {DMZ_VLAN_PURPOSE.get(suffix, 'DMZ servers')}"})
    return out


def build_fw(site: str, plan: list[dict], attach_ports: list[dict], attachments: list[dict], l2vlans: list[dict]) -> list[dict]:
    """FortiGate interfaces, one dict per FW unit: ports toward the switches, aggregates, HA ports, VLAN
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
              "ha": [(p, f"{peer} {p}") for p in fw_ha_ports(r["role"])], "virtuals": []}
        for ap in attach_ports:
            if ap["far"] != unit:
                continue
            sw_role = next(x["role"] for x in plan if x["name"] == ap["device"])
            agg, unit_ports = fw_ports(r["role"], sw_role)
            fw_port = unit_ports[int(ap["device"][-1]) - 1]
            fw["ports"].append((fw_port, f"{ap['device']} {ap['port']}", agg))
            sw_short = ap["device"].removeprefix(f"{site}-")[:-1]
            fw["aggs"][agg] = f"{site}-{sw_short}1/{sw_short}2 {ap['po']}"
        for a in attachments:
            if unit in a["devices"]:
                fw["subifs"].append({"name": f"{a['vrf']}-{a['vlan']}", "parent": "agg-inside", "desc": a["name"],
                                     "addr": (a["addrs"]["fw"], ATTACHMENT_PLEN) if n == 1 else None, "new_addr": False})
                if r["role"] == "internal_fw" and a["vrf"] != TRANSIT_VRF:
                    net = ipaddress.ip_network(f"{infra}.{FW_LEAK_THIRD}.{FW_LEAK_BASE + 4 * INTFW_VRF_SLOT[a['vrf']]}/30")
                    fw["npu"].append({"vrf": a["vrf"], "prefix": str(net),
                                      "tenant": {"name": f"LK-{a['vrf']}", "parent": FW_NPU_LINKS[0], "addr": str(net[1])},
                                      "transit": {"name": f"LK-T-{a['vrf']}", "parent": FW_NPU_LINKS[1], "addr": str(net[2])}})
        if r["role"] == "internet_fw":
            edge = edge_vlan_addresses(site)
            fw["subifs"].append({"name": FW_OUTSIDE_SUBIF.format(vlan=EDGE_VLAN), "parent": "agg-outside", "desc": f"Edge VLAN {EDGE_VLAN}",
                                 "addr": (edge["inetfw"], EDGE_VLAN_PLEN) if n == 1 else None, "new_addr": False})
            for v in l2vlans:
                if v["pair"].endswith("DMZ1-DMZ2"):
                    gw = str(ipaddress.ip_network(v["prefix"])[FW_DMZ_GW_HOST])
                    fw["subifs"].append({"name": FW_DMZ_SUBIF.format(vlan=v["vlan"]), "parent": "agg-dmz", "desc": v["name"],
                                         "addr": (gw, DMZ_VLAN_PLEN) if n == 1 else None, "new_addr": True})
        if r["role"] in WAN_FW_ROLES:      # outside leg on the edge VLAN, public WAN loopback, SD-WAN hub tunnel
            short = unit.removeprefix(f"{site}-")[:-1]
            fw["subifs"].append({"name": FW_OUTSIDE_SUBIF.format(vlan=EDGE_VLAN), "parent": "agg-outside", "desc": f"Edge VLAN {EDGE_VLAN}",
                                 "addr": (f"{infra}.{EDGE_VLAN_THIRD}.{EDGE_VLAN_HOST[short]}", EDGE_VLAN_PLEN) if n == 1 else None, "new_addr": True})
            wan = ipaddress.ip_network(FW_WAN_ENDPOINT[(site, r["role"])])
            fw["virtuals"].append({"name": FW_WAN_LOOPBACK, "desc": f"WAN endpoint (public {wan})",
                                   "addr": (str(wan[FW_WAN_HOST]), wan.prefixlen) if n == 1 else None,
                                   "plan_prefix": str(wan), "own_prefix": None})
            if r["role"] == "sdwan_fw":
                ov = ipaddress.ip_network(SDWAN_OVERLAY[site])
                fw["virtuals"].append({"name": SDWAN_TUNNEL, "desc": f"ADVPN 2.0 hub tunnel, overlay {ov}",
                                       "addr": (str(ov[SDWAN_HUB_HOST]), ov.prefixlen) if n == 1 else None,
                                       "plan_prefix": None, "own_prefix": str(ov),
                                       "own_prefix_desc": f"{site} SD-WAN overlay (ADVPN hub tunnel)"})
        for x in fw["virtuals"]:
            if len(x["name"]) > 15:
                raise ValueError(f"{unit}: FortiOS interface name {x['name']} longer than 15 characters")
        for sub in fw["subifs"]:
            if len(sub["name"]) > 15:
                raise ValueError(f"{unit}: FortiOS interface name {sub['name']} longer than 15 characters")
        out.append(fw)
    return out


def build_dci(site: str, plan: list[dict]) -> dict:
    """BGW <-> DCI links, the DCI pair link, the two inter-site circuits, DCI loopback0, the Multi-Site VIP."""
    infra = SITE_INFRA[site]
    names = {r["name"] for r in plan}
    for n in range(1, ROLE_COUNT["dci"] + 1):
        if f"{site}-DCI{n}" not in names:
            raise ValueError(f"{site}: {site}-DCI{n} missing from the plan")
    for n in range(1, ROLE_COUNT["bgw"] + 1):
        if f"{site}-BGW{n}" not in names:
            raise ValueError(f"{site}: {site}-BGW{n} missing from the plan")
    links = []
    for n, bgw_port, dci_port, host in BGW_DCI_LINKS:
        net = ipaddress.ip_network(f"{infra}.{DCI_BLOCK_THIRD}.{host}/{P2P_PLEN}")
        links.append({"bgw": f"{site}-BGW{n}", "bgw_port": bgw_port, "dci": f"{site}-DCI{n}", "dci_port": dci_port,
                      "prefix": str(net), "dci_addr": str(net[BGW_DCI_END_HOST["dci"]]), "bgw_addr": str(net[BGW_DCI_END_HOST["bgw"]])})
    pl = DCI_PAIR_LINK
    pnet = ipaddress.ip_network(f"{infra}.{DCI_BLOCK_THIRD}.{pl['host']}/{P2P_PLEN}")
    pair = {"po": pl["po"], "members": pl["members"], "prefix": str(pnet),
            "addrs": {f"{site}-DCI{n}": str(pnet[DCI_PAIR_MEMBER_HOST[n]]) for n in range(1, ROLE_COUNT["dci"] + 1)}}
    circuits = []
    for n in range(1, ROLE_COUNT["dci"] + 1):
        net = ipaddress.ip_network(DCI_CIRCUIT[n])
        circuits.append({"dci": f"{site}-DCI{n}", "port": DCI_CIRCUIT_PORT, "prefix": str(net), "plen": net.prefixlen, "name": f"DCI-{n}",
                         "addr": str(net[DCI_CIRCUIT_HOST[site]]), "far": f"{OTHER_SITE[site]}-DCI{n} {DCI_CIRCUIT_PORT}"})
    return {"links": links, "pair": pair, "circuits": circuits, "vip": f"{infra}.{MS_VIP_THIRD}.{MS_VIP_HOST}",
            "loopback0": {f"{site}-DCI{n}": f"{infra}.{DCI_BLOCK_THIRD}.{DCI_LOOPBACK0_HOST[n]}" for n in range(1, ROLE_COUNT["dci"] + 1)}}


def build_cloud(site: str, plan: list[dict], attachments: list[dict]) -> list[dict]:
    """One dict per cloud router: loopback0, fabric bundle + members toward the BL vPC port-channel, the TRANSIT
    sub-interface (.3 of its attachment /29), iBGP cross-link /31, the cloud-exchange port and its AWS / ExpressRoute handoffs."""
    infra = SITE_INFRA[site]
    roles = {r["name"]: r["role"] for r in plan}
    bl_side = {far_short: (port, po) for port, far_short, po in ATTACH_PORTS["border_leaf"] if far_short.startswith("CLOUD")}
    out = []
    for n in range(1, ROLE_COUNT["cloud_router"] + 1):
        name = f"{site}-CLOUD{n}"
        if roles.get(name) != "cloud_router":
            raise ValueError(f"{site}: {name} missing from the plan")
        att = [a for a in attachments if a["devices"] == (name,)]
        if len(att) != 1:
            raise ValueError(f"{name}: expected 1 TRANSIT attachment, got {len(att)}")
        a = att[0]
        bl_port, po = bl_side[f"CLOUD{n}"]
        xnet = ipaddress.ip_network(f"{infra}.{CLOUD_BLOCK_THIRD}.{CLOUD_XLINK_HOST}/{P2P_PLEN}")
        aws_net, er_net = ipaddress.ip_network(CLOUD_AWS_NET[site][n]), ipaddress.ip_network(CLOUD_ER_NET[site][n])
        aws_vlan, er_vlan = CLOUD_AWS_VLAN[site][n], CLOUD_ER_VLAN[site]
        out.append({
            "name": name, "n": n, "loopback0": f"{infra}.{CLOUD_BLOCK_THIRD}.{CLOUD_LOOPBACK0_HOST[n]}",
            "fabric_ports": [(port, f"{site}-{bl}", bl_port) for port, bl in CLOUD_FABRIC_PORTS],
            "bundle": CLOUD_BUNDLE, "bl_po": f"port-channel{po}",
            "subif": f"{CLOUD_BUNDLE}.{a['vlan']}", "subif_addr": a["addrs"]["fw"], "attachment": a["name"],
            "crosslink": {"port": CLOUD_XLINK_PORT, "prefix": str(xnet), "addr": str(xnet[CLOUD_XLINK_MEMBER_HOST[n]]), "peer": f"{site}-CLOUD{3 - n}"},
            "xc": {"port": CLOUD_XC_PORT, "desc": f"Cloud exchange port {n} (AWS VIF {n}, ExpressRoute {ER_LEG[n]})"},
            "handoffs": [
                {"provider": CLOUD_PROVIDER["aws"], "subif": f"{CLOUD_XC_PORT}.{aws_vlan}", "vlan": aws_vlan, "prefix": str(aws_net), "plen": aws_net.prefixlen,
                 "ours": str(aws_net[AWS_CUSTOMER_HOST]), "theirs": str(aws_net[AWS_PEER_HOST]),
                 "desc": f"{CLOUD_PROVIDER['aws']} private VIF {n} to the Direct Connect Gateway, VLAN {aws_vlan}"},
                {"provider": CLOUD_PROVIDER["azure"], "subif": f"{CLOUD_XC_PORT}.{er_vlan}", "vlan": er_vlan, "prefix": str(er_net), "plen": er_net.prefixlen,
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
        mgmt = MGMT_IF_BY_ROLE[r["role"]]
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


def print_plan(site, fabric_asn, plan, links, pairs, domains, attach_ports, attachments, isp, expected, site_vrfs, l2vlans, fws=(), dci=None, cloud=(),
               rule_mgmt=None, rule_backup=None) -> None:
    """Every object a write run creates or updates for this site, as tables (plan-only mode)."""
    mgmt_pool = f"{site}-MGMT0"
    rule_mgmt, rule_backup = rule_mgmt or {}, rule_backup or {}
    print(f"=== PLAN {site}: fabric ASN {fabric_asn}; site VRFs {sorted(site_vrfs)}")
    print(f"--- devices ({len(plan)}): name | role | platform | os_version | asn rule | device type | mgmt interface (pool) | loopbacks")
    for r in plan:
        mgmt = MGMT_IF_BY_ROLE[r["role"]] if r["role"] not in NO_MGMT_ROLES else None
        lo = []
        if r["role"] in FIXED_LOOPBACK0_ROLES:
            lo.append(f"loopback0={fixed_loopback0(site, r['name'], r['role'])}/{LOOPBACK_PLEN} (fixed)")
        else:
            if r["role"] not in NO_LOOPBACK0_ROLES:
                lo.append(f"loopback0 (pool {site}-LOOPBACK0)")
            if r["role"] in LOOPBACK1_ROLES:
                lo.append(f"loopback1 (pool {site}-LOOPBACK1-VTEP)")
            if r["role"] == "bgw":
                lo.append(f"{MS_VIP_IF}={SITE_INFRA[site]}.{MS_VIP_THIRD}.{MS_VIP_HOST}/{LOOPBACK_PLEN} (fixed, on BGW1)" if r["name"].endswith("1") else f"{MS_VIP_IF} (VIP read from BGW1)")
        asn = {"fabric": fabric_asn, "dci": fabric_asn + DCI_ASN_OFFSET, "internal_fw": fabric_asn + INTERNAL_FW_ASN_OFFSET,
               "internet_fw": fabric_asn + INTERNET_FW_ASN_OFFSET,
               "sdwan_fw": fabric_asn + SDWAN_FW_ASN_OFFSET, "vpn_fw": fabric_asn + VPN_FW_ASN_OFFSET, "isp": ISP_ROUTER_ASN, "cloud": fabric_asn + CLOUD_ASN_OFFSET,
               None: "-"}[r["asn"]]
        print(f"  {r['name']:<12} {r['role']:<13} {r['platform']:<17} {str(OS_VERSION_BY_PLATFORM.get(r['platform'])):<9} {str(asn):<6} "
              f"{DEVICE_TYPE[r['role']]:<17} {(str(mgmt) + ('=' + rule_mgmt[r['name']] + ' (rule)' if r['name'] in rule_mgmt else ' (' + mgmt_pool + ')')) if mgmt else 'None':<40} {', '.join(lo)}")
    print(f"--- spine-leaf links: {len(links)} (/{P2P_PLEN} from pool {site}-FABRIC-P2P, identifier <spine>-<spine port>)")
    print(f"--- switch pairs ({len(pairs)}): name | kind | domain id | members | anycast VTEP")
    for p in pairs:
        print(f"  {p['name']:<24} {p['kind']:<16} {str(p['domain_id']):<5} {p['members'][0]} + {p['members'][1]}  "
              f"anycast={'pool ' + site + '-ANYCAST-VTEP' if p['nve'] and p['domain_id'] else '-'}")
    print(f"--- vPC plumbing ({len(domains)} domains): peer-link {VPC_PEER_LINK} members | keepalive (/{P2P_PLEN} pool {site}-VPC-KEEPALIVE) | backup SVI (/{P2P_PLEN} by rule in {site}-VPC-BACKUP)")
    for d in domains:
        print(f"  {d['name']:<24} {', '.join(d['peer_link']):<52} {d['keepalive']:<13} {VPC_BACKUP_SVI + ' ' + str(rule_backup[d['name']]) if d['backup'] else 'none'}")
    print(f"--- switch ports toward attached devices ({len(attach_ports)}): device | port | port-channel | description")
    for ap in attach_ports:
        print(f"  {ap['device']:<10} {ap['port']:<13} {ap['po']:<16} {ap['far_port_desc']}")
    print(f"--- attachments ({len(attachments)}): name | VLAN | /{ATTACHMENT_PLEN} | leaf pair | .1 | .2 | .3 (FW, no interface) | FW units")
    for a in attachments:
        m1, m2 = a["members"]
        print(f"  {a['name']:<22} {a['vlan']} {a['prefix']:<16} {a['pair']:<20} {m1} Vlan{a['vlan']}={a['addrs'][m1]} "
              f"{m2} Vlan{a['vlan']}={a['addrs'][m2]} fw={a['addrs']['fw']} {'+'.join(a['devices'])}")
    e = edge_vlan_addresses(site)
    print(f"--- edge VLAN {EDGE_VLAN} standalone addresses: VRRP VIP {e['vip']}/{EDGE_VLAN_PLEN}, INETFW outside {e['inetfw']}/{EDGE_VLAN_PLEN} (no interface yet)")
    print(f"--- ISP routers ({len(isp)}): loopback0 | bundle + members -> edge | sub-interface address | cross-link | carrier /{CARRIER_PLEN} (ours / theirs, provider)")
    for r in isp:
        edge = ", ".join(f"{p}->{far} {fp}" for p, far, fp in r["edge_ports"])
        x, c = r["crosslink"], r["carrier"]
        print(f"  {r['name']:<9} lo0={r['loopback0']}/{LOOPBACK_PLEN} | {r['bundle']}: {edge} | {r['subif']}={r['subif_addr']}/{EDGE_VLAN_PLEN} | "
              f"{x['port']}={x['addr']}/{P2P_PLEN} <-> {x['peer']} | {c['port']}={c['ours']}/{CARRIER_PLEN} ({c['theirs']} {c['provider']})")
    print(f"--- L2-only VLANs ({len(l2vlans)}): name | VLAN | pair | prefix (from the IP plan, must exist)")
    for v in l2vlans:
        print(f"  {v['name']:<22} {v['vlan']:<5} {v['pair']:<22} {v['prefix']}")
    print(f"--- FortiGate interfaces: unit | ports | aggregates | HA | sub-interfaces (address on unit 1) | vlinks")
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
        print(f"--- Multi-Site + DCI: VIP {dci['vip']}/{LOOPBACK_PLEN} | BGW <-> DCI links | DCI pair link | circuits (IP plan)")
        for l in dci["links"]:
            print(f"  {l['bgw']} {l['bgw_port']}={l['bgw_addr']}/{P2P_PLEN} (upstream) <-> {l['dci']} {l['dci_port']}={l['dci_addr']}/{P2P_PLEN} (core)  {l['prefix']}")
        p = dci["pair"]
        print(f"  {p['po']} [{', '.join(p['members'])}] " + " ".join(f"{d}={a}/{P2P_PLEN}" for d, a in p["addrs"].items()) + f"  {p['prefix']}")
        for c in dci["circuits"]:
            print(f"  {c['dci']} {c['port']}={c['addr']}/{c['plen']} ({c['name']}, {ROLE_DCI_CIRCUIT}) -> {c['far']}  {c['prefix']}")
    if cloud:
        print(f"--- cloud routers ({len(cloud)}): loopback0 | bundle + members -> BL | TRANSIT sub-interface | cross-link | "
              f"cloud-exchange port: handoffs (ours, provider side; /30 from the IP plan)")
        for r in cloud:
            fab = ", ".join(f"{p}->{far} {fp}" for p, far, fp in r["fabric_ports"])
            x = r["crosslink"]
            hs = ", ".join(f"{h['subif']}={h['ours']}/{h['plen']} ({h['provider']} {h['theirs']})" for h in r["handoffs"])
            print(f"  {r['name']:<11} lo0={r['loopback0']}/{LOOPBACK_PLEN} | {r['bundle']}: {fab} | {r['subif']}={r['subif_addr']}/{ATTACHMENT_PLEN} ({r['attachment']}) | "
                  f"{x['port']}={x['addr']}/{P2P_PLEN} <-> {x['peer']} | {r['xc']['port']}: {hs}")
    total_if = sum(len(v) for v in expected.values())
    print(f"--- artifact groups: " + ", ".join(f"{role}->{g}" for role, g in ARTIFACT_GROUPS.items()))
    print(f"--- totals: devices {len(plan)}, interfaces owned {total_if}, pairs {len(pairs)}, attachments {len(attachments)}, "
          f"fixed prefixes {len(attachments) + 1 + len(isp)} (/{ATTACHMENT_PLEN}s, cross-link /{P2P_PLEN}, carrier /{CARRIER_PLEN}s), "
          f"L2-only VLANs {len(l2vlans)}")


class FabricSiteGenerator(InfrahubGenerator):
    async def allocate(self, pool, identifier: str, data: dict):
        """Same identifier -> same address on every run."""
        address = await self.client.allocate_next_ip_address(resource_pool=pool, identifier=identifier, data=data)
        if address is None:
            raise ValueError(f"{identifier}: pool {pool.name.value} returned nothing")
        return address

    async def allocate_p31(self, pool, identifier: str, description: str, site):
        """Same identifier -> same /31 on every run; `site` set after allocation (the pool call drops it)."""
        prefix = await self.client.allocate_next_ip_prefix(
            resource_pool=pool, identifier=identifier, data={"description": description, "status": LINK_STATUS})
        if prefix is None:
            raise ValueError(f"{identifier}: pool {pool.name.value} returned nothing")
        prefix.site = site
        await prefix.save()
        network = ipaddress.ip_network(str(prefix.prefix.value))
        if network.prefixlen != P2P_PLEN:
            raise ValueError(f"{identifier}: expected a /{P2P_PLEN} (p2p_link_prefix_length), got {network}")
        return network

    async def pool_block(self, pool):
        """The one prefix a pool hands out from (its resource), as an ip_network."""
        await pool.resources.fetch()
        ids = [p.id for p in pool.resources.peers]
        if len(ids) != 1:
            raise ValueError(f"pool {pool.name.value}: expected 1 resource prefix, found {len(ids)}")
        prefix = await self.client.get(kind="IpamPrefix", id=ids[0])
        return ipaddress.ip_network(str(prefix.prefix.value))

    async def rule_address(self, ip: str, plen: int, description: str, namespace):
        """A mgmt address by rule (upsert by value); same description and status as a pool-allocated one."""
        return await self.upsert("IpamIPAddress", {
            "address": f"{ip}/{plen}", "description": description, "status": MGMT0_ADDRESS_STATUS, "ip_namespace": namespace.id})

    async def upsert(self, kind: str, data: dict):
        obj = await self.client.create(kind=kind, data=data)
        await obj.save(allow_upsert=True)
        return obj

    async def address(self, ip: str, plen: int, description: str, namespace):
        """An address by value (upsert): links, attachments, edge VLAN, carriers, ISP loopbacks."""
        return await self.upsert("IpamIPAddress", {
            "address": f"{ip}/{plen}", "description": description, "status": LINK_STATUS, "ip_namespace": namespace.id})

    async def p31_address(self, ip, device_name: str, ifname: str, namespace):
        return await self.address(str(ip), P2P_PLEN, f"{device_name} {ifname}", namespace)

    async def fixed_prefix(self, prefix: str, description: str, site, namespace, role: str | None = None):
        """A prefix by value (fixed-slot /29s, cross-link /31s, carrier /30s), no pool.

        Get-or-create (not upsert): the same prefix keeps its id on every run.
        An existing prefix is saved on every run, changed or not: save() registers it in the generator's
        tracking group; an unsaved prefix is treated as unused and deleted at the end of the run.
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

    async def port_channel(self, device, name: str, description: str, role: str, mtu: int | None, members, l2_mode: str | None = None,
                           addresses=None):
        data = {"name": name, "device": device.id, "description": description, "role": role}
        if mtu is not None:                     # None: no MTU on the object (vPC peer-link)
            data["mtu"] = mtu
        data.update({"status": INTERFACE_STATUS, "members": [m.id for m in members]})
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
        """Delete what exists in the generator's scope but is not in the plan."""
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
        """VRF name -> node (id, Internal FW slot and flag) for every VRF that lists this site (GraphQL, one request)."""
        r = await self.client.execute_graphql(
            query="query { FabricVRF { edges { node { id name { value } internal_fw_slot { value } internal_fw_attached { value } "
                  "sites { edges { node { name { value } } } } } } } }")
        out = {}
        for e in r["FabricVRF"]["edges"]:
            n = e["node"]
            if site_name in [s["node"]["name"]["value"] for s in n["sites"]["edges"]]:
                out[n["name"]["value"]] = n
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

        # Design values from Infrahub (a missing value stops the run here, before anything is written)
        configure(await load_design(self.client), site_name)

        plan = build_plan(site_name, counts["vpc_leaf_pairs"], counts["esi_leaf_pairs"], counts["single_leaves"])
        vrf_nodes = await self.site_vrfs(site_name)
        configure_vrfs(vrf_nodes)
        vrfs = {name: n["id"] for name, n in vrf_nodes.items()}

        # Resolve every reference once; get() raises if the object is missing.
        site = await self.client.get(kind="LocationSite", name__value=site_name)
        platforms = {name: await self.client.get(kind="DcimPlatform", name__value=name)
                     for name in {r["platform"] for r in plan}}
        types = {name: await self.client.get(kind="DcimDeviceType", name__value=name) for name in set(DEVICE_TYPE.values())}
        configure_hardware(types, platforms)
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
        rule_mgmt = rule_mgmt_addresses(plan, await self.pool_block(pools["mgmt0"]))
        rule_backup = rule_backup_networks(domains, await self.pool_block(backup_pool))

        if DRY_RUN:
            print_plan(site_name, fabric_asn, plan, links, pairs, domains, attach_ports, attachments, isp, expected, list(vrfs), l2vlans, fws, dci, cloud,
                       rule_mgmt, rule_backup)
            print("DRY RUN: nothing written")
            return

        # ---- rule mgmt addresses first, so a pool allocation below can never take one of them
        mgmt_plen = pools["mgmt0"].default_prefix_length.value
        fixed_mgmt = {}
        for name, ip in rule_mgmt.items():
            mgmt_if = MGMT_IF_BY_ROLE[next(r["role"] for r in plan if r["name"] == name)]
            fixed_mgmt[name] = await self.rule_address(ip, mgmt_plen, f"{name} {mgmt_if}", namespace)

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
            mgmt_if = MGMT_IF_BY_ROLE[role]
            if name in fixed_mgmt:
                mgmt_address = fixed_mgmt[name]
                payload["primary_address"] = mgmt_address.id
            elif role not in NO_MGMT_ROLES and mgmt_if:
                mgmt_address = await self.allocate(pools["mgmt0"], f"{name}-{mgmt_if}",
                                                   {"description": f"{name} {mgmt_if}", "status": MGMT0_ADDRESS_STATUS})
                if str(mgmt_address.address.value).split("/")[0] in rule_mgmt.values():
                    raise ValueError(f"{name}: pool {pools['mgmt0'].name.value} returned rule address {mgmt_address.address.value}")
                payload["primary_address"] = mgmt_address.id
            device = await self.upsert("DcimDevice", payload)
            devices[name] = device
            report = []
            if mgmt_address is not None:
                await self.physical(device, mgmt_if, MGMT0_DESCRIPTION, MGMT0_ROLE, addresses=[mgmt_address])
                report.append(f"{mgmt_if}={mgmt_address.address.value}")

            if role in FIXED_LOOPBACK0_ROLES:
                lo_ip = fixed_loopback0(site_name, name, role)
                lo = await self.address(lo_ip, LOOPBACK_PLEN, f"{name} loopback0", namespace)
                await self.virtual(device, "loopback0", LOOPBACK0_DESCRIPTION, addresses=[lo])
                report.append(f"loopback0={lo.address.value}")
            else:
                loopbacks = []
                if role not in NO_LOOPBACK0_ROLES:
                    loopbacks.append(("loopback0", LOOPBACK0_DESCRIPTION))
                if role in LOOPBACK1_ROLES:
                    loopbacks.append(("loopback1", LOOPBACK1_DESCRIPTION))
                for ifname, description in loopbacks:
                    address = await self.allocate(pools[ifname], f"{name}-{ifname}", {"description": f"{name} {ifname}", "status": MGMT0_ADDRESS_STATUS})
                    await self.virtual(device, ifname, description, addresses=[address])
                    report.append(f"{ifname}={address.address.value}")
            if role == "bgw":
                vip = [await self.address(dci["vip"], LOOPBACK_PLEN, f"{site_name} Multi-Site VIP (anycast BGW pair, address on {name})", namespace)] \
                    if name.endswith("1") else []
                await self.virtual(device, MS_VIP_IF, MS_VIP_DESCRIPTION, addresses=vip)
                report.append(f"{MS_VIP_IF}={dci['vip'] + '/' + str(LOOPBACK_PLEN) if vip else '(VIP on BGW1)'}")
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
            ends = ((link["spine"], link["spine_port"], link["leaf"], network[FABRIC_LINK_END_HOST["spine"]]),
                    (link["leaf"], link["leaf_port"], link["spine"], network[FABRIC_LINK_END_HOST["leaf"]]))
            for device_name, ifname, peer, ip in ends:
                address = await self.p31_address(ip, device_name, ifname, namespace)
                await self.physical(devices[device_name], ifname, peer, FABRIC_ROLE, mtu=MTU["spine_leaf"], addresses=[address])
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
                    address = await self.allocate(pools["anycast"], pair["name"], {"description": f"{pair['name']} anycast VTEP", "status": MGMT0_ADDRESS_STATUS})
                    data["anycast_vtep"] = address.id
                    anycast = str(address.address.value)
            pair_objs[pair["name"]] = await self.upsert(pair["kind"], data)
            print(f"{pair['name']:<24} {pair['kind']:<16} id={str(pair['domain_id']):<5} {m1} + {m2}  anycast={anycast}")

        # ---- vPC plumbing on every vPC domain: keepalive, peer-link, backup SVI (NVE pairs only)
        for d in domains:
            m1, m2 = d["members"]
            ka_net = await self.allocate_p31(keepalive_pool, f"{m1}-{d['keepalive']}", f"{d['name']} vPC peer-keepalive", site)
            bk_net = None
            if d["backup"]:                                                       # by rule, not from the pool
                bk_net = rule_backup[d["name"]]
                await self.fixed_prefix(str(bk_net), f"{d['name']} vPC backup underlay", site, namespace)
            for device_name, peer, k in ((m1, m2, 1), (m2, m1, 2)):        # k = vPC member 1 / 2
                device = devices[device_name]
                far = (lambda port: f"{peer} {port}") if d["role"] in PEER_PORT_ROLES else (lambda port: peer)
                ka_ip = ka_net[VPC_KEEPALIVE_MEMBER_HOST[k]]
                ka_addr = await self.p31_address(ka_ip, device_name, d["keepalive"], namespace)
                await self.physical(device, d["keepalive"], far(d["keepalive"]), ROLE_KEEPALIVE, mtu=MTU["vpc_keepalive"], addresses=[ka_addr])
                bk_ip = bk_net[VPC_BACKUP_MEMBER_HOST[k]] if bk_net is not None else None
                if bk_net is not None:
                    bk_addr = await self.p31_address(bk_ip, device_name, VPC_BACKUP_SVI, namespace)
                    await self.virtual(device, VPC_BACKUP_SVI, VPC_BACKUP_DESCRIPTION, role=ROLE_BACKUP, mtu=MTU["vpc_backup"], addresses=[bk_addr])
                # peer-link members and port-channel: no MTU on the objects (NX-OS refused a non-default peer-link MTU)
                members = [await self.physical(device, ifname, far(ifname), ROLE_PEER_LINK) for ifname in d["peer_link"]]
                await self.port_channel(device, VPC_PEER_LINK, far(VPC_PEER_LINK), ROLE_PEER_LINK, None, members)
                print(f"{device_name:<10} {d['keepalive']}={ka_ip}/{P2P_PLEN} "
                      f"{VPC_BACKUP_SVI + '=' + str(bk_ip) + '/' + str(P2P_PLEN) if bk_net is not None else 'no backup SVI':<24} "
                      f"{VPC_PEER_LINK}=[{', '.join(d['peer_link'])}] peer={peer}")

        # ---- switch ports toward attached devices: one member port + its port-channel (vPC number = Po number)
        for ap in attach_ports:
            device = devices[ap["device"]]
            member = await self.physical(device, ap["port"], ap["far_port_desc"], ROLE_PEERING, mtu=MTU["attachment"], l2_mode=ATTACH_L2_MODE)
            await self.port_channel(device, ap["po"], ap["far_po_desc"], ROLE_PEERING, MTU["attachment"], [member], l2_mode=ATTACH_L2_MODE)
            print(f"{ap['device']:<10} {ap['port']:<13} -> {ap['far']:<12} {ap['po']}")

        # ---- attachments: prefix by slot, leaf SVIs (attachment_leaf_hosts: lower member, upper), FW side at attachment_device_host without interface
        for a in attachments:
            pfx = await self.fixed_prefix(a["prefix"], f"{a['name']} attachment /{ATTACHMENT_PLEN} (VLAN {a['vlan']})", site, namespace, role="link")
            for m in a["members"]:
                addr = await self.address(a["addrs"][m], ATTACHMENT_PLEN, f"{m} Vlan{a['vlan']} ({a['name']})", namespace)
                await self.virtual(devices[m], f"Vlan{a['vlan']}", a["name"], role=ROLE_PEERING, mtu=MTU["attachment"], addresses=[addr])
            if not a["router"]:
                await self.address(a["addrs"]["fw"], ATTACHMENT_PLEN, f"{a['devices'][0][:-1]} {a['vrf']} (shared by both units; the interface is in the FortiGate config)", namespace)
            await self.upsert("FabricAttachment", {
                "name": a["name"], "slot": a["slot"], "vlan_id": a["vlan"],
                "description": f"{' + '.join(a['devices'])} in {a['vrf']} on {a['pair']}" if a["router"]
                               else f"{a['devices'][0][:-1]} pair in {a['vrf']} on {a['pair']}",
                "vrf": vrfs[a["vrf"]], "prefix": pfx.id, "switch_pair": pair_objs[a["pair"]].id,
                "devices": [devices[d].id for d in a["devices"]], "site": site.id})
            print(f"{a['name']:<24} VLAN {a['vlan']} {a['prefix']:<16} {a['members'][0]}={a['addrs'][a['members'][0]]} "
                  f"{a['members'][1]}={a['addrs'][a['members'][1]]} fw={a['addrs']['fw']}")

        # ---- edge VLAN standalone addresses: VRRP VIP and the Internet FW outside leg (no interface)
        edge = edge_vlan_addresses(site_name)
        await self.address(edge["vip"], EDGE_VLAN_PLEN, f"{site_name} edge VLAN {EDGE_VLAN} VRRP VIP (VRID {EDGE_VRRP_GROUP_ID})", namespace)
        await self.address(edge["inetfw"], EDGE_VLAN_PLEN, f"{site_name}-INETFW outside leg, edge VLAN {EDGE_VLAN} (shared by both units; the interface is in the FortiGate config)", namespace)

        # ---- L2-only VLANs: edge VLAN on the EDGE pair, DMZ VLANs on the DMZ pair
        for v in l2vlans:
            # The /24 belongs to the IP plan: fetched, never saved, so it never joins this generator's tracking group
            pfx = await self.client.get(kind="IpamPrefix", prefix__value=v["prefix"])   # raises if missing
            await self.upsert("FabricL2Vlan", {"name": v["name"], "vlan_id": v["vlan"], "description": v["description"],
                                               "switch_pair": pair_objs[v["pair"]].id, "site": site.id, "prefix": pfx.id})
            print(f"{v['name']:<22} VLAN {v['vlan']} {v['prefix']} on {v['pair']}")

        # ---- ISP routers: edge bundle + tagged sub-interface, iBGP cross-link /31, carrier /30
        for r in isp:
            device = devices[r["name"]]
            members = [await self.physical(device, port, f"{far} {far_port}", ROLE_PEERING, mtu=MTU["isp_edge"]) for port, far, far_port in r["edge_ports"]]
            po = f"port-channel{ATTACH_PORTS['edge'][r['n'] - 1][2]}"
            bundle = await self.port_channel(device, r["bundle"], f"{site_name}-EDGE1/EDGE2 {po}", ROLE_PEERING, MTU["isp_edge"], members)
            sub_addr = await self.address(r["subif_addr"], EDGE_VLAN_PLEN, f"{r['name']} {r['subif']}", namespace)
            await self.virtual(device, r["subif"], f"Edge VLAN {EDGE_VLAN}", role=ROLE_PEERING, mtu=MTU["isp_edge"], addresses=[sub_addr], parent=bundle)
            x = r["crosslink"]
            await self.fixed_prefix(x["prefix"], f"{site_name} ISP router iBGP cross-link", site, namespace, role="link")
            x_addr = await self.address(x["addr"], P2P_PLEN, f"{r['name']} {x['port']}", namespace)
            await self.physical(device, x["port"], f"{x['peer']} {x['port']}", FABRIC_ROLE, mtu=MTU["isp_crosslink"], addresses=[x_addr])
            c = r["carrier"]
            await self.fixed_prefix(c["prefix"], f"{r['name']} <-> {c['provider']} handoff", site, namespace, role="link")
            ours = await self.address(c["ours"], CARRIER_PLEN, f"{r['name']} {c['port']}", namespace)
            await self.address(c["theirs"], CARRIER_PLEN, f"{c['provider']} side of the {r['name']} handoff", namespace)
            await self.physical(device, c["port"], f"{c['provider']} carrier handoff", ROLE_UPSTREAM, mtu=MTU["carrier"], addresses=[ours])
            print(f"{r['name']:<9} lo0={r['loopback0']} {r['subif']}={r['subif_addr']} xlink={x['addr']}/{P2P_PLEN} "
                  f"carrier={c['ours']}/{CARRIER_PLEN} ({c['provider']} {c['theirs']})")

        # ---- FortiGate interfaces: ports, aggregates, HA ports, VLAN sub-interfaces, NPU vlinks (Internal FW)
        for fw in fws:
            device = devices[fw["name"]]
            unit_stem = fw["name"][:-1]
            members: dict = {}
            for p, desc, agg in fw["ports"]:
                members.setdefault(agg, []).append(await self.physical(device, p, desc, ROLE_PEERING, mtu=MTU["fw_attach"]))
            aggs = {agg: await self.port_channel(device, agg, desc, ROLE_PEERING, MTU["fw_attach"], members[agg]) for agg, desc in fw["aggs"].items()}
            for p, desc in fw["ha"]:
                await self.physical(device, p, desc, FABRIC_ROLE)
            for sub in fw["subifs"]:
                addrs = []
                if sub["addr"]:
                    addrs = [await self.address(sub["addr"][0], sub["addr"][1], f"{unit_stem} {sub['name']} (shared by both units)", namespace)]
                await self.virtual(device, sub["name"], sub["desc"], role=ROLE_PEERING, mtu=MTU["fw_attach"], addresses=addrs, parent=aggs[sub["parent"]])
            if fw["npu"]:
                npu = {link: await self.physical(device, link, "NPU inter-VDOM link (VRF leaking)", FABRIC_ROLE) for link in FW_NPU_LINKS}
                for x in fw["npu"]:
                    if fw["unit"] == 1:
                        await self.fixed_prefix(x["prefix"], f"{unit_stem} vlink {x['vrf']} <-> {TRANSIT_VRF}", site, namespace, role="link")
                    for side in ("tenant", "transit"):
                        e = x[side]
                        addrs = [await self.address(e["addr"], 30, f"{unit_stem} {e['name']} (shared by both units)", namespace)] if fw["unit"] == 1 else []
                        await self.virtual(device, e["name"], f"vlink {x['vrf']} <-> {TRANSIT_VRF}", role=FABRIC_ROLE, addresses=addrs, parent=npu[e["parent"]])
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

        # ---- BGW <-> DCI links (/31 fixed by rule), DCI pair link (routed port-channel), inter-site circuits
        for l in dci["links"]:
            await self.fixed_prefix(l["prefix"], f"{l['bgw']} <-> {l['dci']} link", site, namespace, role="link")
            a = await self.address(l["dci_addr"], P2P_PLEN, f"{l['dci']} {l['dci_port']}", namespace)
            await self.physical(devices[l["dci"]], l["dci_port"], f"{l['bgw']} {l['bgw_port']}", FABRIC_ROLE, mtu=MTU["bgw_dci"], addresses=[a])
            b = await self.address(l["bgw_addr"], P2P_PLEN, f"{l['bgw']} {l['bgw_port']}", namespace)
            await self.physical(devices[l["bgw"]], l["bgw_port"], f"{l['dci']} {l['dci_port']}", ROLE_UPSTREAM, mtu=MTU["bgw_dci"], addresses=[b])
            print(f"{l['bgw']:<9} {l['bgw_port']}={l['bgw_addr']}/{P2P_PLEN} <-> {l['dci']} {l['dci_port']}={l['dci_addr']}/{P2P_PLEN}")
        p = dci["pair"]
        await self.fixed_prefix(p["prefix"], f"{site_name} DCI switch iBGP link ({p['po']})", site, namespace, role="link")
        dci_names = list(p["addrs"])
        for dev in dci_names:
            peer = next(x for x in dci_names if x != dev)
            members = [await self.physical(devices[dev], m, f"{peer} {m}", FABRIC_ROLE, mtu=MTU["dci_pair"]) for m in p["members"]]
            a = await self.address(p["addrs"][dev], P2P_PLEN, f"{dev} {p['po']}", namespace)
            await self.port_channel(devices[dev], p["po"], f"{peer} {p['po']}", FABRIC_ROLE, MTU["dci_pair"], members, addresses=[a])
            print(f"{dev:<9} {p['po']}=[{', '.join(p['members'])}] {p['addrs'][dev]}/{P2P_PLEN} <-> {peer}")
        for c in dci["circuits"]:
            await self.client.get(kind="IpamPrefix", prefix__value=c["prefix"])   # owned by the IP plan; raises if missing
            a = await self.address(c["addr"], c["plen"], f"{c['dci']} {c['port']} ({c['name']})", namespace)
            await self.physical(devices[c["dci"]], c["port"], c["far"], ROLE_DCI_CIRCUIT, mtu=MTU["dci_circuit"], addresses=[a])
            print(f"{c['dci']:<9} {c['port']}={c['addr']}/{c['plen']} {c['name']} -> {c['far']}")
        # ---- cloud routers: fabric bundle + TRANSIT sub-interface, iBGP cross-link /31, cloud-exchange handoffs
        for r in cloud:
            device = devices[r["name"]]
            members = [await self.physical(device, port, f"{far} {far_port}", ROLE_PEERING, mtu=MTU["cloud_bundle"]) for port, far, far_port in r["fabric_ports"]]
            bundle = await self.port_channel(device, r["bundle"], f"{site_name}-BL1/BL2 {r['bl_po']}", ROLE_PEERING, MTU["cloud_bundle"], members)
            sub_addr = await self.address(r["subif_addr"], ATTACHMENT_PLEN, f"{r['name']} {r['subif']} ({r['attachment']})", namespace)
            await self.virtual(device, r["subif"], r["attachment"], role=ROLE_PEERING, mtu=MTU["cloud_bundle"], addresses=[sub_addr], parent=bundle)
            x = r["crosslink"]
            await self.fixed_prefix(x["prefix"], f"{site_name} cloud router iBGP link", site, namespace, role="link")
            x_addr = await self.address(x["addr"], P2P_PLEN, f"{r['name']} {x['port']}", namespace)
            await self.physical(device, x["port"], f"{x['peer']} {x['port']}", FABRIC_ROLE, mtu=MTU["cloud_crosslink"], addresses=[x_addr])
            xc = await self.physical(device, r["xc"]["port"], r["xc"]["desc"], ROLE_CLOUD, mtu=MTU["cloud_exchange"])
            for h in r["handoffs"]:
                await self.client.get(kind="IpamPrefix", prefix__value=h["prefix"])   # owned by the IP plan; raises if missing
                # the handoff length is h["plen"], the length of the dc*_cloud_aws_net_n / dc*_cloud_er_net_n field
                ours = await self.address(h["ours"], h["plen"], f"{r['name']} {h['subif']}", namespace)
                await self.address(h["theirs"], h["plen"], f"{h['provider']} side of the {r['name']} {h['subif']} handoff", namespace)
                await self.virtual(device, h["subif"], h["desc"], role=ROLE_CLOUD, mtu=MTU["cloud_handoff"], addresses=[ours], parent=xc)
            print(f"{r['name']:<11} lo0={r['loopback0']} {r['subif']}={r['subif_addr']} xlink={x['addr']}/{P2P_PLEN} "
                  + " ".join(f"{h['subif']}={h['ours']}/{h['plen']}" for h in r["handoffs"]))

        await self.reconcile(site_name, plan, links, pairs, domains, attach_ports, attachments, isp, fws, dci, cloud)

