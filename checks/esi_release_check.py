import re

from infrahub_sdk.checks import InfrahubCheck

# NX-OS release "10.6(1)F" -> (10, 6, 1): major.minor(maintenance); the trailing release-type letter is not part of the order
RELEASE = re.compile(r"^(\d+)\.(\d+)\((\d+)\)")


def release_key(text):
    m = RELEASE.match(text or "")
    return tuple(int(x) for x in m.groups()) if m else None


class EsiReleaseCheck(InfrahubCheck):
    """Every member of a FabricEsiPair runs (DcimPlatform.os_version of its platform) at least the lowest release that
    supports ESI multihoming on its hardware (DcimDeviceType.esi_min_release). A missing value fails the check."""

    query = "esi_release_check"

    def validate(self, data):
        for pair_edge in data["FabricEsiPair"]["edges"]:
            pair = pair_edge["node"]
            pair_name = pair["name"]["value"]
            for edge in pair["members"]["edges"]:
                dev = edge["node"]
                name = dev["name"]["value"]

                def report(text):
                    self.log_error(message=f"{name} (ESI pair {pair_name}): {text}", object_id=name, object_type="DcimDevice")

                dtype = (dev.get("device_type") or {}).get("node")
                platform = (dev.get("platform") or {}).get("node")
                if not dtype:
                    report("no device_type; fix it in Infrahub")
                    continue
                if not platform:
                    report("no platform; fix it in Infrahub")
                    continue
                minimum = dtype["esi_min_release"]["value"]
                running = platform["os_version"]["value"]
                if minimum is None:
                    report(f"DcimDeviceType {dtype['name']['value']}.esi_min_release is empty; fix it in Infrahub")
                    continue
                if running is None:
                    report(f"DcimPlatform {platform['name']['value']}.os_version is empty; fix it in Infrahub")
                    continue
                k_min, k_run = release_key(minimum), release_key(running)
                if k_min is None or k_run is None:
                    report(f"cannot compare releases: os_version '{running}', esi_min_release '{minimum}' (expected N.N(N)...)")
                elif k_run < k_min:
                    report(f"DcimPlatform {platform['name']['value']}.os_version {running} is below "
                           f"DcimDeviceType {dtype['name']['value']}.esi_min_release {minimum}")
