import ipaddress
import os
import re

import yaml
from infrahub_sdk.checks import InfrahubCheck

SITE_FIELD = re.compile(r"^(dc\d+)_infra_block$")   # sites = the dcN_ prefixes present in the query
ARTIFACT_GROUP_FIELD = re.compile(r"^\w+_artifact_group$")   # every <role>_artifact_group field present in the query
# .infrahub.yml cannot read Infrahub, so its artifact targets are compared with the *_artifact_group fields here
REPO_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".infrahub.yml")

# FabricDesign fields that copy an IP plan prefix. Per-site fields (dcN_...) must also match the prefix's owner
# site when the prefix has one. dci_circuits (dict values) and external_guardrail_prefixes (list) hold several prefixes.
PREFIX_FIELDS = ("carrier_block", "cloud_aws_block", "cloud_azure_block", "oob_supernet", "sdwan_branch_summary")
SITE_PREFIX_FIELDS = ("cloud_aws_net_1", "cloud_aws_net_2", "cloud_er_net_1", "cloud_er_net_2", "infra_block", "tenant_block",
                      "sdwan_fw_wan_endpoint", "vpn_fw_wan_endpoint")
PREFIX_COLLECTION_FIELDS = ("dci_circuits", "external_guardrail_prefixes")
# Rack U fields that copy a LocationRackReservation position, by the rack set the reservation sits in
NETWORK_RACK_U_FIELDS = ("cloud_router_rack_u", "vpn_fw_rack_u", "sdwan_fw_rack_u")
OOB_RACK_U_FIELDS = ("console_server_rack_u", "oob_access_rack_u", "oob_agg_rack_u", "oob_fw_rack_u")
PDU_TYPE_FIELDS = ("pdu_type_30a", "pdu_type_60a")
BMC_LABEL = "BMC switch"   # reservation label written in objects/08-racks.yml for the BMC access switch


class DesignCopyCheck(InfrahubCheck):
    """FabricDesign values that copy existing objects must equal them.

    IP plan prefixes (IpamPrefix, owner site for dcN_ fields), the OOB zone gateways (oob_gateway_host: an IpamIPAddress
    of the IP plan in every site and OOB zone), rack reservations (LocationRackReservation position in the
    network / OOB / server racks), the rack names and purposes behind network_racks / oob_racks / network_patch_racks,
    and the PDU device types (DcimDeviceType names).
    Also the device-group targets of the .infrahub.yml artifact definitions equal the *_artifact_group values, and
    the VRF in vpn_attachment_slots equals partner_vrf.
    """

    query = "design_copy_check"

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

        def report(field, text):
            self.log_error(message=f"FabricDesign.{field}: {text}", object_id=design_id, object_type="FabricDesign")

        def value(field):
            v = (node.get(field) or {}).get("value")
            if v is None:
                report(field, "is empty; fix it in Infrahub")
            return v

        racks = {e["node"]["name"]["value"]: e["node"]["purpose"]["value"] for e in data["LocationRack"]["edges"]}
        sites = sorted(m.group(1).upper() for key in node if (m := SITE_FIELD.match(key)))

        # ---- IP plan prefixes
        owners = {}
        for edge in data["IpamPrefix"]["edges"]:
            p = edge["node"]
            site = ((p.get("site") or {}).get("node") or {}).get("name", {}).get("value")
            owners.setdefault(p["prefix"]["value"], set()).add(site)

        def prefix_exists(field, prefix, site=None):
            if prefix not in owners:
                report(field, f"{prefix} is not an IpamPrefix in the IP plan")
            elif site and owners[prefix] - {None} and site not in owners[prefix]:
                report(field, f"{prefix} is owned by {sorted(owners[prefix] - {None})}, not {site}")

        for field in PREFIX_FIELDS:
            v = value(field)
            if v is not None:
                prefix_exists(field, v)
        for field in PREFIX_COLLECTION_FIELDS:
            v = value(field)
            if v is not None:
                for prefix in (v.values() if isinstance(v, dict) else v):
                    prefix_exists(field, prefix)
        for site in sites:
            for base in SITE_PREFIX_FIELDS:
                field = f"{site.lower()}_{base}"
                v = value(field)
                if v is not None:
                    prefix_exists(field, v, site)

        # ---- OOB zone gateways: <oob_supernet> + (dcN_oob_block_base + third_offset) << 8, host oob_gateway_host
        addresses = {e["node"]["address"]["value"] for e in data["IpamIPAddress"]["edges"]}
        supernet = (node.get("oob_supernet") or {}).get("value")          # emptiness already reported above
        gw_host, oob_vlans = value("oob_gateway_host"), value("oob_vlans")
        if supernet is not None and gw_host is not None and oob_vlans is not None:
            for site in sites:
                base = value(f"{site.lower()}_oob_block_base")
                if base is None:
                    continue
                for v in oob_vlans:
                    third = ipaddress.ip_network(supernet).network_address + ((base + v["third_offset"]) << 8)
                    gw = f"{ipaddress.ip_network(f'{third}/{v['prefix_length']}')[gw_host]}/{v['prefix_length']}"
                    if gw not in addresses:
                        report("oob_gateway_host", f"{site} OOB zone {v['zone']}: gateway {gw} is not an IpamIPAddress in the IP plan")

        # ---- racks and rack reservations
        reserved = {}
        for edge in data["LocationRackReservation"]["edges"]:
            r = edge["node"]
            rack = ((r.get("rack") or {}).get("node") or {}).get("name", {}).get("value")
            reserved.setdefault(rack, []).append((r["position"]["value"], r["label"]["value"]))

        rack_sets = {}
        for field, purpose in (("network_racks", "network"), ("oob_racks", "oob"), ("network_patch_racks", "patch")):
            v = value(field)
            if v is None:
                continue
            shorts = list(v.values()) if isinstance(v, dict) else list(v)
            rack_sets[field] = shorts
            for site in sites:
                for short in shorts:
                    name = f"{site}-{short}"
                    if name not in racks:
                        report(field, f"rack {name} does not exist")
                    elif racks[name] != purpose:
                        report(field, f"rack {name} has purpose {racks[name]}, expected {purpose}")

        def reservation_at(field, rack_shorts):
            u = value(field)
            if u is None:
                return
            for site in sites:
                for short in rack_shorts:
                    name = f"{site}-{short}"
                    if name in racks and u not in [pos for pos, _ in reserved.get(name, [])]:
                        report(field, f"no rack reservation at U{u} in {name}")

        for field in NETWORK_RACK_U_FIELDS:
            reservation_at(field, rack_sets.get("network_racks", []))
        for field in OOB_RACK_U_FIELDS:
            reservation_at(field, rack_sets.get("oob_racks", []))
        server_racks = sorted({n.split("-", 1)[1] for n, purpose in racks.items() if purpose == "server"})
        reservation_at("rack_local_panel_u", server_racks)

        bmc_u = value("bmc_switch_rack_u")
        if bmc_u is not None:
            bmc = [(rack, pos) for rack, items in reserved.items() for pos, label in items if label == BMC_LABEL]
            if not bmc:
                report("bmc_switch_rack_u", f"no rack reservation labelled '{BMC_LABEL}' to compare with")
            for rack, pos in bmc:
                if pos != bmc_u:
                    report("bmc_switch_rack_u", f"{rack} reserves the BMC switch at U{pos}, FabricDesign says U{bmc_u}")

        # ---- PDU device types
        device_types = {e["node"]["name"]["value"] for e in data["DcimDeviceType"]["edges"]}
        for field in PDU_TYPE_FIELDS:
            v = value(field)
            if v is not None and v not in device_types:
                report(field, f"'{v}' is not a DcimDeviceType")

        # ---- .infrahub.yml artifact targets = the *_artifact_group values. Site groups (the targets of the generator
        # definitions) are not device groups and are left out.
        groups = {}
        for field in sorted(k for k in node if ARTIFACT_GROUP_FIELD.match(k)):
            v = value(field)
            if v is not None:
                groups.setdefault(v, []).append(field)
        try:
            repo = yaml.safe_load(open(REPO_CONFIG))
        except OSError as e:
            report("*_artifact_group", f"cannot read .infrahub.yml to compare its artifact targets: {e}")
        else:
            site_groups = {g["targets"] for g in repo.get("generator_definitions", [])}
            targets = {}
            for a in repo.get("artifact_definitions", []):
                if a["targets"] not in site_groups:
                    targets.setdefault(a["targets"], []).append(a["name"])
            for group, fields in sorted(groups.items()):
                if group not in targets:
                    report(fields[0], f"'{group}' is not the target of any artifact definition in .infrahub.yml")
            for group, names in sorted(targets.items()):
                if group not in groups:
                    report("*_artifact_group", f".infrahub.yml artifact definitions {names} target '{group}', "
                                               "which no FabricDesign *_artifact_group field holds")

        # ---- The VRF named in vpn_attachment_slots (pair -> [leaf pair, VRF, slot]) is partner_vrf
        slots, partner_vrf = value("vpn_attachment_slots"), value("partner_vrf")
        if slots is not None and partner_vrf is not None:
            for pair, slot in sorted(slots.items()):
                if slot[1] != partner_vrf:
                    report("vpn_attachment_slots", f"{pair} names VRF '{slot[1]}', partner_vrf is '{partner_vrf}'")
