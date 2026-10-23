/* wss.c - touch SIZE_MB at random, PASSES times; print progress.
 * build: cc -O2 -o /opt/stasis/wss wss.c
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
	if (argc < 3) {
		fprintf(stderr, "usage: %s SIZE_MB PASSES\n", argv[0]);
		return 1;
	}
	size_t pages = strtoul(argv[1], NULL, 10) * 256;	/* 4 KiB pages */
	long passes = strtol(argv[2], NULL, 10);
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
			buf[(x % pages) * 4096] += 1;
		}
		printf("%ld %.3f\n", p, now());
		fflush(stdout);
	}
	return buf[0] == 0;				/* keep the work observable */
}
