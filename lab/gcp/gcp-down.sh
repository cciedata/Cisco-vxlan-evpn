#!/usr/bin/env bash
# gcp-down.sh <stop|delete>: stop the VM (disk kept, billed for disk only) or delete it (the boot disk is deleted with it).
# Budget, firewall rule, bucket and service account are kept, so gcp-up.sh can recreate the VM with no new input.
set -u
ACT=${1:-}; [[ "$ACT" == stop || "$ACT" == delete ]] || { echo "usage: gcp-down.sh stop|delete"; exit 2; }
D=$(cd "$(dirname "$0")" && pwd); source "$D/lab.conf"; G=${GCLOUD:-gcloud}
$G compute instances describe $VM --zone=$ZONE --project=$P >/dev/null 2>&1 || { echo "$VM does not exist"; exit 0; }
if [ "$ACT" = stop ]; then $G compute instances stop $VM --zone=$ZONE --project=$P
else $G compute instances delete $VM --zone=$ZONE --project=$P --quiet; fi
$G compute instances list --project=$P --format="value(name,status)"
$G compute disks list --project=$P --format="value(name,sizeGb,status)"
