import { useId, useMemo, useState } from 'react';
import RadarStage from './RadarStage.jsx';
import useViewerState, {
  countPhrase,
  describeAction,
  describeDecisionPoint,
  describeInvalidAction,
  formatNum,
  formatSigned,
  humanize,
  humanizeLabel,
  isSafetyTriggeredCall,
  prettifyScenarioName
} from './useViewerState.js';

function aircraftSetFromRecords(records = []) {
  return new Set(records.flatMap((record) => (Array.isArray(record.aircraft) ? record.aircraft : [record.a, record.b].filter(Boolean))));
}

function describeScenario(state, aircraft, metadata, stressors) {
  if (!aircraft.length) return 'This replay has no aircraft in the first tick, so only file-level details can be shown.';
  const arrivals = aircraft.filter((ac) => ac.role === 'arrival').length;
  const departures = aircraft.filter((ac) => ac.role === 'departure').length;
  const emergencies = aircraft.filter((ac) => ac.emergency).length;
  const runway = state.airport?.active_runway || state.airport?.runway_id || 'the active runway';
  const queue = state.airport?.departure_queue || [];
  const parts = [`This scenario starts with ${countPhrase(arrivals, 'arrival')} and ${countPhrase(departures, 'departure')} around runway ${runway}.`];
  if (queue.length) parts.push(`${queue.length === 1 ? queue[0] : `${queue.length} aircraft`} ${queue.length === 1 ? 'is' : 'are'} waiting to depart.`);
  if (emergencies) parts.push(`${countPhrase(emergencies, 'aircraft')} ${emergencies === 1 ? 'has' : 'have'} an emergency.`);
  if (stressors.length) parts.push(`The main challenge is ${stressors.map(humanize).join(', ')}.`);
  else if (metadata?.tags?.length) parts.push(`The focus is ${metadata.tags.map(humanize).join(', ')}.`);
  return parts.join(' ');
}

function describeConflicts(conflicts, predicted) {
  if (conflicts.length) return `${countPhrase(conflicts.length, 'active conflict')} needs immediate attention.`;
  if (predicted.length) return `${countPhrase(predicted.length, 'predicted conflict')} may need attention soon.`;
  return 'No aircraft conflict is detected on this tick.';
}

function describeOutcome(kind, totalDelta, explanation) {
  const before = explanation.score_before;
  const after = explanation.score_after;
  const scoreText = typeof before === 'number' && typeof after === 'number'
    ? ` The score moved from ${formatNum(before)} to ${formatNum(after)}.`
    : '';
  if (kind === 'helped') return `The action helped the run on this tick (${formatSigned(totalDelta)} points).${scoreText}`;
  if (kind === 'hurt') return `The action hurt the run on this tick (${formatSigned(totalDelta)} points).${scoreText}`;
  if (kind === 'neutral') return `The action did not materially change the score on this tick.${scoreText}`;
  return `The outcome was not classified for this tick.${scoreText}`;
}

function primaryReason(event) {
  const points = event.decision_points || [];
  if (points.length) return describeDecisionPoint(points[0]);
  const triggered = event.triggered_events || [];
  if (triggered.length) return humanizeLabel(triggered[0].type || 'Triggered event');
  return 'Routine replay tick';
}

function scoreImpactLabel(totalDelta, immediateDelta) {
  if (totalDelta === 0 && immediateDelta === 0) return 'No score change';
  return `${formatSigned(totalDelta)} pts - norm ${formatSigned(immediateDelta)}`;
}

function summarizeAction(actions) {
  if (!Array.isArray(actions) || !actions.length) return 'No action';
  return actions.map(describeAction).join('; ');
}

function Stat({ label, value, help }) {
  return (
    <div className="stat" title={help}>
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

function SectionHeading({ eyebrow, title }) {
  return (
    <div className="section-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
      </div>
    </div>
  );
}

function DisclosurePanel({ title, children, className = '', triggerClassName = '', contentClassName = '', defaultOpen = false }) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const contentId = useId();
  return (
    <section className={`disclosure-panel ${isOpen ? 'is-open' : 'is-closed'} ${className}`.trim()}>
      <button
        className={`disclosure-trigger ${triggerClassName}`.trim()}
        type="button"
        aria-expanded={isOpen}
        aria-controls={contentId}
        onClick={() => setIsOpen((open) => !open)}
      >
        <span className="disclosure-icon" aria-hidden="true"></span>
        <span className="disclosure-title">{title}</span>
      </button>
      <div id={contentId} className={`disclosure-content ${contentClassName}`.trim()} hidden={!isOpen}>
        {children}
      </div>
    </section>
  );
}

function TechnicalDetails({ label, value }) {
  return (
    <DisclosurePanel className="technical-details" title={label}>
      <pre>{JSON.stringify(value || {}, null, 2)}</pre>
    </DisclosurePanel>
  );
}

function ReadableList({ title, items, emptyText }) {
  return (
    <>
      <h4>{title}</h4>
      {items.length ? (
        <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
      ) : (
        <p className="muted">{emptyText}</p>
      )}
    </>
  );
}

function ComponentTable({ explanation, componentTotals = {} }) {
  const scoreBefore = Number(explanation.score_before || 0);
  const scoreAfter = Number(explanation.score_after || 0);
  const totalDelta = scoreAfter - scoreBefore;
  const nonZeroComponents = Object.entries(explanation.score_delta_by_component || {}).filter(([, delta]) => Number(delta) !== 0);
  if (!nonZeroComponents.length) return <p className="muted">No component-level score changes on this tick.</p>;
  return (
    <table className="component-table">
      <thead>
        <tr>
          <th>What changed</th>
          <th>Before</th>
          <th>After</th>
          <th>Delta</th>
          <th>Share</th>
        </tr>
      </thead>
      <tbody>
        {nonZeroComponents.map(([component, delta]) => {
          const deltaNum = Number(delta || 0);
          const before = Number(componentTotals[component] || 0);
          const pct = totalDelta === 0 ? 0 : (deltaNum / totalDelta) * 100;
          return (
            <tr key={component}>
              <td>{humanizeLabel(component)}</td>
              <td>{formatNum(before)}</td>
              <td>{formatNum(before + deltaNum)}</td>
              <td>{formatSigned(deltaNum)}</td>
              <td>{formatNum(pct)}%</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Header({ state }) {
  return (
    <header className="app-header">
      <div className="app-title">
        <p className="eyebrow">ATC Benchmark</p>
        <h1>Live + Replay Viewer</h1>
      </div>
      <ModeControls state={state} />
      <div className="status-pill load-status" role="status">{state.loadStatus}</div>
    </header>
  );
}

function ModeControls({ state }) {
  const isLive = state.currentMode === 'live';
  return (
    <section className="panel controls mode-controls" aria-label="Viewer mode">
      <label className="mode-switch">
        <span className="mode-label">Replay</span>
        <input
          aria-label="Live mode"
          role="switch"
          type="checkbox"
          checked={isLive}
          onChange={(event) => state.handleModeChange(event.target.checked ? 'live' : 'replay')}
        />
        <span className="mode-track" aria-hidden="true">
          <span className="mode-thumb"></span>
        </span>
        <span className="mode-label">Live</span>
      </label>
      <span className="muted">
        {isLive ? 'Live mode streams ticks from the backend.' : 'Replay mode loads files from disk.'}
      </span>
    </section>
  );
}

function FileControls({ state }) {
  return (
    <section className="panel controls file-controls" aria-label="Load replay files">
      <label className="file-picker">
        Trace JSONL
        <input aria-label="Trace JSONL" type="file" accept=".jsonl,.txt,application/json" onChange={(event) => state.loadTraceFile(event.target.files?.[0])} />
      </label>
      <label className="file-picker">
        Score JSON
        <input aria-label="Score JSON" type="file" accept=".json,application/json" onChange={(event) => state.loadScoreFile(event.target.files?.[0])} />
      </label>
      <button type="button" disabled={!state.hasSavedRun} title="Reload the most recent finished live game" onClick={state.loadLastRun}>
        Load last run
      </button>
    </section>
  );
}

function ScenarioSummary({ state }) {
  const first = state.traceEvents[0] || {};
  const last = state.traceEvents[state.traceEvents.length - 1] || {};
  const firstState = first.state || first.observation?.snapshot || {};
  const aircraft = Object.values(firstState.aircraft || {});
  const manifest = state.score?.run_manifest || {};
  const metadata = manifest.scenario_metadata || {};
  const scenarioName = prettifyScenarioName(manifest.scenario_file || 'Loaded replay');
  const agentName = manifest.agent_name ? humanize(manifest.agent_name) : 'Unknown agent';
  const runway = firstState.airport?.active_runway || firstState.airport?.runway_id || 'unknown runway';
  const duration = typeof last.time === 'number' ? `${last.time}s` : `${Math.max(0, state.traceEvents.length - 1)} ticks`;
  const tags = Array.isArray(metadata.tags) ? metadata.tags : [];
  const stressors = Array.isArray(metadata.intended_stressors) ? metadata.intended_stressors : [];

  if (!state.traceEvents.length) {
    return (
      <section className="summary-band scenario-summary" aria-label="Scenario summary">
        <div>
          <p className="eyebrow">Scenario</p>
          <h2>No replay loaded</h2>
          <p className="summary-copy">Choose a trace JSONL file to see a plain-language summary of the aircraft, runway, and main safety challenge.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="summary-band scenario-summary" aria-label="Scenario summary">
      <div>
        <p className="eyebrow">Scenario</p>
        <h2>{scenarioName}</h2>
        <p className="summary-copy">{describeScenario(firstState, aircraft, metadata, stressors)}</p>
      </div>
      <div className="summary-stats">
        <Stat label="Agent" value={agentName} help="The controller logic that produced these actions." />
        <Stat label="Score" value={state.score ? formatNum(state.score.score) : 'No score loaded'} help="Higher is better for this benchmark run." />
        <Stat label="Difficulty" value={metadata.difficulty_tier ? humanize(metadata.difficulty_tier) : 'Unknown'} help="Scenario complexity label from the benchmark metadata." />
        <Stat label="Aircraft" value={String(aircraft.length)} help="Aircraft present at the start of the replay." />
        <Stat label="Runway" value={runway} help="The active runway at the start of the replay." />
        <Stat label="Duration" value={duration} help="Replay time covered by the loaded trace." />
      </div>
      {[...tags, ...stressors].length > 0 && (
        <div className="chip-row">
          {[...tags, ...stressors].slice(0, 6).map((tag) => <span key={tag} className="chip">{humanize(tag)}</span>)}
        </div>
      )}
    </section>
  );
}

function ScorePanel({ score }) {
  if (!score) {
    return (
      <article className="panel hud-score-panel score-panel">
        <SectionHeading eyebrow="Run Result" title="Score" />
        <p className="muted">No score file loaded.</p>
      </article>
    );
  }
  const metrics = [
    ['Active conflicts', score.metrics?.active_conflicts_count_total ?? score.safety?.loss_of_separation ?? 0, 'Times aircraft were too close during the replay.'],
    ['Predicted conflicts', score.metrics?.predicted_conflicts_count_total ?? 0, 'Future conflicts detected by the simulator.'],
    ['Invalid commands', score.control_quality?.invalid_commands ?? score.metrics?.malformed_agent_outputs_count ?? 0, 'Commands rejected by the simulator rules.'],
    ['Throughput/hr', formatNum(score.metrics?.throughput_ops_per_hour ?? 0), 'Landings and departures completed per simulated hour.']
  ];
  const breakdown = Object.entries(score.score_breakdown || {}).filter(([, value]) => Number(value) !== 0);
  return (
    <article className="panel hud-score-panel score-panel">
      <SectionHeading eyebrow="Run Result" title="Score" />
      <div className="score-hero">
        <span>Total score</span>
        <b>{formatNum(score.score)}</b>
      </div>
      <div className="mini-stat-grid">
        {metrics.map(([label, value, help]) => <Stat key={label} label={label} value={String(value)} help={help} />)}
      </div>
      {breakdown.length > 0 && (
        <>
          <h3>What changed the score</h3>
          <ul>
            {breakdown.map(([key, value]) => (
              <li key={key}><b>{humanizeLabel(key)}</b>: {formatSigned(Number(value))}</li>
            ))}
          </ul>
        </>
      )}
      <TechnicalDetails label="Technical score JSON" value={score} />
    </article>
  );
}

function LiveSessionControls({ state }) {
  const isLive = state.currentMode === 'live';
  const connected = state.liveConnectionState;
  const [showMore, setShowMore] = useState(false);
  if (!isLive) return null;
  const confirmLiveControl = (control, label) => {
    if (!window.confirm(`${label} the live session?`)) return;
    state.sendLiveControl(control);
    setShowMore(false);
  };
  return (
    <section className="live-session-card live-session-header" aria-label="Live session controls">
      <span className="status-pill live-run-state">{state.liveRunState}</span>
      <div className="live-button-row">
        {!connected ? (
          <button type="button" onClick={state.connectLiveTransport}>Start</button>
        ) : (
          <>
            <button type="button" onClick={() => state.sendLiveControl(state.livePaused ? 'resume' : 'pause')}>
              {state.livePaused ? 'Resume' : 'Pause'}
            </button>
            <button type="button" onClick={state.disconnectLiveTransport}>Disconnect</button>
            <div className="live-more-actions">
              <button type="button" aria-expanded={showMore} aria-controls="liveDangerActions" onClick={() => setShowMore((open) => !open)}>More</button>
              {showMore && (
                <div id="liveDangerActions" className="live-danger-menu">
                  <button type="button" onClick={() => confirmLiveControl('reset', 'Reset')}>Reset</button>
                  <button type="button" onClick={() => confirmLiveControl('end_session', 'End')}>End</button>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}

const COMMAND_CHEAT_SHEET = [
  ['ARR1 HDG 090', 'Turn to heading 090'],
  ['ARR1 ALT 4000', 'Climb or descend to 4,000 ft'],
  ['ARR1 SPD 180', 'Adjust speed to 180 kt'],
  ['ARR1 LAND', 'Clear to land (must be aligned)'],
  ['DEP1 TAKEOFF', 'Clear for takeoff'],
  ['ARR1 GA', 'Go around'],
  ['ARR1 HOLD ALPHA', 'Hold at fix ALPHA (right turns)'],
  ['ARR1 HOLD ALPHA LEFT 6000', 'Hold at ALPHA, left turns, 6,000 ft'],
  ['ARR1 EXIT HOLD', 'Leave the holding pattern'],
  ['ARR1 RESUME', 'Resume the published procedure']
];

function CommandCheatSheet() {
  return (
    <DisclosurePanel className="command-cheat-sheet" title="Command reference">
      <table>
        <tbody>
          {COMMAND_CHEAT_SHEET.map(([syntax, meaning]) => (
            <tr key={syntax}>
              <td><code>{syntax}</code></td>
              <td>{meaning}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </DisclosurePanel>
  );
}

function LiveControlPanel({ state }) {
  if (state.currentMode !== 'live') return null;
  return (
    <section className="live-control-panel overlay-panel" aria-label="Live controller panel">
      <div className="live-command-grid">
        <article className="live-command-card">
          <div className="live-panel-title">
            <p className="eyebrow">Clearance</p>
            <h2>Command</h2>
          </div>
          <div className="live-command-fields">
            <input
              className="command-text-input"
              aria-label="Command text"
              type="text"
              autoComplete="off"
              spellCheck="false"
              placeholder="ARR1 HDG 090"
              value={state.commandText}
              onChange={(event) => state.handleCommandTextChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') state.sendLiveCommand();
              }}
            />
            <button className="send-command" type="button" onClick={state.sendLiveCommand} disabled={!state.liveConnectionState}>Send</button>
          </div>
          <p className="muted command-hint">Click a flight strip to pick an aircraft, then type a command.</p>
          <p className={`command-feedback ${state.commandFeedback.status || ''}`} role="status" aria-live="polite">
            {state.commandFeedback.message}
          </p>
          <CommandCheatSheet />
        </article>
      </div>
    </section>
  );
}

function ScoreHud({ state, event }) {
  const running = event?.running_score;
  if (state.currentMode !== 'live' || !running) return null;
  const previous = state.traceEvents[state.currentTickIndex - 1]?.running_score?.score;
  const scoreValue = Number(running.score ?? 0);
  const delta = Number.isFinite(previous) ? scoreValue - previous : 0;
  const landings = running.efficiency?.successful_landings ?? 0;
  const departures = running.efficiency?.successful_departures ?? 0;
  const lossSep = running.safety?.loss_of_separation ?? 0;
  return (
    <section className="score-hud overlay-panel" aria-label="Running score">
      <div className="score-hud-main">
        <span className="eyebrow">Score</span>
        <b>{formatNum(scoreValue)}</b>
        {delta !== 0 && (
          <span key={event.tick_id} className={`score-delta ${delta > 0 ? 'gain' : 'loss'}`}>
            {formatSigned(delta)}
          </span>
        )}
      </div>
      <div className="score-hud-stats">
        <span title="Successful landings">LDG {landings}</span>
        <span title="Successful departures">DEP {departures}</span>
        <span title="Loss of separation ticks" className={lossSep ? 'bad' : ''}>LoS {lossSep}</span>
      </div>
    </section>
  );
}

function FlightStrips({ state, event }) {
  const aircraft = Object.values(event?.state?.aircraft || {});
  const conflictSet = aircraftSetFromRecords(event?.conflicts || []);
  const predictedSet = aircraftSetFromRecords(event?.predicted_conflicts || []);
  if (!aircraft.length) return <p className="muted">No aircraft in this live session.</p>;
  return (
    <div className="flight-strips">
      {aircraft.map((ac) => {
        const role = String(ac.role || '').toLowerCase();
        const isSelected = state.selectedCallsign === ac.callsign;
        const classes = ['flight-strip', role];
        if (isSelected) classes.push('selected');
        if (conflictSet.has(ac.callsign)) classes.push('critical');
        else if (predictedSet.has(ac.callsign) || ac.emergency) classes.push('warn');
        return (
          <button
            key={ac.callsign}
            type="button"
            className={classes.join(' ')}
            onClick={() => (isSelected ? state.selectAircraftForCommand(null) : state.prefillCommand(ac.callsign))}
          >
            <span className="strip-title">
              <b>{ac.callsign}</b>
              <span>{humanize(role || 'aircraft')}</span>
            </span>
            <span className="strip-state">
              {Math.round(ac.altitude_ft)} ft · {Math.round(ac.speed_kt)} kt · HDG {Math.round(ac.heading_deg)}
            </span>
            <span className="strip-clearance">
              {ac.emergency ? 'EMERGENCY · ' : ''}{ac.clearance ? humanize(ac.clearance) : humanize(ac.status || 'airborne')}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function LiveDashboard({ state, event }) {
  const [activeTab, setActiveTab] = useState('strips');
  if (state.currentMode !== 'live') return null;
  const aircraft = Object.values(event?.state?.aircraft || {});
  const emergencies = aircraft.filter((ac) => ac.emergency).length;
  return (
    <aside className="live-game-layout" aria-label="Live game dashboard">
      <div className="live-sidebar-tabs" role="tablist" aria-label="Live sidebar">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'strips'}
          aria-controls="liveStripsPanel"
          id="liveStripsTab"
          onClick={() => setActiveTab('strips')}
        >
          Strips
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'alerts'}
          aria-controls="liveAlertsPanel"
          id="liveAlertsTab"
          onClick={() => setActiveTab('alerts')}
        >
          Alerts
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'log'}
          aria-controls="liveLogPanel"
          id="liveLogTab"
          onClick={() => setActiveTab('log')}
        >
          Log
        </button>
      </div>
      <div className="live-sidebar-body">
        {activeTab === 'strips' && (
          <article id="liveStripsPanel" className="panel live-strips-card" role="tabpanel" aria-labelledby="liveStripsTab">
            <SectionHeading eyebrow="Traffic" title="Flight Strips" />
            <FlightStrips state={state} event={event} />
          </article>
        )}
        {activeTab === 'alerts' && <LiveAlerts event={event} emergencies={emergencies} />}
        {activeTab === 'log' && <LiveEventLog entries={state.liveLogEntries} />}
      </div>
      <LiveControlPanel state={state} />
    </aside>
  );
}

function LiveAlerts({ event, emergencies }) {
  const alerts = [];
  (event?.conflicts || []).forEach((conflict) => {
    const aircraft = conflict.aircraft || [];
    alerts.push({ key: `active-conflict-${aircraft.join('-')}`, level: 'critical', text: `Active conflict: ${aircraft.join(', ')}` });
  });
  (event?.predicted_conflicts || []).forEach((conflict) => {
    const aircraft = conflict.aircraft || [];
    alerts.push({ key: `predicted-conflict-${aircraft.join('-')}`, level: 'warn', text: `Predicted conflict: ${aircraft.join(', ')}` });
  });
  (event?.decision_points || []).forEach((point) => {
    const aircraft = Array.isArray(point.aircraft) ? point.aircraft.join('-') : 'none';
    alerts.push({ key: `decision-${point.type || 'unknown'}-${point.severity || 'info'}-${aircraft}`, level: point.severity || 'info', text: describeDecisionPoint(point) });
  });
  if (emergencies) alerts.push({ key: 'emergency-priority', level: 'critical', text: `${countPhrase(emergencies, 'emergency aircraft')} require priority.` });
  return (
    <article id="liveAlertsPanel" className="panel live-alert-card" role="tabpanel" aria-labelledby="liveAlertsTab">
      <SectionHeading eyebrow="Alerts" title="Controller Queue" />
      <div className="live-alert-list">
        {alerts.length ? alerts.slice(0, 8).map((alert) => (
          <div key={alert.key} className={`live-alert live-alert-${String(alert.level).toLowerCase()}`}>{alert.text}</div>
        )) : <p className="muted">No active controller alerts.</p>}
      </div>
    </article>
  );
}

function LiveEventLog({ entries }) {
  return (
    <article id="liveLogPanel" className="panel live-log-card" role="tabpanel" aria-labelledby="liveLogTab">
      <SectionHeading eyebrow="Log" title="Recent Events" />
      <div className="live-event-log">
        {entries.length ? entries.map((entry) => (
          <div key={entry.id} className="live-log-row">
            <span>{entry.time}</span>
            <b>{entry.message}</b>
          </div>
        )) : <p className="muted">No live events yet.</p>}
      </div>
    </article>
  );
}

function RadarPanel({ state }) {
  return (
    <article className="panel radar-panel">
      <div className="radar-wrap">
        <RadarStage
          traceEvents={state.traceEvents}
          currentTickIndex={state.currentTickIndex}
          selectedCallsign={state.selectedCallsign}
          hoveredCallsign={state.hoveredCallsign}
          setHoveredCallsign={state.setHoveredCallsign}
          selectAircraftForCommand={state.selectAircraftForCommand}
          showPredictionOverlay={state.showPredictionOverlay}
          currentMode={state.currentMode}
          radarScopeRangeNm={state.radarScopeRangeNm}
          radarView={state.radarView}
          setRadarView={state.setRadarView}
          radarBounds={state.radarBounds}
          liveSnapshotsByCallsign={state.liveSnapshotsByCallsign}
          latestLiveArrivalMs={state.latestLiveArrivalMs}
        />
      </div>
    </article>
  );
}

function RadarScopeControls({ state, hasTrace }) {
  return (
    <div className="scope-controls" aria-label="Radar scope controls">
      <button className="icon-button" type="button" disabled={!hasTrace} aria-label="Set scope to 40 nautical miles" title="Set scope to 40 nautical miles" onClick={() => state.handleZoom('out')}>40nm</button>
      <button className="icon-button" type="button" disabled={!hasTrace} aria-label="Set scope to 80 nautical miles" title="Set scope to 80 nautical miles" onClick={() => state.handleZoom('in')}>80nm</button>
      <button type="button" disabled={!hasTrace} aria-label={state.currentMode === 'live' ? 'Return to latest live traffic' : 'Reset radar view'} title={state.currentMode === 'live' ? 'Return to latest live traffic' : 'Reset radar view'} onClick={state.handleResetView}>
        {state.currentMode === 'live' ? 'Live' : 'Reset'}
      </button>
      <label className="prediction-toggle" title="Show 1-3 minute projected path for the selected aircraft.">
        <input type="checkbox" checked={state.showPredictionOverlay} onChange={(event) => state.setShowPredictionOverlay(event.target.checked)} /> Prediction
      </label>
    </div>
  );
}

function LiveRadarControls({ state }) {
  const hasTrace = state.traceEvents.length > 0;
  return (
    <section className="live-radar-controls overlay-panel" aria-label="Live radar controls">
      <RadarScopeControls state={state} hasTrace={hasTrace} />
    </section>
  );
}

function ReplayControls({ state }) {
  const sliderId = useId();
  const speedId = useId();
  const hasTrace = state.traceEvents.length > 0;
  const maxTick = Math.max(0, state.traceEvents.length - 1);
  const tickLabel = hasTrace
    ? `${state.currentTickIndex + 1} / ${state.traceEvents.length} - t=${state.traceEvents[state.currentTickIndex]?.time}s`
    : '0 / 0';
  return (
    <div className="timeline-row replay-controls overlay-panel" aria-label="Replay controls">
      <button className="icon-button playback-control" type="button" disabled={!hasTrace} aria-label="Previous tick" title="Previous tick" onClick={() => state.stepTick(-1)}>&#9664;</button>
      <button className="playback-control" type="button" disabled={!hasTrace} aria-label={state.isPlaying || state.liveFollowTail ? 'Pause replay' : 'Play replay'} title="Play replay" onClick={state.togglePlayback}>
        {state.isPlaying || (state.currentMode === 'live' && state.liveFollowTail) ? 'Pause' : 'Play'}
      </button>
      <button className="icon-button playback-control" type="button" disabled={!hasTrace} aria-label="Next tick" title="Next tick" onClick={() => state.stepTick(1)}>&#9654;</button>
      <span className="tick-readout">{tickLabel}</span>
      <label className="playback-control" htmlFor={sliderId}>Time</label>
      <input
        id={sliderId}
        className="tick-slider playback-control"
        aria-label="Time"
        type="range"
        min="0"
        max={maxTick}
        value={state.currentTickIndex}
        disabled={!hasTrace}
        onChange={(event) => state.setCurrentTickIndex?.(Number(event.target.value))}
      />
      <RadarScopeControls state={state} hasTrace={hasTrace} />
      <label className="playback-control" htmlFor={speedId}>Speed</label>
      <select id={speedId} className="playback-control" aria-label="Replay speed" title="Replay speed" value={state.playSpeed} onChange={(event) => state.setPlaySpeed(Number(event.target.value))}>
        <option value="1000">1x</option>
        <option value="500">2x</option>
        <option value="250">4x</option>
      </select>
    </div>
  );
}

function AircraftPanel({ event, selectedCallsign }) {
  const ac = selectedCallsign ? event?.state?.aircraft?.[selectedCallsign] : null;
  let body = <p className="muted">Hover over an aircraft for a quick label, or click one to keep its details here.</p>;
  if (selectedCallsign && !ac) {
    body = <p className="muted">The selected aircraft is not present on this tick.</p>;
  } else if (ac) {
    const conflictSet = aircraftSetFromRecords(event.conflicts || []);
    const predictedSet = aircraftSetFromRecords(event.predicted_conflicts || []);
    const conflictState = conflictSet.has(selectedCallsign) ? 'Active conflict' : predictedSet.has(selectedCallsign) ? 'Predicted conflict' : 'Nominal';
    const alertClass = conflictSet.has(selectedCallsign) ? 'critical' : predictedSet.has(selectedCallsign) || ac.emergency ? 'warn' : 'nominal';
    const roleClass = String(ac.role || '').toLowerCase() === 'departure' ? 'departure' : 'arrival';
    body = (
      <div className={`aircraft-glance ${roleClass} ${alertClass}`}>
        <div className="aircraft-glance-head">
          <div>
            <b>{ac.callsign}</b>
            <span>{humanize(ac.role || 'aircraft')} / {humanize(ac.status || 'unknown')}</span>
          </div>
          <em>{conflictState}</em>
        </div>
        <div className="aircraft-glance-metrics">
          <span><small>Alt</small><b>{Math.round(ac.altitude_ft)}</b><small>FT</small></span>
          <span><small>Spd</small><b>{Math.round(ac.speed_kt)}</b><small>KT</small></span>
          <span><small>Hdg</small><b>{Math.round(ac.heading_deg)}</b><small>DEG</small></span>
        </div>
        <div className="aircraft-glance-tags">
          <span>{ac.clearance ? humanize(ac.clearance) : 'No clearance'}</span>
          <span>{ac.emergency ? 'Emergency' : 'No emergency'}</span>
          <span>{conflictState}</span>
        </div>
      </div>
    );
  }
  return (
    <aside className="panel inspector-panel overlay-panel" aria-label="Selected aircraft">
      <SectionHeading eyebrow="Inspect" title="Aircraft" />
      {body}
    </aside>
  );
}

function TickPanel({ event, embedded = false }) {
  const body = (
    <article>
      <SectionHeading eyebrow="Now" title="Current Tick" />
      {event ? (
        <>
          <section className="sub-panel">
            <h3>Current Situation</h3>
            <p>At {event.time}s, {countPhrase(Object.values(event.state?.aircraft || {}).length, 'aircraft')} {Object.values(event.state?.aircraft || {}).length === 1 ? 'is' : 'are'} in the replay. {describeConflicts(event.conflicts || [], event.predicted_conflicts || [])}</p>
            <ReadableList title="Why the controller was called" items={(event.decision_points || []).map(describeDecisionPoint)} emptyText="No safety or scheduling issue triggered a controller call on this tick." />
          </section>
          <section className="sub-panel">
            <h3>Controller Action</h3>
            <ReadableList title="Issued command" items={(event.tick_explanation?.action_chosen || event.actions || []).map(describeAction)} emptyText="The controller issued no command." />
            {(event.invalid_actions || []).length > 0 && <ReadableList title="Rejected command" items={(event.invalid_actions || []).map(describeInvalidAction)} emptyText="No rejected commands." />}
          </section>
          <section className="sub-panel">
            <h3>Outcome</h3>
            <span className={`outcome-badge outcome-${event.tick_explanation?.outcome?.kind || 'unknown'}`}>{humanize(event.tick_explanation?.outcome?.kind || 'unknown')}</span>
            <p>{describeOutcome(event.tick_explanation?.outcome?.kind || 'unknown', Number(event.tick_explanation?.score_after || 0) - Number(event.tick_explanation?.score_before || 0), event.tick_explanation || {})}</p>
            <ComponentTable explanation={event.tick_explanation || {}} />
          </section>
          <TechnicalDetails label="Technical tick JSON" value={{
            decision_points: event.decision_points,
            actions: event.actions,
            invalid_actions: event.invalid_actions,
            conflicts: event.conflicts,
            predicted_conflicts: event.predicted_conflicts,
            triggered_events: event.triggered_events,
            tick_explanation: event.tick_explanation
          }} />
        </>
      ) : <p className="muted">Load a trace file to see tick details.</p>}
    </article>
  );
  if (embedded) {
    return <section className="panel replay-sidebar-panel tick-drawer">{body}</section>;
  }
  return (
    <DisclosurePanel className="panel hud-drawer tick-drawer" title="Current Tick" contentClassName="hud-drawer-body">
      {body}
    </DisclosurePanel>
  );
}

function TimelinePanel({ state, embedded = false }) {
  const rows = useMemo(() => {
    const componentTotals = {};
    const filteredRows = [];
    state.traceEvents.forEach((event, index) => {
      const explanation = event.tick_explanation || {};
      const previousTotals = { ...componentTotals };
      for (const [component, delta] of Object.entries(explanation.score_delta_by_component || {})) {
        componentTotals[component] = Number(componentTotals[component] || 0) + Number(delta || 0);
      }

      const outcome = explanation.outcome?.kind || 'unknown';
      const immediateDelta = Number(explanation.outcome?.immediate_delta || 0);
      if (state.filterHurt && outcome !== 'hurt') return;
      if (state.filterSafety && !isSafetyTriggeredCall(event, explanation)) return;
      if (state.filterLargeDelta && Math.abs(immediateDelta) < 0.05) return;
      const rowKey = `${event.session_id || 'trace'}-${event.tick_id ?? event.time ?? primaryReason(event)}-${index}`;
      filteredRows.push({ event, index, explanation, componentTotals: previousTotals, rowKey });
    });
    return filteredRows;
  }, [state.traceEvents, state.filterHurt, state.filterSafety, state.filterLargeDelta]);
  const body = (
    <>
      <div className="timeline-filters">
        <label title="Show only moments where the controller's action reduced the score.">
          <input type="checkbox" checked={state.filterHurt} onChange={(event) => state.setFilterHurt(event.target.checked)} /> Hurt
        </label>
        <label title="Show calls caused by conflicts, runway risks, emergencies, or explicit events.">
          <input type="checkbox" checked={state.filterSafety} onChange={(event) => state.setFilterSafety(event.target.checked)} /> Safety
        </label>
        <label title="Show only moments where the normalized score changed by at least 0.05.">
          <input type="checkbox" checked={state.filterLargeDelta} onChange={(event) => state.setFilterLargeDelta(event.target.checked)} /> Large delta
        </label>
      </div>
      <div className="timeline-list">
        {!state.traceEvents.length && <p className="muted">Load a trace file to see replay events.</p>}
        {state.traceEvents.length > 0 && !rows.length && <p className="muted">No replay events match the selected filters.</p>}
        {rows.map(({ event, index, explanation, componentTotals: totals, rowKey }) => {
          const outcomeKind = explanation.outcome?.kind || 'unknown';
          const immediateDelta = Number(explanation.outcome?.immediate_delta || 0);
          const totalDelta = Number(explanation.score_after || 0) - Number(explanation.score_before || 0);
          return (
            <DisclosurePanel
              key={rowKey}
              className="timeline-item"
              triggerClassName="timeline-summary"
              title={(
                <>
                <span className="timeline-summary-left">
                  <span>#{index + 1} - {event.time}s</span>
                  <span className={`outcome-badge outcome-${outcomeKind}`}>{humanize(outcomeKind)}</span>
                  <span>{primaryReason(event)}</span>
                  {!embedded && <span>{summarizeAction(explanation.action_chosen || event.actions || [])}</span>}
                </span>
                <span className="timeline-summary-right">{scoreImpactLabel(totalDelta, immediateDelta)}</span>
                </>
              )}
            >
              <div className="timeline-body">
                <p>{describeOutcome(outcomeKind, totalDelta, explanation)}</p>
                <ComponentTable explanation={explanation} componentTotals={totals} />
                <div className="timeline-actions">
                  <button className="jump-link" type="button" onClick={() => state.setCurrentTickIndex?.(index)}>Jump to this moment</button>
                </div>
              </div>
            </DisclosurePanel>
          );
        })}
      </div>
    </>
  );
  if (embedded) {
    return <section className="panel replay-sidebar-panel history-drawer">{body}</section>;
  }
  return (
    <DisclosurePanel className="panel hud-drawer history-drawer" title="History" contentClassName="hud-drawer-body">
      {body}
    </DisclosurePanel>
  );
}

function BottomHud({ state }) {
  return (
    <section className="bottom-hud" aria-label="Replay bottom HUD">
      <ReplayControls state={state} />
    </section>
  );
}

function ReplayInfoPanel({ state }) {
  return (
    <section className="replay-info-panel" aria-label="Replay setup and summary">
      <SectionHeading eyebrow="Replay" title="Scenario" />
      <FileControls state={state} />
      <ScenarioSummary state={state} />
      <ScorePanel score={state.score} />
    </section>
  );
}

function ReplaySidebar({ state, event }) {
  const [activeTab, setActiveTab] = useState('history');
  return (
    <aside className="replay-sidebar" aria-label="Replay detail sidebar">
      <ReplayInfoPanel state={state} />
      <div className="replay-detail-section">
        <div className="replay-sidebar-tabs" role="tablist" aria-label="Replay sidebar">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'history'}
            aria-controls="replayHistoryPanel"
            id="replayHistoryTab"
            onClick={() => setActiveTab('history')}
          >
            History
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'tick'}
            aria-controls="replayTickPanel"
            id="replayTickTab"
            onClick={() => setActiveTab('tick')}
          >
            Tick
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'aircraft'}
            aria-controls="replayAircraftPanel"
            id="replayAircraftTab"
            onClick={() => setActiveTab('aircraft')}
          >
            Aircraft
          </button>
        </div>
        <div className="replay-sidebar-body">
          {activeTab === 'history' && (
            <div id="replayHistoryPanel" role="tabpanel" aria-labelledby="replayHistoryTab">
              <TimelinePanel state={state} embedded />
            </div>
          )}
          {activeTab === 'tick' && (
            <div id="replayTickPanel" role="tabpanel" aria-labelledby="replayTickTab">
              <TickPanel event={event} embedded />
            </div>
          )}
          {activeTab === 'aircraft' && (
            <div id="replayAircraftPanel" role="tabpanel" aria-labelledby="replayAircraftTab">
              <AircraftPanel event={event} selectedCallsign={state.selectedCallsign} />
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

function Stars({ count, max = 3 }) {
  return (
    <span className="stars" aria-label={`${count} of ${max} stars`}>
      {Array.from({ length: max }, (_, idx) => (
        <span key={idx} className={idx < count ? 'star earned' : 'star'} aria-hidden="true">
          {idx < count ? '★' : '☆'}
        </span>
      ))}
    </span>
  );
}

function LevelSelect({ state }) {
  const showLobby = state.liveConnectionState
    && !state.debrief
    && !state.currentLevel
    && state.traceEvents.length === 0;
  if (!showLobby) return null;
  return (
    <section className="level-select-panel overlay-panel" aria-label="Level select">
      <SectionHeading eyebrow="Tower" title="Choose a Level" />
      {!state.levels && <p className="muted">Loading levels…</p>}
      {state.levels && !state.levels.length && <p className="muted">No scenarios found on the server.</p>}
      <div className="level-grid">
        {(state.levels || []).map((level) => {
          const best = state.bestResults[level.id];
          return (
            <button
              key={level.id}
              type="button"
              className="level-card"
              onClick={() => state.startLevel(level.id)}
            >
              <span className="level-card-head">
                <b>{level.name}</b>
                {level.difficulty_tier && <span className="chip">{humanize(level.difficulty_tier)}</span>}
              </span>
              <span className="level-card-traffic">
                {countPhrase(level.arrivals ?? 0, 'arrival')} · {countPhrase(level.departures ?? 0, 'departure')}
                {level.has_events ? ' · scripted events' : ''}
              </span>
              {level.description && <span className="level-card-desc">{level.description}</span>}
              {(level.tags || []).length > 0 && (
                <span className="level-card-tags">{level.tags.slice(0, 3).map(humanize).join(', ')}</span>
              )}
              <span className="level-card-best">
                {best ? (
                  <>
                    <Stars count={best.stars ?? 0} />
                    <span>Best {formatNum(best.bestScore)}</span>
                  </>
                ) : (
                  <span className="muted">Not played yet</span>
                )}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function DebriefOverlay({ state }) {
  const debrief = state.debrief;
  if (!debrief) return null;
  const verdict = debrief.debrief?.outcome || debrief.outcome || 'complete';
  const scoreValue = Number(debrief.score?.score ?? 0);
  const breakdown = Object.entries(debrief.score?.score_breakdown || {}).filter(([, value]) => Number(value) !== 0);
  const details = debrief.debrief?.details || [];
  return (
    <div className="debrief-overlay" role="dialog" aria-label="Mission debrief">
      <article className={`debrief-card outcome-${verdict}`}>
        <p className="eyebrow">Mission debrief</p>
        <h2>{humanize(verdict)}</h2>
        <Stars count={debrief.stars ?? 0} />
        <div className="debrief-score">
          <span>Final score</span>
          <b>{formatNum(scoreValue)}</b>
        </div>
        {details.length > 0 && (
          <ul className="debrief-details">
            {details.map((line) => <li key={line}>{line}</li>)}
          </ul>
        )}
        {breakdown.length > 0 && (
          <ul className="debrief-breakdown">
            {breakdown.map(([key, value]) => (
              <li key={key}><b>{humanizeLabel(key)}</b>: {formatSigned(Number(value))}</li>
            ))}
          </ul>
        )}
        <div className="debrief-actions">
          <button type="button" onClick={() => state.startLevel(debrief.scenario)}>Play again</button>
          <button type="button" onClick={state.watchReplay}>Watch replay</button>
          <button type="button" onClick={state.returnToLevelSelect}>Choose level</button>
        </div>
      </article>
    </div>
  );
}

function PauseOverlay({ state }) {
  if (state.currentMode !== 'live' || !state.livePaused || state.debrief) return null;
  return (
    <div className="pause-overlay" aria-label="Simulation paused">
      <b>PAUSED</b>
      <span>Resume to continue controlling traffic.</span>
    </div>
  );
}

function LiveLayout({ state, event }) {
  return (
    <section className="live-overlay-layer workspace-overlay" aria-label="Live mode workspace">
      <LiveSessionControls state={state} />
      <ScoreHud state={state} event={event} />
      <LiveDashboard state={state} event={event} />
      <LiveRadarControls state={state} />
      <PauseOverlay state={state} />
      <LevelSelect state={state} />
      <DebriefOverlay state={state} />
    </section>
  );
}

function ReplayLayout({ state, event }) {
  return (
    <section className="replay-overlay-layer workspace-overlay" aria-label="Replay mode workspace">
      <ReplaySidebar state={state} event={event} />
      <BottomHud state={state} />
    </section>
  );
}

export default function App() {
  const state = useViewerState();
  const event = state.traceEvents[state.currentTickIndex];
  const isLive = state.currentMode === 'live';
  return (
    <main className={`app-shell ${isLive ? 'live-mode mode-live' : 'mode-replay'}`}>
      <Header state={state} />
      <section className="workspace-container" aria-label={isLive ? 'Live radar workspace' : 'Replay radar workspace'}>
        <div className="radar-stage-canvas">
          <RadarPanel state={state} />
        </div>
        {isLive ? <LiveLayout state={state} event={event} /> : <ReplayLayout state={state} event={event} />}
      </section>
    </main>
  );
}
