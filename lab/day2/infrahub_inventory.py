#!/usr/bin/env python3
"""Day-2 inventory, live from Infrahub (Open-Phase3-Lab, decided 7 Oct: inventory live from Infrahub).

One query, two consumers:
  Nornir:  from day2.infrahub_inventory import devices        -> [{name, host, platform, role, site}]
  Ansible: infrahub_inventory.py --list                       -> dynamic-inventory JSON (groups: nxos, site_<SITE>, role_<role>)
           (ansible-inventory -i lab/day2/ansible/inventory.sh --graph)

Scope: devices whose platform is Cisco NX-OS (the Day-2 push target), with their primary (management) address.
Filters: DAY2_SITE / --site, DAY2_DEVICES / --devices (comma-separated). Nothing is typed per device.

Snapshot (FU-17, auto-approved 7 Oct): laptop 1 cannot run Infrahub and an N9Kv together, so
  infrahub_inventory.py --site DC1 --devices DC1-L11 --export <dir>   writes the inventory and each startup-config artifact
and DAY2_CACHE=<dir> makes both tools read that snapshot instead of Infrahub (same data; the live path is the default).
"""
import argparse
import json
import os
import sys

import httpx

INFRAHUB = os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000")     # = labctl (P3D-03)
INFRAHUB_USER = os.environ.get("INFRAHUB_USERNAME", "admin")
INFRAHUB_PASS = os.environ.get("INFRAHUB_PASSWORD", "infrahub")
PLATFORM = "Cisco NX-OS"                                                    # objects/04-hardware.yml
NETMIKO = {"Cisco NX-OS": "cisco_nxos"}                                     # netmiko device_type / Nornir platform
ANSIBLE_OS = {"Cisco NX-OS": "cisco.nxos.nxos"}                             # ansible_network_os


def client():
    http = httpx.Client(base_url=INFRAHUB, timeout=120)
    r = http.post("/api/auth/login", json={"username": INFRAHUB_USER, "password": INFRAHUB_PASS})
    r.raise_for_status()
    http.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    return http


CACHE = os.environ.get("DAY2_CACHE")


def devices(site=None, names=None, branch="main"):
    if CACHE:
        snap = json.load(open(os.path.join(CACHE, "inventory.json")))
        out = [d for d in snap if (not site or d["site"] == site) and (not names or d["name"] in names)]
        if names and len(out) != len(names):
            sys.exit(f"STOP: not in the snapshot {CACHE}: {sorted(set(names) - {d['name'] for d in out})}")
        return out
    q = """query($platform: String!) { DcimDevice(platform__name__value: $platform) { edges { node {
             name { value } role { value } location { node { name { value } } }
             platform { node { name { value } } } primary_address { node { address { value } } } } } } }"""
    r = client().post(f"/graphql/{branch}", json={"query": q, "variables": {"platform": PLATFORM}})
    d = r.json()
    if d.get("errors"):
        sys.exit(f"STOP: GraphQL: {d['errors'][0].get('message')}")
    out = []
    for e in d["data"]["DcimDevice"]["edges"]:
        n = e["node"]
        dev = {"name": n["name"]["value"], "role": n["role"]["value"], "site": n["location"]["node"]["name"]["value"],
               "platform": n["platform"]["node"]["name"]["value"],
               "host": (n["primary_address"]["node"] or {}).get("address", {}).get("value", "").split("/")[0]}
        if site and dev["site"] != site:
            continue
        if names and dev["name"] not in names:
            continue
        if not dev["host"]:
            sys.exit(f"STOP: {dev['name']} has no primary address in Infrahub")
        out.append(dev)
    if names and len(out) != len(names):
        sys.exit(f"STOP: not found in Infrahub as {PLATFORM}: {sorted(set(names) - {d['name'] for d in out})}")
    return sorted(out, key=lambda x: x["name"])


def ansible_json(devs):
    inv = {"_meta": {"hostvars": {}}, "nxos": {"hosts": []}}
    for d in devs:
        inv["nxos"]["hosts"].append(d["name"])
        for g in (f"site_{d['site']}", f"role_{d['role']}"):
            inv.setdefault(g, {"hosts": []})["hosts"].append(d["name"])
        inv["_meta"]["hostvars"][d["name"]] = {"ansible_host": d["host"], "ansible_network_os": ANSIBLE_OS[d["platform"]],
                                               "infrahub_site": d["site"], "infrahub_role": d["role"]}
    return inv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--host")
    ap.add_argument("--site", default=os.environ.get("DAY2_SITE"))
    ap.add_argument("--devices", default=os.environ.get("DAY2_DEVICES"))
    ap.add_argument("--export", metavar="DIR", help="write a snapshot (inventory + startup-config artifacts) for DAY2_CACHE")
    a = ap.parse_args()
    devs = devices(a.site, a.devices.split(",") if a.devices else None)
    if a.export:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import labctl
        ih = labctl.Infrahub("main")
        os.makedirs(os.path.join(a.export, "configs"), exist_ok=True)
        sums = {}
        for d in devs:
            text, art = ih.artifact("startup-config", d["name"])
            open(os.path.join(a.export, "configs", f"{d['name']}.cfg"), "w").write(text)
            sums[d["name"]] = art["checksum"]
        json.dump(devs, open(os.path.join(a.export, "inventory.json"), "w"), indent=1)
        json.dump(sums, open(os.path.join(a.export, "checksums.json"), "w"), indent=1)
        print(f"snapshot {a.export}: {len(devs)} devices, {len(sums)} artifacts")
        return
    if a.host:
        print(json.dumps(ansible_json(devs)["_meta"]["hostvars"].get(a.host, {})))
    else:
        print(json.dumps(ansible_json(devs), indent=1))


if __name__ == "__main__":
    main()
