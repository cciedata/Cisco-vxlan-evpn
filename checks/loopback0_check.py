from infrahub_sdk.checks import InfrahubCheck


class Loopback0Check(InfrahubCheck):
    """Every device in a site's fabric ASN has exactly one loopback0 with exactly one address."""

    query = "loopback_check"

    def validate(self, data):
        site_name = self.params.get("name", "unknown")
        sites = data["LocationSite"]

        if sites["count"] != 1:
            self.log_error(
                message=f"{site_name}: expected exactly 1 site, the query returned {sites['count']}",
                object_id=site_name,
                object_type="LocationSite",
            )
            return

        site = sites["edges"][0]["node"]
        fabric_asn = (site.get("fabric_asn") or {}).get("node")
        if not fabric_asn:
            self.log_error(
                message=f"{site_name}: site has no fabric_asn",
                object_id=site_name,
                object_type="LocationSite",
            )
            return

        asn = fabric_asn["asn"]["value"]
        for edge in fabric_asn["devices"]["edges"]:
            device = edge["node"]
            name = device["name"]["value"]
            role = device["role"]["value"]
            loopbacks = device["interfaces"]

            if loopbacks["count"] != 1:
                self.log_error(
                    message=f"{name} ({role}, AS{asn}): expected exactly 1 loopback0, found {loopbacks['count']}",
                    object_id=name,
                    object_type="DcimDevice",
                )
                continue

            addresses = loopbacks["edges"][0]["node"].get("ip_addresses") or {"count": 0, "edges": []}
            if addresses["count"] != 1:
                values = [a["node"]["address"]["value"] for a in addresses["edges"]]
                self.log_error(
                    message=f"{name} ({role}, AS{asn}): loopback0 must have exactly 1 address, found {addresses['count']} {values}",
                    object_id=name,
                    object_type="DcimDevice",
                )
