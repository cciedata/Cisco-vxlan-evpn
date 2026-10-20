import logging

from infrahub_sdk import InfrahubClient

ADDRESS = {"default_address_type": "IpamIPAddress"}
P2P = {
    "default_prefix_length": 31,
    "default_member_type": "address",
    "default_prefix_type": "IpamPrefix",
}

POOLS = [
    {
        "kind": "CoreIPAddressPool",
        "name": "DC1-LOOPBACK0",
        "prefix": "10.19.0.0/24",
        "description": "DC1 loopback0: router ID, BGP peering",
        "settings": {**ADDRESS, "default_prefix_length": 32},
    },
    {
        "kind": "CoreIPAddressPool",
        "name": "DC1-LOOPBACK1-VTEP",
        "prefix": "10.19.1.0/24",
        "description": "DC1 loopback1: VTEP source (leaves and BGWs only)",
        "settings": {**ADDRESS, "default_prefix_length": 32},
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
        "settings": {**ADDRESS, "default_prefix_length": 24},
    },
    {
        "kind": "CoreIPAddressPool",
        "name": "DC2-LOOPBACK0",
        "prefix": "10.23.0.0/24",
        "description": "DC2 loopback0: router ID, BGP peering",
        "settings": {**ADDRESS, "default_prefix_length": 32},
    },
    {
        "kind": "CoreIPAddressPool",
        "name": "DC2-LOOPBACK1-VTEP",
        "prefix": "10.23.1.0/24",
        "description": "DC2 loopback1: VTEP source (leaves and BGWs only)",
        "settings": {**ADDRESS, "default_prefix_length": 32},
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
        "settings": {**ADDRESS, "default_prefix_length": 24},
    },
    # Phase 1: vPC plumbing
    {
        "kind": "CoreIPAddressPool",
        "name": "DC1-ANYCAST-VTEP",
        "prefix": "10.19.2.0/25",
        "description": "DC1 vPC anycast VTEPs: one per vPC domain with NVE",
        "settings": {**ADDRESS, "default_prefix_length": 32},
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
        "settings": {**ADDRESS, "default_prefix_length": 32},
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
    # Phase 4 PC6: OOB (generator oob_site allocates OOB-INFRA; OOB-BMC is for the server BMCs, no allocation yet)
    {
        "kind": "CoreIPAddressPool",
        "name": "DC1-OOB-INFRA",
        "prefix": "10.250.1.0/24",
        "description": "DC1 OOB infrastructure: console servers, OOB switches, OOB FW mgmt",
        "settings": {**ADDRESS, "default_prefix_length": 24},
    },
    {
        "kind": "CoreIPAddressPool",
        "name": "DC1-OOB-BMC",
        "prefix": "10.250.4.0/22",
        "description": "DC1 server BMCs (OOB VLAN 102)",
        "settings": {**ADDRESS, "default_prefix_length": 22},
    },
    {
        "kind": "CoreIPAddressPool",
        "name": "DC2-OOB-INFRA",
        "prefix": "10.250.17.0/24",
        "description": "DC2 OOB infrastructure: console servers, OOB switches, OOB FW mgmt",
        "settings": {**ADDRESS, "default_prefix_length": 24},
    },
    {
        "kind": "CoreIPAddressPool",
        "name": "DC2-OOB-BMC",
        "prefix": "10.250.20.0/22",
        "description": "DC2 server BMCs (OOB VLAN 102)",
        "settings": {**ADDRESS, "default_prefix_length": 22},
    },
]


async def run(client: InfrahubClient, log: logging.Logger, branch: str, **kwargs) -> None:
    namespace = await client.get(kind="IpamNamespace", name__value="default", branch=branch)
    for item in POOLS:
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
