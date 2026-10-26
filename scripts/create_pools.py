import logging

from infrahub_sdk import InfrahubClient

ADDRESS = {"default_address_type": "IpamIPAddress"}


def need(design, field):
    attr = getattr(design, field, None)
    value = None if attr is None else attr.value
    if value is None:
        raise ValueError(f"FabricDesign.{field} is empty; fix it in Infrahub")
    return value


def pools(design) -> list[dict]:
    """The pools, with their lengths and the BMC VLAN id from FabricDesign: loopback / anycast addresses
    loopback_prefix_length, /31 prefix pools p2p_link_prefix_length, MGMT0 / OOB-INFRA / OOB-BMC the oob_vlans
    prefix_length of zone NETMGMT / OOBINFRA / BMC."""
    lo_len = need(design, "loopback_prefix_length")
    zones = {v["zone"]: v for v in need(design, "oob_vlans")}
    for z in ("NETMGMT", "OOBINFRA", "BMC"):
        if z not in zones:
            raise ValueError(f"FabricDesign.oob_vlans has no zone {z}; fix it in Infrahub")
    mgmt_len, infra_len = zones["NETMGMT"]["prefix_length"], zones["OOBINFRA"]["prefix_length"]
    bmc_len, bmc_vlan = zones["BMC"]["prefix_length"], zones["BMC"]["vlan"]
    P2P = {
        "default_prefix_length": need(design, "p2p_link_prefix_length"),
        "default_member_type": "address",
        "default_prefix_type": "IpamPrefix",
    }
    return [
        {
            "kind": "CoreIPAddressPool",
            "name": "DC1-LOOPBACK0",
            "prefix": "10.19.0.0/24",
            "description": "DC1 loopback0: router ID, BGP peering",
            "settings": {**ADDRESS, "default_prefix_length": lo_len},
        },
        {
            "kind": "CoreIPAddressPool",
            "name": "DC1-LOOPBACK1-VTEP",
            "prefix": "10.19.1.0/24",
            "description": "DC1 loopback1: VTEP source (leaves and BGWs only)",
            "settings": {**ADDRESS, "default_prefix_length": lo_len},
        },
        {
            "kind": "CoreIPPrefixPool",
            "name": "DC1-FABRIC-P2P",
            "prefix": "10.19.4.0/22",
            "description": "DC1 fabric p2p links: one /31 per link",
            "settings": P2P,
        },
        {
            "kind": "CoreIPAddressPool",
            "name": "DC1-MGMT0",
            "prefix": "10.250.0.0/24",
            "description": "DC1 mgmt0 on the OOB network",
            "settings": {**ADDRESS, "default_prefix_length": mgmt_len},
        },
        {
            "kind": "CoreIPAddressPool",
            "name": "DC2-LOOPBACK0",
            "prefix": "10.23.0.0/24",
            "description": "DC2 loopback0: router ID, BGP peering",
            "settings": {**ADDRESS, "default_prefix_length": lo_len},
        },
        {
            "kind": "CoreIPAddressPool",
            "name": "DC2-LOOPBACK1-VTEP",
            "prefix": "10.23.1.0/24",
            "description": "DC2 loopback1: VTEP source (leaves and BGWs only)",
            "settings": {**ADDRESS, "default_prefix_length": lo_len},
        },
        {
            "kind": "CoreIPPrefixPool",
            "name": "DC2-FABRIC-P2P",
            "prefix": "10.23.4.0/22",
            "description": "DC2 fabric p2p links: one /31 per link",
            "settings": P2P,
        },
        {
            "kind": "CoreIPAddressPool",
            "name": "DC2-MGMT0",
            "prefix": "10.250.16.0/24",
            "description": "DC2 mgmt0 on the OOB network",
            "settings": {**ADDRESS, "default_prefix_length": mgmt_len},
        },
        # vPC plumbing
        {
            "kind": "CoreIPAddressPool",
            "name": "DC1-ANYCAST-VTEP",
            "prefix": "10.19.2.0/25",
            "description": "DC1 vPC anycast VTEPs: one per vPC domain with NVE",
            "settings": {**ADDRESS, "default_prefix_length": lo_len},
        },
        {
            "kind": "CoreIPPrefixPool",
            "name": "DC1-VPC-BACKUP",
            "prefix": "10.19.3.0/24",
            "description": "DC1 vPC backup underlay SVI: one /31 per vPC pair with NVE (server, BL, SL)",
            "settings": P2P,
        },
        {
            "kind": "CoreIPPrefixPool",
            "name": "DC1-VPC-KEEPALIVE",
            "prefix": "10.19.17.0/24",
            "description": "DC1 vPC peer-keepalive link: one /31 per vPC pair (server, BL, SL, EDGE, DMZ)",
            "settings": P2P,
        },
        {
            "kind": "CoreIPAddressPool",
            "name": "DC2-ANYCAST-VTEP",
            "prefix": "10.23.2.0/25",
            "description": "DC2 vPC anycast VTEPs: one per vPC domain with NVE",
            "settings": {**ADDRESS, "default_prefix_length": lo_len},
        },
        {
            "kind": "CoreIPPrefixPool",
            "name": "DC2-VPC-BACKUP",
            "prefix": "10.23.3.0/24",
            "description": "DC2 vPC backup underlay SVI: one /31 per vPC pair with NVE (server, BL, SL)",
            "settings": P2P,
        },
        {
            "kind": "CoreIPPrefixPool",
            "name": "DC2-VPC-KEEPALIVE",
            "prefix": "10.23.17.0/24",
            "description": "DC2 vPC peer-keepalive link: one /31 per vPC pair (server, BL, SL, EDGE, DMZ)",
            "settings": P2P,
        },
        # OOB (generator oob_site allocates OOB-INFRA; OOB-BMC is for the server BMCs, no allocation yet)
        {
            "kind": "CoreIPAddressPool",
            "name": "DC1-OOB-INFRA",
            "prefix": "10.250.1.0/24",
            "description": "DC1 OOB infrastructure: console servers, OOB switches, OOB FW mgmt",
            "settings": {**ADDRESS, "default_prefix_length": infra_len},
        },
        {
            "kind": "CoreIPAddressPool",
            "name": "DC1-OOB-BMC",
            "prefix": "10.250.4.0/22",
            "description": f"DC1 server BMCs (OOB VLAN {bmc_vlan})",
            "settings": {**ADDRESS, "default_prefix_length": bmc_len},
        },
        {
            "kind": "CoreIPAddressPool",
            "name": "DC2-OOB-INFRA",
            "prefix": "10.250.17.0/24",
            "description": "DC2 OOB infrastructure: console servers, OOB switches, OOB FW mgmt",
            "settings": {**ADDRESS, "default_prefix_length": infra_len},
        },
        {
            "kind": "CoreIPAddressPool",
            "name": "DC2-OOB-BMC",
            "prefix": "10.250.20.0/22",
            "description": f"DC2 server BMCs (OOB VLAN {bmc_vlan})",
            "settings": {**ADDRESS, "default_prefix_length": bmc_len},
        },
    ]


async def run(client: InfrahubClient, log: logging.Logger, branch: str, **kwargs) -> None:
    designs = await client.all(kind="FabricDesign", branch=branch)
    if len(designs) != 1:
        raise ValueError(f"FabricDesign: expected exactly 1 object, found {len(designs)}; load design/fabric-design.yml first")
    namespace = await client.get(kind="IpamNamespace", name__value="default", branch=branch)
    for item in pools(designs[0]):
        prefix = await client.get(kind="IpamPrefix", prefix__value=item["prefix"], branch=branch)
        pool = await client.create(
            kind=item["kind"],
            branch=branch,
            name=item["name"],
            description=item["description"],
            resources=[prefix],
            ip_namespace=namespace,
            **item["settings"],
        )
        await pool.save(allow_upsert=True)
        print(f"{item['kind']} {pool.name.value}: id={pool.id} source={item['prefix']} {item['settings']}")
