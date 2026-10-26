#!/usr/bin/env bash
# vm-bootstrap.sh: runs ON the lab VM. docs/QUICKSTART.md step 1 as written; docker.io is held so an upgrade cannot
# restart Docker under a running lab. Idempotent.
set -u
echo "== apt packages (QUICKSTART step 1)"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -q >/dev/null || { echo "STOP: apt update"; exit 1; }
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io docker-compose-v2 git curl python3 python3-yaml >/dev/null || { echo "STOP: apt install"; exit 1; }
sudo apt-mark hold docker.io
sudo usermod -aG docker "$USER"
echo "== uv (QUICKSTART step 1)"
command -v uv >/dev/null 2>&1 || [ -x ~/.local/bin/uv ] || { curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null || { echo "STOP: uv install"; exit 1; }; }
echo "== versions"
dpkg-query -W -f='${Package} ${Version}\n' docker.io docker-compose-v2 git curl python3 python3-yaml
apt-mark showhold
~/.local/bin/uv --version
echo BOOTSTRAP_END
