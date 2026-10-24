# STASIS: Novelty, Prior Art, and Candidate Features

Research date: 2026-10-23. Scope: what in STASIS is novel, what is not, and ranked additional
features. Honest-novelty framing throughout; where I could not find a source I say so in **Gaps**.

A standing caveat for the whole document: searching turned up **no paper that matches STASIS's
full combination**, but it also turned up **no survey of thrashing-control systems post-PSI**.
Absence of a found paper is weak evidence of absence. Several arXiv IDs returned by search are
very recent (2026) preprints I could only see via search snippets, not full text; those are
flagged.

---

## Q1. Which specific combination of (page-fault frequency + pluggable CPU scheduler + fair rotation + cgroup freezing + admission control) has or has not been done before?

### Takeaway
Every one of the five ingredients has strong, named prior art, and three of the pairings are
essentially solved problems in industry (PFF→load control: Denning/Chu-Opderbeck 1968-76;
PSI→actuation: Meta's Senpai/TMO and systemd-oomd; freeze/suspend-on-thrash: thrash-protect).
The one pairing I found **no** prior art for is **(page-fault-frequency signal computed inside a
pluggable BPF CPU scheduler) × (userspace freeze/admission control)** — i.e. putting the thrashing
detector in the CPU scheduler and closing the loop with memory actuation. That cross-subsystem
coupling is the defensible novelty claim, and it is narrow.

### Cited Findings

**PFF as a thrashing control signal — fully prior art, 1960s-70s.**
- Denning's working-set model: the working set is the set of pages recently referenced in a window
  Δ; thrashing occurs when summed demand D exceeds available frames m, and the OS then "selects a
  process to suspend" — which is exactly STASIS's park action — [Working set (Wikipedia)](https://en.wikipedia.org/wiki/Working_set); [Techniques to handle thrashing](https://www.geeksforgeeks.org/operating-systems/techniques-to-handle-thrashing/)
- Chu (UCLA) and Opderbeck (Telenet) formalised the **Page Fault Frequency (PFF)** algorithm:
  it "uses the measured page fault frequency as its main input, and it attempts to dynamically
  control the rate of page faults by varying the memory allocated to the program." Opderbeck's 1973
  UCLA dissertation is literally titled *The Page Fault Frequency Replacement Algorithm in
  Multi-Programing Environment*; a 1976 CACM paper analysed PFF via a semi-Markov model — [search synthesis of Chu/Opderbeck sources](https://www.academia.edu/58449733/Program_behavior_and_the_page_fault_frequency_replacement_algorithm) (dates conflict between sources: 1972 vs 1973 vs 1976; the 1970s range is reliable, the exact year is not)
- Practitioner literature already warns of the exact failure mode STASIS's controller must avoid:
  "PFF will oscillate if measurement is noisy" — [The Linux Code: Thrashing, working sets and PFF](https://thelinuxcode.com/thrashing-handling-it-in-practice-with-working-sets-and-page-fault-frequency/)
- Normalising faults per unit of the process's **own CPU time** (STASIS's variant) I could not find
  as a named prior metric — see Gaps.

**Freezing/suspending the thrasher — fully prior art, and open-source.**
- `thrash-protect` (tobixen) detects thrashing from `/proc/vmstat` swap counters and sends
  SIGSTOP/SIGCONT: "attempts to detect thrashing situations and stop rogue processes for short
  intervals"; if swapping resumes "the last resumed PID will be refrozen." Its third victim-selection
  algorithm **is page-fault counts**, and the README admits that approach "produces false positives
  and misses write-only memory growth" — [thrash-protect README](https://github.com/tobixen/thrash-protect)
- thrash-protect also already does a crude **rotation**: frozen PIDs are managed by "get from the
  head of the list sometimes, pop from the tail most of the times," because a pure stack "may
  permanently freeze relatively innocent processes" and a pure queue "is inefficient and causes
  paging." And it states outright: the tool "is not optimized to be 'fair'" — process B may flap
  while A runs at 100% — [thrash-protect README](https://github.com/tobixen/thrash-protect)
- It plans to move to PSI: "in the future, /proc/pressure/memory will probably be used instead" —
  [thrash-protect README](https://github.com/tobixen/thrash-protect)
- earlyoom and nohang are the kill-based cousins; nohang's README calls itself "earlyoom on
  steroids" — [nohang](https://github.com/hakavlad/nohang). `systemd-oomd` "uses cgroups-v2 and PSI
  to monitor and act before an OOM occurs in kernel space," opted into per unit via
  `ManagedOOMSwap=` / `ManagedOOMMemoryPressure=` — [systemd-oomd(8)](https://man7.org/linux/man-pages/man8/systemd-oomd.service.8.html)

**PSI-driven control loop + proactive reclaim — fully prior art, Meta, production scale.**
- Senpai "runs a control loop around observed memory pressure," uses the kernel's reclaim to find
  cold pages, and aims reclaim "at specific applications via cgroup2's memory controller" — i.e.
  STASIS's `memory.reclaim` actuation path — [Meta: Transparent memory offloading](https://engineering.fb.com/2022/06/20/data-infrastructure/transparent-memory-offloading-more-memory-at-a-fraction-of-the-cost-and-power/)
- TMO (ASPLOS'22): Senpai "uses PSI information to drive the offload process, writing to cgroup
  control files," "needs no offline profiling," and deliberately operates at a *subliminal* pressure
  level rather than treating swap as emergency overflow — [TMO, ASPLOS'22 (PDF)](https://www.cs.cmu.edu/~dskarlat/publications/tmo_asplos22.pdf)
- PSI itself "measures lost work from resource shortage across CPU, memory and I/O... reported per
  process and per container," reflecting sensitivity to slowdown rather than raw page-in counts —
  [TMO paper](https://www.cs.cmu.edu/~dskarlat/publications/tmo_asplos22.pdf)

**Admission control on estimated working set — prior art in tiered memory and cloud oversubscription.**
- xTier (2026 preprint) applies "three admission-control rules at the page, epoch, and fast-tier
  levels before issuing a promotion intent," holding recently-moved pages out for a fixed number of
  epochs — structurally the same hysteresis STASIS needs — [xTier (arXiv 2609.27266)](https://arxiv.org/html/2609.27266)
- TierBPF is titled "Page Migration Admission Control for Tiered Memory via eBPF" and makes "binary
  page admission decisions" — [TierBPF (arXiv 2604.12300)](https://arxiv.org/pdf/2604.12300) (title and
  abstract-level only; I did not read the full text)
- Google Autopilot picks memory statistics according to a job's OOM-tolerance and raises limits as
  usage grows — admission/limit control from observed working set at cluster scale — [Autopilot, EuroSys'20](https://john.e-wilkes.com/papers/2020-EuroSys-Autopilot.pdf)
- Coach (2025) does all-resource cloud oversubscription and "trims cold pages before asking for more
  memory" — [Coach (arXiv 2501.11179)](https://arxiv.org/html/2501.11179v2)

**Fair rotation of suspended jobs — closest prior art is HPC/MapReduce, not memory.**
- Slurm gang scheduling suspends jobs *in memory* and explicitly warns the admin to track per-job
  memory "to ensure that memory page swapping does not occur" — i.e. Slurm's rotation is CPU-fair
  and memory-naive, the inverse of STASIS — [Slurm gang scheduling](https://slurm.schedmd.com/gang_scheduling.html)
- OS-assisted preemption for Hadoop argues suspended jobs don't themselves thrash, but "thrashing
  could only happen if a given job is continuously suspended and resumed by the scheduling
  mechanism. A reasonable scheduler implementation should take into account that suspending and
  resuming a job has a cost" — the precedent for STASIS's CPU-time quantum — [OS-Assisted Task Preemption for Hadoop (arXiv 1402.2107)](https://arxiv.org/pdf/1402.2107)
- HFSP measured that suspend/resume swap traffic "operates roughly at disk raw-speed," and that at
  most ~10% of running tasks were ever suspended/resumed — a useful cost baseline — [Practical Size-based Scheduling for MapReduce (arXiv 1302.2749)](https://arxiv.org/pdf/1302.2749)

**Pluggable CPU scheduler × memory pressure — the thin spot.**
- sched_ext is a "BPF-extensible scheduler class"; the kernel kills a BPF scheduler and falls back
  to CFS/EEVDF if a task is starved for ~30s — [LWN: The extensible scheduler class](https://lwn.net/Articles/922405/); [sched_ext (Igalia)](https://blogs.igalia.com/changwoo/sched-ext-a-bpf-extensible-scheduler-class-part-1/)
- scx_lavd, the flagship interactivity scheduler, infers latency-criticality from "wake/wait
  frequency, runtime duration, waker/wakee relationships, and IRQ context" — purely CPU-side signals,
  **no memory signal** — [scx_lavd docs (third-party mirror; secondary source)](https://gitea.hillion.co.uk/JakeHillion/scx-upstream/raw/branch/experimental-scheds/ai_docs/SCX_LAVD.md)
- A sched_ext batch-pacing PoC uses PSI as a *success metric* and warns about bursty phases raising
  "elevated Pressure Stall Information (PSI)" and tail latency — but explicitly notes "this is not an
  I/O scheduler, so it doesn't solve I/O pressure directly" — [Pacing batch workloads with sched_ext](https://dev.to/xsub/pacing-batch-workloads-with-linux-schedext-an-engineering-poc-2chf)
- An scx issue thread about stuttering under heavy I/O shows maintainers treating memory pressure as
  a *separate diagnosis* from scheduler behaviour ("we're not dealing with memory pressure
  conditions") — evidence the coupling is not currently made — [scx issue #96](https://github.com/sched-ext/scx/issues/96)
- The kernel community is actively building the *other* half (BPF for MM) right now: LSF/MM/BPF 2026
  sessions on BPF OOM handlers (Roman Gushchin, struct_ops based), BPF memcg callbacks
  (`bpf_memcg_charge_succeed()` to kick off background reclaim), BPF NUMA balancing and page-cache
  eviction. Gushchin's open question: "what happens if a faulty program leaves the system unable to
  reclaim memory" — [LWN: Controlling memory management with BPF](https://lwn.net/Articles/1072538/)
- Same article, the quote that most threatens the project's framing: BPF maintainer Alexei
  Starovoitov said **"sched_ext was a mistake"** because no production schedulers came with it —
  [LWN](https://lwn.net/Articles/1072538/)

### Inferences
- The honest novelty statement is narrow and structural, not algorithmic: *the control signal lives
  in the CPU scheduler, the actuator lives in the memory cgroup, and the loop is closed across the
  two subsystems.* Nothing I found does that. Every individual box is occupied.
- STASIS's strongest *differentiator against thrash-protect* is not the freeze — it is (a) a
  CPU-time-normalised fault rate instead of raw fault counts or vmstat deltas, and (b) explicit
  fairness in rotation, which thrash-protect's README concedes it lacks. Both are directly citable
  against a named open-source baseline, which is a much better viva position than "versus Denning."
- The LWN BPF-MM thread is simultaneously the best and worst news: it shows the research direction
  is live and timely (good), and that kernel developers will shortly make it easy (bad for a
  durable claim). Write the dissertation as "what a scheduler-coupled policy can do *before* BPF-MM
  hooks land, and what it tells us about which hooks are needed" — that is a contribution the LWN
  piece's own open questions invite.

### Gaps
- No source found for "major faults per millisecond of the process's own CPU time" as a prior metric.
  I could not establish whether this normalisation is novel or merely unpublished folklore. It is
  close in spirit to PFF (which is faults per unit *virtual* time between faults), so a reviewer may
  call it a re-parameterisation of PFF. Treat as **probably not novel in kind, possibly novel in form.**
- I found no systematic survey of PSI-era thrashing-control systems, and no paper that evaluates
  freeze-vs-kill-vs-reclaim as alternative actuators under a common benchmark. This is itself a gap
  in the literature STASIS could occupy.
- The HPDC'22 DAMON paper exists per the project site but I could not retrieve its full citation.

---

## Q2. What would a reviewer say is "just Denning re-implemented"? What is the strongest counter-argument?

### Takeaway
The attack is legitimate and should be pre-empted in the thesis, not dodged: detector (PFF),
actuator (suspend a process), and admission rule (D ≤ m) are all Denning-era. The strongest
counter-argument is not "we did something new" but **"Denning's load control was never evaluated
against the actuators and signals that exist in 2026, and the three things that make it hard today
— per-cgroup actuation granularity, a stall-time rather than fault-count signal, and a pluggable
scheduler that can see per-task fault behaviour — did not exist."**

### Cited Findings
- The viva attack, stated from the literature: thrashing is handled by working-set or PFF, and under
  overload "the operating system selects a process to suspend." That is STASIS's architecture in one
  sentence, from a textbook — [Working set](https://en.wikipedia.org/wiki/Working_set); [Techniques to handle thrashing](https://www.geeksforgeeks.org/operating-systems/techniques-to-handle-thrashing/)
- Counter 1 — the signal is categorically different. PSI "measures lost work from resource shortage
  ... in real time ... reflecting how sensitive the workload is to slowdown rather than just counting
  page-ins." Denning's D>m and PFF count *events*; PSI measures *lost work*. This is not a
  re-parameterisation, it is a different observable — [TMO](https://www.cs.cmu.edu/~dskarlat/publications/tmo_asplos22.pdf)
- Counter 2 — the actuator is categorically different. Denning-era suspension was SIGSTOP on a
  process; cgroup v2 gives atomic hierarchical freeze plus *directed* reclaim (`memory.reclaim`),
  which Senpai uses to aim reclaim "at specific applications via cgroup2's memory controller."
  Freeze + proactive reclaim is a two-actuator policy Denning had no vocabulary for — [Meta TMO blog](https://engineering.fb.com/2022/06/20/data-infrastructure/transparent-memory-offloading-more-memory-at-a-fraction-of-the-cost-and-power/)
- Counter 3 — the hardware/software substrate invalidates the old parameterisation. thrash-protect
  weights disk swap "128 times (HDD) or 8 times (SSD) compared with zswap pages," its author
  "realized the default values aren't optimized for SSDs," and reports a zram laptop that "froze
  processes 37 + 4 times in eleven hours without real pressure" — i.e. Denning-era thresholds
  produce false positives on modern storage — [thrash-protect README](https://github.com/tobixen/thrash-protect); [PR #42](https://github.com/tobixen/thrash-protect/pull/42)
- Counter 4 — the problem is demonstrably unsolved in practice. nohang's own known-problems list
  includes incorrect PSI metrics without `CONFIG_CGROUP_CPUACCT=y`, and ZFS ARC not counting toward
  MemAvailable — [nohang README](https://github.com/hakavlad/nohang). HN discussion cited in the same
  search notes "you'll still get thrashing before the OOM killer gets triggered, even with no swap
  and overcommit disabled."
- Counter 5 — it is being actively re-opened by kernel developers, not treated as settled: LSF/MM/BPF
  2026 has live sessions on BPF OOM handlers, BPF memcg callbacks, and "a generic reclaim hook that
  frees memory under pressure, possibly by killing processes" — [LWN](https://lwn.net/Articles/1072538/)
- Counter 6 — mobile OSes went the *other* way for want of a good policy: Marvin (ATC'20) notes
  "mobile OSes often kill apps instead of swapping to disk" — evidence that suspend-vs-kill policy
  remains contested, not resolved — [End the Senseless Killing / Marvin, USENIX ATC'20](https://www.usenix.org/system/files/atc20-lebeck.pdf)

### Inferences
- The defensible thesis claim is **empirical, not conceptual**: "Denning's load control, re-derived
  with a stall-based signal and cgroup actuators, works — and here is the quantified cost of each
  design choice, measured." A master's thesis is allowed to be an *evaluation* contribution. Trying
  to claim conceptual novelty against Denning will lose; claiming a measured comparison against
  thrash-protect / earlyoom / systemd-oomd / do-nothing baselines will win.
- Concretely: name thrash-protect as the baseline and reproduce its documented pathologies (zram
  false positives, unfairness where "process B may flap while A runs at 100%", whitelisting that
  "lowers the score ... and nothing more" so a whitelisted terminal is still unusable). If STASIS
  fixes those three with measurements, that is a real contribution independent of Denning.
- The single best rhetorical move: frame STASIS as **"load control as a feedback controller, not a
  heuristic"** (see Q4) and as **"load control with a per-task signal the scheduler already has."**

### Gaps
- I found no published critique or reproduction study of thrash-protect/earlyoom/nohang that could be
  cited as "prior work shows these fail." The evidence is the maintainers' own READMEs and issues —
  credible but self-reported. STASIS measuring this itself is therefore genuinely new data.

---

## Q3. Ranked candidate features

Ranking = (novelty impact) / (effort for one student, one 4 GB VM). "Frozen design" column is the
critical constraint: whether the feature forces re-registration of the pre-registered thresholds or
hypotheses.

| # | Feature | Novelty impact | Effort | Touches frozen thresholds/hypotheses? |
|---|---|---|---|---|
| 1 | Real-workload replacement for synthetic jobs | High | Medium | **Yes — new workloads mean new H's. Add as a separate, newly pre-registered arm; do not touch arm 1.** |
| 2 | Named-baseline comparison (thrash-protect / earlyoom / systemd-oomd / none) | High | Low | No — same thresholds, extra treatment arms |
| 3 | Formal control-theory treatment of the control law | High | Medium (analysis-heavy, little code) | No, if purely analytical on the *existing* law. Yes if the law changes. |
| 4 | DAMON-based working-set estimation instead of `memory.peak` | Medium-High | Medium | **Yes — admission threshold is defined in terms of the estimator.** Run as an estimator-swap ablation with its own hypothesis. |
| 5 | Priority classes / multi-tenant fairness with a measured fairness metric | Medium-High | Medium | No, if added as a new metric on existing runs; Yes if the rotation quantum changes |
| 6 | Predicting thrashing before onset | High if it works | High | **Yes — a predictive trigger replaces the PSI trigger.** |
| 7 | Energy measurement | Low-Medium | Low-Medium (RAPL unreliable in a VM) | No — observation only |
| 8 | Checkpoint/restore (CRIU) instead of freeze | Medium | High | **Yes — changes the actuator the hypotheses are about** |
| 9 | Container/Kubernetes integration | Low | Medium-High | No (engineering, not science) |
| 10 | CXL / tiered-memory awareness | Low here (can't be done honestly on one VM) | Very High | Yes |

### Takeaway
Items 1-3 are the whole dissertation's novelty budget. Items 4-5 are good ablations. Items 6-10 are
traps for a solo student on one 4 GB VM: 6 and 8 are high-effort and invalidate the pre-registration,
and 10 cannot be evaluated without hardware STASIS does not have.

### Cited Findings per feature

**1. Real application workloads.** No single standard "memory-pressure suite" exists; the de facto
practice is to assemble one.
- Hermes (memory allocation under pressure) evaluates **RocksDB and Redis** query latency and SLO
  violations "under different memory pressure levels," reporting up to 84.3% fewer SLO violations
  vs glibc/jemalloc/TCMalloc — the closest template for a pressure-vs-latency evaluation —
  [Memory at Your Service (arXiv 2109.02922)](https://arxiv.org/html/2109.02922v1)
- RocksDB and memcached are commonly driven from the **Phoronix `pts/server` suite**; the memcached
  test uses `memtier_benchmark` — ready-made, low-effort harnesses —
  [ARM MTE in practice](https://arxiv.org/pdf/2601.11786); [Memcached on OpenBenchmarking](https://openbenchmarking.org/test/pts/memcached)
- Caladan's canonical pairing is **memcached as the latency-critical task against a
  garbage-collected best-effort task**, with tail latency as the headline metric (580 ms → 52 µs vs
  Parties) — the standard LC/BE experimental shape to imitate — [Caladan, OSDI'20](https://pdos.csail.mit.edu/papers/caladan:osdi20.pdf)
- I found **no** source establishing a kernel-compile workload or an LLM inference server as a
  standard memory-pressure benchmark. Kernel compile is a defensible *memory-hungry batch job* (it
  is self-evidently real and reviewers accept it), but it is not a cited standard. An LLM inference
  server on a 4 GB VM is not credible; do not attempt it.
- Recommendation: memcached (`memtier_benchmark`) or RocksDB as the protected interactive tenant,
  replacing cyclictest; kernel compile and/or RocksDB bulk-load as the memory-hungry parked jobs.
  Keep cyclictest as a cheap sanity probe.

**2. Named-baseline comparison.** Highest value per unit of effort in the whole table.
- thrash-protect is installable, documents its own three victim-selection algorithms, and documents
  its own failure modes (unfairness, zram false positives, ineffective whitelisting) — a baseline
  that hands you your own hypotheses — [thrash-protect README](https://github.com/tobixen/thrash-protect)
- earlyoom's CPU-usage-vs-check-interval tradeoff is an open issue, giving a second, measurable axis
  (controller overhead) — [earlyoom issue #61](https://github.com/rfjakob/earlyoom/issues/61)
- systemd-oomd is already on Ubuntu and is configured per unit via `ManagedOOMMemoryPressure=` —
  a zero-install baseline — [systemd-oomd(8)](https://man7.org/linux/man-pages/man8/systemd-oomd.service.8.html)
- Caution: one secondary source and another disagree on whether the key is
  `DefaultMemoryPressureDurationSec` or `...USec`; verify against `systemd-oomd(8)` and
  `systemd.resource-control(5)` before writing it into a config — [search-noted conflict](https://man7.org/linux/man-pages/man8/systemd-oomd.service.8.html)

**3. Formal control-theory framing.** See Q4 — this is the single best novelty-per-line-of-code item.

**4. DAMON-based working-set estimation.**
- DAMON exists precisely for this and has a worked working-set-size estimation module demonstrated
  at LPC 2021; the project hosts "dynamic working set size distribution and chronological working
  set size changes for various workloads" — [DAMON/DAMOS, LPC'21 slides](https://lpc.events/event/11/contributions/984/attachments/870/1670/daos_ksummit_2021.pdf); [damonitor.github.io](https://damonitor.github.io/)
- DAMOS adds "automated access-aware system operations and user-space driven auto-tuning" per an
  HPDC'22 paper — meaning **DAMON+DAMOS already is an access-aware actuation framework**, so
  "we used DAMON" is not itself novel; only "we used DAMON's WSS estimate as the admission predicate
  and measured it against `memory.peak`" is — [damonitor.github.io](https://damonitor.github.io/)
- There is also an eBPF-based WSS-estimation line of work, relevant if you want to keep everything
  in BPF rather than adding DAMON — [eBPF-based Working Set Size Estimation (arXiv 2303.05919)](https://arxiv.org/pdf/2303.05919)
- A practical replication risk: DAMON needs to be sampling *before* you want the estimate, so the
  estimator has a lead-time problem that `memory.peak` does not. Diao et al. name exactly this class
  of problem — "handling sensor delays, and accounting for lead times in actuator actions" — as an
  open challenge in self-managing systems — [A control theory foundation for self-managing computing systems](https://www.researchgate.net/publication/3236201_A_control_theory_foundation_for_self-managing_computing_systems)

**5. Priority classes / multi-tenant fairness.**
- Equilibria ("Fair Multi-Tenant CXL Memory Tiering At Scale") is the closest recent framing, and in
  its thrashing experiments it works by deliberately rate-limiting one container's promotions —
  i.e. fairness enforced by throttling a tenant, the same shape as STASIS's rotation —
  [Equilibria (ResearchGate listing; secondary)](https://www.researchgate.net/publication/400622094_Equilibria_Fair_Multi-Tenant_CXL_Memory_Tiering_At_Scale)
- Borg's distinction is the vocabulary to borrow: oversubscribed resources were "latency-tolerant CPU
  and non-production memory," and importance is separate from urgency — [Brian Grant on Borg QoS/oversubscription](https://twitter.com/bgrant0607/status/1153341109692588032) (secondary; the Borg/Omega papers are the proper citation)
- Autopilot's memory policy is keyed to a job's **OOM-tolerance class** — a ready-made priority-class
  taxonomy with a real citation — [Autopilot, EuroSys'20](https://john.e-wilkes.com/papers/2020-EuroSys-Autopilot.pdf)
- thrash-protect's self-reported unfairness ("process B may flap while process A runs at 100%") is
  the baseline you measure against — [thrash-protect README](https://github.com/tobixen/thrash-protect)

**6. Predicting thrashing before onset.** Attractive, poorly supported, high risk.
- I found **no** work that forecasts node-level memory pressure and acts on the forecast to drive
  preemption. The nearest is a 2025 ACM paper using eBPF telemetry + LightGBM to predict **memory
  fragmentation** and trigger defragmentation pre-emptively — the right methodological template,
  wrong target variable — [Prediction and optimization of memory fragmentation based on eBPF and LightGBM (ACM DL)](https://dl.acm.org/doi/10.1145/3703935.3704087)
- Martin Maas et al.'s ML-predicted **allocation lifetimes** is the other adjacent precedent (ML on
  memory behaviour, inside the allocator) — [Learning-based memory work, per search synthesis](https://par.nsf.gov/servlets/purl/10187649)
- Counter-argument against doing it: TMO's design philosophy is that you *don't need* prediction if
  you act at a *subliminal* pressure level continuously — Senpai "needs no offline profiling" —
  [TMO](https://www.cs.cmu.edu/~dskarlat/publications/tmo_asplos22.pdf). A reviewer can reasonably
  ask why prediction beats acting earlier on a continuous signal. Strong, cheap alternative: instead
  of ML, add a **derivative/lead term** (PSI rate-of-change, or fault-rate slope) to the existing
  control law and analyse it as a D-term. Same claim ("acts before onset"), a tenth of the work, and
  it folds into feature 3.

**7. Energy.** Low novelty; mainly a figure that reviewers like.
- No source found linking thrashing control to energy measurements. In a VM, RAPL/`powercap` is
  usually unavailable or meaningless, so the honest version is a *proxy* (swap bytes written, CPU
  seconds consumed by the controller). Report it as a proxy, not as joules.

**8. CRIU checkpoint/restore instead of freeze.**
- The cost evidence is against it: HFSP measured suspend/resume swap traffic running "roughly at disk
  raw-speed" — and CRIU dumps the *whole* address space, which is strictly worse than freeze +
  targeted reclaim for the thrashing case — [HFSP (arXiv 1302.2749)](https://arxiv.org/pdf/1302.2749)
- The GPU-job-switching patent is the mechanism precedent (copy a job's memory out, suspend, run
  another, select victims by memory size/utilisation/priority) — [USPTO 11494228](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11494228)
- Verdict: interesting as a *discussion* section ("why freeze beats checkpoint under thrashing, with
  numbers"), not worth implementing.

**9. Container/Kubernetes integration.** Engineering, near-zero novelty.
- Kubernetes already has burstable QoS as its oversubscription story, and "Kubernetes has proposals
  for oversubscription, but none appeared as shipped features" — [search synthesis, Borg/K8s history](https://twitter.com/bgrant0607/status/1153341109692588032); [Autopilot](https://john.e-wilkes.com/papers/2020-EuroSys-Autopilot.pdf)
- Cluster-scale utilisation numbers (Autopilot "seldom pushes memory use above 50%", vs Alibaba's
  reported 80%) are good *motivation* for the thesis intro without doing the integration —
  [The Register on Borg/Alibaba utilisation (secondary)](https://www.theregister.com/2020/04/29/new_google_borg_data_revealed/)

**10. CXL / tiered memory.** Cannot be done honestly on one 4 GB VM.
- The field is already crowded and moving fast: TPP (ASPLOS'23) for reactive promotion
  — [TPP](https://symbioticlab.org/publications/files/tpp:asplos23/tpp-asplos23.pdf); HybridTier
  (ASPLOS'25) batching 100k samples for promotion decisions —
  [HybridTier](https://www.sihangliu.com/docs/hybridtier_asplos25.pdf); xTier and TierBPF (2026) on
  admission control specifically — [xTier](https://arxiv.org/html/2609.27266), [TierBPF](https://arxiv.org/pdf/2604.12300)
- A zswap/zram tier *is* a legitimate, cheap stand-in for a second memory tier, and thrash-protect's
  zram findings (weighting zswap pages differently; zram "can freeze a box just as badly" once full
  and the kernel starts evicting file pages) give a real, citable phenomenon to study on one VM —
  [thrash-protect README](https://github.com/tobixen/thrash-protect)

### Inferences
- Do items **2, 3, 1** in that order. 2 costs days and converts "Denning re-implemented" into
  "measured against the state of the practice." 3 costs analysis not code and is the only item that
  could plausibly be called a conceptual contribution. 1 costs the most but is what a viva will
  demand first ("does it work on anything real?").
- Items 4 and 5 are the natural "further work implemented if time permits" pair and both make clean
  ablations.
- The pre-registration is an asset, so protect it: treat every new feature as a **new arm with its
  own pre-registered hypothesis appended with a date**, never as an edit to arm 1. Reporting
  "H1-H4 pre-registered 2026-xx and unchanged; H5-H7 pre-registered later for the real-workload arm"
  is more credible than a single frozen set that quietly grew.

### Gaps
- No evidence found on whether RAPL/energy counters are exposed in the specific VM setup; untested.
- I could not retrieve TierBPF's or Equilibria's full texts, only titles/abstracts; the feature-4 and
  feature-5 precedent claims rest on secondary listings and should be verified before citing in the
  thesis.

---

## Q4. Is there a formal control-theory treatment of thrashing control? Would formal framing be novel?

### Takeaway
I found **no** formal stability/convergence analysis of a thrashing controller — no Lyapunov
argument, no AIMD analysis, no transfer-function model of a memory load-control loop. The general
machinery for doing this to computing systems is well established and citable, and memory has been
treated as a controlled resource in self-managing-systems work, but not thrashing specifically.
This is the best novelty-per-effort item in the project, with one honest caveat: STASIS's current law
(halve the active set under pressure; admit when the estimate fits) is **MIMD-like and almost
certainly not provably stable as written** — it is a switched/hybrid system with a saturating
actuator, and a rigorous treatment will more likely *prove it can oscillate* than prove it stable.
That negative result is itself publishable and is a stronger thesis chapter than a hand-wave.

### Cited Findings
- The standard framework to cite: stability, accuracy, settling time, overshoot — the **SASO**
  properties — are the accepted criteria for feedback control of computing systems, and feedback
  loops are designed to "avoid wild oscillations, hit target response times, and settle quickly" —
  [Abdelzaher et al., Introduction to Control Theory and Its Application to Computing Systems](https://www.eecs.umich.edu/courses/eecs571/reading/control-to-computer-zaher.pdf)
- Crucially for STASIS: computing systems "behave acceptably only in certain combinations of workload
  and configuration, so stability is usually assessed within an **operating region** rather than
  globally" — this gives you a defensible, bounded claim: stability over the pre-registered operating
  region, not universal stability — [Abdelzaher et al.](https://www.eecs.umich.edu/courses/eecs571/reading/control-to-computer-zaher.pdf)
- Memory *has* been a controlled variable: MIMO control has regulated CPU and memory together in the
  Apache HTTP server, and the named open challenges are "building usable resource models, handling
  sensor delays, and accounting for lead times in actuator actions" — all three of which STASIS has
  (PSI window lag, reclaim latency, freeze/thaw lead time) — [Diao et al., A control theory foundation for self-managing computing systems](https://www.researchgate.net/publication/3236201_A_control_theory_foundation_for_self-managing_computing_systems)
- Control-theoretic *scheduling* has been formalised, so the methodology transfers — [A control-theoretical methodology for the scheduling problem (arXiv 1009.3455)](https://arxiv.org/pdf/1009.3455)
- Standard analysis tools available: Routh-Hurwitz, Nyquist, root locus, Bode; closed-loop stability
  iff characteristic-polynomial roots have negative real parts — [Controller Stability Analysis](https://apmonitor.com/pdc/index.php/Main/StabilityAnalysis)
- Oscillation is the known, documented failure mode of exactly STASIS's class of controller: "PFF
  will oscillate if measurement is noisy" — [The Linux Code](https://thelinuxcode.com/thrashing-handling-it-in-practice-with-working-sets-and-page-fault-frequency/)
- Observed oscillation in the wild in the closest baseline: a zram laptop where thrash-protect
  "froze processes 37 + 4 times in eleven hours without real pressure" — a measured limit-cycle /
  false-positive chatter — [thrash-protect PR #42](https://github.com/tobixen/thrash-protect/pull/42)
- Prior-art hysteresis designs to compare against: xTier holds a recently-moved page out "for a fixed
  number of epochs" and admits only candidates clearing "a threshold drawn from the recent score
  distribution" — i.e. dead-band plus adaptive threshold — [xTier](https://arxiv.org/html/2609.27266)
- And the actuation-cost term the model needs: "suspending and resuming a job has a cost" and a
  scheduler must account for it — [OS-Assisted Task Preemption for Hadoop](https://arxiv.org/pdf/1402.2107)

### Inferences
- A tractable, honest scope for one student: model the loop as a discrete-time switched system with
  state = active-set size, input = PSI (with a one-sample measurement delay), actuator = multiplicative
  decrease / fit-gated increase. Then (a) derive the dead-band/hysteresis condition under which the
  system has no limit cycle, (b) show the current parameterisation does or does not satisfy it,
  (c) predict the period of the limit cycle when it does not, and (d) **test that prediction against
  the already-collected replay traces.** Prediction-then-verification against existing data is a
  genuine contribution and does not require re-running the frozen experiments.
- Reframe "halve the active set" as **MIMD** and ask the AIMD question explicitly: AIMD's fairness
  and convergence results are classical for congestion control; the analogy (memory as a congested
  resource, parked jobs as backed-off flows) is natural, reviewer-legible, and — as far as I can find
  — unclaimed for thrashing. If you add an additive-increase admission rule you inherit a body of
  convergence argument. **Caveat: I found no source making the AIMD-to-thrashing mapping, so I cannot
  confirm it is unclaimed; I can only say I did not find it.**
- Does this touch the frozen design? **No, if the analysis is of the law as implemented.** Analyse
  first, publish the stability condition, and only *propose* the AIMD variant as further work. If you
  change the law to make it provable, you have invalidated the pre-registration — so do that as a new
  arm with a new hypothesis, if at all.

### Gaps
- I searched for formal treatments of memory load control and found none; this is a *failure to find*,
  not a proof of absence. Before claiming it in the thesis, do a targeted citation search on Denning's
  1968 "Thrashing: Its Causes and Prevention" and on the 1970s multiprogramming-level-control
  literature (Multics load control, "level of multiprogramming" controllers), which is exactly where
  a stability analysis would most plausibly be buried. I could not cover that older literature in
  this pass.
- No source found for AIMD applied to memory admission.

---

## Q5. Venues and the typical bar

### Takeaway
For work at this scale the realistic targets are a workshop or a short/poster track, not a top-tier
full paper. EuroSys publishes its criteria explicitly (novelty, significance, correctness, clarity);
HotOS publishes no acceptance criteria but is explicitly a venue for ideas and agendas rather than
finished systems, which fits a one-VM project better than ATC or EuroSys. Artifact evaluation is the
place a reproducible one-VM system with a frozen pre-registration can *beat* better-resourced work.

### Cited Findings
- EuroSys judges submissions on "novelty, significance, correctness, and clarity" — [EuroSys 2021 CFP](https://www.sigops.org/?p=1758)
- Historically EuroSys ranked "impact on the systems community" first with novelty secondary, and
  reserved sessions for **"idea" and "experience" papers with lighter novelty expectations** — a
  category STASIS could credibly target, though this is from the 2006/2007 CFPs and may no longer
  hold — [EuroSys 2007 CFP (archived list posting)](https://lists.ibr.cs.tu-bs.de/hyperkitty/list/cfp@ibr.cs.tu-bs.de/thread/6KSBXRILZTEC3IMHXMVETCJSG6IJVMNJ)
- HotOS is where "participants will present new ideas and debate future research agendas in systems
  research"; I found no published acceptance criteria — [SIGOPS HotOS pages](https://www.sigops.org/category/events/hotos/)
- Artifact evaluation is a live concern the community itself finds unsettled: a HotOS XIX panel
  reported that "most members of the audience believe the community wrongly assumes all artifacts are
  software that can be packaged in a container," with hardware-centric work (Enzian) hard to evaluate
  — [HotOS XIX Panel Report (arXiv 2308.05762)](https://arxiv.org/pdf/2308.05762)
- EuroSys runs dedicated artifact-evaluation rounds (spring/fall HotCRP instances) — [EuroSys'25 AE](https://eurosys25-ae-spring.hotcrp.com/)
- A directly relevant non-academic venue with a real bar: **LSF/MM/BPF**, where the BPF-MM topics
  above were discussed. Getting a topic proposal accepted there, or posting an RFC patch series to
  linux-mm, is a form of peer review that carries weight in a viva and is achievable from one VM —
  [LWN coverage of LSF/MM/BPF 2026 BPF-MM sessions](https://lwn.net/Articles/1072538/)

### Inferences
- Venue strategy for a solo student: aim the writing at **HotOS/workshop-style "the thrashing-control
  problem is not solved and here is a scheduler-coupled design plus measurements"**, with an artifact
  (the pre-registration plus replay dashboard is unusually strong artifact material). Do not aim at
  OSDI/SOSP/ASPLOS from one 4 GB VM; the CXL/tiering papers above show what that competition looks
  like.
- The frozen pre-registration is the project's most unusual asset relative to the systems-venue norm,
  where pre-registration is rare. Lead with it.

### Gaps
- I could not retrieve current official HotOS or USENIX ATC call-for-papers text on novelty standards
  or artifact requirements; the ATC result I found was a third-party page, not USENIX. Verify both
  directly before making venue claims in the thesis.
- No source found for acceptance rates at any of these venues in this pass.
