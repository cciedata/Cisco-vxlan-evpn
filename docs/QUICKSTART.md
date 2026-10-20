# Quickstart: from an empty machine to both sites in Infrahub

This page takes you from a fresh Ubuntu machine to an Infrahub that holds DC1 and DC2 and renders all 134 configs and
58 lab artifacts equal to the baselines in this repository. The lab itself is a second step ([docs/LAB.md](LAB.md)):
one Nexus 9000v fits a 12 GB laptop, the full DC1 site needs a host with about 80 GB.

Run each command block in one go. Each step names the command that proves it worked.

Status of this page: the build from Git alone was verified on an empty Infrahub at the Phase 0 commit (schema, objects,
pools, `fabric_site` for both sites, renders equal to the baselines), and every later addition was loaded the same way on
its own branch before it was merged. The whole sequence has not yet been rerun from an empty instance at the current
commit; docs/BUILD.md marks each step accordingly.

## The host

| | Needed |
|---|---|
| OS | x86-64 Ubuntu 24.04: a Linux machine or VM, or Windows 10/11 with WSL2 |
| Memory | Infrahub alone: about 4 GB free (the stack uses 3.7 GiB). One N9Kv on top: 8 GB for WSL with Infrahub stopped |
| Disk | 20 GB free (plus the Nexus 9000v image for the lab) |
| Tools | Docker Engine with Compose v2, git, curl, python3, uv |

**Windows only, before step 1.** Install an Ubuntu 24.04 distro and give WSL enough memory in `C:\Users\<you>\.wslconfig`
(`memory=8GB`, `swap=8GB`), then `wsl --shutdown` once. Run everything below inside Ubuntu.

## 1. Install the tools

```
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git curl python3 python3-yaml
sudo usermod -aG docker "$USER"
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Log out and in again (WSL: close the window, `wsl --shutdown` in PowerShell, open Ubuntu again) so the docker group applies.

Check:
```
docker run --rm hello-world | grep -m1 Hello
docker compose version
uv --version
```

## 2. Get the code

```
cd ~
git clone https://github.com/cciedata/Cisco-vxlan-evpn.git
cd Cisco-vxlan-evpn
uv sync
uv run python -m pytest -q tests/test_repo_consistency.py
```
Expected: pytest ends with `passed` and no `failed`. Every later step runs from this folder.

## 3. Start an empty Infrahub

The repo's own `docker-compose.yml` starts Infrahub 1.11.2 with no built-in secrets: every credential comes from `.env`.
```
cp .env.example .env
python3 - <<'EOF'
import pathlib, secrets, uuid
p = pathlib.Path(".env"); out = []
for line in p.read_text().splitlines():
    k, sep, v = line.partition("=")
    if sep and v == "CHANGE_ME":
        v = str(uuid.uuid4()) if k.endswith(("_TOKEN", "_SECRET_KEY")) else secrets.token_urlsafe(24)
    out.append(k + sep + v)
p.write_text("\n".join(out) + "\n")
EOF
chmod 600 .env
docker compose up -d
```
Check (the first start downloads the images; wait for `healthy`):
```
docker compose ps
curl -sf -o /dev/null http://localhost:8000/api/schema/summary && echo OK
```
Expected: `infrahub-server`, `database`, `message-queue`, `cache`, `task-manager`, `task-manager-db` healthy, two
`task-worker` containers running, and `OK`.

Infrahub is at http://localhost:8000 (on Windows with WSL2, open it in the Windows browser). Log in as `admin` with the
password the `.env` step generated: `grep '^INFRAHUB_INITIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-`.

Client settings for every later terminal:
```
export INFRAHUB_ADDRESS=http://localhost:8000
export INFRAHUB_API_TOKEN="$(grep '^INFRAHUB_INITIAL_ADMIN_TOKEN=' .env | cut -d= -f2-)"
source .venv/bin/activate
```

## 4. Load the schema and the data, build both sites

[docs/BUILD.md](BUILD.md) has every step with its check. In short: the schema, the object files in file-name order, the IP
pools, then the three generators per site, each run first in plan-only mode and then written:
```
infrahubctl schema load schemas --branch main --wait 120
for f in objects/0[0-9]-*.yml; do infrahubctl object load "$f" --branch main || break; done
python scripts/create_pools.py
for s in DC1 DC2; do
  FABRIC_DRY_RUN=1 infrahubctl generator fabric_site --branch main site=$s
  infrahubctl generator fabric_site --branch main site=$s
done
for f in objects/1[0-9]-*.yml; do infrahubctl object load "$f" --branch main || break; done
for s in DC1 DC2; do
  infrahubctl generator oob_site --branch main site=$s
  SITE_PHYSICAL_DRY_RUN=1 infrahubctl generator site_physical --branch main site=$s
  infrahubctl generator site_physical --branch main site=$s
done
```
Expected: no error from any load, every plan-only run prints its table, and every write run ends with its reconcile
summary and nothing deleted.

## 5. Render everything and compare with the baselines

```
bash scripts/render_all.sh lab/configs main
```
Expected: `configs: 134 of 134 equal tests/expected`, `lab artifacts: 58 of 58 equal tests/expected-lab`, and the last
line `VERIFIED 134 configs and 58 lab artifacts on branch main`. The rendered files are in `lab/configs/` (git-ignored).

## 6. The switch password

The NX-OS configs carry a local user `vamdadmin`. Its password hash is filled in when a switch gets its config, and is
never stored in Git:
```
bash ztp/make-secret.sh
```
It asks for a password and writes `~/.vamd/secrets.env` (mode 600). Keep that password: it is the SSH login of every switch
in the lab, and ZTP cannot verify a switch without it.

## 7. The lab

[docs/LAB.md](LAB.md): the Nexus 9000v image, one node on the laptop, the full DC1 site on a big host, post-checks,
failure tests, ZTP and the Day-2 push.

## Clean up

Keep Infrahub and its data: `docker compose stop` (and `start` later). Remove everything, data included:
`docker compose down -v`.

## When something fails

| Symptom | What to do |
|---|---|
| `permission denied` on docker | the docker group applies only after a new login |
| `docker compose`: unknown command | `sudo apt-get install -y docker-compose-v2` |
| `docker compose up`: `set INFRAHUB_... in .env` | a credential is still `CHANGE_ME` (step 3) |
| schema load answers 500 right after a fresh start | wait a minute and run it again (a brand-new Infrahub can answer 500 while it converges) |
| `object load` error naming a missing object | an earlier file was skipped; load in the step 4 order |
| port 8000 already used | another Infrahub runs here: set the alternate ports in `.env` (see `.env.example`) and `export INFRAHUB_ADDRESS=http://localhost:8001` |
| a generator run stops with `GUARD` or `STOP` | it refused to move or delete something it does not own; read the message, nothing was written |
