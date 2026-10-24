# Modern systems research on memory overcommit, working-set estimation, and admission control under memory pressure (≈2018–2026)

Scope note: this file is research notes for a report writer. Every factual claim carries an inline source. Where I could not verify something, it is listed under **Gaps** rather than guessed. Several sources below are kernel mailing-list posts, conference slides, vendor engineering blogs, or preprints — these are flagged as such, because their numbers are self-reported and not peer-reviewed.

---

## Q1. Working-set size (WSS) estimation: current techniques, reported accuracy, overhead

### Takeaway
The production-grade techniques are all *sampling-based access tracking* inside the kernel — DAMON (region-sampling of Accessed bits), MGLRU generation histograms, idle-page tracking, and PSI-driven feedback — and the literature reports **overhead** well (DAMON: ≲1% of one CPU regardless of memory size) but reports **accuracy** very poorly. ML-based prediction exists in production only one level up the stack (predicting a *VM's* or *job's* memory need from history, as in Pond and Autopilot), not as a page-level WSS estimator.

### Cited Findings

**DAMON (Linux, upstream; production-deployed)**
- DAMON's explicit design trade is monitoring accuracy for overhead, with a user-settable upper bound on overhead that "does not depend on the memory size" — i.e. overhead is independent of how much memory is being monitored — [DAMON kernel-summit deck](https://www.linuxplumbersconf.org/event/16/contributions/1224/attachments/1107/2137/damon_status_plan_ksummit_2022.pdf); [DAMON v6 patch series](https://lkml.iu.edu/2002.3/00550.html)
- Measured overhead in the v6 series came in at 0.6% of the configured bound in the best case and 13.288% of the bound on average; the series claims DAMON's overhead is smaller than a straightforward periodic access-check by 94,242× (best) / 3,159× (average) — [DAMON v6 patch series](https://lkml.iu.edu/2002.3/00550.html)
- The default configuration consumes "only up to 1% of CPU time" on realistic PARSEC3 / SPLASH-2X workloads — [DAMON v6 patch series](https://lkml.iu.edu/2002.3/00550.html)
- On a production setup DAMON monitored accesses across 68.60 GB of system memory every 5 ms at <1% of a single CPU, and the deck states overhead does not grow with memory size — [DAMON kernel-summit deck](https://www.linuxplumbersconf.org/event/16/contributions/1224/attachments/1107/2137/damon_status_plan_ksummit_2022.pdf)
- **Known accuracy failure mode:** DAMON reads the hardware Accessed bits *without* a manual TLB flush, to minimise overhead. As a consequence a small working set can be fully covered by the TLB and therefore appear idle. A Jan-2026 selftest patch had to raise the tested working-set size to 160 MiB because "there are test setups that have TLBs large enough to cover the 10 MiB data accesses. As a result, the test fails depending on the test machine." — [selftests/damon/wss_estimation patch](https://lkml.iu.edu/2601.2/05553.html)

**MGLRU generations (Linux, upstream; production-deployed in Android/ChromeOS)**
- The kernel documentation frames working-set estimation as "measuring how much memory an application needs over a given interval" with little performance impact; reading `lru_gen` gives a per-memcg, per-node **histogram of page counts binned by access-time interval**, with `MAX_NR_GENS` bins — [Multi-Gen LRU kernel docs](https://docs.kernel.org/6.13/admin-guide/mm/multigen_lru.html)
- The documentation names **job schedulers** as the intended consumer: they "need to estimate the working sets of existing jobs" to decide whether a server can take a new job — [Multi-Gen LRU kernel docs](https://docs.kernel.org/6.13/admin-guide/mm/multigen_lru.html)
- A 2022 RFC (Yuanchu Xie) argued MGLRU aging is *driven by reclaim*, making generation timing non-deterministic, and proposed **periodic aging** (`memory.periodic_aging` cgroup file plus a `kold` kthread) so that generations become time-based working-set information usable by a proactive reclaimer — [MGLRU working set extensions RFC](https://lkml.iu.edu/hypermail/linux/kernel/2212.1/06163.html); LWN coverage at [lwn.net/Articles/917907](https://lwn.net/Articles/917907/)

**ML-based prediction (one level up: per-VM / per-job, not per-page)**
- Pond (Azure) uses ML models to predict how much *local* vs *pool* memory a VM needs, including a latency-sensitivity predictor — [Pond, ASPLOS '23](https://www.microsoft.com/en-us/research/publication/pond-cxl-based-memory-pooling-systems-for-cloud-platforms/); corroborated by [Dissecting CXL Memory Performance at Scale](https://arxiv.org/pdf/2409.14317)
- Azure Harvest VM work reports prediction of available harvestable capacity with **average error under 2% short-term and under 6% for longer terms** — [Harvest VMs / Memory-Harvesting VMs, MSR](https://www.microsoft.com/en-us/research/publication/memory-harvesting-vms-in-cloud-platforms/)
- Google Autopilot uses ML over each job's history plus hand-tuned heuristics to set per-task CPU and memory limits — [Autopilot, EuroSys 2020](https://research.google/pubs/autopilot-workload-autoscaling-at-google-scale/)

### Inferences
- There is a genuine, citable **accuracy-reporting gap**: both DAMON and MGLRU publish overhead numbers but neither publishes a WSS-accuracy figure against ground truth. A thesis that *measures* WSS-estimator error (and the TLB-induced under-reporting above) has an open lane.
- The MGLRU documentation itself states the job-scheduler/admission use case but the kernel ships only the *signal*, not the *controller*. The controller is where a load-control thesis sits.
- The reclaim-driven (non-periodic) aging problem the 2022 RFC names is exactly the problem a load controller hits: an estimator that only updates when reclaim happens cannot be used to *avoid* reclaim.

### Gaps
- No published accuracy (e.g. error vs. a ground-truth WSS from full page-table scanning or simulation) for DAMON, MGLRU, or idle-page tracking. I found none in the searches performed.
- Could not confirm whether the `memory.periodic_aging` / `kold` RFC was merged upstream; the current kernel docs do not describe those interfaces, suggesting not, but I did not verify against the source tree.
- Page-fault-sampling and hardware-PEBS-based estimators: only indirectly covered (xTier below uses PEBS via eBPF). No accuracy/overhead numbers obtained.

---

## Q2. Datacenter memory overcommit and admission control — and does anyone PAUSE rather than kill?

### Takeaway
Google (Autopilot), Meta (PSI/oomd/Senpai/TMO) and Microsoft (Harvest VMs, Coach, Pond) all publish production overcommit systems, but their terminal action under pressure is **kill or offload**, not pause. The only *pause-like* mechanisms are (a) Linux `memory.high` allocator throttling, (b) Fuchsia's warning tier that asks processes to slow allocation, and (c) an unimplemented 2001 kernel proposal. **I found no production datacenter system whose documented response to memory pressure is to suspend and later resume a job.** This is the clearest novelty opening in the whole survey.

### Cited Findings

**Google — Autopilot (production)**
- Autopilot (EuroSys 2020, Rzadca, Findeisen, Wilkes et al.) vertically scales per-task CPU and memory limits and also changes the number of concurrent tasks in a job; its stated goal is to shrink the limit-vs-usage gap "while keeping OOM kills rare" — [Autopilot, research.google](https://research.google/pubs/autopilot-workload-autoscaling-at-google-scale/); [PDF](https://homepages.dcc.ufmg.br/~cunha/teaching/20221/cloudcomp/readings/10-readings/rzadca20autopilot.pdf)
- Reported results: slack of **23% for autopiloted jobs vs 46% for manually managed**, and a **tenfold reduction** in jobs severely affected by OOMs — [Autopilot, research.google](https://research.google/pubs/autopilot-workload-autoscaling-at-google-scale/)

**Meta — PSI, oomd, Senpai, TMO (production since 2021)**
- TMO offloads colder memory to compressed memory or SSD without application changes; **in production since 2021**, saving **20–32% of memory per server** — [Meta engineering blog](https://engineering.fb.com/2022/06/20/data-infrastructure/transparent-memory-offloading-more-memory-at-a-fraction-of-the-cost-and-power/); [CACM research highlight](https://cacm.acm.org/research-highlights/tmo-transparent-memory-offloading-in-datacenters)
- TMO contributed **Pressure Stall Information (PSI)** to Linux, which measures lost work from CPU/memory/I/O shortage per-process and per-container — [Meta engineering blog](https://engineering.fb.com/2022/06/20/data-infrastructure/transparent-memory-offloading-more-memory-at-a-fraction-of-the-cost-and-power/)
- **Senpai** is the userspace agent that uses PSI to decide dynamically how much memory to offload, interfacing with kernel reclaim and changing memory-tier provisioning — [CACM research highlight](https://cacm.acm.org/research-highlights/tmo-transparent-memory-offloading-in-datacenters)
- `systemd-oomd` (the upstream descendant of the same PSI-based approach) acts *before* the kernel OOM killer, but its action is terminal: "systemd-oomd will select a cgroup to terminate, and send SIGKILL to all processes in it" — [systemd-oomd.service(8)](https://man7.org/linux/man-pages/man8/systemd-oomd.service.8.html)

**Microsoft / Azure (production + research)**
- **Memory-Harvesting VMs** (ASPLOS): characterised spare memory in Azure and built harvesting VMs using buffering, batching, and pre-reclamation; estimated user cost reduction **up to 93%**. Earlier work harvested CPU cores only, with memory size fixed — [MSR publication](https://www.microsoft.com/en-us/research/publication/memory-harvesting-vms-in-cloud-platforms/)
- **Coach**: uses long-term predictions plus a scheduling policy to co-locate VMs with complementary usage; splits each VM's allocation into a **guaranteed part and an oversubscribed part**, monitoring the oversubscribed portion for contention; hosts **~26% more VMs with minimal performance loss** — [Coach, MSR](https://www.microsoft.com/en-us/research/publication/coach-exploiting-temporal-patterns-for-all-resource-oversubscription-in-cloud-platforms/)

**Pause-rather-than-kill: what actually exists**
- **Linux `memory.high`** is explicitly designed as throttling, not killing. Johannes Weiner: "The idea behind memory.high is to throttle allocations long enough for the admin or a management daemon to intervene, but not to trigger the kernel oom killer." — [LKML thread](https://lkml.iu.edu/hypermail/linux/kernel/1902.0/00640.html)
- The upstream commit adding throttling on reclaim failure notes containment "can frequently fail completely and cause OOM situations outside of the cgroup," especially when swap is absent or full; the added delay is **clamped at 2*HZ per allocation** (`MEMCG_MAX_HIGH_DELAY_JIFFIES`) — [commit 0e4b01df8659](https://android-kvm.googlesource.com/linux/+/0e4b01df865935007bd712cbc8e7299005b28894)
- Weiner also states the core detection problem for load control: the kernel OOM killer "does not kick in reliably when page cache is thrashing heavily, because the kernel still sees successful allocation and reclaim, while the workload spends most of its time in page faults" — [LKML thread](https://lkml.iu.edu/hypermail/linux/kernel/1902.0/00640.html)
- A **2025 RFC for `memory.high.throttle`** states that "the upstream Kubernetes community hesitates to use memory.high, and instead uses only memory.max for memory control," and proposes a tunable throttling knob (0 = maximum throttling) — [RFC, LKML Jan 2025](https://lkml.iu.edu/hypermail/linux/kernel/2501.3/06588.html)
- In a related dmem (device-memory) high-limit throttling series, Tejun Heo raised the fundamental objection: "what are we slowing them down for if the only recovery action is killing them?" — [LKML](https://lkml.iu.edu/hypermail/linux/kernel/2501.3/06588.html)
- **Fuchsia** is the closest to tiered pressure-based gating: a **warning** threshold signals that processes should slow down memory allocations, a separate **critical** threshold signals that processes should free memory, and only a final **OOM** level begins killing — [Fuchsia OomConfig reference](https://fuchsia.dev/reference/assembly/OomConfig)
- A 2013 LKML suggestion (David Rientjes) proposed a userspace OOM handler holding off kernel kills for a grace period (`memory.oom_delay_millisecs`) — i.e. pause-before-kill — [LKML, user defined OOM policies](https://lkml.iu.edu/hypermail/linux/kernel/1311.2/02406.html)
- A 2001 LKML "humility" proposal would have *blocked low-priority processes from allocating* under pressure instead of killing them later; per the search result it was never implemented as described — [LKML OOM killer thread](https://lkml.indiana.edu/0109.3/0944.html)

### Inferences
- Tejun Heo's objection is the exact gap a *pause-and-resume* load controller fills: throttling is only defensible if the recovery action is something other than killing. A thesis can cite that line directly as the motivating open question.
- Kubernetes' refusal to adopt `memory.high` means the *de facto* datacenter policy today is still `memory.max` + kill. That is a strong positioning statement.
- Fuchsia's warning tier is the best existing citation that tiered, non-lethal pressure response is considered viable in a shipping OS — but it asks *applications* to cooperate, whereas a load controller would not need cooperation.

### Gaps
- Could not confirm from a primary source whether Autopilot ever *suspends* a task (as opposed to resizing limits or changing replica count). The paper's framing is resizing + OOM-rate reduction.
- Did not find the Borg paper's own admission-control description (I relied on Autopilot). Worth fetching Verma et al., "Large-scale cluster management at Google with Borg" (EuroSys 2015) for the admission/eviction-tier detail.
- No source found confirming whether oomd/Senpai have a non-kill mode.

---

## Q3. Far memory, CXL tiering, zswap/zram — do they obsolete classical load control?

### Takeaway
Tiering changes the *shape* of thrashing (a miss becomes a slower access rather than a page-fault storm) and is now production reality at Meta and in Azure prototypes, but every system surveyed still relies on a pressure-based feedback controller above it. The evidence points to **complementary**, not obsolete: tiering widens the operating range, and the newest work (2026 preprints) is adding *fair-share* and *admission* controls on top of tiering — which is itself evidence the control problem did not go away.

### Cited Findings
- **TMO/zswap (Meta, production)** offloads to compressed memory or SSD and is driven by Senpai's PSI feedback loop — the tier does not remove the need for a controller — [Meta engineering blog](https://engineering.fb.com/2022/06/20/data-infrastructure/transparent-memory-offloading-more-memory-at-a-fraction-of-the-cost-and-power/); [CACM](https://cacm.acm.org/research-highlights/tmo-transparent-memory-offloading-in-datacenters)
- **Pond (ASPLOS '23, Microsoft Azure; Distinguished Paper; research prototype on production traces)**: pools spanning only **8–16 sockets** capture most of the benefit; ML predicts local-vs-pool split; **DRAM cost reduced 7% across 158 workloads with performance within 1–5% of same-NUMA-node allocation**; pooling effectiveness evaluated by simulation on VM traces from **100 production clusters** — [Pond, MSR](https://www.microsoft.com/en-us/research/publication/pond-cxl-based-memory-pooling-systems-for-cloud-platforms/); [arXiv preprint](https://arxiv.org/pdf/2203.00241)
- **Managing Memory Tiers with CXL in Virtualized Environments (OSDI '24**, Columbia + Microsoft Azure + Intel + U. Michigan): presents **Intel Flat Memory Mode** (hardware-managed tiering) plus **Memstrata**, an allocator for hardware tiering; reduces degradation for performance-outlier VMs **from above 30% to below 6%** in multi-VM prototype experiments — [USENIX OSDI '24](https://www.usenix.org/conference/osdi24/presentation/zhong-yuhong); [slides](https://usenix.org/system/files/osdi24_slides-zhong-yuhong.pdf)
- Flat Memory Mode operates at **cache-line granularity inside the memory controller**, promoting recently accessed lines to local DRAM by swapping them with the lines previously occupying local DRAM; software alternatives named are **AutoNUMA and TPP** — [USENIX OSDI '24](https://www.usenix.org/conference/osdi24/presentation/zhong-yuhong); hardware-vs-software comparison on MongoDB in [Intel technical paper](https://cdrdv2-public.intel.com/886601/advantages-managing-cxl-memory-tier-in-hardware-technical-paper.pdf)
- **xTier** (arXiv 2609.27266, **preprint — venue unverified**): kernel-resident *learned* tiering, attaching eBPF programs to PEBS events and scoring sampled pages with a quantised MLP — [arXiv](https://arxiv.org/pdf/2609.27266)
- **Equilibria** (arXiv 2602.08800, **preprint — venue unverified**): an OS framework for **fair, multi-tenant CXL tiering at datacenter scale with per-container fair-share controls** — [arXiv](https://arxiv.org/abs/2602.08800v1)
- Background framing: memory-intensive applications "suffer when their working sets do not completely fit in the available memory" — Al Maruf & Chowdhury, *Memory Disaggregation: Advances and Open Challenges* — [via search result](https://par.nsf.gov/servlets/purl/10516024)

### Inferences
- The existence of Equilibria (fair-share controls *for* tiering) and Memstrata (outlier protection *for* hardware tiering) is direct evidence that tiering reintroduces, rather than removes, the load-control and fairness problems — a useful argument for a thesis positioning section.
- Hardware tiering (Flat Memory Mode) removes the OS from the promotion/demotion loop, which *weakens* OS-level WSS visibility. A thesis depending on OS-visible access information should acknowledge this as a threat to its approach's longevity.

### Gaps
- No numbers found quantifying how tiering changes *thrashing* specifically (e.g. PSI-stall reduction under a fixed overcommit ratio). The surveyed papers report cost/perf, not thrash dynamics.
- TPP (Meta's Transparent Page Placement, ASPLOS 2023) was named only as an alternative in the OSDI '24 paper; I did not retrieve the TPP paper itself or its numbers.
- zram (as distinct from zswap) found no research-grade source in these searches.

---

## Q4. Fairness in memory allocation and reclaim; Jain's index applied to memory

### Takeaway
This is the thinnest-covered of all the key questions, and that itself is the finding: **I found no peer-reviewed paper that combines memory reclaim, multi-tenant fairness, and a fairness metric such as Jain's index in one study.** Fairness work in memory exists mostly as (a) cache-partitioning fairness, (b) utility/marginal-value reallocation, and (c) one 2026 preprint adding fair-share to CXL tiering.

### Cited Findings
- **Equilibria** (arXiv 2602.08800, preprint): OS framework for fair multi-tenant CXL tiering with per-container fair-share controls — the closest thing found to memory fairness as a first-class goal — [arXiv](https://arxiv.org/abs/2602.08800v1)
- **MemExchange** (arXiv 2607.11579, preprint): utility-driven distributed memory reallocation for multi-tenant datacenters; matches tenants seeking memory against tenants that can relinquish it using **marginal-utility scoring**. Its fairness metric was not visible in the search result — [arXiv](https://arxiv.org/pdf/2607.11579)
- **Cache fairness lineage**: a cache-allocation fairness paper computes **Jain's fairness index over per-tenant shares, reporting values around 0.7–0.85** for different allocations — [arXiv 2201.09833](https://arxiv.org/pdf/2201.09833); a related controller paper reserves a per-tenant minimum and pools the remainder (the MemShare approach) — [arXiv 2608.14561](https://arxiv.org/pdf/2608.14561)
- **CrystalMem** (arXiv 2608.00303, preprint, LLM-agent memory — *different sense of "memory"*, include with care): reports pooled **Jain fairness ≥ 0.999 for every policy and tenant count**, concluding remaining violations come from what the bytes can rebuild rather than how they are split — [arXiv](https://arxiv.org/pdf/2608.00303)
- Jain's index definition as used in these papers: ranges 0 to 1, with 1 indicating perfectly equal sharing — [arXiv 2201.09833](https://arxiv.org/pdf/2201.09833)
- **Dependency-Aware Dominant Resource Fairness** for multi-tenant multi-resource systems (arXiv 2606.25540, preprint) extends the DRF line, which is the standard multi-resource fairness reference — [arXiv](https://arxiv.org/pdf/2606.25540)
- Industrial prior art: a USPTO patent family, "Memory management for multiple process instances," reclaims memory **based on value, to minimise cost across instances** (e.g. SQL Server instances); it does not appear to use Jain's index — [US 11,726,905](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11726905)

### Inferences
- **Strong novelty signal:** applying a conventional fairness metric (Jain's index, or DRF-style dominant-resource fairness) to *memory-bound* workloads under reclaim/thrashing appears genuinely under-explored. The analogous CPU work is mature; the memory work is not.
- Fairness in memory is being approached as *utility/value* (MemExchange, the SQL Server patents) rather than as *equality of share*. A thesis could explicitly contrast equality-based fairness against utility-based allocation under memory pressure.

### Gaps
- No peer-reviewed source found for Jain's-index-evaluated memory reclaim. Multiple reformulated queries would be needed; on the evidence here I would report this as an open area, not as "no such work exists."
- The 2026 arXiv preprints (Equilibria, MemExchange, CrystalMem, DA-DRF, xTier) are **unverified as to venue and peer-review status** — I saw them only via search results and did not fetch and read them. The report should mark them as preprints.

---

## Q5. Interactive-latency protection under memory pressure

### Takeaway
The canonical line is Heracles → PerfIso → Caladan, with Caladan (OSDI 2020) the standard reference for microsecond-scale interference mitigation. **I found no clearly identified 2024–2026 successor to Caladan**, and the newer work I did find protects latency through *tiering/allocation* (Memstrata) rather than through scheduling.

### Cited Findings
- **Caladan (OSDI 2020, Fried, Ruan, Ousterhout, Belay; research prototype)** manages contention in the memory hierarchy via **fast core allocation rather than static partitioning**, pairing a centralised scheduler core with a kernel module that bypasses the standard Linux scheduler for microsecond-scale placement — [USENIX OSDI '20](https://usenix.org/conference/osdi20/presentation/fried)
- **Memstrata (OSDI '24)** is the closest recent latency-protection result found: it cuts degradation for performance-outlier VMs from **>30% to <6%** under hardware memory tiering — [USENIX OSDI '24](https://www.usenix.org/conference/osdi24/presentation/zhong-yuhong)
- **Coach (Microsoft)** protects latency by splitting each VM's allocation into a guaranteed and an oversubscribed portion and monitoring only the oversubscribed part for contention — [Coach, MSR](https://www.microsoft.com/en-us/research/publication/coach-exploiting-temporal-patterns-for-all-resource-oversubscription-in-cloud-platforms/)
- **PSI** is the production signal used to detect that latency-sensitive work is losing time to memory shortage, reported per-process and per-container — [Meta engineering blog](https://engineering.fb.com/2022/06/20/data-infrastructure/transparent-memory-offloading-more-memory-at-a-fraction-of-the-cost-and-power/)

### Inferences
- Caladan's core idea — rapid *reallocation* of a contended resource rather than static partitioning — transfers directly to memory load control, and the absence of a memory-side equivalent (fast, centralised, microsecond-ish reallocation of memory *admission*) is a defensible gap.

### Gaps
- I did not locate a 2024–2026 Caladan successor. One result in this area was an ASPLOS 2025 paper on an **ultra-low-latency Ethernet fabric for memory disaggregation**, which addresses network latency and is *not* an interference-isolation successor. A targeted search on the Caladan authors (Fried, Ruan, Ousterhout, Belay) is the recommended next step.
- Heracles (ISCA 2015) and PerfIso (ATC 2018) were not retrieved as primary sources in this pass; they should be cited from their own proceedings, not from this file.
- One search result purporting to host the Caladan paper ([blog.forejustice.org](https://blog.forejustice.org/download-pdf/osdi-20-caladan-mitigating-interference-at-microsecond-timescales.pdf)) contained placeholder text and no real content — **do not cite it.**

---

## Q6. Serverless/container cold-start and checkpoint-restore (CRIU): is pause-and-resume established elsewhere?

### Takeaway
Yes — suspending and resuming memory-heavy work via checkpoint/restore is an established, partly-productised technique in serverless and GPU inference, and it is well documented that the bottleneck is the *memory image path*, not storage speed alone. This is the strongest body of citable precedent for a pause-and-resume load controller, though none of it is framed as memory *admission control*.

### Cited Findings
- **Spice (MIT CSAIL)** argues restoring from disk is held back by OS-level constraints rather than storage, and reports near-warm performance on cold restores from disk, **reducing latency by up to 14.9× versus process-based systems like CRIU** — [Taming Serverless Cold Starts Through OS Co-Design, arXiv](https://arxiv.org/html/2509.14292v1)
- **VAS-CRIU** identifies that "expensive filesystem write/read operations on image files containing memory pages" dominate CRIU's runtime cost, and keeps the snapshot in DRAM instead — [via NSF PAR record](https://par.nsf.gov/biblio/10192799)
- **TrEnv-X** notes that snapshot approaches "still require establishing a new isolated sandbox," so overhead remains — [arXiv 2509.09525](https://arxiv.org/pdf/2509.09525)
- **Production GPU use (industrial, deployed):** Photoroom reports moving GPU inference cold starts from ~220 s to ~45–65 s using NVIDIA `cuda-checkpoint` with CRIU — [Photoroom engineering](https://www.photoroom.com/inside-photoroom/how-we-cut-gpu-cold-starts-from-minutes-to-seconds-with-memory-checkpointing)
- **NVIDIA Dynamo Snapshot** docs report ~1 minute cold start vs ~10 seconds from checkpoint, but the feature is **in preview** and "may only be functional in some cluster setups"; restore time depends on storage bandwidth, GPU model, and whether the restore stays on the same node — [NVIDIA Dynamo snapshot docs](https://docs.dynamo.nvidia.com/dynamo/dev/kubernetes-deployment/deployment-guide/snapshot)
- **Counter-evidence / limit:** one write-up notes that for larger models, "restore time actually exceeds cold-start time, which negates the benefit of checkpointing," and that upstream CRIU restore remains a bottleneck pending parallel-restore optimisations — [Photoroom engineering](https://www.photoroom.com/inside-photoroom/how-we-cut-gpu-cold-starts-from-minutes-to-seconds-with-memory-checkpointing)

### Inferences
- The CRIU literature supplies both the precedent *and* the honest cost model for a pause-and-resume design: the cost scales with the resident memory image, so a load controller that pauses a memory-heavy job pays roughly a working-set-sized I/O on resume. A thesis should quantify this, and the "restore slower than cold start for large models" result is the key counter-argument to pre-empt.
- A load controller that *pauses in place* (leaving pages resident or in compressed memory) rather than checkpointing to disk avoids the dominant CRIU cost identified by VAS-CRIU — a differentiator worth stating explicitly.

### Gaps
- No source found that frames checkpoint/restore as a *memory admission control* mechanism (i.e. pausing to relieve host memory pressure). The serverless literature's motivation is cold-start latency and density, not pressure relief. This is a positioning opportunity, but means there is no direct prior art to compare against.
- Firecracker/microVM snapshotting and SOCK/Catalyst-class systems were not retrieved in this pass.

---

## Q7. Explicitly named open problems in recent papers and HotOS position papers

### Takeaway
I could **not** confirm a HotOS 2024/2025 position paper specifically about memory open problems; the HotOS material I retrieved was the call for papers and venue data, not the program. The best-evidenced explicitly-named open problems in this pass come from the Linux kernel mailing list, which states the thrashing-detection and throttle-without-recovery problems in unusually quotable terms.

### Cited Findings
- **HotOS 2025 venue/format (confirmed):** the 20th Workshop on Hot Topics in Operating Systems, Banff, Alberta, 2025-05-14; submission deadline 2025-01-15, notification 2025-04-01; position papers capped at 5 pages plus references, double-blind. The call solicits papers that "propose new directions of systems research, advocate innovative approaches to long-standing problems, or report on deep insights gained from experience with real-world systems" — [HotOS 2025 CFP](https://sigops.org/s/conferences/hotos/2025/cfp.html)
- **Open problem — thrashing is invisible to the OOM killer:** "the kernel OOM killer does not kick in reliably when page cache is thrashing heavily, because the kernel still sees successful allocation and reclaim, while the workload spends most of its time in page faults" — [Weiner, LKML](https://lkml.iu.edu/hypermail/linux/kernel/1902.0/00640.html)
- **Open problem — throttling with no recovery action:** "what are we slowing them down for if the only recovery action is killing them?" (Tejun Heo) — [LKML 2025](https://lkml.iu.edu/hypermail/linux/kernel/2501.3/06588.html)
- **Open problem — `memory.high` containment fails without swap:** containment "can frequently fail completely and cause OOM situations outside of the cgroup," especially when no swap is configured or swap is full — [commit 0e4b01df8659](https://android-kvm.googlesource.com/linux/+/0e4b01df865935007bd712cbc8e7299005b28894)
- **Open problem — orchestrators won't use the throttle:** "the upstream Kubernetes community hesitates to use memory.high, and instead uses only memory.max for memory control" — [LKML RFC 2025](https://lkml.iu.edu/hypermail/linux/kernel/2501.3/06588.html)
- **Open problem — generation timing is reclaim-driven, not time-driven**, so MGLRU generations are not directly usable as time-based working-set information without periodic aging — [MGLRU working set extensions RFC](https://lkml.iu.edu/hypermail/linux/kernel/2212.1/06163.html)
- **Open problem — WSS estimators silently under-report small working sets** because Accessed bits are read without a TLB flush — [selftests/damon/wss_estimation patch, Jan 2026](https://lkml.iu.edu/2601.2/05553.html)
- **Open challenges in disaggregation** are collected in Al Maruf & Chowdhury, *Memory Disaggregation: Advances and Open Challenges* — [via NSF PAR](https://par.nsf.gov/servlets/purl/10516024)

### Inferences
- The four LKML-sourced problems above line up into a single coherent argument for a working-set load controller: the OOM killer cannot see thrashing; the only existing throttle has no recovery action and fails without swap; and orchestrators therefore avoid it entirely. That is a thesis motivation built from primary production-kernel sources rather than from papers.

### Gaps
- **Unresolved:** no HotOS program, proceedings page, or paper title confirming a memory-focused position paper for 2024 or 2025. A DBLP-based listing of HotOS 2025 exists ([rankdblp](https://rankdblp.com/venues/db_conf_hotos_hotos2025_html.html)) but the excerpt seen covered only part of the list. The USENIX HotOS XX program page should be checked directly.
- No OSDI 2025 paper on CXL tiering surfaced in these searches; absence here is weak evidence and the OSDI '25 proceedings should be checked directly before any claim of absence is made in the report.

---

## Production vs research-only — quick classification table

| System / technique | Status | Source |
|---|---|---|
| Linux DAMON | Upstream kernel, production-deployed (68.6 GB prod setup reported) | [ksummit deck](https://www.linuxplumbersconf.org/event/16/contributions/1224/attachments/1107/2137/damon_status_plan_ksummit_2022.pdf) |
| Linux MGLRU generations | Upstream kernel, documented for job schedulers | [kernel docs](https://docs.kernel.org/6.13/admin-guide/mm/multigen_lru.html) |
| MGLRU periodic aging (`memory.periodic_aging`, `kold`) | RFC/proposal; merge unconfirmed | [LKML RFC](https://lkml.iu.edu/hypermail/linux/kernel/2212.1/06163.html) |
| PSI | Upstream kernel, Meta production | [Meta blog](https://engineering.fb.com/2022/06/20/data-infrastructure/transparent-memory-offloading-more-memory-at-a-fraction-of-the-cost-and-power/) |
| Meta TMO + Senpai | Production since 2021, 20–32% memory saved/server | [Meta blog](https://engineering.fb.com/2022/06/20/data-infrastructure/transparent-memory-offloading-more-memory-at-a-fraction-of-the-cost-and-power/), [CACM](https://cacm.acm.org/research-highlights/tmo-transparent-memory-offloading-in-datacenters) |
| systemd-oomd | Shipping; kill-based | [man page](https://man7.org/linux/man-pages/man8/systemd-oomd.service.8.html) |
| `memory.high` throttling | Upstream; avoided by Kubernetes | [LKML](https://lkml.iu.edu/hypermail/linux/kernel/1902.0/00640.html), [RFC 2025](https://lkml.iu.edu/hypermail/linux/kernel/2501.3/06588.html) |
| Google Autopilot | Production at Google scale | [EuroSys 2020](https://research.google/pubs/autopilot-workload-autoscaling-at-google-scale/) |
| Azure Harvest VMs / Coach | Microsoft research on production traces; deployment extent unclear | [MSR](https://www.microsoft.com/en-us/research/publication/memory-harvesting-vms-in-cloud-platforms/), [Coach](https://www.microsoft.com/en-us/research/publication/coach-exploiting-temporal-patterns-for-all-resource-oversubscription-in-cloud-platforms/) |
| Pond | Research prototype evaluated on 100 production clusters' traces; ASPLOS '23 Distinguished Paper | [MSR](https://www.microsoft.com/en-us/research/publication/pond-cxl-based-memory-pooling-systems-for-cloud-platforms/) |
| Memstrata / Intel Flat Memory Mode | OSDI '24 research prototype; Flat Memory Mode is shipping Intel hardware capability | [OSDI '24](https://www.usenix.org/conference/osdi24/presentation/zhong-yuhong), [Intel paper](https://cdrdv2-public.intel.com/886601/advantages-managing-cxl-memory-tier-in-hardware-technical-paper.pdf) |
| Caladan | Research prototype | [OSDI '20](https://usenix.org/conference/osdi20/presentation/fried) |
| Fuchsia tiered OOM (warning/critical/OOM) | Shipping OS configuration | [Fuchsia docs](https://fuchsia.dev/reference/assembly/OomConfig) |
| CRIU checkpoint/restore for cold starts | CRIU production; GPU snapshotting in preview / industrial use | [NVIDIA Dynamo](https://docs.dynamo.nvidia.com/dynamo/dev/kubernetes-deployment/deployment-guide/snapshot), [Photoroom](https://www.photoroom.com/inside-photoroom/how-we-cut-gpu-cold-starts-from-minutes-to-seconds-with-memory-checkpointing) |
| xTier, Equilibria, MemExchange, CrystalMem, DA-DRF | arXiv preprints; venue and peer review **unverified** | [xTier](https://arxiv.org/pdf/2609.27266), [Equilibria](https://arxiv.org/abs/2602.08800v1), [MemExchange](https://arxiv.org/pdf/2607.11579) |

## Source-quality warnings for the report writer
- DAMON overhead figures are **self-reported by the developer** on specific test setups (LKML and conference slides), not independently reproduced.
- Meta's 20–32% memory-saving figure comes from Meta's own engineering blog; the peer-reviewed version is the CACM research highlight.
- The 2026-dated arXiv entries (2602.*, 2606.*, 2607.*, 2608.*, 2609.*) were seen only as search results. Treat as preprints; fetch and verify before citing specific numbers.
- Memory-specific details of Autopilot (e.g. peak usage per 5-minute window, 48-hour decay half-life for memory history) appeared in a **secondary LinkedIn source** and could **not** be confirmed against the paper text. Verify against the PDF before use.
- A USPTO patent on VM balloon sizing via time-series prediction is relevant in substance, but the **assignee was not visible** in results, so it should not be attributed to Microsoft.
