// Live and Replay read from the VM; without the server, say how to start it.
import { S } from '../store';
import { setView } from '../controller';

export function Offline() {
  return (
    <article className="card offline-card">
      <div className="empty">
        <h3 id="off-title">{S.view === 'live' ? 'Live' : 'Replay'} needs the STASIS server on your VM</h3>
        <p id="off-text">Live and Replay read from your VM. Start the server there, then open its address from the host's browser.</p>
        <pre className="cmd">{'cd ~/stasis\nsudo python3 dashboard/server.py --results /srv/results\n# then open http://<vm-ip>:8686'}</pre>
        <p className="small">Until then, the Simulator runs the same control law on a modelled 4 GiB machine.</p>
        <button className="btn primary" type="button" id="go-sim" onClick={() => setView('sim')}>Open the simulator</button>
      </div>
    </article>
  );
}
