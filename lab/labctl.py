#!/usr/bin/env python3
"""Lab driver: build a containerlab directory from Infrahub artifacts, then deploy, carve, destroy.

  labctl.py fetch    <site>   pull the site's lab artifacts from Infrahub into lab/build/<site>/ (topology, configs, expectations)
  labctl.py validate <site>   offline checks on the build directory: files, links, mgmt addresses, images, host memory
  labctl.py deploy   <site>   containerlab deploy (refuses when validate fails, unless --force); N9Kv boot in groups with a
                              boot watchdog that redeploys the site once when an N9Kv hangs in the vrnetlab bootstrap
  labctl.py push     <site>   8 Oct: after a minimal-boot deploy, push every N9Kv's full config (NX-API, refused blocks over SSH), save
  labctl.py carve    <site>   waits (max 60 min) until every N9Kv is healthy, then ARP-suppression TCAM carve + reset + config re-push on every NX-OS node whose config uses suppress-arp
  labctl.py destroy  <site>   containerlab destroy --cleanup
  labctl.py status   <site>   containerlab inspect

  <site> may be DC1-full / DC2-full (option 5): the entire site from the lab-topology-full artifact, in lab/build/<site>-full/.

Every value here is a lab setting chosen for this lab.
Nothing is typed by hand per device: node list, kinds, files and links come from the lab-topology artifact.
"""
import argparse
import calendar
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import warnings

import httpx
import yaml

import vamd_secrets  # delivery-time secrets (lab/vamd_secrets.py)

warnings.filterwarnings("ignore")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(REPO, "lab", "build")

# ---------------------------------------------------------------- approved constants
INFRAHUB = os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000")              # the local Infrahub instance
INFRAHUB_TOKEN = os.environ.get("INFRAHUB_API_TOKEN", "")                           # as infrahubctl: the token comes first
INFRAHUB_USER = os.environ.get("INFRAHUB_USERNAME", "")                             # username/password only when no token is set
INFRAHUB_PASS = os.environ.get("INFRAHUB_PASSWORD", "")
NXOS_USER, NXOS_PASS = "admin", "admin"                                             # vrnetlab N9Kv default login (lab only)
LAB_NXOS_LINES = ["feature nxapi"]                                                 # NX-API is lab-only, added at deploy.
                                                                                   # feature scp-server is rendered by the production
                                                                                   # templates (FabricDesign.nxos_scp_server)
CARVE = ["hardware access-list tcam region racl 512",                              # racl first, else
         "hardware access-list tcam region arp-ether 256 double-wide"]              # "exceeded the available Ingress TCAM slices"
HEALTHY_WAIT_S = 3600                                                                  # 8 Oct: carve waits up to 60 min for every N9Kv
HEALTHY_POLL_S = 30
BOOT_TOP = ("hostname ", "username ", "feature nxapi", "feature scp-server", "feature ssh")   # 8 Oct: minimal vrnetlab boot config
BOOT_BLOCKS = ("interface mgmt0", "vrf context management")
CARVE_TRIGGER = "suppress-arp"                                                      # carve nodes = configs that use it
QEMU_MONITOR_PORT = 4000                                                            # vrnetlab QEMU monitor inside the container
SKIP_BLOCKS = ("interface mgmt0", "vrf context management")                         # mgmt is set by containerlab
NODE_MEM_MB = {"cisco_n9kv": None, "cisco_xrd": 2048, "linux": 256}                 # n9kv from QEMU_MEMORY; XRd/linux estimates
HOST_RESERVE_MB = 2048                                                              # kept free for the host
ARTIFACT_EXT = {"lab-topology": "yml", "lab-topology-full": "yml", "lab-expected": "json"}
# Staged N9Kv boot + boot watchdog (one lab run: 20 N9Kv booted at once, DC1-S2 hung in the vrnetlab bootstrap
# at 'feature telnet'; vrnetlab only catches EOFError, so the node never became healthy; a site redeploy recovered it)
BOOT_GROUP_SIZE = 5           # N9Kv per boot group, in topology order; 0 = all at once (deploy --group-size)
#                               group k+1 is created once every N9Kv of group k is healthy (containerlab stages)
BOOT_STUCK_S = 600            # an N9Kv that is not healthy and whose vrnetlab log has not moved for 10 min is hung
BOOT_RETRIES = 1              # on a hang, destroy and redeploy the site this many times, then stop
BOOT_POLL_S = 30


def die(msg, code=1):
    print(f"STOP: {msg}", flush=True)
    sys.exit(code)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- Infrahub
class Infrahub:
    def __init__(self, branch):
        self.branch = branch
        self.http = httpx.Client(base_url=INFRAHUB, timeout=120)
        # The repo's compose generates the admin password into .env, so the old demo admin/infrahub default gave
        # "401 Incorrect password" with nothing in the docs saying otherwise (repo-fixes-pending #6). A token is the
        # documented way to reach Infrahub, so it comes first; username/password still work when both are set.
        if INFRAHUB_TOKEN:
            self.http.headers["X-INFRAHUB-KEY"] = INFRAHUB_TOKEN
        elif INFRAHUB_USER and INFRAHUB_PASS:
            r = self.http.post("/api/auth/login", json={"username": INFRAHUB_USER, "password": INFRAHUB_PASS})
            if r.status_code != 200:
                die(f"Infrahub login failed: {r.status_code} {r.text[:200]}")
            self.http.headers["Authorization"] = "Bearer " + r.json()["access_token"]
        else:
            die("set INFRAHUB_API_TOKEN (as for infrahubctl), or INFRAHUB_USERNAME and INFRAHUB_PASSWORD")
        self._index = None

    def gql(self, query):
        r = self.http.post(f"/graphql/{self.branch}", json={"query": query})
        d = r.json()
        if d.get("errors"):
            die(f"GraphQL: {d['errors'][0].get('message')}")
        return d["data"]

    def index(self):
        """(artifact name, target name) -> {id, status, storage_id, checksum}, for every artifact on the branch."""
        if self._index is None:
            q = """{ CoreArtifact { edges { node { id name { value } status { value } storage_id { value } checksum { value }
                     object { node { display_label ... on DcimDevice { name { value } } ... on LocationSite { name { value } } } } } } } }"""
            self._index = {}
            for e in self.gql(q)["CoreArtifact"]["edges"]:
                n = e["node"]
                tgt = ((n.get("object") or {}).get("node") or {}).get("name", {}).get("value")
                self._index[(n["name"]["value"], tgt)] = {"id": n["id"], "status": n["status"]["value"],
                                                          "storage_id": n["storage_id"]["value"], "checksum": n["checksum"]["value"]}
        return self._index

    def artifact(self, name, target):
        a = self.index().get((name, target))
        if not a:
            die(f"no artifact '{name}' for {target} on branch {self.branch}")
        if a["status"] != "Ready" or not a["storage_id"]:
            die(f"artifact '{name}' for {target} is {a['status']} (storage {a['storage_id']})")
        r = self.http.get(f"/api/storage/object/{a['storage_id']}", params={"branch": self.branch})
        if r.status_code != 200:
            die(f"storage read for '{name}' {target}: {r.status_code}")
        return r.text, a


# ---------------------------------------------------------------- build directory
def site_dir(site):
    return os.path.join(BUILD, site)


def load_topology(site):
    path = os.path.join(site_dir(site), "topology.yml")
    if not os.path.exists(path):
        die(f"{path} missing: run 'labctl.py fetch {site}' first")
    return path, yaml.safe_load(open(path))


def carrier_isp(topo, node):
    """Carrier stand-in <site>-ISP-A takes the carrier-lab-init of the ISP router on its 'isp' port (from the links)."""
    for link in topo["topology"]["links"]:
        a, b = link["endpoints"]
        for mine, far in ((a, b), (b, a)):
            if mine == f"{node}:isp":
                return far.split(":")[0]
    die(f"{node}: no link on port 'isp' in the topology")


def baseline_path(name, target):
    if name == "startup-config":
        return os.path.join(REPO, "tests", "expected", f"{target}.cfg")
    ext = ARTIFACT_EXT.get(name, "sh" if name.endswith("-init") else "cfg")
    return os.path.join(REPO, "tests", "expected-lab", f"{name}.{target}.{ext}")


def cmd_fetch(args):
    site = args.site
    ih = Infrahub(args.branch)
    out = site_dir(site)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "configs"))
    manifest, diffs = [], []

    def save(name, target, rel, extra_lines=()):
        body, meta = ih.artifact(name, target)
        if args.compare_baselines:
            bp = baseline_path(name, target)
            same = os.path.exists(bp) and open(bp).read().rstrip("\n") == body.rstrip("\n")
            if not same:
                diffs.append(f"{name}.{target}")
        text = body.rstrip("\n") + "\n"
        if name == "startup-config":                                              # fill __CISCOADMIN_HASH__
            text, warns = vamd_secrets.fill(text)
            for w in warns:
                print(f"WARNING {target}: {w}", file=sys.stderr)
        if extra_lines:
            text += "".join(f"{l}\n" for l in extra_lines)
        with open(os.path.join(out, rel), "w") as f:
            f.write(text)
        if rel.endswith(".sh"):
            os.chmod(os.path.join(out, rel), 0o755)
        manifest.append({"artifact": name, "target": target, "file": rel, "artifact_id": meta["id"],
                         "storage_id": meta["storage_id"], "checksum": meta["checksum"], "sha256_16": sha(text),
                         "lab_lines_added": list(extra_lines)})

    full = site.endswith("-full")                                                # option 5: the entire site
    base_site = site[:-len("-full")] if full else site
    save("lab-topology-full" if full else "lab-topology", base_site, "topology.yml")
    if not full:
        save("lab-expected", site, "expected.json")
    topo = yaml.safe_load(open(os.path.join(out, "topology.yml")))
    infrahub_nodes = set() if full else set(json.load(open(os.path.join(out, "expected.json")))["lab_nodes"])
    for node, spec in sorted(topo["topology"]["nodes"].items()):
        kind = spec["kind"]
        if kind == "cisco_n9kv":
            save("startup-config", node, f"configs/{node}.cfg", LAB_NXOS_LINES)
        elif kind == "cisco_xrd":
            save("iosxr-lab-config", node, f"configs/{node}.cfg")
        elif kind == "linux" and any(b.startswith(f"configs/{node}.sh:") for b in spec.get("binds", [])):
            labels = spec.get("labels") or {}
            if "vamd.artifact" in labels:                                         # option 5: the artifact is named on the node
                save(labels["vamd.artifact"], labels["vamd.target"], f"configs/{node}.sh")
            elif node in infrahub_nodes:
                save("frr-lab-init", node, f"configs/{node}.sh")
            else:
                save("carrier-lab-init", carrier_isp(topo, node), f"configs/{node}.sh")
    boot = write_boot_configs(out, topo)                                        # 8 Oct: minimal vrnetlab boot (boot-hang fix)
    with open(os.path.join(out, "MANIFEST.json"), "w") as f:
        json.dump({"site": site, "branch": args.branch, "infrahub": INFRAHUB, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "artifacts": manifest, "boot_minimal": boot}, f, indent=2)
    print(f"{site}: {len(manifest)} artifacts from Infrahub ({args.branch}) -> {out}")
    for m in manifest:
        added = f" (+{len(m['lab_lines_added'])} lab line)" if m["lab_lines_added"] else ""
        print(f"  {m['artifact']:<18} {m['target']:<10} -> {m['file']}{added}")
    if args.compare_baselines:
        print(f"baselines: {len(manifest) - len(diffs)} of {len(manifest)} MATCH" + (f"; DIFF: {' '.join(diffs)}" if diffs else ""))
        if diffs:
            sys.exit(3)


def boot_config(full):
    """The lines NX-API / SSH need right after boot: hostname, users, lab features, mgmt0 and the management VRF."""
    out = []
    for b in blocks(full.splitlines()):
        head = b[0].strip()
        if head.startswith(BOOT_TOP) or head in BOOT_BLOCKS:
            out.extend(b)
    return "\n".join(out) + "\n"


def write_boot_configs(out, topo):
    """configs/<node>.boot.cfg per N9Kv + topology.boot.yml pointing at them (8 Oct: vrnetlab stalls inside 'vpc domain')."""
    t = yaml.safe_load(yaml.safe_dump(topo))
    done = []
    for node, spec in t["topology"]["nodes"].items():
        if spec["kind"] != "cisco_n9kv":
            continue
        full = open(os.path.join(out, spec["startup-config"])).read()
        rel = spec["startup-config"][:-4] + ".boot.cfg"
        open(os.path.join(out, rel), "w").write(boot_config(full))
        spec["startup-config"] = rel
        done.append(node)
    with open(os.path.join(out, "topology.boot.yml"), "w") as f:
        f.write("# lab-topology artifact; N9Kv boot with a minimal config, then 'labctl push' sends the full rendered config (8 Oct)\n")
        yaml.safe_dump(t, f, sort_keys=False)
    print(f"boot configs: {len(done)} N9Kv -> configs/<node>.boot.cfg, topology.boot.yml")
    return done


# ---------------------------------------------------------------- validate
def mem_need(topo, only=None):
    total, rows = 0, []
    for node, spec in topo["topology"]["nodes"].items():
        if only and node not in only:
            continue
        kind = spec["kind"]
        mb = int(spec.get("env", {}).get("QEMU_MEMORY", 0)) if kind == "cisco_n9kv" else NODE_MEM_MB[kind]
        total += mb
        rows.append((node, kind, mb))
    return total, rows


def host_avail_mb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def validate(site, quiet=False, only=None, memory=None, reserve=HOST_RESERVE_MB):
    """only: subset of nodes (deploy --node-filter); memory: N9Kv QEMU_MEMORY override in MB (deploy --memory)."""
    path, topo = load_topology(site)
    base = os.path.dirname(path)
    problems, notes = [], []
    if only:
        unknown = sorted(set(only) - set(topo["topology"]["nodes"]))
        if unknown:
            problems.append(f"--node-filter names nodes not in the topology: {unknown}")
    if memory:
        topo = with_memory(topo, memory)
    nodes = {n: s for n, s in topo["topology"]["nodes"].items() if not only or n in only}
    for node, spec in nodes.items():
        files = [spec["startup-config"]] if "startup-config" in spec else []
        files += [b.split(":")[0] for b in spec.get("binds", [])]
        for f in files:
            p = os.path.join(base, f)
            if not os.path.exists(p) or os.path.getsize(p) == 0:
                problems.append(f"{node}: file {f} missing or empty")
    for link in topo["topology"]["links"]:
        for ep in link["endpoints"]:
            if ep.split(":")[0] not in topo["topology"]["nodes"]:
                problems.append(f"link endpoint {ep}: node not in topology")
    seen = {}
    for node, spec in nodes.items():
        ip = spec.get("mgmt-ipv4")
        if ip in seen:
            problems.append(f"mgmt {ip} used by {seen[ip]} and {node}")
        seen[ip] = node
    for image in sorted({s["image"] for s in nodes.values()}):
        ok = subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0
        (notes if ok else problems).append(f"image {image}: {'present' if ok else 'NOT on this host'}")
    need, rows = mem_need(topo, only)
    avail = host_avail_mb()
    fits = need + reserve <= avail
    msg = f"memory: {len(nodes)} node(s) need ~{need} MB + {reserve} MB reserve, host has {avail} MB available -> {'fits' if fits else 'does NOT fit'}"
    (notes if fits else problems).append(msg)
    if not quiet:
        print(f"{site}: {len(nodes)} of {len(topo['topology']['nodes'])} nodes" + (f" (filter), N9Kv memory {memory} MB" if only or memory else "") + f", {len(topo['topology']['links'])} links in the topology")
        for n in notes:
            print("  ok  ", n)
        for p in problems:
            print("  FAIL", p)
        print(f"validate: {'PASS' if not problems else f'{len(problems)} problem(s)'}")
    return problems


def cmd_validate(args):
    only = args.node_filter.split(",") if args.node_filter else None
    sys.exit(0 if not validate(args.site, only=only, memory=args.memory, reserve=args.reserve) else 4)


# ---------------------------------------------------------------- containerlab
def clab(*argv):
    print("+ containerlab " + " ".join(argv), flush=True)
    return subprocess.run(["containerlab", *argv]).returncode


def with_memory(topo, mb):
    """Copy of the topology with every N9Kv QEMU_MEMORY set to mb (laptop runs); the artifact itself is untouched."""
    t = json.loads(json.dumps(topo))
    for spec in t["topology"]["nodes"].values():
        if spec["kind"] == "cisco_n9kv":
            spec.setdefault("env", {})["QEMU_MEMORY"] = mb
    return t


def staged_topology(topo, size, only=None):
    """Copy of the topology in which N9Kv group k+1 is created only when every N9Kv of group k is healthy.

    Groups of `size` N9Kv in topology order (the artifact's order), limited to `only` (deploy --node-filter) when given;
    other nodes are not staged. Returns (topology, groups).
    """
    t = json.loads(json.dumps(topo))
    n9k = [n for n, spec in t["topology"]["nodes"].items() if spec["kind"] == "cisco_n9kv" and (not only or n in only)]
    if size <= 0 or size >= len(n9k):
        return t, [n9k] if n9k else []
    groups = [n9k[i:i + size] for i in range(0, len(n9k), size)]
    for prev, cur in zip(groups, groups[1:]):
        for node in cur:
            stages = t["topology"]["nodes"][node].setdefault("stages", {})
            stages.setdefault("create", {})["wait-for"] = [{"node": p, "stage": "healthy"} for p in prev]
    return t, groups


def last_log_time(container):
    """Epoch seconds of the container's last log line (docker logs -t), None when the container does not exist yet."""
    r = subprocess.run(["docker", "logs", "-t", "--tail", "1", container], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    line = (r.stdout.strip() or r.stderr.strip()).splitlines()
    if not line:
        return started_time(container)
    try:
        return calendar.timegm(time.strptime(line[-1][:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def started_time(container):
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", container], capture_output=True, text=True)
    try:
        return calendar.timegm(time.strptime(r.stdout.strip()[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def hung_nodes(nodes, lab, now, health=None, log_time=last_log_time, start_time=started_time, stuck=BOOT_STUCK_S):
    """N9Kv that exist, are not healthy, have run longer than `stuck` and whose log has not moved for `stuck` seconds."""
    health = health or docker_health
    out = []
    for node in nodes:
        c = f"clab-{lab}-{node}"
        h = health(c)
        if h in ("healthy", "missing"):
            continue
        last, began = log_time(c), start_time(c)
        if last is None or began is None:
            continue
        if now - began > stuck and now - last > stuck:
            out.append(f"{node}({h}, log quiet {int(now - last)} s)")
    return out


def deploy_watched(path, lab, nodes, extra):
    """containerlab deploy, then watch every N9Kv until all are healthy; a hung node -> destroy + redeploy."""
    for attempt in range(BOOT_RETRIES + 1):
        print("+ containerlab deploy -t " + path + " --reconfigure " + " ".join(extra), flush=True)
        proc = subprocess.Popen(["containerlab", "deploy", "-t", path, "--reconfigure", *extra])
        start, hung = time.time(), []
        while True:
            time.sleep(BOOT_POLL_S)
            rc = proc.poll()
            if rc not in (None, 0):
                print(f"deploy: containerlab exited {rc}", flush=True)
                return rc
            waiting = [n for n in nodes if docker_health(f"clab-{lab}-{n}") != "healthy"]
            if rc == 0 and not waiting:
                print(f"deploy: all {len(nodes)} N9Kv healthy after {int(time.time() - start)} s", flush=True)
                return 0
            hung = hung_nodes(nodes, lab, time.time())
            if hung:
                break
            print(f"{time.strftime('%T')} boot: {len(nodes) - len(waiting)} of {len(nodes)} N9Kv healthy"
                  f"{'' if rc is None else ' (containerlab done)'}", flush=True)
        print(f"watchdog: hung in the vrnetlab bootstrap: {' '.join(hung)} (attempt {attempt + 1} of {BOOT_RETRIES + 1})", flush=True)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
        clab("destroy", "-t", path, "--cleanup")       # 25 Oct: a single-node restart breaks containerlab links; redeploy the site
    print(f"deploy: FAIL: N9Kv still hung after {BOOT_RETRIES} redeploy(s)", flush=True)
    return 7


def cmd_deploy(args):
    only = args.node_filter.split(",") if args.node_filter else None
    problems = validate(args.site, only=only, memory=args.memory, reserve=args.reserve)
    if problems and not args.force:
        die("validate failed; fix it or pass --force", 4)
    path, topo = load_topology(args.site)
    boot = os.path.join(os.path.dirname(path), "topology.boot.yml")
    if os.path.exists(boot) and not args.full_boot:
        path, topo = boot, yaml.safe_load(open(boot))
        print("deploying topology.boot.yml (N9Kv minimal boot config; run 'labctl push' next)")
    if args.memory:
        path = os.path.join(os.path.dirname(path), "topology.override.yml")
        with open(path, "w") as f:
            f.write(f"# lab-topology artifact with N9Kv QEMU_MEMORY {args.memory} (labctl deploy --memory)\n")
            yaml.safe_dump(with_memory(topo, args.memory), f, sort_keys=False)
        print(f"deploying {os.path.basename(path)} (N9Kv memory {args.memory} MB)")
    extra = ["--node-filter", args.node_filter] if args.node_filter else []
    if args.no_watchdog:
        sys.exit(clab("deploy", "-t", path, "--reconfigure", *extra))
    only = set(args.node_filter.split(",")) if args.node_filter else None
    staged, groups = staged_topology(yaml.safe_load(open(path)), args.group_size, only)    # the file being deployed
    if len(groups) > 1:
        path = os.path.join(os.path.dirname(path), "topology.staged.yml")
        with open(path, "w") as f:
            f.write(f"# deployed topology with the N9Kv booted in groups of {args.group_size}: "
                    + " | ".join(",".join(g) for g in groups) + "\n")
            yaml.safe_dump(staged, f, sort_keys=False)
        print(f"deploying {os.path.basename(path)}: {len(groups)} N9Kv boot groups: " + " | ".join(" ".join(g) for g in groups))
    nodes = [n for g in groups for n in g]
    sys.exit(deploy_watched(path, topo["name"], nodes, extra))


def cmd_destroy(args):
    path, _ = load_topology(args.site)
    sys.exit(clab("destroy", "-t", path, "--cleanup"))


def cmd_status(args):
    path, _ = load_topology(args.site)
    sys.exit(clab("inspect", "-t", path))


# ---------------------------------------------------------------- NX-API
class NXAPI:
    def __init__(self, ip):
        self.url = f"https://{ip}/ins"
        self.http = httpx.Client(auth=(NXOS_USER, NXOS_PASS), verify=False, timeout=600)

    def up(self):
        try:
            return "result" in self.http.post(self.url, headers={"content-type": "application/json-rpc"}, timeout=10,
                                               content=json.dumps([{"jsonrpc": "2.0", "method": "cli", "params": {"cmd": "show clock", "version": 1}, "id": 1}])).text
        except Exception:
            return False

    def wait(self, tries=60, every=20):
        for _ in range(tries):
            if self.up():
                return True
            time.sleep(every)
        return False

    def ascii(self, cmd):
        r = self.http.post(self.url, headers={"content-type": "application/json-rpc"},
                           content=json.dumps([{"jsonrpc": "2.0", "method": "cli_ascii", "params": {"cmd": cmd, "version": 1}, "id": 1}]))
        d = r.json()
        d = d[0] if isinstance(d, list) else d
        return (d.get("result") or {}).get("msg", "") if "result" in d else ""

    def conf(self, lines):
        """One cli_conf call, continue-on-error; returns [(line, ok, msg)]."""
        r = self.http.post(self.url, headers={"content-type": "application/json"}, content=json.dumps({"ins_api": {
            "version": "1.0", "type": "cli_conf", "chunk": "0", "sid": "1", "input": " ; ".join(l.strip() for l in lines),
            "output_format": "json", "rollback": "continue-on-error"}}))
        try:
            outs = r.json()["ins_api"]["outputs"]["output"]
        except Exception:
            return [(lines[0], False, f"bad response {r.status_code}: {r.text[:200]}")]
        outs = [outs] if isinstance(outs, dict) else outs
        res = []
        for i, line in enumerate(lines):
            o = outs[i] if i < len(outs) else {}
            msg = " ".join(str(o.get(k, "")) for k in ("msg", "clierror") if o.get(k) and str(o.get(k)) != "Success").strip()
            res.append((line, str(o.get("code", "")) == "200", msg))
        return res


def blocks(lines):
    out, cur = [], None
    for l in lines:
        if not l.strip() or l.strip().startswith("!"):
            continue
        if not l.startswith(" "):
            cur = [l]
            out.append(cur)
        elif cur is not None:
            cur.append(l)
    return out


def push_config(api, cfg_text):
    res = []
    for b in blocks(cfg_text.splitlines()):
        if b[0].strip() in SKIP_BLOCKS:
            continue
        res += api.conf(b)
    return res


def qemu_reset(container):
    """vrnetlab QEMU monitor 'system_reset' (NX-OS 'reload' over NX-API waits for a prompt)."""
    code = ("import socket,time; s=socket.create_connection(('127.0.0.1',%d)); time.sleep(1); "
            "s.sendall(b'system_reset\\n'); time.sleep(1); print('reset sent')" % QEMU_MONITOR_PORT)
    return subprocess.run(["timeout", "15", "docker", "exec", container, "/.venv/bin/python", "-c", code]).returncode


def docker_health(container):
    r = subprocess.run(["docker", "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", container],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "missing"


def wait_all_healthy(lab, topo):
    """8 Oct: before any carve, every cisco_n9kv node must be healthy: container health 'healthy' and NX-API answering."""
    nodes = {n: spec["mgmt-ipv4"] for n, spec in sorted(topo["topology"]["nodes"].items()) if spec["kind"] == "cisco_n9kv"}
    start = time.time()
    while True:
        waiting = []
        for node, ip in nodes.items():
            h = docker_health(f"clab-{lab}-{node}")
            if h != "healthy" or not NXAPI(ip).up():
                waiting.append(f"{node}({h})")
        took = int(time.time() - start)
        if not waiting:
            print(f"all {len(nodes)} N9Kv healthy after {took} s", flush=True)
            return True
        if took >= HEALTHY_WAIT_S:
            print(f"after {took} s still not healthy: {' '.join(waiting)}", flush=True)
            return False
        print(f"{time.strftime('%T')} waiting for {len(waiting)} of {len(nodes)} N9Kv: {' '.join(waiting)}", flush=True)
        time.sleep(HEALTHY_POLL_S)


def cmd_carve(args):
    path, topo = load_topology(args.site)
    base = os.path.dirname(path)
    lab = topo["name"]
    targets = []
    for node, spec in sorted(topo["topology"]["nodes"].items()):
        if spec["kind"] != "cisco_n9kv":
            continue
        cfg = open(os.path.join(base, spec["startup-config"])).read()
        if CARVE_TRIGGER in cfg and (not args.nodes or node in args.nodes.split(",")):
            targets.append((node, spec["mgmt-ipv4"], cfg))
    print(f"carve targets ({len(targets)}): {' '.join(t[0] for t in targets)}")
    failed = []
    if not wait_all_healthy(lab, topo):
        print("carve: FAIL: not every N9Kv healthy within %d min, nothing carved" % (HEALTHY_WAIT_S // 60))
        sys.exit(6)
    for node, ip, cfg in targets:
        api = NXAPI(ip)
        if not api.wait():
            failed.append(f"{node}: NX-API not up")
            continue
        region = api.ascii("show hardware access-list tcam region")
        if re.search(r"\[arp-ether\] size =\s+256 \(double-wide\)", region):
            print(f"{node}: arp-ether already 256 double-wide, no reset needed")
        else:
            res = api.conf(CARVE)
            bad = [r for r in res if not r[1]]
            for line, ok, msg in res:
                print(f"{node}: {'OK ' if ok else 'ERR'} {line} {msg[:160]}")
            if bad:
                failed.append(f"{node}: carve refused")
                continue
            api.conf(["copy running-config startup-config"])
            print(f"{node}: reset {time.strftime('%T')}", flush=True)
            qemu_reset(f"clab-{lab}-{node}")
            time.sleep(120)
            if not api.wait():
                failed.append(f"{node}: NX-API not up after reset")
                continue
            time.sleep(60)
        res, _, left = push_full(api, ip, cfg)                                  # 9 Oct: refused blocks re-sent over SSH, as in push
        errs = [(l, "errors left after the SSH re-send") for l in left]
        have = CARVE_TRIGGER in api.ascii("show running-config | include suppress-arp")
        api.conf(["copy running-config startup-config"])
        print(f"{node}: re-pushed {len(res)} lines, CLI errors {len(errs)}, suppress-arp in running-config: {have}")
        for l, m in errs[:20]:
            print(f"   ERR {l} -> {m[:200]}")
        if errs or not have:
            failed.append(f"{node}: {len(errs)} errors, suppress-arp {have}")
    print("carve: " + ("PASS" if not failed else "FAIL: " + "; ".join(failed)))
    sys.exit(0 if not failed else 5)


def ssh_push(ip, lines):
    """Re-send config blocks over SSH (CLI); returns the output lines carrying an error marker."""
    from netmiko import ConnectHandler
    with ConnectHandler(device_type="cisco_nxos", host=ip, username=NXOS_USER, password=NXOS_PASS, conn_timeout=30, fast_cli=False) as c:
        out = c.send_config_set(lines, read_timeout=600, cmd_verify=False, exit_config_mode=False)
        c.write_channel("end\n"); time.sleep(2); c.clear_buffer()
    return [l.strip() for l in out.splitlines() if l.strip().startswith(("%", "ERROR")) or "Invalid" in l]   # 9 Oct: NX-OS "ERROR: ..." too


def push_full(api, ip, cfg):
    """Full config per block over NX-API; blocks NX-API refused are re-sent over SSH. Returns (NX-API results, refused blocks, errors left)."""
    res, bad_blocks = [], []
    for b in blocks(cfg.splitlines()):
        if b[0].strip() in SKIP_BLOCKS:
            continue
        r = api.conf(b); res += r
        if any(not ok for _, ok, _ in r):
            bad_blocks.append(b)
    retry = [l for b in bad_blocks for l in b]
    return res, bad_blocks, (ssh_push(ip, retry) if retry else [])


def cmd_push(args):
    """8 Oct: push each N9Kv's full rendered config after a minimal boot; NX-API first, refused blocks again over SSH; save."""
    path, topo = load_topology(args.site)
    base, lab = os.path.dirname(path), topo["name"]
    if not wait_all_healthy(lab, topo):
        print("push: FAIL: not every N9Kv healthy"); sys.exit(6)
    failed = []
    for node, spec in sorted(topo["topology"]["nodes"].items()):
        if spec["kind"] != "cisco_n9kv" or (args.nodes and node not in args.nodes.split(",")):
            continue
        cfg = open(os.path.join(base, spec["startup-config"])).read()
        api = NXAPI(spec["mgmt-ipv4"])
        res, bad_blocks, left = push_full(api, spec["mgmt-ipv4"], cfg)
        api.conf(["copy running-config startup-config"])
        print(f"{node}: {len(res)} lines over NX-API, {len(bad_blocks)} blocks refused and re-sent over SSH, errors left {len(left)}")
        for l in left[:10]:
            print("   ", l[:200])
        if left:
            failed.append(node)
    print("push: " + ("PASS" if not failed else "FAIL: " + " ".join(failed)))
    sys.exit(0 if not failed else 5)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("fetch", cmd_fetch), ("validate", cmd_validate), ("deploy", cmd_deploy), ("carve", cmd_carve),
                     ("destroy", cmd_destroy), ("status", cmd_status), ("push", cmd_push)):
        s = sub.add_parser(name)
        s.add_argument("site")
        s.set_defaults(fn=fn)
        if name == "fetch":
            s.add_argument("--branch", default="main")
            s.add_argument("--compare-baselines", action="store_true", help="diff every artifact with tests/expected(-lab)")
        if name in ("deploy", "validate"):
            s.add_argument("--node-filter", help="comma-separated nodes (containerlab --node-filter)")
            s.add_argument("--memory", type=int, help="N9Kv QEMU_MEMORY in MB for this run (laptop)")
            s.add_argument("--reserve", type=int, default=HOST_RESERVE_MB, help="host memory kept free, MB")
        if name == "deploy":
            s.add_argument("--force", action="store_true")
            s.add_argument("--full-boot", action="store_true", help="boot N9Kv with the full config (vrnetlab console push; old way)")
            s.add_argument("--group-size", type=int, default=BOOT_GROUP_SIZE, help="N9Kv per boot group; 0 = all at once")
            s.add_argument("--no-watchdog", action="store_true", help="plain containerlab deploy: no boot groups, no hang watchdog")
        if name in ("carve", "push"):
            s.add_argument("--nodes", help="comma-separated subset")
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
