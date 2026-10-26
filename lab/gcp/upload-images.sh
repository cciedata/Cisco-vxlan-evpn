#!/usr/bin/env bash
# upload-images.sh: docker save | gzip each lab image from this machine into gs://$BUCKET/images/ with an image-ID manifest.
# Build the N9Kv image with vrnetlab first (docs/LAB.md); the other images are pulled if missing.
# Idempotent: an image whose object exists and whose manifest ID equals the local image ID is skipped.
set -u
D=$(cd "$(dirname "$0")" && pwd); source "$D/lab.conf"; G=${GCLOUD:-gcloud}
IMAGES="vrnetlab/cisco_n9kv:10.6.4.M-lite quay.io/frrouting/frr:10.6.0 alpine:3.22 docker.cloudsmith.io/isc/docker/kea-dhcp4:2.6.3"   # the lab topology and ztp/run-ztp.sh use these
MAN=$D/images-manifest.txt
OLD=$($G storage cat gs://$BUCKET/images/manifest.txt --project=$P 2>/dev/null || true)
: > "$MAN"
for IMG in $IMAGES; do
  KEY="images/$(echo "$IMG" | tr '/:' '__').tar.gz"
  docker image inspect "$IMG" >/dev/null 2>&1 || { echo "$IMG not here: pulling that exact tag"; docker pull -q "$IMG" || { echo "STOP: pull $IMG"; exit 1; }; }
  ID=$(docker image inspect -f '{{.Id}}' "$IMG") || { echo "STOP: $IMG not here"; exit 1; }
  if echo "$OLD" | grep -qP "^\Q$IMG\E\t\Q$KEY\E\t\Q$ID\E$" && $G storage objects describe gs://$BUCKET/$KEY --project=$P >/dev/null 2>&1; then
    echo "$(date +%T) skip  $IMG (already uploaded, same ID)"
  else
    echo "$(date +%T) start $IMG -> gs://$BUCKET/$KEY"
    docker save "$IMG" | gzip -1 | $G storage cp - gs://$BUCKET/$KEY --project=$P || { echo "STOP: upload $IMG"; exit 1; }
    echo "$(date +%T) done  $IMG"
  fi
  echo -e "$IMG\t$KEY\t$ID" >> "$MAN"
done
$G storage cp "$MAN" gs://$BUCKET/images/manifest.txt --project=$P || { echo "STOP: manifest"; exit 1; }
echo "== bucket now"; $G storage ls -l gs://$BUCKET/** --project=$P
echo "== manifest"; cat "$MAN"
