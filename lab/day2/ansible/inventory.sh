#!/bin/sh
# Ansible dynamic inventory, live from Infrahub (lab/day2/infrahub_inventory.py). Filters: DAY2_SITE, DAY2_DEVICES.
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/../../../.venv/bin/python" "$HERE/../infrahub_inventory.py" "$@"
