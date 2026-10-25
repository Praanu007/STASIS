/* wss.c - allocate SIZE_MB, then touch its hot part at random, PASSES times;
 * print progress.
 * build: cc -O2 -o /opt/stasis/wss wss.c
 *
 *   wss SIZE_MB PASSES [HOT_PCT [JUMP_PASS]]
 *
 * The whole buffer is written once, so the job's footprint is SIZE_MB. Each
 * pass then makes SIZE_MB worth of random page touches (one per 4 KiB page of
 * the buffer), all within the first HOT_PCT percent of it. With HOT_PCT = 100
 * (the default) every page is hot and the working set equals the footprint.
 * With HOT_PCT = 60 the working set is 60% of the footprint and the rest goes
 * cold: that is the workload where measuring working sets, rather than
 * counting memory, changes what fits.
 *
 * With JUMP_PASS, the job changes phase after that many passes: from then on
 * every page is hot. Its working set jumps from HOT_PCT to 100% of a buffer
 * that is already allocated, so the extra demand arrives as refaults of
 * swapped-out pages rather than as new allocations (experiment C). The job
 * prints "jump <wall-clock seconds>" when it does.
 *
 * With $STASIS_GO set to a path, the job waits after writing its start
 * marker until that file exists (deviation 23). run_mix.sh uses this as a
 * start barrier in experiment A: every job fills its memory first, then all
 * start working together. Without the variable nothing changes.
 *
 * Each progress line is "<pass> <wall-clock seconds>". The timestamp uses
 * CLOCK_REALTIME (not CLOCK_MONOTONIC as in the build guide) so job logs,
 * samples.jsonl and decisions.jsonl all share one clock for the fairness
 * and time-to-first-park metrics.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static double now(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_REALTIME, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
	if (argc < 3 || argc > 5) {
		fprintf(stderr, "usage: %s SIZE_MB PASSES [HOT_PCT [JUMP_PASS]]\n", argv[0]);
		return 1;
	}
	size_t pages = strtoul(argv[1], NULL, 10) * 256;	/* 4 KiB pages */
	long passes = strtol(argv[2], NULL, 10);
	long hot_pct = argc >= 4 ? strtol(argv[3], NULL, 10) : 100;
	long jump = argc == 5 ? strtol(argv[4], NULL, 10) : -1;	/* -1: never */
	if (pages == 0 || hot_pct < 1 || hot_pct > 100 || (argc == 5 && jump < 0)) {
		fprintf(stderr, "%s: SIZE_MB must be > 0, HOT_PCT 1..100, JUMP_PASS >= 0\n", argv[0]);
		return 1;
	}
	size_t hot = pages * (size_t)hot_pct / 100;
	if (hot == 0)
		hot = 1;
	char *buf = malloc(pages * 4096);
	if (!buf) {
		perror("malloc");
		return 1;
	}
	memset(buf, 1, pages * 4096);			/* fault the whole set in */
	printf("0 %.3f\n", now());			/* start marker */
	fflush(stdout);
	/* The start barrier (deviation 23). The marker is flushed first: the
	 * harness releases the barrier once every job's marker is in its log. */
	const char *go = getenv("STASIS_GO");
	if (go && *go) {
		struct timespec w = {0, 10000000};		/* 10 ms */
		while (access(go, F_OK) != 0)
			nanosleep(&w, NULL);
	}

	uint64_t x = 88172645463325252ULL;		/* xorshift64 state */
	for (long p = 1; p <= passes; p++) {
		if (p - 1 == jump) {				/* the phase change */
			hot = pages;
			printf("jump %.3f\n", now());
			fflush(stdout);
		}
		for (size_t i = 0; i < pages; i++) {
			x ^= x << 13; x ^= x >> 7; x ^= x << 17;
			buf[(x % hot) * 4096] += 1;
		}
		printf("%ld %.3f\n", p, now());
		fflush(stdout);
	}
	return buf[0] == 0;				/* keep the work observable */
}
