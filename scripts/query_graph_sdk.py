import logging

from infrahub_sdk import InfrahubClient


async def run(client: InfrahubClient, log: logging.Logger, branch: str, **kwargs) -> None:
    device_name = kwargs["device"]
    vrf_name = kwargs["vrf"]

    # Request 1: the device. get() raises if nothing matches (raise_when_missing=True).
    device = await client.get(kind="DcimDevice", name__value=device_name, branch=branch)
    asn = None
    if device.asn.id:  # untested path: a device without an ASN
        await device.asn.fetch()  # request 2: the ASN object
        asn = device.asn.peer
    print(
        f"{device.name.value}: role {device.role.value}, "
        f"ASN {asn.asn.value if asn else 'none'}"
    )

    # Request 3: the VRF. Request 4: its segments, filtered from the segment side.
    vrf = await client.get(kind="FabricVRF", name__value=vrf_name, branch=branch)
    segments = await client.filters(
        kind="FabricSegment", vrf__ids=[vrf.id], include=["sites"], branch=branch
    )
    print(
        f"VRF {vrf.name.value}: L3 VNI {vrf.l3_vni.value}, "
        f"multisite {vrf.multisite.value}, {len(segments)} segment(s)"
    )
    for segment in segments:
        await segment.prefix.fetch()  # one request per segment
        await segment.sites.fetch()  # and another per segment
        sites = ", ".join(rel.peer.name.value for rel in segment.sites.peers)
        print(
            f"  {segment.name.value}: VLAN {segment.vlan_id.value}, "
            f"L2 VNI {segment.l2_vni.value}, "
            f"prefix {segment.prefix.peer.prefix.value}, sites {sites}"
        )
