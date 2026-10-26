import ipaddress
import re

from infrahub_sdk.checks import InfrahubCheck

SITE_FIELD = re.compile(r"^(dc\d+)_vpc_domain_base$")
# Further per-site families (dcN_<field>) that must not overlap between sites
PREFIX_FAMILIES = (
    ("infra blocks", ("infra_block",)),
    ("tenant blocks", ("tenant_block",)),
    ("OOB WAN prefixes", ("oob_wan_prefix",)),
    ("SD-WAN overlays", ("sdwan_overlay",)),
    ("WAN endpoints", ("sdwan_fw_wan_endpoint", "vpn_fw_wan_endpoint")),
    ("cloud networks", ("cloud_aws_net_1", "cloud_aws_net_2", "cloud_er_net_1", "cloud_er_net_2")),
)
CLOUD_VLAN_FIELDS = ("cloud_aws_vlan_1", "cloud_aws_vlan_2", "cloud_er_vlan")
FW_HA_ROLES = ("internal_fw", "internet_fw", "oob_fw", "sdwan_fw", "vpn_fw")   # FW HA group id = <role>_ha_group_base + site offset


class DesignOverlapCheck(InfrahubCheck):
    """Per-site FabricDesign values (dc1_/dc2_ ...) must not overlap between sites.

    vPC domain ranges (base + 1 .. base + span), OOB vPC domains, OOB third octets (block base + VLAN/keepalive/RA carving),
    carrier /30 indexes (base .. base + isp_router_count - 1), infra / tenant blocks, OOB WAN prefixes, SD-WAN overlays,
    WAN endpoints, cloud networks and VLANs, and FW HA group ids (<role>_ha_group_base + site offset) are compared site
    against site. The vPC domains in use (FabricVpcDomain) must sit in their own site's range or be that site's OOB vPC domain.
    """

    query = "design_overlap_check"

    def validate(self, data):
        designs = data["FabricDesign"]
        if designs["count"] != 1:
            self.log_error(
                message=f"FabricDesign: expected exactly 1 object, found {designs['count']}; fix it in Infrahub",
                object_type="FabricDesign",
            )
            return
        node = designs["edges"][0]["node"]
        design_id = node["name"]["value"]
        missing = []

        def need(field):
            value = (node.get(field) or {}).get("value")
            if value is None:
                missing.append(field)
            return value

        sites = sorted(m.group(1) for key in node if (m := SITE_FIELD.match(key)))
        span = need("vpc_domain_slot_span")
        first_server = need("vpc_first_server_slot")
        first_extra = need("vpc_first_extra_server_slot")
        fixed_slots = need("vpc_fixed_slots")
        max_leaves = need("max_server_leaves")
        oob_vlans = need("oob_vlans")
        ka_third = need("oob_keepalive_third_octet")
        ra_third = need("oob_ra_pool_third_octet")
        ra_len = need("oob_ra_pool_prefix_length")
        isp_count = need("isp_router_count")
        carrier_block = need("carrier_block")
        carrier_len = need("carrier_link_prefix_length")
        per_site = {s: {f: need(f"{s}_{f}") for f in ("vpc_domain_base", "oob_vpc_domain", "oob_block_base", "carrier_index_base")}
                    for s in sites}
        family_fields = [f for _, fields in PREFIX_FAMILIES for f in fields] + list(CLOUD_VLAN_FIELDS) + ["fw_ha_group_offset"]
        fam = {s: {f: need(f"{s}_{f}") for f in family_fields} for s in sites}
        ha_bases = {r: need(f"{r}_ha_group_base") for r in FW_HA_ROLES}
        if missing:
            for field in missing:
                self.log_error(message=f"FabricDesign.{field} is empty; fix it in Infrahub", object_id=design_id,
                               object_type="FabricDesign")
            return

        def octets(third, plen):
            return set(range(third, third + (2 ** (24 - plen) if plen <= 24 else 1)))

        # Per-site sets, all from FabricDesign values
        vpc_range, oob_octets, carrier_idx, fabric_ids = {}, {}, {}, {}
        for s, v in per_site.items():
            base = v["vpc_domain_base"]
            vpc_range[s] = set(range(base + 1, base + span + 1))
            slots = {first_server, *fixed_slots.values()} | {first_extra + k for k in range(max_leaves // 2 - 1)}
            fabric_ids[s] = {base + slot for slot in slots}
            ob = v["oob_block_base"]
            used = set()
            for vlan in oob_vlans:
                used |= octets(ob + vlan["third_offset"], vlan["prefix_length"])
            used |= octets(ob + ka_third, 24) | octets(ob + ra_third, ra_len)
            oob_octets[s] = used
            cb = v["carrier_index_base"]
            carrier_idx[s] = set(range(cb, cb + isp_count))

        def report(field, text):
            self.log_error(message=f"FabricDesign.{field}: {text}", object_id=design_id, object_type="FabricDesign")

        for s, v in per_site.items():
            extra = sorted(i for i in fabric_ids[s] if i not in vpc_range[s])
            if extra:
                report(f"{s}_vpc_domain_base", f"fabric slots give domain IDs {extra} outside {s}'s range")
            if min(oob_octets[s]) < 0 or max(oob_octets[s]) > 255:
                report(f"{s}_oob_block_base", f"OOB third octets {min(oob_octets[s])}-{max(oob_octets[s])} leave 0-255")
            slots_in_block = 2 ** (carrier_len - ipaddress.ip_network(carrier_block).prefixlen)
            outside = sorted(i for i in carrier_idx[s] if not 0 <= i < slots_in_block)
            if outside:
                report(f"{s}_carrier_index_base", f"carrier indexes {outside} do not fit in {carrier_block} ({slots_in_block} slots)")
            if v["oob_vpc_domain"] in fabric_ids[s]:
                report(f"{s}_oob_vpc_domain", f"{v['oob_vpc_domain']} is also a fabric vPC domain ID in {s}")

        for i, a in enumerate(sites):
            for b in sites[i + 1:]:
                pairs = (
                    ("vpc_domain_base", vpc_range[a] & vpc_range[b], "vPC domain ranges overlap on"),
                    ("oob_block_base", oob_octets[a] & oob_octets[b], "OOB third octets overlap on"),
                    ("carrier_index_base", carrier_idx[a] & carrier_idx[b], "carrier /30 indexes overlap on"),
                )
                for field, common, text in pairs:
                    if common:
                        shown = sorted(common) if len(common) <= 10 else f"{min(common)}-{max(common)} ({len(common)} values)"
                        report(f"{a}_{field} / {b}_{field}", f"{text} {shown}")
                oa, ob = per_site[a]["oob_vpc_domain"], per_site[b]["oob_vpc_domain"]
                if oa == ob:
                    report(f"{a}_oob_vpc_domain / {b}_oob_vpc_domain", f"same OOB vPC domain {oa} in both sites")
                if oa in vpc_range[b]:
                    report(f"{a}_oob_vpc_domain", f"{oa} is inside {b}'s vPC domain range")
                if ob in vpc_range[a]:
                    report(f"{b}_oob_vpc_domain", f"{ob} is inside {a}'s vPC domain range")

        # infra / tenant blocks, OOB WAN, SD-WAN overlay, WAN endpoints, cloud networks: no prefix of one site overlaps the other's
        for i, a in enumerate(sites):
            for b in sites[i + 1:]:
                for family, fields in PREFIX_FAMILIES:
                    for fa in fields:
                        for fb in fields:
                            na, nb = ipaddress.ip_network(str(fam[a][fa])), ipaddress.ip_network(str(fam[b][fb]))
                            if na.overlaps(nb):
                                report(f"{a}_{fa} / {b}_{fb}", f"{family} overlap: {na} and {nb}")
                common = {fam[a][f] for f in CLOUD_VLAN_FIELDS} & {fam[b][f] for f in CLOUD_VLAN_FIELDS}
                if common:
                    report(f"{a}_cloud_*_vlan / {b}_cloud_*_vlan", f"cloud VLANs overlap on {sorted(common)}")
                ids_a = {base + fam[a]["fw_ha_group_offset"] for base in ha_bases.values()}
                ids_b = {base + fam[b]["fw_ha_group_offset"] for base in ha_bases.values()}
                if ids_a & ids_b:
                    report(f"{a}_fw_ha_group_offset / {b}_fw_ha_group_offset", f"FW HA group ids overlap on {sorted(ids_a & ids_b)}")

        # vPC domains in use: site from the name prefix (DC1-...), id inside that site's range or its OOB domain
        for edge in data["FabricVpcDomain"]["edges"]:
            name = edge["node"]["name"]["value"]
            did = edge["node"]["domain_id"]["value"]
            site = name.split("-", 1)[0].lower()
            if site not in per_site:
                self.log_error(message=f"{name}: no FabricDesign.{site}_vpc_domain_base for site {site.upper()}",
                               object_id=name, object_type="FabricVpcDomain")
                continue
            if did not in vpc_range[site] and did != per_site[site]["oob_vpc_domain"]:
                self.log_error(message=f"{name}: domain ID {did} is outside {site.upper()}'s range and is not its OOB vPC domain",
                               object_id=name, object_type="FabricVpcDomain")
