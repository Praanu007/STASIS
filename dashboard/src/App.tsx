import { S, useVersion } from './store';
import { monitorShown, needsServer } from './controller';
import { TopBar } from './components/TopBar';
import { ControlBar } from './components/ControlBar';
import { Notice } from './components/controls';
import { Kpis } from './components/Kpis';
import { Timeline } from './components/Timeline';
import { Rail } from './components/Rail';
import { Results } from './components/Results';
import { Offline } from './components/Offline';
import { Footer } from './components/Footer';
import { Tooltip } from './components/Tooltip';

export function App() {
  useVersion();
  if (S.end > S.frames.length - 1) S.end = S.frames.length - 1;   // the history was trimmed under us
  return (
    <>
      <div className="wrap">
        <TopBar />
        <main>
          {/* live, replay and the simulator share this view */}
          {monitorShown() && (
            <section id="monitor">
              <ControlBar />
              <Notice id="notice">{S.notice}</Notice>
              <Kpis />
              <div className="deck">
                <Timeline />
                <Rail />
              </div>
            </section>
          )}
          {S.view === 'results' && <section id="results"><Results /></section>}
          {needsServer(S.view) && <section id="offline"><Offline /></section>}
          <Footer />
        </main>
      </div>
      <Tooltip />
    </>
  );
}
