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

## Diagnosing the missing collapse (deviation 23)

The first 45 EEVDF runs of experiment A never thrashed. These scripts found
why, all writing to `/srv/diag` on the VM, never to the campaign's
`/srv/results`. Every setting they change is undone on exit.

| File | What it does |
|---|---|
| `diag-thrash.py`, `diag-run.py`, `diag-overlap.py` | read the 45 runs: no swapping in 4 of 5 N=8 runs, the sampler's gaps, and that only 4–5 of 8 jobs were ever past their fill at once (the waves) |
| `diag-swap.sh` | every limit between a job and swap: none set, so not a cap |
| `preflight-io.sh` | proves a 3 MB/s cap on `stasis.slice` throttles (30 MB at 3.0 MB/s) before the slow-swap runs rely on it |
| `diag-eevdf.sh` | N=8 under control, MGLRU off, and the jobs' I/O capped at 3 MB/s: waves in all three |
| `diag-barrier.sh` | N=4 and N=8 with a copy of wss that waits after its fill: N=8 collapses (26 % and 7 % of peak). It self-tests the binary first, after a first version deadlocked by waiting before flushing its start marker. |
| `diag-compare.py` | the comparison table quoted in deviation 23 |

## After deviation 23

| File | What it does |
|---|---|
| `update-and-e2e.sh` | moves the VM's repo to the `deviation/start-barrier` branch (from a git bundle), installs the barrier `wss`, and tests the barrier end to end at N=2 for `eevdf` and `ctrl` in `/srv/diag/e2e` |
| `redo-eevdf.sh` | sets the 45 barrier-free EEVDF runs aside in `/srv/superseded/nobarrier` (kept, unscored) and redoes them under the barrier. Started 2026-10-25 08:04 UTC; stopped by a hard reset at 14:37 UTC after 10 runs, and its job taken over by `stasis-all.sh` |
| `stasis-all.sh`, `stasis-all.service`, `install-all.sh` | the whole campaign, unattended, as a service that starts at every boot: A's EEVDF runs and `PFF_HI`'s calibration, then a wait for the frozen `PFF_HI` in `/etc/stasis-all.env`, a scheduler smoke test (`scxonly` and `stasis` in `/srv/gates`; a failure stops everything), the rest of A, a 2-run pilot of C, B and C. One run at a time, resumable, rebooting every 40 runs. A run with a sampler gap over 300 s gets `sampler_gap.txt`, and one with kernel soft lockups gets `host_stall.txt`; neither is discarded or rerun |
| `restart-all.sh` | one-off, 2026-10-25 15:39 UTC: stops the service and the run in flight to install a fixed `stasis-all.sh` (see below) |
| `live-test.sh` | not the VM: starts fake jobs in `stasis.slice` under WSL so the dashboard's Live view has something to show |

`stasis-all.sh`'s first version moved any run with a sampler gap over 300 s
aside and ran it again, taking the gap for host sleep. A 7-job EEVDF run
starves the sampler that long by itself, so `eevdf/N7/run5` ran three
times: once through a real host sleep (kept in
`/srv/superseded/host-sleep/`), then a 1200 s TIMEOUT, then OK.
`restart-all.sh` kept the TIMEOUT, the second attempt and the first without
a sleep, and deleted the third, which only existed because of the bug.

## Conditions every run depends on

`PASSES = 5874` holds only on this VM with the host on AC power, in Windows'
*Best performance* power mode, with other applications closed. The power
mode alone changed the per-pass time by about 40 %. The per-run MANIFEST does
not record it, so it is stated here and in docs/00-prereg.md.

Two more, found on 2026-10-25:

- **The host must not sleep.** Windows' Modern Standby (events 506/507)
  froze the VM from 08:52 to 13:45 UTC, in the middle of `eevdf/N7/run5`.
  The guest's clock follows the host's through sleep, so the VM can't
  detect it; check runs flagged `sampler_gap.txt` against the host's power
  log. Sleep is now set to Never on AC.
- **The host must have memory to spare.** With 0.6–1.6 GB free on the
  15.5 GB host, Windows paged out up to 1.7 GB of the VM's 4 GB, and the
  guest logged soft lockups, idle CPUs included (`swapper`), which only the
  host can cause. Every overloaded EEVDF run since 2026-10-24 has some: 1 at
  N=3 up to 21 at N=8; N=2 and the e2e runs none. Closing the browsers and
  stopping WSL brought the VM's resident memory back to 3.8 of 4 GB. How
  flagged runs are treated is for a deviation to decide, before scoring.
