/* wss.c - allocate SIZE_MB, then touch its hot part at random, PASSES times;
 * print progress.
 * build: cc -O2 -o /opt/stasis/wss wss.c
 *
 *   wss SIZE_MB PASSES [HOT_PCT]
 *
 * The whole buffer is written once, so the job's footprint is SIZE_MB. Each
 * pass then makes SIZE_MB worth of random page touches (one per 4 KiB page of
 * the buffer), all within the first HOT_PCT percent of it. With HOT_PCT = 100
 * (the default) every page is hot and the working set equals the footprint.
 * With HOT_PCT = 60 the working set is 60% of the footprint and the rest goes
 * cold: that is the workload where measuring working sets, rather than
 * counting memory, changes what fits.
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

static double now(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_REALTIME, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
	if (argc < 3 || argc > 4) {
		fprintf(stderr, "usage: %s SIZE_MB PASSES [HOT_PCT]\n", argv[0]);
		return 1;
	}
	size_t pages = strtoul(argv[1], NULL, 10) * 256;	/* 4 KiB pages */
	long passes = strtol(argv[2], NULL, 10);
	long hot_pct = argc == 4 ? strtol(argv[3], NULL, 10) : 100;
	if (pages == 0 || hot_pct < 1 || hot_pct > 100) {
		fprintf(stderr, "%s: SIZE_MB must be > 0 and HOT_PCT 1..100\n", argv[0]);
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

	uint64_t x = 88172645463325252ULL;		/* xorshift64 state */
	for (long p = 1; p <= passes; p++) {
		for (size_t i = 0; i < pages; i++) {
			x ^= x << 13; x ^= x >> 7; x ^= x << 17;
			buf[(x % hot) * 4096] += 1;
		}
		printf("%ld %.3f\n", p, now());
		fflush(stdout);
	}
	return buf[0] == 0;				/* keep the work observable */
}
