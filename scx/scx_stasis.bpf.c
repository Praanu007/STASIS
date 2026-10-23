/* SPDX-License-Identifier: GPL-2.0 */
/*
 * scx_stasis: scx_simple + per-slice page-fault-frequency accounting
 * + a priority DSQ for protected interactive tasks. Never parks.
 *
 * Parking is done from user space by stasisd through the cgroup v2 freezer.
 * This scheduler only (1) measures, for every process, the major faults it
 * takes per millisecond of its own CPU time - page-fault frequency in Chu and
 * Opderbeck's sense - and (2) keeps a small set of protected tasks (the
 * latency probe, the demo shell) ahead of the batch jobs.
 *
 * Why counting in running/stopping catches every fault: a major fault is
 * accounted in the faulting task's own context, so p->maj_flt can only move
 * while the task is on a CPU. Every increment falls inside some
 * running -> stopping interval.
 *
 * Based on scx_simple (Copyright (c) 2022 Meta Platforms, Inc. and affiliates).
 */
#include <scx/common.bpf.h>
#include "scx_stasis.h"

char _license[] SEC("license") = "GPL";

#define SHARED_DSQ 0
#define PRIO_DSQ   1

static u64 vtime_now;
UEI_DEFINE(uei);

struct task_ctx {
	u64 start_ns;
	u64 start_flt;
};

struct {
	__uint(type, BPF_MAP_TYPE_TASK_STORAGE);
	__uint(map_flags, BPF_F_NO_PREALLOC);
	__type(key, int);
	__type(value, struct task_ctx);
} task_ctxs SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_LRU_HASH);	/* LRU: short-lived pids age out */
	__uint(max_entries, 8192);
	__type(key, u32);			/* tgid */
	__type(value, struct pff);
} pff_by_tgid SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 64);
	__type(key, u32);			/* tgid of a protected task */
	__type(value, u8);
} prio_tgid SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(key_size, sizeof(u32));
	__uint(value_size, sizeof(u64));
	__uint(max_entries, STASIS_NR_STATS);
} stats SEC(".maps");

static void stat_inc(u32 idx)
{
	u64 *cnt_p = bpf_map_lookup_elem(&stats, &idx);
	if (cnt_p)
		(*cnt_p)++;
}

s32 BPF_STRUCT_OPS(stasis_select_cpu, struct task_struct *p, s32 prev_cpu,
		   u64 wake_flags)
{
	bool is_idle = false;
	s32 cpu = scx_bpf_select_cpu_dfl(p, prev_cpu, wake_flags, &is_idle);

	if (is_idle) {
		stat_inc(STASIS_STAT_LOCAL);
		scx_bpf_dsq_insert(p, SCX_DSQ_LOCAL, SCX_SLICE_DFL, 0);
	}
	return cpu;
}

void BPF_STRUCT_OPS(stasis_enqueue, struct task_struct *p, u64 enq_flags)
{
	u32 tgid = p->tgid;
	u64 vtime = p->scx.dsq_vtime;

	if (bpf_map_lookup_elem(&prio_tgid, &tgid)) {
		stat_inc(STASIS_STAT_PRIO);
		scx_bpf_dsq_insert(p, PRIO_DSQ, SCX_SLICE_DFL, enq_flags);
		return;
	}
	stat_inc(STASIS_STAT_SHARED);
	/* A task coming out of a freeze gets at most one slice of credit. */
	if (time_before(vtime, vtime_now - SCX_SLICE_DFL))
		vtime = vtime_now - SCX_SLICE_DFL;
	scx_bpf_dsq_insert_vtime(p, SHARED_DSQ, SCX_SLICE_DFL, vtime, enq_flags);
}

void BPF_STRUCT_OPS(stasis_dispatch, s32 cpu, struct task_struct *prev)
{
	if (!scx_bpf_dsq_move_to_local(PRIO_DSQ))
		scx_bpf_dsq_move_to_local(SHARED_DSQ);
}

void BPF_STRUCT_OPS(stasis_running, struct task_struct *p)
{
	struct task_ctx *tctx;

	if (time_before(vtime_now, p->scx.dsq_vtime))
		vtime_now = p->scx.dsq_vtime;

	tctx = bpf_task_storage_get(&task_ctxs, p, 0,
				    BPF_LOCAL_STORAGE_GET_F_CREATE);
	if (!tctx)
		return;
	tctx->start_ns = bpf_ktime_get_ns();
	tctx->start_flt = p->maj_flt;
}

void BPF_STRUCT_OPS(stasis_stopping, struct task_struct *p, bool runnable)
{
	struct task_ctx *tctx;
	struct pff *v, zero = {};
	u32 tgid = p->tgid;

	p->scx.dsq_vtime += (SCX_SLICE_DFL - p->scx.slice) * 100 / p->scx.weight;

	tctx = bpf_task_storage_get(&task_ctxs, p, 0, 0);
	if (!tctx || !tctx->start_ns)
		return;

	v = bpf_map_lookup_elem(&pff_by_tgid, &tgid);
	if (!v) {
		bpf_map_update_elem(&pff_by_tgid, &tgid, &zero, BPF_NOEXIST);
		v = bpf_map_lookup_elem(&pff_by_tgid, &tgid);
		if (!v)
			return;
	}
	__sync_fetch_and_add(&v->maj_flt, p->maj_flt - tctx->start_flt);
	__sync_fetch_and_add(&v->run_ns, bpf_ktime_get_ns() - tctx->start_ns);
	tctx->start_ns = 0;
}

void BPF_STRUCT_OPS(stasis_enable, struct task_struct *p)
{
	p->scx.dsq_vtime = vtime_now;
}

s32 BPF_STRUCT_OPS_SLEEPABLE(stasis_init)
{
	s32 ret = scx_bpf_create_dsq(SHARED_DSQ, -1);

	if (ret)
		return ret;
	return scx_bpf_create_dsq(PRIO_DSQ, -1);
}

void BPF_STRUCT_OPS(stasis_exit, struct scx_exit_info *ei)
{
	UEI_RECORD(uei, ei);
}

SCX_OPS_DEFINE(stasis_ops,
	       .select_cpu		= (void *)stasis_select_cpu,
	       .enqueue			= (void *)stasis_enqueue,
	       .dispatch		= (void *)stasis_dispatch,
	       .running			= (void *)stasis_running,
	       .stopping		= (void *)stasis_stopping,
	       .enable			= (void *)stasis_enable,
	       .init			= (void *)stasis_init,
	       .exit			= (void *)stasis_exit,
	       .name			= "stasis");
