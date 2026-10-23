| H | claim | N | STASIS (median, range) | best baseline | result |
|---|---|---|---|---|---|
| H1 | Full STASIS has higher throughput than every baseline under overload | 6 | no data | - | MISSING |
| H1 | Full STASIS has higher throughput than every baseline under overload | 8 | no data | - | MISSING |
| H1c | Controller-only beats EEVDF on throughput | 8 | no data | - | MISSING |
| H2 | No workload process is killed under full STASIS (it sends no kills; this checks that the OOM killer and oomd didn't either) | 4 | no data | (= 0) | MISSING |
| H2 | No workload process is killed under full STASIS (it sends no kills; this checks that the OOM killer and oomd didn't either) | 6 | no data | (= 0) | MISSING |
| H2 | No workload process is killed under full STASIS (it sends no kills; this checks that the OOM killer and oomd didn't either) | 8 | no data | (= 0) | MISSING |
| H2c | No workload process is killed under controller-only STASIS | 4 | no data | (= 0) | MISSING |
| H2c | No workload process is killed under controller-only STASIS | 6 | no data | (= 0) | MISSING |
| H2c | No workload process is killed under controller-only STASIS | 8 | no data | (= 0) | MISSING |
| H3 | Full STASIS is fairer (Jain) than thrash-protect, oomd and MGLRU | 8 | no data | - | MISSING |
| H3c | Controller-only is fairer than thrash-protect | 8 | no data | - | MISSING |
| H4 | The protected probe's p99 latency is lower under full STASIS | 8 | no data | - | MISSING |
| H5 | Scheduler PFF parks jobs earlier than the controller alone | 6 | no data | - | MISSING |
| H5 | Scheduler PFF parks jobs earlier than the controller alone | 8 | no data | - | MISSING |
| H6 | The starvation bound holds: no job stays parked more than 180 s (120 s, then one swap at a time) | 6 | no data | (at most 180) | MISSING |
| H6 | The starvation bound holds: no job stays parked more than 180 s (120 s, then one swap at a time) | 8 | no data | (at most 180) | MISSING |
| H6c | The starvation bound holds under controller-only STASIS | 6 | no data | (at most 180) | MISSING |
| H6c | The starvation bound holds under controller-only STASIS | 8 | no data | (at most 180) | MISSING |
| H7 | Fair rotation shortens the longest time a job goes without progress | 8 | no data | - | MISSING |
| H7b | No job goes as long without progress under STASIS as under thrash-protect or MGLRU | 8 | no data | - | MISSING |
| H10-q5 | Full STASIS still beats every baseline at N = 8 with a 5 s rotation quantum (--quantum=5) | 8 | no data | - | MISSING |
| H10-q30 | Full STASIS still beats every baseline at N = 8 with a 30 s rotation quantum (--quantum=30) | 8 | no data | - | MISSING |
| H10-w60 | Full STASIS still beats every baseline at N = 8 with a 60 s starvation bound (--max-wait=60) | 8 | no data | - | MISSING |
| H10-w240 | Full STASIS still beats every baseline at N = 8 with a 240 s starvation bound (--max-wait=240) | 8 | no data | - | MISSING |
| H10-t50 | Full STASIS still beats every baseline at N = 8 with a 50 ms PSI trigger (--trigger-ms=50) | 8 | no data | - | MISSING |
| H10-t200 | Full STASIS still beats every baseline at N = 8 with a 200 ms PSI trigger (--trigger-ms=200) | 8 | no data | - | MISSING |
