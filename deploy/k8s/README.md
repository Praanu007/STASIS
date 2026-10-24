# Running stasisd on Kubernetes (arm 10)

This is the Kubernetes half of feature 10. It is **engineering, not
research**, and the thesis should present it that way: it shows the
controller is deployable where memory overcommit is actually managed, and it
makes one contrast concrete. It is not evaluated, because it cannot be on a
single 4 GB VM.

## The contrast it makes concrete

Kubernetes' de facto memory policy is a hard limit followed by a kill. A
January 2025 RFC states that "the upstream Kubernetes community hesitates to
use memory.high, and instead uses only memory.max". A pod that exceeds its
limit is OOM-killed and, if it belongs to a Deployment or Job, restarted from
scratch, losing its progress. stasisd pauses instead, so a parked batch pod
keeps its progress.

## Before you deploy

- **Never point it at Guaranteed pods or system services.** The manifest
  targets the Burstable QoS tier only.
- **stasisd's own pod must stay Guaranteed** (CPU and memory requests equal
  to their limits, as in the manifest). A Burstable stasisd would sit in the
  tier it manages and could freeze itself, leaving every pod it had parked
  frozen. Its check that refuses a cgroup containing itself reads the
  pod-level `cgroup.procs`, which is empty under cgroup v2 because a pod's
  processes live in its containers' child cgroups, so that check cannot catch
  this case.
- **Liveness probes defeat it.** A pod frozen longer than its liveness probe's
  failure threshold is restarted by the kubelet, which is a kill with extra
  steps. Batch pods that may be parked should have no liveness probe, or one
  generous enough to outlast a park.
- **Check the cgroup path on your nodes.** It differs between the systemd and
  cgroupfs cgroup drivers. `STASIS_CG` and `STASIS_JOB_PREFIX` must match what
  your kubelet actually creates.
- **Opt nodes in explicitly** with `kubectl label node <n> stasis.io/enabled=true`.
- **The controller signal here is `memstat`.** `--signal=bpf` needs
  `scx_stasis` loaded on the node, which a managed Kubernetes node will not
  allow. That limits this deployment to the controller-only ablation arm.

## Deploying

```bash
docker build -t <registry>/stasisd:<tag> -f deploy/k8s/Dockerfile .
kubectl label node <node> stasis.io/enabled=true
kubectl apply -f deploy/k8s/stasisd-daemonset.yaml
```

Replace the `image:` placeholder in the manifest with the image you built,
pinned by tag or digest.
