/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Loader for scx_stasis. Based on scx_simple.c
 * (Copyright (c) 2022 Meta Platforms, Inc. and affiliates.)
 *
 * Additions: -p TGID marks a process as protected (PRIO_DSQ), and the
 * per-tgid fault map is exported to /run/stasis/pff every 100 ms as
 * "tgid maj_flt run_ns" lines for stasisd --signal=bpf.
 */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>
#include <assert.h>
#include <libgen.h>
#include <sys/stat.h>
#include <bpf/bpf.h>
#include <scx/common.h>
#include "scx_stasis.h"
#include "scx_stasis.bpf.skel.h"

#define PFF_DIR  "/run/stasis"
#define PFF_PATH PFF_DIR "/pff"
#define STATS_PATH PFF_DIR "/stats"
#define MAX_PRIO 64

const char help_fmt[] =
"scx_stasis: scx_simple + per-process page-fault-frequency accounting\n"
"            + a priority queue for protected interactive tasks.\n"
"\n"
"Usage: %s [-p TGID]... [-w MS] [-v]\n"
"\n"
"  -p TGID       Protect this process (latency probe, demo shell); repeatable\n"
"  -w MS         sched_ext watchdog timeout (default 30000). Lower it, e.g. to\n"
"                5000, while developing so stalls show up quickly. Kernels\n"
"                from 6.12 have no sysfs knob for this; it is set here.\n"
"  -v            Print libbpf debug messages\n"
"  -h            Display this help and exit\n";

static bool verbose;
static volatile int exit_req;

static int libbpf_print_fn(enum libbpf_print_level level, const char *format, va_list args)
{
	if (level == LIBBPF_DEBUG && !verbose)
		return 0;
	return vfprintf(stderr, format, args);
}

static void sigint_handler(int sig)
{
	exit_req = 1;
}

static void read_stats(struct scx_stasis *skel, __u64 *stats)
{
	int nr_cpus = libbpf_num_possible_cpus();
	assert(nr_cpus > 0);
	__u64 cnts[STASIS_NR_STATS][nr_cpus];
	__u32 idx;

	memset(stats, 0, sizeof(stats[0]) * STASIS_NR_STATS);

	for (idx = 0; idx < STASIS_NR_STATS; idx++) {
		int ret, cpu;

		ret = bpf_map_lookup_elem(bpf_map__fd(skel->maps.stats),
					  &idx, cnts[idx]);
		if (ret < 0)
			continue;
		for (cpu = 0; cpu < nr_cpus; cpu++)
			stats[idx] += cnts[idx][cpu];
	}
}

/* Called from the main loop every 100 ms. Written to a temp file and renamed
 * so stasisd never reads a half-written snapshot. */
static void export_pff(int map_fd)
{
	FILE *f = fopen(PFF_PATH ".tmp", "w");
	__u32 key, next, *prev = NULL;
	struct pff v;

	if (!f)
		return;
	while (bpf_map_get_next_key(map_fd, prev, &next) == 0) {
		if (bpf_map_lookup_elem(map_fd, &next, &v) == 0)
			fprintf(f, "%u %llu %llu\n", next,
				(unsigned long long)v.maj_flt,
				(unsigned long long)v.run_ns);
		key = next;
		prev = &key;
	}
	fclose(f);
	rename(PFF_PATH ".tmp", PFF_PATH);	/* atomic swap */
}

/* Once a second: cumulative dispatch counters for the dashboard. */
static void export_stats(const __u64 *stats)
{
	FILE *f = fopen(STATS_PATH ".tmp", "w");

	if (!f)
		return;
	fprintf(f, "local %llu\nshared %llu\nprio %llu\n",
		(unsigned long long)stats[STASIS_STAT_LOCAL],
		(unsigned long long)stats[STASIS_STAT_SHARED],
		(unsigned long long)stats[STASIS_STAT_PRIO]);
	fclose(f);
	rename(STATS_PATH ".tmp", STATS_PATH);
}

int main(int argc, char **argv)
{
	struct scx_stasis *skel;
	struct bpf_link *link;
	__u32 prio[MAX_PRIO];
	int nr_prio = 0, tick;
	__u32 opt;
	__u64 ecode;

	libbpf_set_print(libbpf_print_fn);
	signal(SIGINT, sigint_handler);
	signal(SIGTERM, sigint_handler);
	mkdir(PFF_DIR, 0755);
restart:
	skel = SCX_OPS_OPEN(stasis_ops, scx_stasis);

	optind = 1;
	nr_prio = 0;
	while ((opt = getopt(argc, argv, "p:w:vh")) != -1) {
		switch (opt) {
		case 'w':
			skel->struct_ops.stasis_ops->timeout_ms = strtoul(optarg, NULL, 10);
			break;
		case 'p':
			if (nr_prio == MAX_PRIO) {
				fprintf(stderr, "too many -p (max %d)\n", MAX_PRIO);
				return 1;
			}
			prio[nr_prio++] = strtoul(optarg, NULL, 10);
			break;
		case 'v':
			verbose = true;
			break;
		default:
			fprintf(stderr, help_fmt, basename(argv[0]));
			return opt != 'h';
		}
	}

	SCX_OPS_LOAD(skel, stasis_ops, scx_stasis, uei);

	for (int i = 0; i < nr_prio; i++) {
		__u8 one = 1;

		if (bpf_map_update_elem(bpf_map__fd(skel->maps.prio_tgid), &prio[i],
					&one, BPF_ANY))
			fprintf(stderr, "failed to protect tgid %u\n", prio[i]);
		else
			printf("protected tgid %u\n", prio[i]);
	}

	link = SCX_OPS_ATTACH(skel, stasis_ops, scx_stasis);

	for (tick = 0; !exit_req && !UEI_EXITED(skel, uei); tick++) {
		export_pff(bpf_map__fd(skel->maps.pff_by_tgid));
		if (tick % 10 == 0) {
			__u64 stats[STASIS_NR_STATS];

			read_stats(skel, stats);
			export_stats(stats);
			printf("local=%llu shared=%llu prio=%llu\n",
			       stats[STASIS_STAT_LOCAL], stats[STASIS_STAT_SHARED], stats[STASIS_STAT_PRIO]);
			fflush(stdout);
		}
		usleep(100000);
	}

	bpf_link__destroy(link);
	ecode = UEI_REPORT(skel, uei);
	scx_stasis__destroy(skel);

	if (UEI_ECODE_RESTART(ecode))
		goto restart;
	return 0;
}
