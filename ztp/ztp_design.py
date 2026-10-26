#!/usr/bin/env python3
"""ZTP design values from Infrahub, shared by ztp_server.py and run-ztp.sh. A missing value raises ValueError naming the
field; nothing here has a fallback value. The branch is main unless --branch says otherwise (as labctl does), so a
proposed change can be checked on its own branch before it reaches main.

  FabricDesign  ztp_dhcp_subnet, ztp_server_host (gateway and ZTP server), ztp_pool_start_host / ztp_pool_end_host (Kea pool),
                ztp_verify_window_s / ztp_verify_poll_s (verification), <role>_mgmt_interface (the verified port), local_admin_username
  DcimPlatform  os_version of the device's platform (the verification compares 'show version' with it)

Command line (run-ztp.sh, lab venv):
  ztp_design.py net                        shell assignments ZTP_SUBNET ZTP_SERVER_IP ZTP_SERVER_CIDR (for eval)
  ztp_design.py kea <template> <out>       writes the Kea config: the template's __ZTP_*__ placeholders filled from FabricDesign
  ztp_design.py gateway <device>           <gateway>/<length> of the device's mgmt interface (from its startup-config), or ''

Every command takes an optional --branch <name> (default main).
"""
import ipaddress
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lab"))
import labctl          # noqa: E402  Infrahub client (login + GraphQL + artifact read)

NET_FIELDS = ("ztp_dhcp_subnet", "ztp_server_host", "ztp_pool_start_host", "ztp_pool_end_host")


def design_values(ih, *fields):
    """FabricDesign fields over GraphQL -> {field: value}; ValueError on a missing object or an empty field."""
    fd = ih.gql("{ FabricDesign { count edges { node { %s } } } }" % " ".join(f"{f} {{ value }}" for f in fields))["FabricDesign"]
    if fd["count"] != 1:
        raise ValueError(f"FabricDesign: expected exactly 1 object, found {fd['count']}; fix it in Infrahub")
    out = {}
    for f in fields:
        value = fd["edges"][0]["node"][f]["value"]
        if value is None:
            raise ValueError(f"FabricDesign.{f} is empty; fix it in Infrahub")
        out[f] = value
    return out


def device(ih, name=None, dev_id=None):
    """{id, name, role, platform, os_version} of one DcimDevice (by name or id); ValueError when it is not exactly one device."""
    flt = f'ids: ["{dev_id}"]' if dev_id else f'name__value: "{name}"'
    q = ('{ DcimDevice(%s) { edges { node { id name { value } role { value } '
         'platform { node { name { value } os_version { value } } } } } } }' % flt)
    edges = ih.gql(q)["DcimDevice"]["edges"]
    if len(edges) != 1:
        raise ValueError(f"DcimDevice {name or dev_id}: {len(edges)} matches")
    n = edges[0]["node"]
    plat = (n.get("platform") or {}).get("node") or {}
    return {"id": n["id"], "name": n["name"]["value"], "role": n["role"]["value"],
            "platform": (plat.get("name") or {}).get("value"), "os_version": (plat.get("os_version") or {}).get("value")}


def mgmt_interface(ih, dev):
    """FabricDesign.<role>_mgmt_interface of the device's role."""
    if not dev["role"]:
        raise ValueError(f"DcimDevice {dev['name']}.role is empty; fix it in Infrahub")
    return design_values(ih, f"{dev['role']}_mgmt_interface")[f"{dev['role']}_mgmt_interface"]


def os_version(dev):
    """DcimPlatform.os_version of the device's platform."""
    if not dev["platform"]:
        raise ValueError(f"DcimDevice {dev['name']}.platform is empty; fix it in Infrahub")
    if not dev["os_version"]:
        raise ValueError(f"DcimPlatform {dev['platform']}.os_version is empty; fix it in Infrahub")
    return dev["os_version"]


def ztp_net(values):
    """ZTP subnet, server address (= gateway) and Kea pool from the FabricDesign values (host offsets into ztp_dhcp_subnet)."""
    net = ipaddress.ip_network(str(values["ztp_dhcp_subnet"]))

    def host(field):
        ip = net.network_address + int(values[field])
        if ip not in net:
            raise ValueError(f"FabricDesign.{field} = {values[field]} is outside ztp_dhcp_subnet {net}; fix it in Infrahub")
        return str(ip)
    return {"subnet": str(net), "prefixlen": net.prefixlen, "server_ip": host("ztp_server_host"),
            "pool": f"{host('ztp_pool_start_host')} - {host('ztp_pool_end_host')}"}


def kea_config(template, net):
    """Fill the __ZTP_*__ placeholders of ztp/kea-dhcp4.json; ValueError when one is left over."""
    out = (template.replace("__ZTP_SUBNET__", net["subnet"]).replace("__ZTP_POOL__", net["pool"])
           .replace("__ZTP_SERVER_IP__", net["server_ip"]))
    left = sorted(set(re.findall(r"__ZTP_\w+?__", out)))
    if left:
        raise ValueError(f"kea template: placeholders not filled: {left}")
    return out


def mgmt_address(cfg, ifname):
    """IPv4 address of the interface block '<ifname>' in a rendered NX-OS config, or None."""
    m = re.search(r"^interface %s\n(?:  .*\n)*?\s+ip address (\d+\.\d+\.\d+\.\d+)/\d+" % re.escape(ifname), cfg, re.M)
    return m.group(1) if m else None


def mgmt_gateway(cfg, ifname):
    """'<gateway>/<length>' from the management VRF default route and the mgmt interface prefix length, or ''."""
    gw = re.search(r'^vrf context management\n(?:  .*\n)*?  ip route 0\.0\.0\.0/0 (\S+)', cfg, re.M)
    ip = re.search(r'^interface %s\n(?:  .*\n)*?  ip address \S+/(\d+)' % re.escape(ifname), cfg, re.M)
    return f'{gw.group(1)}/{ip.group(1)}' if gw and ip else ''


def running_version(show_version):
    """Release from NX-OS 'show version' in the DcimPlatform.os_version form: 'NXOS: version 10.6(1) [Feature Release]' -> 10.6(1)F
    (the first letter of the bracketed release type is appended when the version itself does not end in a letter). None when
    there is no 'NXOS: version' line."""
    m = re.search(r"^\s*NXOS:\s+version\s+(\S+)(?:\s+\[(\w)\w*\s+Release\])?", show_version, re.M)
    if not m:
        return None
    ver, kind = m.group(1), m.group(2)
    return ver + kind.upper() if kind and not ver[-1].isalpha() else ver


def main(argv):
    branch = "main"
    if "--branch" in argv:
        at = argv.index("--branch")
        if at + 1 >= len(argv):
            print("--branch needs a branch name"); return 2
        branch = argv[at + 1]
        argv = argv[:at] + argv[at + 2:]
    ih = labctl.Infrahub(branch)
    if argv[:1] == ["net"] and len(argv) == 1:
        net = ztp_net(design_values(ih, *NET_FIELDS))
        print(f"ZTP_SUBNET={net['subnet']} ZTP_SERVER_IP={net['server_ip']} ZTP_SERVER_CIDR={net['server_ip']}/{net['prefixlen']}")
    elif argv[:1] == ["kea"] and len(argv) == 3:
        out = kea_config(open(argv[1]).read(), ztp_net(design_values(ih, *NET_FIELDS)))
        with open(argv[2], "w") as f:
            f.write(out)
    elif argv[:1] == ["gateway"] and len(argv) == 2:
        dev = device(ih, name=argv[1])
        cfg, _ = ih.artifact("startup-config", dev["name"])
        print(mgmt_gateway(cfg, mgmt_interface(ih, dev)))
    else:
        print(__doc__.split("Command line")[1]); return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ValueError as e:
        labctl.die(str(e))
