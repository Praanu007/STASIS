#!/usr/bin/env python3
"""STASIS Control Room: the dashboard's server. Stdlib only, read-only.

  sudo python3 dashboard/server.py [--results /srv/results] [--port 8686]

then open http://<vm-ip>:8686 from the host's browser.

  /               the dashboard (dashboard/index.html)
  /api/info       host, kernel, whether recorded results are available
  /api/stream     live frames, one per second (Server-Sent Events), built from
                  /proc, cgroup v2, sched_ext and /run/stasis/current: the run
                  directory run_mix.sh is writing to
  /api/runs       finished runs under --results
  /api/run?id=LABEL/N<n>/run<k>   one recorded run, as frames, for replay
  /api/results    per-run metrics and the pre-registered score table

The live view uses scripts/sample.py's record(), the same function that writes
samples.jsonl, so a replayed run and a live one are built by the same code.
Importing it also locks this process into RAM (mlockall): the dashboard has to
keep answering while the machine thrashes.

There is no authentication. Run it only on the lab VM's private network.
"""
import argparse, collections, importlib.util, json, math, os, re, socket, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_LINK = os.environ.get("STASIS_CURRENT", "/run/stasis/current")
DEFAULT_DECISIONS = "/var/log/stasis/decisions.jsonl"
RUN_ID = re.compile(r"^[\w.+-]+/N\d+/run\d+$")
PAGE_HEAD = (b'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
             b'<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
             b'<meta name="stasis-server" content="1">\n')     # the page asks the API only when served from here


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

sample = load("stasis_sample", os.path.join(REPO, "scripts", "sample.py"))   # mlockall
analyze = load("stasis_analyze", os.path.join(REPO, "eval", "analyze.py"))


# ---- frames ------------------------------------------------------------------
#
# A frame is one second of the machine, in the shape the dashboard draws:
#   t      wall clock (s)
#   psi    {full, some}: % of the last interval stalled on memory (from the
#          PSI totals), plus full10/some10: the kernel's avg10
#   mem    {total, avail, swap_total, swap_free} bytes
#   rate   {swpin, swpout} pages/s, majflt faults/s
#   oom    OOM kills since the first frame
#   sched  {state, ops, local, shared, prio}; the last three are dispatches/s
#   ctrl   {on, signal}: stasisd, as far as we can tell
#   run    {id, config, n, size, passes, t0, finished} or None
#   jobs   [{id, state: run|park|done, res, peak, pff, cpu, passes}]
#          pff = major faults per ms of the job's own CPU time
#   ev     controller decisions logged during this frame

def job_id(name):
    """stasis-job-3.service -> job-3"""
    return name.replace("stasis-", "").replace(".service", "")

def job_order(jid):
    try:
        return int(jid.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 1 << 30

def num(x):
    """Numbers from log files may be NaN or Infinity, which browsers' JSON.parse rejects."""
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None

def norm_event(e):
    """One line of decisions.jsonl -> a dashboard event, or None."""
    t = num(e.get("t"))
    if t is None:
        return None
    if "frozen" in e:
        return {"t": t, "kind": "park" if e["frozen"] else "wake",
                "job": job_id(e.get("job", "")), "why": e.get("why", "")}
    kind = e.get("event")
    if kind == "pressure":
        hold = e.get("hold")
        return {"t": t, "kind": "pressure", "src": e.get("src", ""),
                "active": num(e.get("active")), "parked": num(e.get("parked")),
                "warming": bool(e.get("warming", False)),
                "hold": hold if hold in ("growing", "reloading", "making-room") else None}
    if kind == "pff_hi":
        return {"t": t, "kind": "pff", "job": job_id(e.get("job", "")),
                "pff": num(e.get("pff")), "growth": num(e.get("growth"))}
    if kind == "reclaim":
        return {"t": t, "kind": "reclaim", "job": job_id(e.get("job", "")),
                "bytes": num(e.get("bytes"))}
    if kind == "start":
        return {"t": t, "kind": "start", "signal": e.get("signal"),
                "pff_hi": num(e.get("pff_hi")), "quantum": num(e.get("quantum"))}
    return None

def parse_config(run_dir):
    try:
        words = open(os.path.join(run_dir, "config")).read().split()
    except FileNotFoundError:
        return {}
    out = {"config": words[0] if words else ""}
    for w in words[1:]:
        k, _, v = w.partition("=")
        if k in ("N", "SIZE", "PASSES") and v.isdigit():
            out[k.lower()] = int(v)
    return out

def run_meta(run_dir, run_id=None):
    if not run_dir:
        return None
    cfg = parse_config(run_dir)
    meta = {"id": run_id or run_dir, "config": cfg.get("config", ""),
            "n": cfg.get("n"), "size": cfg.get("size"), "passes": cfg.get("passes"),
            "t0": analyze.read_float(os.path.join(run_dir, "t0")),
            "t_end": analyze.read_float(os.path.join(run_dir, "t_end"))}
    try:
        meta["status"] = open(os.path.join(run_dir, "status")).read().strip()
    except FileNotFoundError:
        meta["status"] = None
    meta["finished"] = meta["t_end"] is not None
    return meta


class FrameBuilder:
    """Turns consecutive sample records into frames. Remembers jobs that have
    disappeared (finished) so they stay on screen as done."""

    def __init__(self):
        self.known = {}          # job id -> last job dict
        self.oom0 = None

    def build(self, prev, rec, run, progress, events, ctrl):
        dt = max(1e-3, rec["t"] - prev["t"])
        psi = {}
        for kind in ("full", "some"):
            cur, old = rec["psi"].get(kind, {}), prev["psi"].get(kind, {})
            stalled = cur.get("total", 0) - old.get("total", 0)       # µs
            psi[kind] = round(max(0.0, min(100.0, stalled / (dt * 1e4))), 2)
            psi[kind + "10"] = cur.get("avg10", 0.0)
        if self.oom0 is None:
            self.oom0 = prev.get("oom_kill", 0)
        sch = dict(rec.get("sched") or {})
        old_sch = prev.get("sched") or {}
        for k in ("local", "shared", "prio"):
            if k in sch and k in old_sch:
                sch[k] = round(max(0, sch[k] - old_sch[k]) / dt, 1)
            else:
                sch.pop(k, None)

        target = (run or {}).get("passes")
        seen = set()
        for name, j in rec["jobs"].items():
            jid = job_id(name)
            seen.add(jid)
            p = prev["jobs"].get(name)
            pff = None
            if p:
                dms = (j["cpu_us"] - p["cpu_us"]) / 1000
                dflt = j.get("pgmajfault", 0) - p.get("pgmajfault", 0)
                pff = round(dflt / dms, 3) if dms > 0.05 else (0.0 if dflt == 0 else None)
            passes = progress.get(jid)
            state = "park" if j["frozen"] else "run"
            if target and passes is not None and passes >= target:
                state = "done"
            self.known[jid] = {"id": jid, "state": state, "res": j["cur"],
                               "peak": j.get("peak", j["cur"]), "pff": pff,
                               "cpu": round(j["cpu_us"] / 1e6, 2), "passes": passes}
        for jid, j in self.known.items():
            if jid not in seen:
                j.update(state="done", res=0, pff=None)
                if progress.get(jid) is not None:
                    j["passes"] = progress[jid]
        for jid, passes in progress.items():      # logged but never sampled
            if jid not in self.known:
                self.known[jid] = {"id": jid, "state": "done", "res": 0, "peak": 0,
                                   "pff": None, "cpu": 0, "passes": passes}

        return {"t": round(rec["t"], 3), "psi": psi, "mem": rec.get("mem", {}),
                "rate": {"swpin": round((rec["pswpin"] - prev["pswpin"]) / dt, 1),
                         "swpout": round((rec["pswpout"] - prev["pswpout"]) / dt, 1),
                         "majflt": round((rec["pgmajfault"] - prev["pgmajfault"]) / dt, 1)},
                "oom": rec.get("oom_kill", 0) - self.oom0,
                "sched": sch, "ctrl": ctrl, "run": run,
                "jobs": sorted((dict(j) for j in self.known.values()),
                               key=lambda j: job_order(j["id"])),
                "ev": events}


class Tail:
    """Follows a growing text file, returning complete new lines."""

    def __init__(self, path):
        self.path, self.pos, self.buf = path, 0, ""

    def lines(self):
        try:
            with open(self.path) as f:
                f.seek(self.pos)
                data = f.read()
                self.pos = f.tell()
        except FileNotFoundError:
            return []
        data = self.buf + data
        *lines, self.buf = data.split("\n")
        return lines


class RunFollower:
    """Progress (passes per job) and decisions for the run being written."""

    def __init__(self, run_dir):
        self.dir = run_dir
        self.logs = {}
        self.passes = {}
        decisions = os.path.join(run_dir, "decisions.jsonl") if run_dir else DEFAULT_DECISIONS
        self.decisions = Tail(decisions)
        self.ctrl_signal = None

    def poll(self):
        if self.dir:
            try:
                names = os.listdir(self.dir)
            except FileNotFoundError:
                names = []
            for n in names:
                if n.startswith("job-") and n.endswith(".log") and n not in self.logs:
                    self.logs[n] = Tail(os.path.join(self.dir, n))
            for n, tail in self.logs.items():
                for line in tail.lines():
                    parts = line.split()
                    if len(parts) == 2 and parts[0].isdigit():
                        self.passes[n[:-4]] = int(parts[0])
        events = []
        for line in self.decisions.lines():
            try:
                e = norm_event(json.loads(line))
            except json.JSONDecodeError:
                continue
            if e:
                if e["kind"] == "start":
                    self.ctrl_signal = e.get("signal")
                events.append(e)
        return dict(self.passes), events


def controller():
    """Is stasisd running, and with which signal?"""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"stasisd.py") for a in argv):
            signal = "memstat"
            for i, a in enumerate(argv):
                if a.startswith(b"--signal="):
                    signal = a.split(b"=", 1)[1].decode()
                elif a == b"--signal" and i + 1 < len(argv):
                    signal = argv[i + 1].decode()
            return {"on": True, "signal": signal}
    return {"on": False, "signal": None}


class LiveFeed(threading.Thread):
    """Samples once a second and keeps the last `history` frames."""

    def __init__(self, history):
        super().__init__(daemon=True)
        self.frames = collections.deque(maxlen=history)
        self.cond = threading.Condition()
        self.seq = 0

    def current_run(self):
        try:
            d = os.path.realpath(RUN_LINK) if os.path.islink(RUN_LINK) else None
        except OSError:
            d = None
        return d if d and os.path.isdir(d) else None

    def run(self):
        builder = FrameBuilder()
        run_dir = self.current_run()
        follower = RunFollower(run_dir)
        prev = sample.record()
        while True:
            time.sleep(max(0.05, 1.0 - (time.time() - prev["t"])))
            try:
                rec = sample.record()
                now_dir = self.current_run()
                if now_dir != run_dir:                    # a new run started
                    run_dir, follower = now_dir, RunFollower(now_dir)
                    builder = FrameBuilder()
                progress, events = follower.poll()
                frame = builder.build(prev, rec, run_meta(run_dir), progress, events,
                                      controller())
                prev = rec
            except Exception as e:                        # keep sampling no matter what
                print(f"sample failed: {e!r}", file=sys.stderr)
                continue
            with self.cond:
                self.frames.append(frame)
                self.seq += 1
                self.cond.notify_all()


# ---- recorded runs ------------------------------------------------------------

def replay_frames(run_dir, run_id):
    samples = analyze.jsonl(os.path.join(run_dir, "samples.jsonl"))
    meta = run_meta(run_dir, run_id)
    logs = analyze.job_logs(run_dir)                  # job-3 -> [(pass, t), ...]
    events = sorted(filter(None, (norm_event(e) for e in
                                  analyze.jsonl(os.path.join(run_dir, "decisions.jsonl")))),
                    key=lambda e: e["t"])
    start = next((e for e in events if e["kind"] == "start"), None)
    ctrl = {"on": bool(start), "signal": start.get("signal") if start else None}
    builder, frames, ei = FrameBuilder(), [], 0
    for i in range(1, len(samples)):
        prev, rec = samples[i - 1], samples[i]
        progress = {}
        for jid, pts in logs.items():
            done = [p for p, t in pts if t <= rec["t"]]
            if done:
                progress[jid] = max(done)
        ev = []
        while ei < len(events) and events[ei]["t"] <= rec["t"]:
            ev.append(events[ei])
            ei += 1
        frames.append(builder.build(prev, rec, meta, progress, ev, ctrl))
    return meta, frames

def probe_summary(run_dir):
    hist, mx = analyze.probe_hist(run_dir)
    if not hist:
        return None
    return {"p50": analyze.percentile(hist, 0.50), "p99": analyze.percentile(hist, 0.99),
            "max": mx}


class Results:
    """Per-run metrics, recomputed only when the set of runs changes."""

    def __init__(self, root, prereg):
        self.root, self.prereg = root, prereg
        self.key, self.rows = None, []
        self.lock = threading.Lock()

    def runs(self):
        return list(analyze.runs(self.root)) if self.root else []

    def get(self):
        with self.lock:
            found = self.runs()
            key = tuple((d, os.path.getmtime(os.path.join(d, "status"))) for *_, d in found)
            if key != self.key:
                rows = []
                for r in found:
                    try:
                        rows.append(analyze.run_metrics(*r))
                    except Exception as e:                # one bad run must not hide the rest
                        print(f"metrics failed for {r[3]}: {e!r}", file=sys.stderr)
                self.key, self.rows = key, rows
            rows = self.rows
        score, error = [], None
        try:
            score = analyze.score_table(rows, analyze.load_hypotheses(self.prereg))
        except (OSError, ValueError) as e:
            error = str(e)
        return {"rows": rows, "score": score, "score_error": error}


# ---- HTTP -------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "stasis-control-room"

    def log_message(self, fmt, *args):
        pass                                              # quiet: the VM is busy

    def send_json(self, obj, code=200):
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        app = self.server.app
        try:
            if url.path in ("/", "/index.html"):
                # index.html is written without a document skeleton so the same
                # file can also be published as a preview; add it here.
                body = PAGE_HEAD + open(os.path.join(REPO, "dashboard", "index.html"), "rb").read() + b"\n</html>\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/info":
                self.send_json({"server": "stasis", "host": socket.gethostname(),
                                "kernel": os.uname().release,
                                "results": bool(app.results.root)})
            elif url.path == "/api/stream":
                self.stream(app.live)
            elif url.path == "/api/runs":
                self.send_json({"runs": [
                    {"id": f"{label}/N{n}/run{k}", "label": label, "n": n, "run": k,
                     "status": open(os.path.join(d, "status")).read().strip()}
                    for label, n, k, d in app.results.runs()]})
            elif url.path == "/api/run":
                run_id = parse_qs(url.query).get("id", [""])[0]
                root = app.results.root
                d = os.path.realpath(os.path.join(root or "", run_id))
                if (not root or not RUN_ID.match(run_id)
                        or not d.startswith(os.path.realpath(root) + os.sep)
                        or not os.path.isdir(d)):
                    return self.send_json({"error": f"no such run: {run_id}"}, 404)
                meta, frames = replay_frames(d, run_id)
                label, n, k = run_id.split("/")
                try:
                    metrics = analyze.run_metrics(label, int(n[1:]), int(k[3:]), d)
                except Exception:
                    metrics = None
                self.send_json({"meta": meta, "frames": frames, "metrics": metrics,
                                "probe": probe_summary(d)})
            elif url.path == "/api/results":
                self.send_json(app.results.get())
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def stream(self, live):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        with live.cond:
            history, seq = list(live.frames), live.seq
        self.wfile.write(b"retry: 2000\n\n")
        self.wfile.write(b"event: history\ndata: " +
                         json.dumps(history, separators=(",", ":")).encode() + b"\n\n")
        self.wfile.flush()
        while True:
            with live.cond:
                live.cond.wait_for(lambda: live.seq > seq, timeout=15)
                fresh = list(live.frames)[-(live.seq - seq):] if live.seq > seq else []
                seq = live.seq
            if fresh:
                for f in fresh:
                    self.wfile.write(b"event: frame\ndata: " +
                                     json.dumps(f, separators=(",", ":")).encode() + b"\n\n")
            else:
                self.wfile.write(b": keep-alive\n\n")
            self.wfile.flush()


class App:
    def __init__(self, args):
        self.live = LiveFeed(args.history)
        self.results = Results(os.path.abspath(args.results) if args.results else None,
                               args.prereg)


def main():
    ap = argparse.ArgumentParser(description="STASIS Control Room server")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8686)
    ap.add_argument("--results", help="results root written by sweep.sh / campaign.sh")
    ap.add_argument("--prereg", default=os.path.join(REPO, "docs", "00-prereg.md"))
    ap.add_argument("--history", type=int, default=1800, help="live frames kept (s)")
    args = ap.parse_args()
    try:
        os.nice(-5)                                       # stay responsive under load
    except OSError:
        pass
    threading.stack_size(1 << 20)                         # threads are mlocked too
    app = App(args)
    app.live.start()
    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    httpd.daemon_threads = True
    httpd.app = app
    print(f"STASIS Control Room on port {args.port}. Open from the host:", flush=True)
    wildcard = args.bind in ("", "0.0.0.0", "::")
    for a in (host_ips() or ["<vm-ip>"]) if wildcard else [args.bind]:
        print(f"  http://{a}:{args.port}", flush=True)
    if args.results:
        print(f"recorded runs from {os.path.abspath(args.results)}", flush=True)
    httpd.serve_forever()


def host_ips():
    """IPv4 addresses the host's browser can use (the hostname often maps to 127.0.1.1)."""
    ips = set()
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout
        ips.update(a for a in out.split() if ":" not in a)
    except (OSError, subprocess.SubprocessError):
        pass
    try:                                                  # the address of the default route
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))                   # TEST-NET-1: nothing is sent
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ips - {"127.0.0.1", "127.0.1.1"})


if __name__ == "__main__":
    main()
