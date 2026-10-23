import { S } from '../store';

function source(): string {
  switch (S.view) {
    case null: return 'Data: starting';
    case 'sim': return 'Data: simulated model';
    case 'replay': return 'Data: recorded run, replayed from samples.jsonl';
    case 'results': return S.server && S.server.results ? 'Data: finished runs, metrics from eval/analyze.py' : 'Data: simulated runs of the model';
    default: return S.server ? 'Data: live from /proc, cgroup v2 and sched_ext' : 'Data: none until the server runs';
  }
}

export function Footer() {
  return (
    <footer className="foot">
      <span><b>STASIS</b> · Scheduling To Avert Swap-Induced Stalls</span>
      <span id="foot-src">{source()}</span>
      <span>PSI = Linux pressure stall information · PFF = page-fault frequency (major faults per ms of the job's own CPU time)</span>
    </footer>
  );
}
