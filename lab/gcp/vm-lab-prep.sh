#!/usr/bin/env bash
# vm-lab-prep.sh: runs ON the lab VM as "BUCKET=<your bucket> bash vm-lab-prep.sh". Installs containerlab 0.79.0 (pinned
# .deb) and loads the lab images from gs://$BUCKET, checking each image ID against the manifest. Reads the bucket with
# the VM's own service account through the metadata token: no keys. Idempotent.
set -u
B=${BUCKET:?set BUCKET to your image bucket}
echo "== containerlab 0.79.0"
if containerlab version 2>/dev/null | grep -q '0.79.0'; then echo "present"; else
  curl -fsSL -o /tmp/clab.deb https://github.com/srl-labs/containerlab/releases/download/v0.79.0/containerlab_0.79.0_linux_amd64.deb || { echo "STOP: clab download"; exit 1; }
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q /tmp/clab.deb >/dev/null || { echo "STOP: clab install"; exit 1; }
fi
containerlab version | grep -E '^\s*version' | head -1
TOK=$(curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])') || { echo "STOP: no SA token"; exit 1; }
get(){ curl -fsS -H "Authorization: Bearer $TOK" "https://storage.googleapis.com/storage/v1/b/$B/o/$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1")?alt=media"; }
echo "== manifest"
get images/manifest.txt > /tmp/manifest.txt || { echo "STOP: manifest read"; exit 1; }
cat /tmp/manifest.txt
echo "== images (load if missing or ID differs, then compare ID)"
bad=0
while IFS=$'\t' read -r IMG KEY ID; do
  [ -n "$IMG" ] || continue
  have=$(docker image inspect -f '{{.Id}}' "$IMG" 2>/dev/null)
  if [ "$have" != "$ID" ]; then
    get "$KEY" | gunzip | docker load -q >/dev/null || { echo "STOP: load $IMG"; exit 1; }
    have=$(docker image inspect -f '{{.Id}}' "$IMG" 2>/dev/null)
  fi
  [ "$have" = "$ID" ] && echo "MATCH $IMG" || { echo "MISMATCH $IMG got=$have want=$ID"; bad=1; }
done < /tmp/manifest.txt
echo "== negative control: a wrong expected ID must not match"
x=$(head -1 /tmp/manifest.txt | cut -f1); [ "$(docker image inspect -f '{{.Id}}' "$x")" = "sha256:0000" ] && echo "control FAILED" || echo "control OK"
[ $bad = 0 ] && echo LABPREP_END || { echo "STOP: image ID mismatch"; exit 1; }
