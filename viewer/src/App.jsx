import RadarStage from './RadarStage.jsx';

function Header() {
  return (
    <header className="app-header">
      <div className="hud-primary">
        <p className="eyebrow">ATC Benchmark</p>
        <h1>Live + Replay Viewer</h1>
        <div className="hud-inline">
          <ModeControls />
          <FileControls />
          <ScenarioSummary />
          <article className="panel hud-score-panel" id="scorePanel"></article>
        </div>
      </div>
      <div id="loadStatus" className="status-pill" role="status">
        Load a trace file to begin. Score file optional.
      </div>
    </header>
  );
}

function ModeControls() {
  return (
    <section className="panel controls mode-controls" aria-label="Viewer mode">
      <label htmlFor="modeSelect">Mode</label>
      <select id="modeSelect" defaultValue="replay">
        <option value="replay">Replay mode</option>
        <option value="live">Live mode</option>
      </select>
      <span id="modeStatus" className="muted">
        Replay mode loads files from disk.
      </span>
    </section>
  );
}

function FileControls() {
  return (
    <section className="panel controls file-controls" aria-label="Load replay files">
      <label className="file-picker">
        Trace JSONL <input id="traceFile" type="file" accept=".jsonl,.txt,application/json" />
      </label>
      <label className="file-picker">
        Score JSON <input id="scoreFile" type="file" accept=".json,application/json" />
      </label>
    </section>
  );
}

function LiveControlPanel() {
  return (
    <section className="panel live-control-panel" id="livePanel" aria-label="Live controller panel" hidden>
      <div className="section-heading">
        <div>
          <p className="eyebrow">Live Game</p>
          <h2>Session Control</h2>
        </div>
      </div>
      <div className="live-command-grid">
        <article className="panel">
          <label htmlFor="liveEndpoint">Transport endpoint</label>
          <input id="liveEndpoint" type="text" defaultValue="ws://localhost:8080/live" />
          <div className="timeline-row">
            <button id="liveConnect" type="button">Start</button>
            <button id="liveDisconnect" type="button" disabled>Disconnect</button>
            <button id="livePause" type="button" disabled>Pause</button>
            <button id="liveReset" type="button" disabled>Reset Scenario</button>
            <button id="liveEnd" type="button" disabled>End Session</button>
          </div>
        </article>
        <article className="panel">
          <label htmlFor="commandAircraft">Aircraft</label>
          <select id="commandAircraft"></select>
          <label htmlFor="commandType">Command</label>
          <select id="commandType" defaultValue="no_op">
            <option value="no_op">No action</option>
            <option value="assign_heading">Assign heading</option>
            <option value="assign_altitude">Assign altitude</option>
            <option value="assign_speed">Assign speed</option>
            <option value="clear_to_land">Clear to land</option>
            <option value="clear_for_takeoff">Clear for takeoff</option>
            <option value="go_around">Go around</option>
            <option value="hold_short">Hold short</option>
            <option value="hold_position">Hold position</option>
          </select>
          <div className="timeline-row">
            <label htmlFor="commandValue">Value</label>
            <input id="commandValue" type="number" step="1" />
            <button id="sendCommand" type="button" disabled>Send command</button>
          </div>
          <p id="commandHint" className="muted command-hint">No extra value required for this command.</p>
          <p id="commandFeedback" className="command-feedback" role="status" aria-live="polite"></p>
        </article>
      </div>
    </section>
  );
}

function LiveDashboard() {
  return (
    <section className="live-game-layout" id="liveGamePanel" aria-label="Live game dashboard" hidden>
      <article className="panel live-status-card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Objective</p>
            <h2 id="liveObjectiveTitle">Start a live session</h2>
          </div>
          <span id="liveRunState" className="status-pill">Disconnected</span>
        </div>
        <p id="liveObjectiveCopy" className="summary-copy">Start live mode to control traffic in real time.</p>
        <div id="liveStats" className="live-stat-grid"></div>
      </article>
      <article className="panel live-alert-card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Alerts</p>
            <h2>Controller Queue</h2>
          </div>
        </div>
        <div id="liveAlerts" className="live-alert-list"></div>
      </article>
      <article className="panel live-strip-card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Aircraft</p>
            <h2>Flight Strips</h2>
          </div>
        </div>
        <div id="liveStrips" className="flight-strip-list"></div>
      </article>
      <article className="panel live-log-card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Log</p>
            <h2>Recent Events</h2>
          </div>
        </div>
        <div id="liveEventLog" className="live-event-log"></div>
      </article>
    </section>
  );
}

function ScenarioSummary() {
  return (
    <section className="summary-band" id="scenarioSummary" aria-label="Scenario summary">
      <div>
        <p className="eyebrow">Scenario</p>
        <h2>No replay loaded</h2>
        <p className="summary-copy">
          Choose a trace JSONL file to see a plain-language summary of the aircraft, runway, and main safety challenge.
        </p>
      </div>
    </section>
  );
}

function RadarPanel() {
  return (
    <article className="panel radar-panel">
      <div className="section-heading radar-heading">
        <div>
          <p className="eyebrow">Live Replay</p>
          <h2>Radar View</h2>
        </div>
        <div className="radar-actions" aria-label="Radar view controls">
          <span id="tickLabel" className="tick-readout">0 / 0</span>
          <button id="zoomOut" className="icon-button" type="button" disabled aria-label="Set scope to 40 nautical miles" title="Set scope to 40 nautical miles">40nm</button>
          <button id="zoomIn" className="icon-button" type="button" disabled aria-label="Set scope to 80 nautical miles" title="Set scope to 80 nautical miles">80nm</button>
          <button id="resetView" type="button" disabled aria-label="Reset radar view" title="Reset radar view">Reset</button>
        </div>
      </div>
      <div className="radar-wrap">
        <RadarStage />
        <canvas id="radar" className="legacy-radar-canvas" width="900" height="560" aria-label="Radar replay canvas"></canvas>
        <div id="radarTooltip" className="radar-tooltip" hidden></div>
      </div>
      <div className="radar-meta overlay-panel">
        <div id="radarLegend" className="radar-legend" aria-label="Radar legend"></div>
        <label className="legend-toggle" title="Show 1-3 minute projected path for the selected aircraft.">
          <input id="togglePrediction" type="checkbox" defaultChecked /> Show prediction overlay
        </label>
      </div>
      <div className="timeline-row replay-controls overlay-panel" aria-label="Replay controls">
        <button id="stepBack" className="icon-button" type="button" disabled aria-label="Previous tick" title="Previous tick">&#9664;</button>
        <button id="playPause" type="button" disabled aria-label="Play replay" title="Play replay">Play</button>
        <button id="stepForward" className="icon-button" type="button" disabled aria-label="Next tick" title="Next tick">&#9654;</button>
        <label htmlFor="tickSlider">Time</label>
        <input id="tickSlider" type="range" min="0" max="0" defaultValue="0" disabled />
        <label htmlFor="playSpeed">Speed</label>
        <select id="playSpeed" title="Replay speed" defaultValue="1000">
          <option value="1000">1x</option>
          <option value="500">2x</option>
          <option value="250">4x</option>
        </select>
      </div>
    </article>
  );
}

function ReplayLayout() {
  return (
    <section className="replay-layout">
      <RadarPanel />
      <aside className="panel inspector-panel overlay-panel" id="aircraftPanel" aria-label="Selected aircraft">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Inspect</p>
            <h2>Aircraft</h2>
          </div>
        </div>
        <p className="muted">Hover over an aircraft for a quick label, or click one to keep its details here.</p>
      </aside>
    </section>
  );
}

function SecondaryPanels() {
  return (
    <details className="panel hud-drawer tick-drawer">
      <summary>Current Tick</summary>
      <article id="tickPanel"></article>
    </details>
  );
}

function TimelinePanel() {
  return (
    <details className="panel hud-drawer history-drawer" id="timelinePanel">
      <summary>History</summary>
      <div className="timeline-filters">
        <label title="Show only moments where the controller's action reduced the score.">
          <input type="checkbox" id="filterHurt" /> Only hurt ticks
        </label>
        <label title="Show calls caused by conflicts, runway risks, emergencies, or explicit events.">
          <input type="checkbox" id="filterSafety" /> Only safety-triggered calls
        </label>
        <label title="Show only moments where the normalized score changed by at least 0.05.">
          <input type="checkbox" id="filterLargeDelta" /> Only large deltas (|&Delta;| &ge; 0.05)
        </label>
      </div>
      <div id="timelineList" className="timeline-list"></div>
    </details>
  );
}

export default function App() {
  return (
    <main>
      <Header />
      <LiveControlPanel />
      <LiveDashboard />
      <ReplayLayout />
      <SecondaryPanels />
      <TimelinePanel />
    </main>
  );
}
