# Direct code search of the scx tree (closes the gap flagged by the sched_ext researcher)

**Method.** Shallow clone of `https://github.com/sched-ext/scx` at commit `0fb3264`
(merge of PR #3925), October 2026. Searched 722 `.c` / `.bpf.c` / `.h` / `.rs`
files, **excluding** `scheds/vmlinux/` (the generated BTF type dump, which
contains kernel type definitions only and is not scheduler logic — all naive
grep hits land there and are noise).

This was run by the coordinator to close the "code search outstanding" caveat in
`sched_ext_ecosystem.md`. Confidence on the negative is now high, not moderate.

## Result 1 — no scheduler reads per-task fault counters

`grep -E 'maj_flt|min_flt'` over all 722 non-vmlinux source files: **zero hits.**

No sched_ext scheduler in the upstream tree reads `p->maj_flt` or `p->min_flt`.

## Result 2 — the page-fault hits that do exist are not scheduling inputs

Every surviving `page.?fault` hit falls into one of three non-competing categories:

| Location | What it actually is |
|---|---|
| `rust/scx_rustland_core/src/alloc.rs:539-561`, `assets/bpf.rs:291` | Comments explaining why the userspace scheduler calls `mlockall()` — it must *avoid* faulting, or a kthread needed to resolve the fault deadlocks against the scheduler waiting on it. Defensive, not a signal. |
| `rust/scx_utils/src/perf.rs:107`, `scx_cosmos/src/main.rs:144`, `scx_forge/src/main.rs:183` | String parsing of the perf event *name* `"page-faults"` for a generic `--perf-event` CLI flag. Nothing consumes it as a scheduling input. |
| `scheds/rust/scx_rustland/src/main.rs:205-215` | `scx_rustland` counts **its own process's** page faults as a self-health metric (`init_page_faults` baseline, reported in `Metrics`). Self-monitoring of the scheduler, not of scheduled tasks. |

## Result 3 — the most load-bearing finding: the plumbing exists and is unused

`scheds/include/scx/percpu.bpf.h` already declares, as weak ksyms with
ready-made per-CPU accessors:

```c
extern struct psi_group_cpu psi_group_cpu __ksym __weak;      /* line 24 */
extern struct vm_event_state vm_event_states __ksym __weak;   /* line 30 */

DEFINE_PER_CPU_PTR_FUNC(cpu_psi_group,      struct psi_group_cpu, psi_group_cpu)
DEFINE_PER_CPU_PTR_FUNC(cpu_vm_event_state, struct vm_event_state, vm_event_states)
DEFINE_THIS_CPU_PTR_FUNC(cpu_psi_group)
DEFINE_THIS_CPU_PTR_FUNC(cpu_vm_event_state)
```

`struct vm_event_state` is the per-CPU vmstat array — it carries `pgmajfault`,
`pswpin` and `pswpout`.

**Call-site search for `cpu_psi_group`, `cpu_vm_event_state`, `psi_group_cpu`
and `vm_event_states` across the whole tree returns only the declaration and the
macro definitions above. No scheduler calls either accessor.**

Six schedulers include `percpu.bpf.h` — `scx_rustland_core`, `scx_beerland`,
`scx_bpfland`, `scx_cosmos`, `scx_eevdf`, `scx_p2dq` — and all of them use it for
LLC domains, cpufreq/`sugov` and cpustat. None touches the PSI or vmstat
accessors sitting in the same header.

## Result 4 — the ecosystem observes memory but never acts on it

`tools/scxtop/src/columns.rs:389-396` builds a swap summary panel
(`swap_total_kb`, `swap_free_kb`, `swap_cached_kb`, `delta_swap_in`) from a
`MemStatSnapshot`. So scx's own TUI **displays** swap activity to a human, while
no scheduler consumes it as an input.

## Why this matters for the novelty claim

This upgrades the claim from an absence of evidence to a positive, checkable
structural observation:

> The sched_ext maintainers have already exposed per-CPU PSI and vmstat
> (including `pgmajfault`) to BPF schedulers through the project's own helper
> header, and in the current tree **no scheduler uses them**. The seam between
> the pluggable scheduler and the memory subsystem is not merely unexplored —
> it is plumbed and unoccupied.

Recommended wording, still hedged to what was actually checked:

> "As of the October 2026 upstream tree (commit 0fb3264), no sched_ext scheduler
> reads per-task page-fault counters, and the per-CPU PSI and vmstat accessors
> provided by `scx/percpu.bpf.h` have no callers."

## Limits of this check

- Upstream `sched-ext/scx` tree only. Does **not** cover out-of-tree or vendor
  schedulers, the CXLAimPod prototype (arXiv 2508.15980), or research code.
- A shallow clone, so `git log` history for `percpu.bpf.h` is unavailable; the
  date at which the PSI/vmstat ksyms were added was not determined.
- Lexical search. A scheduler reading faults through an unexpected indirection
  (e.g. a kfunc wrapper or arena struct field) would not match these patterns,
  though nothing in the reviewed headers suggests such a path exists.
