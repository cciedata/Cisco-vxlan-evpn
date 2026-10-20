import logging
from pathlib import Path

from infrahub_sdk import InfrahubClient

QUERIES = Path(__file__).resolve().parent.parent / "queries"


def one(result: dict, kind: str, name: str) -> dict:
    """Return the single object of a top-level result; stop if there isn't exactly one."""
    count = result[kind]["count"]
    if count != 1:
        raise ValueError(f"{kind} '{name}': expected 1 object, got {count}")
    return result[kind]["edges"][0]["node"]


async def run(client: InfrahubClient, log: logging.Logger, branch: str, **kwargs) -> None:
    device_name = kwargs["device"]
    vrf_name = kwargs["vrf"]

    # Query 1: device -> ASN (cardinality one)
    result = await client.execute_graphql(
        query=(QUERIES / "device_asn.gql").read_text(),
        variables={"device": device_name},
        branch_name=branch,
    )
    device = one(result, "DcimDevice", device_name)
    asn = device["asn"]["node"]  # None when the device has no ASN
    print(
        f"{device['name']['value']}: role {device['role']['value']}, "
        f"ASN {asn['asn']['value'] if asn else 'none'}"
    )

    # Query 2: VRF -> segments -> prefix, sites (cardinality many, two hops)
    result = await client.execute_graphql(
        query=(QUERIES / "vrf_segments.gql").read_text(),
        variables={"vrf": vrf_name},
        branch_name=branch,
    )
    vrf = one(result, "FabricVRF", vrf_name)
    print(
        f"VRF {vrf['name']['value']}: L3 VNI {vrf['l3_vni']['value']}, "
        f"multisite {vrf['multisite']['value']}, {vrf['segments']['count']} segment(s)"
    )
    for edge in vrf["segments"]["edges"]:
        segment = edge["node"]
        sites = ", ".join(site["node"]["name"]["value"] for site in segment["sites"]["edges"])
        print(
            f"  {segment['name']['value']}: VLAN {segment['vlan_id']['value']}, "
            f"L2 VNI {segment['l2_vni']['value']}, "
            f"prefix {segment['prefix']['node']['prefix']['value']}, sites {sites}"
        )
