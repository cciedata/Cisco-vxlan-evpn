# Running the lab on Google Cloud

Both lab paths together (DC1 and DC2, 10 virtual Nexus per site plus FRR stand-ins and hosts) need about 176 GB of RAM.
This page runs them on one Google Cloud VM with nested virtualization. The scripts are in [`lab/gcp/`](../lab/gcp/).
They were used for the 25 Oct 2026 run described in [LAB.md](LAB.md).

## What you need

- A Google Cloud project with billing on, and a CPU quota of at least 64 vCPUs in the region (us-east4 here).
- `gcloud` on your machine, logged in to that project, and Docker.
- Your own Nexus 9000v image, downloaded from Cisco with your account and built with vrnetlab as in [LAB.md](LAB.md)
  (`vrnetlab/cisco_n9kv:10.6.4.M-lite`). It is not in this repository.

## Setup, once

1. Copy `lab/gcp/lab.conf.example` to `lab/gcp/lab.conf` and fill in the values marked `YOUR-...`. Keep `lab.conf` out of Git.
2. Create the image bucket in the same region:
   ```
   gcloud storage buckets create gs://YOUR-BUCKET-NAME --project=YOUR-PROJECT-ID --location=us-east4 \
     --default-storage-class=STANDARD --uniform-bucket-level-access --public-access-prevention
   ```
3. Upload the lab images (N9Kv, FRR, Alpine, Kea) with an image-ID manifest: `bash lab/gcp/upload-images.sh`.
   A rerun skips images that are already there with the same ID.

## Bring up the VM

`bash lab/gcp/gcp-up.sh ondemand` (or `spot`). It enables the APIs, creates a budget with alerts at 50 % and 90 %,
opens SSH from your current public IP only, gives the VM a service account that can only read the bucket, creates an
n2-standard-64 with nested virtualization and a 200 GB disk, and checks `/dev/kvm` over SSH. Every step checks first, so a
rerun changes nothing that already exists.

The VM stops itself after 8 hours (`MAX_RUN`) and after 30 minutes with no SSH session (`idle-stop.sh`). Stopped, you pay
for the disk only. Run `gcp-up.sh` again to start it.

## Build and run the lab on the VM

Source the helpers on your machine: `. lab/gcp/vm-ssh.sh` (gives `vup`, `vssh`, `vscp`). Then:

| Step | Command | 25 Oct run |
|---|---|---|
| 1. Tools | `vscp lab/gcp/vm-bootstrap.sh "~/"` then `vssh 'bash vm-bootstrap.sh'` | Docker, compose, uv |
| 2. Repository | `vssh 'git clone https://github.com/cciedata/Cisco-vxlan-evpn.git'` | |
| 3. Build | copy `vm-build.sh` the same way, `vssh 'bash vm-build.sh'`: QUICKSTART steps 2 to 5, then every config rendered and compared | the generator took about 2.5 minutes per site |
| 4. Repository in Infrahub | as in [QUICKSTART.md](QUICKSTART.md): the lab fetches the stored artifacts | |
| 5. Images and containerlab | `vssh 'BUCKET=YOUR-BUCKET-NAME bash vm-lab-prep.sh'` | 4 images loaded, IDs checked |
| 6. Lab | `vssh 'bash vm-lab.sh prep'` then `vssh 'bash vm-lab.sh deploy'` | N9Kv boot about 12 minutes; push 3 to 6 minutes per switch; TCAM carve about 25 minutes per site |
| 7. Checks | `lab/postcheck.py` per site, as in [LAB.md](LAB.md) | |

`vm-check.sh` prints the BUILD checks and compares the lab files with their expected copies. `vm-ztp.sh` runs ZTP for
DC1-L11 on the VM's own Infrahub.

## Tear down

- `bash lab/gcp/gcp-down.sh stop`: keeps the disk, so the next start skips steps 1 to 5.
- `bash lab/gcp/gcp-down.sh delete`: removes the VM and its disk. The bucket, budget, firewall rule and service account stay;
  delete them in the console when you are done.

## Cost

Prices on 26 Oct 2026, us-east4, Linux, USD. Check the current price before you start; Google changes them.

| Item | Price | Source |
|---|---|---|
| n2-standard-64, on-demand | $3.50 per hour | Holori calculator (updated 26 Oct 2026) |
| n2-standard-64, spot | $0.96 per hour | Holori calculator (updated 26 Oct 2026) |
| Balanced disk | $0.000137 per GiB-hour (about $0.10 per GiB-month) | Google disk pricing page (region not stated there) |
| Cloud Storage, Standard | $0.000027 per GiB-hour (about $0.02 per GiB-month) | Google Cloud Storage pricing |

- One hour of lab: about $3.53 on-demand (VM plus 200 GB disk), or about $0.99 on spot.
- One 8-hour session: about $28 on-demand, about $8 on spot. Spot VMs can be stopped by Google at any time; the lab has
  to be deployed again after that.
- Stopped VM: the 200 GB disk, about $20 a month. Delete the VM to stop that.
- The bucket holds a few GB of images: cents a month.

Sources:
- [Holori: n2-standard-64 in us-east4](https://calculator.holori.com/gcp/vm/n2-standard-64/us-east4)
- [Google Cloud: disk pricing](https://cloud.google.com/compute/disks-image-pricing)
- [Google Cloud: Cloud Storage pricing](https://cloud.google.com/storage/pricing)
