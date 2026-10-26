# vm-ssh.sh: source it in your shell (". lab/gcp/vm-ssh.sh") to get three helpers for the lab VM:
#   vup            start the VM with gcp-up.sh if it is not running
#   vssh "<cmd>"   run a command on the VM
#   vscp <local> <remote-path>   copy a file to the VM
D_GCP=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$D_GCP/lab.conf"
G=${GCLOUD:-gcloud}
SSHF=(--ssh-flag="-o ConnectTimeout=15" --ssh-flag="-o StrictHostKeyChecking=accept-new" --ssh-flag="-o BatchMode=yes" --ssh-flag="-o ServerAliveInterval=30")
vssh(){ $G compute ssh $VM --zone=$ZONE --project=$P --quiet "${SSHF[@]}" --command="$1"; }
vscp(){ $G compute scp --zone=$ZONE --project=$P --quiet --scp-flag="-o ConnectTimeout=15" --scp-flag="-o BatchMode=yes" "$1" "$VM:$2"; }
vup(){ st=$($G compute instances describe $VM --zone=$ZONE --project=$P --format="value(status)"); [ "$st" = RUNNING ] || { echo "VM is $st: running gcp-up.sh ondemand first"; bash "$D_GCP/gcp-up.sh" ondemand >/dev/null || return 1; }; }
