# The lab VM, and how the campaign is being run on it

These are the scripts that built `stasis-lab`, the VM on which `PASSES` was
calibrated (docs/00-prereg.md, "PASSES calibration") and experiment A is
being run. They are a record of what was actually done, kept so it can be
checked and repeated; paths are those of the machine they ran on.

The host is a Windows 11 Home laptop. Home has no Hyper-V Manager, so the VM
is VirtualBox 7.2.22, the build guide's own fallback, running on top of
Hyper-V because WSL2 is enabled.

## Building the VM (Windows side)

| File | What it does |
|---|---|
| `seed/user-data`, `seed/meta-data` | cloud-init first-boot config: user `stasis`, SSH key login only, no passwords. Built into `seed.iso` with `cloud-localds seed.iso user-data meta-data` (cloud-image-utils). The key in `user-data` is the public half; the private key never leaves the host. |
| `create-vm.ps1` | imports the official Ubuntu 26.04 cloud image (`ubuntu-26.04-server-cloudimg-amd64.ova`, SHA256 checked against Canonical's SHA256SUMS), converts the disk to a 40 GB VDI, and sets the guide's shape: 4 GB **fixed** memory, 4 vCPUs. NAT with SSH (2222) and the dashboard (8687) forwarded to **127.0.0.1 only**, and a serial console logged to a file. |

`create-vm.ps1` stopped once on a fresh import, because the cloud image
already carries IDE controllers. The seed was then attached to the existing
`IDE` controller, port 1, and the remaining steps were run by hand: fixed
memory, NAT forwards, start. A repeat should do the same.

The `stasis` user has passwordless sudo: the campaign runs everything as
root through `sudo`, and the VM is reachable only from the host, by key.

## Inside the VM

| File | What it does |
|---|---|
| `provision.sh` | clones the repo from a git bundle (the GitHub repo is private), strips the CRLF line endings the repo is stored with (working copy only, so every MANIFEST records the commit as `-dirty`), runs `scripts/setup_vm.sh`, then `scripts/check_gate0.sh`. Gate 0 passed with 0 failures and 0 warnings; a VirtualBox snapshot named `clean` was taken after it. |
| `demo.sh` | starts the dashboard and one **demo** full-STASIS run (placeholder `PFF_HI = 1.0`, filed outside the results root). Not campaign data. |
| `phase1.sh` | moves the demo out of `/srv/results`, runs `calibrate.sh` three times, and plans experiment A (155 runs). |
| `calib-long.sh` | the calibration actually used for `PASSES`: `calibrate.sh`'s formula on a 600-pass sample, five times after a discarded warm-up. `calibrate.sh` times 3 passes, about 60 ms on this VM, too short to measure. |
| `run-eevdf.sh` | experiment A's 45 EEVDF runs (campaign.sh given an EEVDF-only plan, then the full plan restored), then `analyze.py metrics` and `calibrate` for `PFF_HI`. |
| `stop-dashboard.sh` | stops the dashboard for the rest of the campaign and writes `/srv/results/NOTE-dashboard-during-runs.txt`. It had been left on from the demo into the first two EEVDF runs, against the README's rule. |
| `rerun-two.sh` | after the sweep, deletes and re-runs those two runs with the dashboard off, then recalibrates `PFF_HI` on all 45. |

## Conditions every run depends on

`PASSES = 5874` holds only on this VM with the host on AC power, in Windows'
*Best performance* power mode, with other applications closed. The power
mode alone changed the per-pass time by about 40 %. The per-run MANIFEST does
not record it, so it is stated here and in docs/00-prereg.md.
