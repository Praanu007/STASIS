/* SPDX-License-Identifier: GPL-2.0 */
/* Shared between scx_stasis.bpf.c and the scx_stasis.c loader. */
#ifndef __SCX_STASIS_H
#define __SCX_STASIS_H

/* Per-process (tgid) page-fault-frequency accounting. */
struct pff {
	__u64 maj_flt;		/* major faults taken while on CPU */
	__u64 run_ns;		/* CPU time consumed */
};

enum stasis_stat {
	STASIS_STAT_LOCAL,		/* direct dispatch to an idle CPU */
	STASIS_STAT_SHARED,		/* queued on the shared vtime DSQ */
	STASIS_STAT_PRIO,		/* queued on the priority DSQ */
	STASIS_NR_STATS,
};

#endif /* __SCX_STASIS_H */
