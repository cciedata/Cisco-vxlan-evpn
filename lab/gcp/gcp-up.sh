#!/usr/bin/env bash
# gcp-up.sh <ondemand|spot>: bring up the GCP lab host from lab.conf. Idempotent: every step checks first and
# only creates what is missing, so a rerun (e.g. after deleting the VM, or switching to spot) needs no new input.
# Never prints your public IP.
set -u
MODEL=${1:-}
[[ "$MODEL" == ondemand || "$MODEL" == spot ]] || { echo "usage: gcp-up.sh ondemand|spot"; exit 2; }
D=$(cd "$(dirname "$0")" && pwd)
[ -f "$D/lab.conf" ] || { echo "copy lab.conf.example to lab.conf and fill it in"; exit 2; }
source "$D/lab.conf"
G=${GCLOUD:-gcloud}
SA="$SA_NAME@$P.iam.gserviceaccount.com"
step(){ echo; echo "== $*"; }
die(){ echo "STOP: $*"; exit 1; }

step "1. APIs (iam, billingbudgets)"
$G services enable iam.googleapis.com billingbudgets.googleapis.com --project=$P || die "services enable"
for i in $(seq 1 24); do
  n=$($G services list --enabled --project=$P --format="value(config.name)" | grep -cE '^(iam|billingbudgets)[.]googleapis[.]com$')
  [ "$n" = 2 ] && break; echo "waiting for APIs ($n of 2)"; sleep 10
done
[ "$n" = 2 ] || die "APIs not enabled after 4 min"

step "2. budget $BUDGET_AMOUNT (alerts at $BUDGET_THRESHOLDS)"
BA=$($G billing projects describe $P --format="value(billingAccountName)" | sed 's#billingAccounts/##')
[ -n "$BA" ] || die "no billing account"
if $G billing budgets list --billing-account=$BA --quiet --format="value(displayName)" | grep -qx "$BUDGET_NAME"; then
  echo "exists, not changed"
else
  TR=""; for t in $BUDGET_THRESHOLDS; do TR="$TR --threshold-rule=percent=$t"; done
  $G billing budgets create --quiet --billing-account=$BA --display-name="$BUDGET_NAME" --budget-amount=${BUDGET_AMOUNT}USD \
     --filter-projects=projects/$P $TR || die "budget create"
fi
$G billing budgets list --billing-account=$BA --format="value(displayName,amount.specifiedAmount.units,amount.specifiedAmount.currencyCode,thresholdRules[].thresholdPercent.list())" | grep "^$BUDGET_NAME"

step "3. firewall $FW_RULE (your public IP /32) and no default-allow-ssh"
IP=$(curl -s --max-time 10 https://ifconfig.me)
[[ "$IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "home IP not read"
CUR=$($G compute firewall-rules describe $FW_RULE --project=$P --format="value(sourceRanges.list())" 2>/dev/null)
if [ -z "$CUR" ]; then
  $G compute firewall-rules create $FW_RULE --project=$P --network=default --direction=INGRESS --action=ALLOW \
    --rules=tcp:22 --source-ranges="$IP/32" --priority=1000 --description="SSH from your public IP only" >/dev/null || die "fw create"
  echo "created"
elif [ "$CUR" != "$IP/32" ]; then
  $G compute firewall-rules update $FW_RULE --project=$P --source-ranges="$IP/32" >/dev/null || die "fw update"
  echo "your public IP changed: source updated"
else echo "exists, source matches home IP"; fi
$G compute firewall-rules describe default-allow-ssh --project=$P >/dev/null 2>&1 && $G compute firewall-rules delete default-allow-ssh --project=$P --quiet
echo "default-allow-ssh absent"
$G compute firewall-rules describe default-allow-rdp --project=$P >/dev/null 2>&1 && $G compute firewall-rules delete default-allow-rdp --project=$P --quiet
echo "default-allow-rdp absent"

step "4. bucket $BUCKET and image object"
$G storage buckets describe gs://$BUCKET --project=$P --format="value(name)" >/dev/null 2>&1 || die "bucket missing: create it first (docs/LAB-GCP.md)"
SZ=$($G storage objects describe gs://$BUCKET/$IMAGE_OBJECT --project=$P --format="value(size)" 2>/dev/null)
[ "$SZ" = "$IMAGE_SIZE" ] && echo "qcow2 present, size OK" || echo "qcow2 not present or size differs ($SZ)"

step "5. service account $SA_NAME + bucket read"
$G iam service-accounts describe $SA --project=$P >/dev/null 2>&1 || \
  $G iam service-accounts create $SA_NAME --project=$P --display-name="lab VM: read $BUCKET only" || die "sa create"
for i in 1 2 3 4 5 6; do $G iam service-accounts describe $SA --project=$P >/dev/null 2>&1 && break; sleep 5; done
$G storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$SA --role=$SA_ROLE --project=$P >/dev/null || die "bucket binding"
$G storage buckets get-iam-policy gs://$BUCKET --project=$P --format=json | python3 -c "import json,sys; [print(b['role'],m) for b in json.load(sys.stdin).get('bindings',[]) for m in b['members'] if sys.argv[1] in m]" "$SA_NAME"

step "6. VM $VM ($MODEL)"
if $G compute instances describe $VM --zone=$ZONE --project=$P >/dev/null 2>&1; then
  ST=$($G compute instances describe $VM --zone=$ZONE --project=$P --format="value(status,scheduling.provisioningModel)")
  echo "exists: $ST"
  case "$ST" in
    RUNNING*) ;;
    TERMINATED*|STOPPED*) $G compute instances start $VM --zone=$ZONE --project=$P || die "start";;
    *) die "VM in state $ST";;
  esac
  [[ "$MODEL" == spot && "$ST" != *SPOT* || "$MODEL" == ondemand && "$ST" == *SPOT* ]] && echo "NOTE: existing VM model differs from '$MODEL'; run gcp-down.sh first to switch"
else
  if [ "$MODEL" = spot ]; then PM="--provisioning-model=SPOT --maintenance-policy=TERMINATE"
  else PM="--provisioning-model=STANDARD --maintenance-policy=$MAINT_ONDEMAND"; fi
  [ "$SECURE_BOOT" = no ] && SB="--no-shielded-secure-boot" || SB="--shielded-secure-boot"
  [ "$DISK_AUTO_DELETE" = yes ] && AD="--boot-disk-auto-delete" || AD="--no-boot-disk-auto-delete"
  [ "$DELETION_PROTECTION" = no ] && DP="--no-deletion-protection" || DP="--deletion-protection"
  $G compute instances create $VM --project=$P --zone=$ZONE --machine-type=$MACHINE \
    --enable-nested-virtualization $PM \
    --instance-termination-action=$TERMINATION_ACTION --max-run-duration=$MAX_RUN \
    --image-family=$IMAGE_FAMILY --image-project=$IMAGE_PROJECT \
    --boot-disk-size=${DISK_GB}GB --boot-disk-type=$DISK_TYPE $AD $DP $SB \
    --network=default \
    --service-account=$SA --scopes=$SA_SCOPE \
    --metadata=idle-limit-sec=$IDLE_LIMIT_SEC --metadata-from-file=startup-script="$D/idle-stop.sh" || die "instance create"
fi
$G compute instances describe $VM --zone=$ZONE --project=$P \
  --format="value(status,machineType.basename(),scheduling.provisioningModel,scheduling.onHostMaintenance,scheduling.instanceTerminationAction,scheduling.maxRunDuration.seconds,advancedMachineFeatures.enableNestedVirtualization,shieldedInstanceConfig.enableSecureBoot,deletionProtection)"

step "7. SSH + host checks (kvm, cpu, memory, disk, idle timer)"
T=/tmp/gcp-up-ssh.$$
RC='echo SSH_OK; echo "kvm: $(ls -l /dev/kvm 2>&1)"; echo "vcpu: $(nproc)"; echo "mem: $(free -g | sed -n 2p)"; echo "vmx flags: $(grep -c -E "vmx|svm" /proc/cpuinfo)"; echo "root fs: $(df -h / | tail -1)"; echo "idle timer: $(systemctl is-active vamd-idle.timer)"; echo "os: $(lsb_release -ds 2>/dev/null)"; echo HOSTCHECK_END'
ok=""
for i in $(seq 1 20); do
  timeout 90 $G compute ssh $VM --zone=$ZONE --project=$P --quiet \
    --ssh-flag="-o ConnectTimeout=15" --ssh-flag="-o StrictHostKeyChecking=accept-new" --ssh-flag="-o BatchMode=yes" \
    --command="$RC" > $T 2>&1
  if grep -qx SSH_OK $T; then ok=1; break; fi
  echo "ssh attempt $i not yet"; sleep 15
done
[ -n "$ok" ] || { rm -f $T; die "no SSH after 20 attempts"; }
sed -n '/^SSH_OK$/,/^HOSTCHECK_END$/p' $T
grep -q '^kvm: crw' $T            || { rm -f $T; die "/dev/kvm missing on $VM"; }
grep -qx 'idle timer: active' $T  || { rm -f $T; die "idle-stop timer not active"; }
grep -qx 'HOSTCHECK_END' $T       || { rm -f $T; die "host checks incomplete"; }
rm -f $T
echo; echo "UP: $VM ($MODEL)"
