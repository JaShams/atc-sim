const traceFileInput = document.getElementById('traceFile');
const scoreFileInput = document.getElementById('scoreFile');
const slider = document.getElementById('tickSlider');
const tickLabel = document.getElementById('tickLabel');
const loadStatus = document.getElementById('loadStatus');
const scenarioSummary = document.getElementById('scenarioSummary');
const scorePanel = document.getElementById('scorePanel');
const tickPanel = document.getElementById('tickPanel');
const aircraftPanel = document.getElementById('aircraftPanel');
const timelineList = document.getElementById('timelineList');
const filterHurt = document.getElementById('filterHurt');
const filterSafety = document.getElementById('filterSafety');
const filterLargeDelta = document.getElementById('filterLargeDelta');
const canvas = document.getElementById('radar');
const ctx = canvas.getContext('2d');
const radarTooltip = document.getElementById('radarTooltip');
const radarLegend = document.getElementById('radarLegend');
const togglePrediction = document.getElementById('togglePrediction');
const playPause = document.getElementById('playPause');
const stepBack = document.getElementById('stepBack');
const stepForward = document.getElementById('stepForward');
const playSpeed = document.getElementById('playSpeed');
const zoomIn = document.getElementById('zoomIn');
const zoomOut = document.getElementById('zoomOut');
const resetView = document.getElementById('resetView');
const modeSelect = document.getElementById('modeSelect');
const modeStatus = document.getElementById('modeStatus');
const livePanel = document.getElementById('livePanel');
const liveEndpointInput = document.getElementById('liveEndpoint');
const liveConnect = document.getElementById('liveConnect');
const liveDisconnect = document.getElementById('liveDisconnect');
const livePause = document.getElementById('livePause');
const liveReset = document.getElementById('liveReset');
const liveEnd = document.getElementById('liveEnd');
const liveGamePanel = document.getElementById('liveGamePanel');
const liveObjectiveTitle = document.getElementById('liveObjectiveTitle');
const liveObjectiveCopy = document.getElementById('liveObjectiveCopy');
const liveRunState = document.getElementById('liveRunState');
const liveStats = document.getElementById('liveStats');
const liveAlerts = document.getElementById('liveAlerts');
const liveStrips = document.getElementById('liveStrips');
const liveEventLog = document.getElementById('liveEventLog');
const commandAircraft = document.getElementById('commandAircraft');
const commandType = document.getElementById('commandType');
const commandValue = document.getElementById('commandValue');
const sendCommand = document.getElementById('sendCommand');
const commandHint = document.getElementById('commandHint');
const commandFeedback = document.getElementById('commandFeedback');

let traceEvents = [];
let score = null;
let radarBounds = null;
let radarView = null;
let playTimer = null;
let radarTargets = [];
let hoveredCallsign = null;
let selectedCallsign = null;
let showPredictionOverlay = true;
let currentTickIndex = 0;
let isPanning = false;
let lastPanPoint = null;
let currentMode = 'replay';
let liveSocket = null;
let livePollTimer = null;
let liveSessionId = null;
let liveFrameHandle = null;
let liveSnapshotsByCallsign = new Map();
let latestLiveArrivalMs = 0;
let liveFollowTail = true;
let livePaused = false;
let liveLogEntries = [];

const LIVE_INTERPOLATION = {
  lagMs: 220,
  maxFrameDeltaMs: 120,
  maxHoldMs: 2200
};

const palette = {
  normal: '#2563eb',
  emergency: '#b45309',
  predicted: '#9333ea',
  conflict: '#dc2626',
  landed: '#64748b',
  runway: '#94a3b8',
  text: '#f8fafc',
  mutedText: '#cbd5e1'
};

const labelMap = {
  no_op: 'No action',
  assign_heading: 'Assign heading',
  assign_altitude: 'Assign altitude',
  assign_speed: 'Assign speed',
  clear_to_land: 'Clear to land',
  clear_for_takeoff: 'Clear for takeoff',
  go_around: 'Go around',
  hold_short: 'Hold short',
  hold_position: 'Hold position',
  wind_runway_mismatch: 'Runway and wind are misaligned',
  departure_ready: 'Departure is ready',
  runway_occupied: 'Runway is occupied',
  runway_arrival_conflict: 'Arrival may conflict with runway use',
  predicted_conflict: 'Possible future aircraft conflict',
  active_conflict: 'Aircraft are too close now',
  emergency: 'Emergency needs priority',
  emergency_declare: 'Emergency declared',
  loss_of_separation: 'Loss of separation',
  invalid_command: 'Invalid command',
  secondary_conflicts_created: 'Secondary conflicts created',
  conflicts_worsened: 'Conflicts worsened',
  conflicts_delayed: 'Conflicts delayed',
  conflict_resolved: 'Conflict resolved',
  arrival_delay_sec: 'Arrival delay',
  departure_delay_sec: 'Departure delay',
  successful_landing: 'Successful landing',
  successful_departure: 'Successful departure',
  emergency_handled: 'Emergency handled',
  emergency_unhandled: 'Emergency unhandled',
  emergency_priority_compliance: 'Emergency priority compliance'
};

traceFileInput.addEventListener('change', loadFiles);
scoreFileInput.addEventListener('change', loadFiles);
slider.addEventListener('input', () => {
  if (currentMode === 'live') liveFollowTail = false;
  renderAtTick(Number(slider.value));
});
slider.addEventListener('keydown', (event) => {
  if (event.key === 'ArrowLeft') {
    event.preventDefault();
    stepTick(-1);
  } else if (event.key === 'ArrowRight') {
    event.preventDefault();
    stepTick(1);
  }
});
playPause.addEventListener('click', togglePlayback);
stepBack.addEventListener('click', () => stepTick(-1));
stepForward.addEventListener('click', () => stepTick(1));
playSpeed.addEventListener('change', () => {
  if (playTimer) {
    stopPlayback();
    startPlayback();
  }
});
zoomIn.addEventListener('click', () => zoomRadar(1.35));
zoomOut.addEventListener('click', () => zoomRadar(1 / 1.35));
resetView.addEventListener('click', resetTimelineOrView);
filterHurt.addEventListener('change', renderTimeline);
filterSafety.addEventListener('change', renderTimeline);
filterLargeDelta.addEventListener('change', renderTimeline);
canvas.addEventListener('mousemove', handleRadarMove);
canvas.addEventListener('mouseleave', hideRadarTooltip);
canvas.addEventListener('click', handleRadarClick);
canvas.addEventListener('wheel', handleRadarWheel, { passive: false });
canvas.addEventListener('mousedown', startRadarPan);
window.addEventListener('mousemove', panRadar);
window.addEventListener('mouseup', stopRadarPan);
togglePrediction.addEventListener('change', () => {
  showPredictionOverlay = togglePrediction.checked;
  drawCurrentRadar();
});
modeSelect.addEventListener('change', handleModeChange);
liveConnect.addEventListener('click', connectLiveTransport);
liveDisconnect.addEventListener('click', disconnectLiveTransport);
livePause.addEventListener('click', () => sendLiveControl(livePaused ? 'resume' : 'pause'));
liveReset.addEventListener('click', () => sendLiveControl('reset'));
liveEnd.addEventListener('click', () => sendLiveControl('end_session'));
sendCommand.addEventListener('click', sendLiveCommand);
commandType.addEventListener('change', syncCommandFormForType);
commandValue.addEventListener('input', () => setCommandFeedback(null, ''));

const COMMAND_SCHEMA = {
  no_op: { label: 'No action', unitHint: null },
  assign_heading: { label: 'Assign heading', field: 'heading', min: 0, max: 359, step: 1, unitHint: 'degrees (0-359)' },
  assign_altitude: { label: 'Assign altitude', field: 'altitude_ft', min: 1000, max: 45000, step: 100, unitHint: 'ft (>= 1000)' },
  assign_speed: { label: 'Assign speed', field: 'speed_kt', min: 120, max: 280, step: 1, unitHint: 'kt (120-280)' },
  clear_to_land: { label: 'Clear to land', unitHint: null },
  clear_for_takeoff: { label: 'Clear for takeoff', unitHint: null },
  go_around: { label: 'Go around', unitHint: null },
  hold_short: { label: 'Hold short', unitHint: null },
  hold_position: { label: 'Hold position', unitHint: null }
};

const VALIDATOR_REASON_MESSAGES = {
  unknown_aircraft: 'Unknown callsign.',
  invalid_action_type: 'Unsupported command type.',
  contradictory_commands: 'Conflicting command for the same aircraft this tick.',
  invalid_heading: 'Heading must be between 0 and 359 degrees.',
  invalid_altitude: 'Altitude is below minimum allowed.',
  invalid_speed: 'Speed is outside allowed range.',
  runway_occupied: 'Runway is currently occupied.',
  not_arrival: 'Only arrivals can receive landing clearance.',
  not_on_final_or_arrival: 'Aircraft is not in a landing-eligible state.',
  not_aligned_with_active_runway: 'Aircraft is not aligned with the active runway.',
  not_in_departure_queue: 'Aircraft is not in the departure queue.',
  arrival_too_close: 'Inbound arrival is too close for safe departure.',
  not_on_approach: 'Aircraft is not on approach for go-around.'
};

renderRadarLegend();
handleModeChange();
syncCommandFormForType();
startLiveFrameLoop();

async function loadFiles() {
  if (!traceFileInput.files[0]) return;
  try {
    stopPlayback();
    traceEvents = await parseJsonl(traceFileInput.files[0]);
    score = scoreFileInput.files[0] ? JSON.parse(await scoreFileInput.files[0].text()) : null;
    radarBounds = calculateBounds(traceEvents);
    resetRadarView(false);
    selectedCallsign = null;
    hoveredCallsign = null;
    const hasTrace = traceEvents.length > 0;
    slider.disabled = !hasTrace;
    playPause.disabled = !hasTrace;
    stepBack.disabled = !hasTrace;
    stepForward.disabled = !hasTrace;
    zoomIn.disabled = !hasTrace;
    zoomOut.disabled = !hasTrace;
    resetView.disabled = !hasTrace;
    slider.min = 0;
    slider.max = Math.max(0, traceEvents.length - 1);
    slider.value = 0;
    loadStatus.textContent = score
      ? `Loaded ${traceEvents.length} ticks and matching score.`
      : `Loaded ${traceEvents.length} ticks. Score file optional.`;
    renderScenarioSummary();
    renderScore();
    renderAtTick(0);
    renderTimeline();
  } catch (err) {
    loadStatus.textContent = `Failed to parse files: ${err.message}`;
  }
}

function handleModeChange() {
  currentMode = modeSelect.value;
  const isLive = currentMode === 'live';
  document.body.classList.toggle('live-mode', isLive);
  livePanel.hidden = !isLive;
  liveGamePanel.hidden = !isLive;
  traceFileInput.closest('section').hidden = isLive;
  modeStatus.textContent = isLive
    ? 'Live mode connects to a backend adapter and streams tick snapshots.'
    : 'Replay mode loads files from disk.';
  if (!isLive) disconnectLiveTransport();
  syncPlaybackButton();
}

function connectLiveTransport() {
  const endpoint = (liveEndpointInput.value || '').trim();
  if (!endpoint) return;
  disconnectLiveTransport();
  resetLiveRunState();
  if (endpoint.startsWith('ws://') || endpoint.startsWith('wss://')) {
    liveSocket = new WebSocket(endpoint);
    liveSocket.onopen = () => {
      setLiveConnectionState(true);
      liveFollowTail = true;
      livePaused = false;
      syncPlaybackButton();
      syncLiveControlButtons();
      updateLiveRunState('Running');
      appendLiveLog('Session started.');
      loadStatus.textContent = `Live connected: ${endpoint}`;
      liveSocket.send(JSON.stringify({ type: 'subscribe_tick_stream' }));
    };
    liveSocket.onmessage = (event) => handleLiveEnvelope(safeParseJson(event.data));
    liveSocket.onerror = () => {
      loadStatus.textContent = 'Live transport error.';
    };
    liveSocket.onclose = () => setLiveConnectionState(false);
    return;
  }
  setLiveConnectionState(true);
  livePollTimer = setInterval(async () => {
    const state = await fetch(`${endpoint.replace(/\/$/, '')}/state`).then((res) => res.json()).catch(() => null);
    if (state) handleLiveEnvelope(state);
  }, 1000);
}

function disconnectLiveTransport() {
  if (liveSocket) liveSocket.close();
  liveSocket = null;
  if (livePollTimer) clearInterval(livePollTimer);
  livePollTimer = null;
  liveFollowTail = false;
  livePaused = false;
  stopPlayback();
  setLiveConnectionState(false);
  updateLiveRunState('Disconnected');
}

function setLiveConnectionState(connected) {
  liveConnect.disabled = connected;
  liveDisconnect.disabled = !connected;
  sendCommand.disabled = !connected;
  livePause.disabled = !connected;
  liveReset.disabled = !connected;
  liveEnd.disabled = !connected;
  syncLiveControlButtons();
}

function syncLiveControlButtons() {
  livePause.textContent = livePaused ? 'Resume' : 'Pause';
  livePause.setAttribute('aria-label', livePaused ? 'Resume simulation' : 'Pause simulation');
}

function resetLiveRunState() {
  stopPlayback();
  resetLiveSessionView();
  liveSessionId = null;
  livePaused = false;
  liveLogEntries = [];
  slider.disabled = true;
  playPause.disabled = true;
  stepBack.disabled = true;
  stepForward.disabled = true;
  zoomIn.disabled = true;
  zoomOut.disabled = true;
  resetView.disabled = true;
  clearNode(commandAircraft);
  renderLiveDashboard(null);
  syncLiveControlButtons();
}

function resetLiveSessionView() {
  traceEvents = [];
  score = null;
  radarBounds = null;
  radarView = null;
  currentTickIndex = 0;
  selectedCallsign = null;
  hoveredCallsign = null;
  liveSnapshotsByCallsign.clear();
  latestLiveArrivalMs = 0;
  liveFollowTail = true;
  slider.min = 0;
  slider.max = 0;
  slider.value = 0;
  tickLabel.textContent = '0 / 0';
  renderTimeline();
  renderScore();
  renderAircraftPanel(null, null);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

function handleLiveEnvelope(payload) {
  if (!payload) return;
  if (payload.session_id) liveSessionId = payload.session_id;
  if (payload.type === 'control_ack' || payload.type === 'control_status') {
    handleLiveControlStatus(payload);
    return;
  }
  if (payload.type === 'command_ack') {
    appendLiveLog(describeCommandAck(payload));
    const reason = extractCommandRejectionReason(payload);
    setCommandFeedback(payload.ok ? 'accepted' : 'rejected', payload.ok ? 'Accepted: command applied.' : `Rejected: ${reason || 'command rejected'}.`);
    return;
  }
  if (payload.type === 'level_complete') {
    persistLiveRun(payload);
    return;
  }
  const event = payload.tick || payload;
  if (!event?.state) return;
  ingestLiveSnapshots(event);
  traceEvents.push(event);
  slider.disabled = false;
  playPause.disabled = false;
  stepBack.disabled = false;
  stepForward.disabled = false;
  zoomIn.disabled = false;
  zoomOut.disabled = false;
  resetView.disabled = false;
  radarBounds = calculateBounds(traceEvents);
  if (!radarView) resetRadarView(false);
  slider.max = Math.max(0, traceEvents.length - 1);
  if (currentMode !== 'live' || liveFollowTail) {
    renderAtTick(traceEvents.length - 1);
  }
  renderTimeline();
  populateAircraftSelector(event);
  renderLiveDashboard(event);
}

function handleLiveControlStatus(payload) {
  const status = payload.status || 'running';
  if (status === 'paused') livePaused = true;
  if (status === 'running' || status === 'reset') livePaused = false;
  if (status === 'ended') {
    livePaused = true;
    setLiveConnectionState(false);
  }
  syncLiveControlButtons();
  updateLiveRunState(humanize(status));
  appendLiveLog(`Simulation ${humanize(status)}.`);
  if (status === 'reset') {
    liveFollowTail = true;
  }
}

function sendLiveControl(type) {
  if (!liveSocket || liveSocket.readyState !== WebSocket.OPEN) {
    setCommandFeedback('rejected', 'Rejected: live transport is not connected.');
    return;
  }
  liveSocket.send(JSON.stringify({ type, session_id: liveSessionId }));
  if (type === 'pause') {
    livePaused = true;
    updateLiveRunState('Paused');
    appendLiveLog('Pause requested.');
  } else if (type === 'resume') {
    livePaused = false;
    liveFollowTail = true;
    updateLiveRunState('Running');
    appendLiveLog('Resume requested.');
  } else if (type === 'reset') {
    resetLiveSessionView();
    updateLiveRunState('Resetting');
    appendLiveLog('Scenario reset requested.');
  } else if (type === 'end_session') {
    updateLiveRunState('Ending');
    appendLiveLog('End session requested.');
  }
  syncLiveControlButtons();
}

function updateLiveRunState(label) {
  liveRunState.textContent = label;
}

function renderLiveDashboard(event) {
  clearNode(liveStats);
  clearNode(liveAlerts);
  clearNode(liveStrips);
  renderLiveLog();

  if (!event?.state) {
    liveObjectiveTitle.textContent = 'Start a live session';
    liveObjectiveCopy.textContent = 'Start live mode to control traffic in real time.';
    [
      ['Clock', '0s', 'Current simulation clock.'],
      ['Aircraft', '0', 'Tracked aircraft in the live session.'],
      ['Conflicts', '0', 'Active aircraft separation conflicts.'],
      ['Runway', '-', 'Current runway state.']
    ].forEach(([label, value, help]) => liveStats.appendChild(renderStat(label, value, help)));
    appendText(liveAlerts, 'p', 'No live traffic yet.').className = 'muted';
    appendText(liveStrips, 'p', 'Flight strips appear after the first tick.').className = 'muted';
    return;
  }

  const state = event.state;
  const aircraft = Object.values(state.aircraft || {});
  const conflicts = event.conflicts || [];
  const predicted = event.predicted_conflicts || [];
  const airport = state.airport || {};
  const runway = airport.active_runway || airport.runway_id || 'unknown';
  const completed = aircraft.filter((ac) => ac.status === 'landed' || ac.status === 'exited_airspace').length;
  const emergencies = aircraft.filter((ac) => ac.emergency).length;
  const runwayStatus = airport.runway_occupied_by
    ? `${runway} occupied by ${airport.runway_occupied_by}`
    : `${runway} clear`;

  liveObjectiveTitle.textContent = `${runway} control`;
  liveObjectiveCopy.textContent = buildLiveObjectiveCopy(aircraft, runway, emergencies);
  [
    ['Clock', `${event.time}s`, 'Current simulation clock.'],
    ['Aircraft', String(aircraft.length), 'Tracked aircraft in the live session.'],
    ['Completed', String(completed), 'Aircraft landed or exited airspace.'],
    ['Conflicts', String(conflicts.length), 'Active aircraft separation conflicts.'],
    ['Predicted', String(predicted.length), 'Projected conflicts in the lookahead window.'],
    ['Runway', runwayStatus, 'Current runway state.']
  ].forEach(([label, value, help]) => liveStats.appendChild(renderStat(label, value, help)));

  renderLiveAlerts(event, emergencies);
  renderFlightStrips(event, aircraft);
}

function buildLiveObjectiveCopy(aircraft, runway, emergencies) {
  const arrivals = aircraft.filter((ac) => ac.role === 'arrival').length;
  const departures = aircraft.filter((ac) => ac.role === 'departure').length;
  const parts = [`Sequence ${countPhrase(arrivals, 'arrival')} and ${countPhrase(departures, 'departure')} around runway ${runway}.`];
  if (emergencies) parts.push(`Prioritize ${countPhrase(emergencies, 'emergency aircraft')}.`);
  parts.push('Keep separation, protect the runway, and issue clearances from the aircraft strips or command panel.');
  return parts.join(' ');
}

function renderLiveAlerts(event, emergencies) {
  const alerts = [];
  (event.conflicts || []).forEach((conflict) => alerts.push({ level: 'critical', text: `Active conflict: ${(conflict.aircraft || []).join(', ')}` }));
  (event.predicted_conflicts || []).forEach((conflict) => alerts.push({ level: 'warn', text: `Predicted conflict: ${(conflict.aircraft || []).join(', ')}` }));
  (event.decision_points || []).forEach((point) => alerts.push({ level: point.severity || 'info', text: describeDecisionPoint(point) }));
  if (emergencies) alerts.push({ level: 'critical', text: `${countPhrase(emergencies, 'emergency aircraft')} require priority.` });
  if (!alerts.length) {
    appendText(liveAlerts, 'p', 'No active controller alerts.').className = 'muted';
    return;
  }
  alerts.slice(0, 8).forEach((alert) => {
    const node = document.createElement('div');
    node.className = `live-alert live-alert-${String(alert.level).toLowerCase()}`;
    node.textContent = alert.text;
    liveAlerts.appendChild(node);
  });
}

function renderFlightStrips(event, aircraft) {
  const conflictSet = aircraftSetFromRecords(event.conflicts || []);
  const predictedSet = aircraftSetFromRecords(event.predicted_conflicts || []);
  if (!aircraft.length) {
    appendText(liveStrips, 'p', 'No aircraft in this live session.').className = 'muted';
    return;
  }
  aircraft.forEach((ac) => {
    const strip = document.createElement('button');
    strip.type = 'button';
    strip.className = 'flight-strip';
    if (selectedCallsign === ac.callsign) strip.classList.add('selected');
    if (conflictSet.has(ac.callsign)) strip.classList.add('critical');
    else if (predictedSet.has(ac.callsign) || ac.emergency) strip.classList.add('warn');
    strip.addEventListener('click', () => {
      selectedCallsign = ac.callsign;
      commandAircraft.value = ac.callsign;
      drawCurrentRadar();
      renderAircraftPanel(traceEvents[currentTickIndex], selectedCallsign);
    });

    const title = document.createElement('span');
    title.className = 'strip-title';
    appendText(title, 'b', ac.callsign);
    appendText(title, 'span', humanize(ac.role || 'aircraft'));
    const state = document.createElement('span');
    state.className = 'strip-state';
    state.textContent = `${Math.round(ac.altitude_ft)} ft | ${Math.round(ac.speed_kt)} kt | HDG ${Math.round(ac.heading_deg)}`;
    const clearance = document.createElement('span');
    clearance.className = 'strip-clearance';
    clearance.textContent = ac.clearance ? humanize(ac.clearance) : humanize(ac.status || 'airborne');
    strip.append(title, state, clearance);
    liveStrips.appendChild(strip);
  });
}

function appendLiveLog(message) {
  if (!message) return;
  liveLogEntries.unshift({ time: new Date().toLocaleTimeString(), message });
  liveLogEntries = liveLogEntries.slice(0, 12);
  renderLiveLog();
}

function renderLiveLog() {
  clearNode(liveEventLog);
  if (!liveLogEntries.length) {
    appendText(liveEventLog, 'p', 'No live events yet.').className = 'muted';
    return;
  }
  liveLogEntries.forEach((entry) => {
    const row = document.createElement('div');
    row.className = 'live-log-row';
    appendText(row, 'span', entry.time);
    appendText(row, 'b', entry.message);
    liveEventLog.appendChild(row);
  });
}

function describeCommandAck(payload) {
  const action = payload.details?.accepted_action || payload.details?.rejected_action;
  const actionText = action ? describeAction(action) : 'Command';
  if (payload.ok) return `Accepted: ${actionText}.`;
  return `Rejected: ${actionText} (${humanize(payload.reason || 'invalid command')}).`;
}

function ingestLiveSnapshots(event) {
  const now = performance.now();
  latestLiveArrivalMs = now;
  const aircraft = Object.values(event.state?.aircraft || {});
  const seen = new Set();
  aircraft.forEach((ac) => {
    const callsign = ac.callsign;
    if (!callsign) return;
    seen.add(callsign);
    const snapshot = { ...ac, __arrivalMs: now };
    const prev = liveSnapshotsByCallsign.get(callsign);
    liveSnapshotsByCallsign.set(callsign, { previous: prev?.target || snapshot, target: snapshot });
  });
  for (const callsign of liveSnapshotsByCallsign.keys()) {
    if (!seen.has(callsign)) liveSnapshotsByCallsign.delete(callsign);
  }
}

function startLiveFrameLoop() {
  let prevFrame = performance.now();
  const frame = (ts) => {
    const dt = Math.min(LIVE_INTERPOLATION.maxFrameDeltaMs, Math.max(0, ts - prevFrame));
    prevFrame = ts;
    if (currentMode === 'live' && traceEvents.length) {
      drawCurrentRadar(ts, dt);
    }
    liveFrameHandle = requestAnimationFrame(frame);
  };
  liveFrameHandle = requestAnimationFrame(frame);
}

function populateAircraftSelector(event) {
  const options = Object.keys(event?.state?.aircraft || {});
  const previous = commandAircraft.value;
  clearNode(commandAircraft);
  options.forEach((callsign) => {
    const node = document.createElement('option');
    node.value = callsign;
    node.textContent = callsign;
    commandAircraft.appendChild(node);
  });
  if (options.includes(previous)) commandAircraft.value = previous;
}

function sendLiveCommand() {
  const envelopeResult = buildLiveCommandEnvelope();
  if (!envelopeResult.ok) {
    setCommandFeedback('rejected', `Rejected: ${envelopeResult.reason}`);
    return;
  }
  const { envelope } = envelopeResult;
  setCommandFeedback(null, 'Sending command…');
  if (liveSocket && liveSocket.readyState === WebSocket.OPEN) {
    liveSocket.send(JSON.stringify(envelope));
    setCommandFeedback(null, 'Command sent. Waiting for controller response.');
    return;
  }
  const endpoint = (liveEndpointInput.value || '').trim().replace(/\/$/, '');
  fetch(`${endpoint}/command`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(envelope) })
    .then(async (res) => {
      const payload = await res.json().catch(() => ({}));
      const responseReason = extractCommandRejectionReason(payload);
      if (!res.ok || responseReason) {
        const reason = responseReason || payload?.error || `HTTP ${res.status}`;
        setCommandFeedback('rejected', `Rejected: ${reason}`);
        return;
      }
      setCommandFeedback('accepted', 'Accepted: command delivered to backend.');
    })
    .catch((err) => {
      setCommandFeedback('rejected', `Rejected: transport error (${err.message}).`);
    });
}

function buildLiveCommandEnvelope() {
  const callsign = (commandAircraft.value || '').trim();
  const actionType = commandType.value;
  const schema = COMMAND_SCHEMA[actionType];
  if (!callsign) return { ok: false, reason: 'Select a callsign.' };
  if (!schema) return { ok: false, reason: 'Select a valid action type.' };
  const command = { aircraft: callsign, type: actionType };
  if (schema.field) {
    const raw = commandValue.value;
    const numericValue = Number(raw);
    if (!raw || Number.isNaN(numericValue)) return { ok: false, reason: `Provide ${schema.unitHint}.` };
    if (numericValue < schema.min || numericValue > schema.max) {
      return { ok: false, reason: `${schema.label} must be ${schema.unitHint}.` };
    }
    command[schema.field] = numericValue;
  }
  return { ok: true, envelope: { type: 'command', session_id: liveSessionId, command } };
}

function syncCommandFormForType() {
  const schema = COMMAND_SCHEMA[commandType.value] || { unitHint: null };
  const needsNumeric = Boolean(schema.field);
  commandValue.disabled = !needsNumeric;
  commandValue.required = needsNumeric;
  commandValue.placeholder = schema.unitHint ? `Enter ${schema.unitHint}` : '';
  if (needsNumeric) {
    commandValue.min = String(schema.min);
    commandValue.max = String(schema.max);
    commandValue.step = String(schema.step || 1);
  } else {
    commandValue.value = '';
    commandValue.removeAttribute('min');
    commandValue.removeAttribute('max');
  }
  commandHint.textContent = schema.unitHint
    ? `Value required: ${schema.unitHint}.`
    : 'No extra value required for this command.';
}

function extractCommandRejectionReason(payload) {
  const reasonCode = payload?.reason || payload?.error_code || payload?.invalid?.[0]?.reason;
  if (reasonCode && VALIDATOR_REASON_MESSAGES[reasonCode]) return VALIDATOR_REASON_MESSAGES[reasonCode];
  if (typeof payload?.message === 'string' && payload.message) return payload.message;
  return reasonCode || null;
}

function setCommandFeedback(status, message) {
  commandFeedback.classList.remove('accepted', 'rejected');
  if (status === 'accepted' || status === 'rejected') commandFeedback.classList.add(status);
  commandFeedback.textContent = message || '';
}

function persistLiveRun(payload) {
  const finalScore = payload.score || null;
  const traceJsonl = traceEvents.map((event) => JSON.stringify(event)).join('\n');
  const scoreJson = JSON.stringify(finalScore || {}, null, 2);
  localStorage.setItem('atc_last_trace_jsonl', traceJsonl);
  localStorage.setItem('atc_last_score_json', scoreJson);
  score = finalScore;
  modeSelect.value = 'replay';
  handleModeChange();
  loadStatus.textContent = 'Level complete. Saved run locally and switched to replay mode.';
  renderScenarioSummary();
  renderScore();
  renderAtTick(0);
  renderTimeline();
}

function safeParseJson(value) {
  try {
    return typeof value === 'string' ? JSON.parse(value) : value;
  } catch {
    return null;
  }
}

async function parseJsonl(file) {
  const text = await file.text();
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, idx) => {
      try {
        return JSON.parse(line);
      } catch {
        throw new Error(`Invalid JSONL at line ${idx + 1}`);
      }
    });
}

function calculateBounds(events) {
  const aircraft = events.flatMap((event) => Object.values(event.state?.aircraft || {}));
  const surfacePoints = events.flatMap((event) => {
    const airport = event.state?.airport || {};
    const runway = airport.active_runway || airport.runway_id;
    const points = layoutWorldPoints(airport.layout);
    if (!findLayoutRunway(airport.layout, runway)) {
      points.push(...runwayWorldPoints(runway));
    }
    return points;
  });
  if (!aircraft.length && !surfacePoints.length) return { minX: -10, maxX: 10, minY: -10, maxY: 10 };
  const xs = [...aircraft.map((a) => a.x_nm), ...surfacePoints.map((point) => point.x_nm)];
  const ys = [...aircraft.map((a) => a.y_nm), ...surfacePoints.map((point) => point.y_nm)];
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const pad = Math.max(2, (Math.max(maxX - minX, maxY - minY) || 10) * 0.12);
  return { minX: minX - pad, maxX: maxX + pad, minY: minY - pad, maxY: maxY + pad };
}

function renderScenarioSummary() {
  clearNode(scenarioSummary);
  const first = traceEvents[0] || {};
  const last = traceEvents[traceEvents.length - 1] || {};
  const state = first.state || first.observation?.snapshot || {};
  const aircraft = Object.values(state.aircraft || {});
  const manifest = score?.run_manifest || {};
  const metadata = manifest.scenario_metadata || {};
  const scenarioName = prettifyScenarioName(manifest.scenario_file || traceFileInput.files[0]?.name || 'Loaded replay');
  const agentName = manifest.agent_name ? humanize(manifest.agent_name) : 'Unknown agent';
  const runway = state.airport?.active_runway || state.airport?.runway_id || 'unknown runway';
  const duration = typeof last.time === 'number' ? `${last.time}s` : `${Math.max(0, traceEvents.length - 1)} ticks`;
  const tags = Array.isArray(metadata.tags) ? metadata.tags : [];
  const stressors = Array.isArray(metadata.intended_stressors) ? metadata.intended_stressors : [];

  const content = document.createElement('div');
  appendText(content, 'p', 'Scenario').className = 'eyebrow';
  appendText(content, 'h2', scenarioName);
  appendText(content, 'p', describeScenario(state, aircraft, metadata, stressors)).className = 'summary-copy';

  const stats = document.createElement('div');
  stats.className = 'summary-stats';
  [
    ['Agent', agentName, 'The controller logic that produced these actions.'],
    ['Score', score ? formatNum(score.score) : 'No score loaded', 'Higher is better for this benchmark run.'],
    ['Difficulty', metadata.difficulty_tier ? humanize(metadata.difficulty_tier) : 'Unknown', 'Scenario complexity label from the benchmark metadata.'],
    ['Aircraft', String(aircraft.length), 'Aircraft present at the start of the replay.'],
    ['Runway', runway, 'The active runway at the start of the replay.'],
    ['Duration', duration, 'Replay time covered by the loaded trace.']
  ].forEach(([label, value, help]) => stats.appendChild(renderStat(label, value, help)));

  const chips = document.createElement('div');
  chips.className = 'chip-row';
  [...tags, ...stressors].slice(0, 6).forEach((tag) => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = humanize(tag);
    chips.appendChild(chip);
  });

  scenarioSummary.append(content, stats);
  if (chips.childElementCount) scenarioSummary.appendChild(chips);
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

function renderStat(label, value, help) {
  const node = document.createElement('div');
  node.className = 'stat';
  node.title = help;
  appendText(node, 'span', label);
  appendText(node, 'b', value);
  return node;
}

function renderScore() {
  clearNode(scorePanel);
  appendSectionTitle(scorePanel, 'Run Result', 'Score');
  if (!score) {
    appendText(scorePanel, 'p', 'No score file loaded. The replay still works, but score explanations and run metadata are limited.').className = 'muted';
    return;
  }

  const scoreLine = document.createElement('div');
  scoreLine.className = 'score-hero';
  appendText(scoreLine, 'span', 'Total score');
  appendText(scoreLine, 'b', formatNum(score.score));
  scorePanel.appendChild(scoreLine);

  const metrics = [
    ['Active conflicts', score.metrics?.active_conflicts_count_total ?? score.safety?.loss_of_separation ?? 0, 'Times aircraft were too close during the replay.'],
    ['Predicted conflicts', score.metrics?.predicted_conflicts_count_total ?? 0, 'Future conflicts detected by the simulator.'],
    ['Invalid commands', score.control_quality?.invalid_commands ?? score.metrics?.malformed_agent_outputs_count ?? 0, 'Commands rejected by the simulator rules.'],
    ['Throughput/hr', formatNum(score.metrics?.throughput_ops_per_hour ?? 0), 'Landings and departures completed per simulated hour.']
  ];
  const statGrid = document.createElement('div');
  statGrid.className = 'mini-stat-grid';
  metrics.forEach(([label, value, help]) => statGrid.appendChild(renderStat(label, String(value), help)));
  scorePanel.appendChild(statGrid);

  appendReadableRecordList(scorePanel, 'What changed the score', score.score_breakdown || {}, true);
  appendTechnicalDetails(scorePanel, 'Technical score JSON', score);
}

function renderAtTick(index) {
  const e = traceEvents[index];
  if (!e) return;
  currentTickIndex = index;
  slider.value = index;
  tickLabel.textContent = `${index + 1} / ${traceEvents.length} - t=${e.time}s`;

  const state = e.state || {};
  const aircraft = Object.values(state.aircraft || {});
  const conflictSet = aircraftSetFromRecords(e.conflicts || []);
  const predictedSet = aircraftSetFromRecords(e.predicted_conflicts || []);

  drawRadar(state, aircraft, conflictSet, predictedSet, e.conflicts || [], e.predicted_conflicts || []);
  renderTickDetails(e);
  renderAircraftPanel(e, selectedCallsign);
}

function aircraftSetFromRecords(records) {
  return new Set(records.flatMap((record) => (Array.isArray(record.aircraft) ? record.aircraft : [record.a, record.b].filter(Boolean))));
}

function drawRadar(state, aircraft, conflictSet, predictedSet, conflicts, predictedConflicts) {
  radarTargets = [];
  const view = getViewBounds();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawRadarGrid();

  const runway = state.airport?.active_runway || state.airport?.runway_id || 'RWY';
  ctx.fillStyle = palette.text;
  ctx.font = '600 14px Arial';
  ctx.fillText(`Runway ${runway}`, 18, 26);
  const activeRunwayDrawn = drawAirportLayout(state.airport?.layout, runway);
  if (!activeRunwayDrawn) {
    drawRunway(runway);
  }

  const byCallsign = Object.fromEntries(aircraft.map((ac) => [ac.callsign, ac]));
  drawConflictLinks(predictedConflicts, byCallsign, palette.predicted, [6, 5]);
  drawConflictLinks(conflicts, byCallsign, palette.conflict, []);

  for (const ac of aircraft) {
    const x = project(ac.x_nm, view.minX, view.maxX, 44, canvas.width - 44);
    const y = project(ac.y_nm, view.minY, view.maxY, canvas.height - 44, 44);
    const isLanded = ac.status === 'landed' || ac.status === 'exited_airspace';
    const isSelected = ac.callsign === selectedCallsign;
    const isHovered = ac.callsign === hoveredCallsign;
    const color = isLanded
      ? palette.landed
      : conflictSet.has(ac.callsign)
        ? palette.conflict
        : predictedSet.has(ac.callsign)
          ? palette.predicted
          : ac.emergency
            ? palette.emergency
            : palette.normal;

    if (isSelected || isHovered) {
      ctx.strokeStyle = '#f8fafc';
      ctx.lineWidth = isSelected ? 3 : 2;
      ctx.beginPath();
      ctx.arc(x, y, isSelected ? 12 : 10, 0, Math.PI * 2);
      ctx.stroke();
    }

    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, Math.PI * 2);
    ctx.fill();

    const headingRad = ((Number(ac.heading_deg || 0) - 90) * Math.PI) / 180;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + 20 * Math.cos(headingRad), y + 20 * Math.sin(headingRad));
    ctx.stroke();

    ctx.fillStyle = palette.text;
    ctx.font = '12px Arial';
    ctx.fillText(ac.callsign, x + 10, y - 12);
    ctx.fillStyle = palette.mutedText;
    ctx.fillText(`${Math.round(ac.altitude_ft)} ft - ${Math.round(ac.speed_kt)} kt`, x + 10, y + 3);

    radarTargets.push({ ac, x, y, conflict: conflictSet.has(ac.callsign), predicted: predictedSet.has(ac.callsign) });
  }

  if (showPredictionOverlay && selectedCallsign) {
    const selectedAircraft = byCallsign[selectedCallsign];
    if (selectedAircraft) drawPredictionOverlay(state, selectedAircraft);
  }
}

function drawPredictionOverlay(state, aircraft) {
  if (!Number.isFinite(Number(aircraft.x_nm)) || !Number.isFinite(Number(aircraft.y_nm))) return;
  const headingDeg = Number(aircraft.heading_deg);
  const speedKt = Number(aircraft.speed_kt);
  if (!Number.isFinite(headingDeg) || !Number.isFinite(speedKt)) return;
  const view = getViewBounds();
  const intervalsSec = [60, 120, 180];
  const wind = resolveWindVector(state);
  const points = intervalsSec.map((seconds) => projectAircraftPosition(aircraft, seconds, wind));
  const all = [{ x_nm: Number(aircraft.x_nm), y_nm: Number(aircraft.y_nm) }, ...points];

  ctx.save();
  ctx.setLineDash([4, 5]);
  ctx.strokeStyle = 'rgba(248, 250, 252, 0.8)';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  all.forEach((point, idx) => {
    const x = project(point.x_nm, view.minX, view.maxX, 44, canvas.width - 44);
    const y = project(point.y_nm, view.minY, view.maxY, canvas.height - 44, 44);
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.setLineDash([]);

  points.forEach((point, idx) => {
    const x = project(point.x_nm, view.minX, view.maxX, 44, canvas.width - 44);
    const y = project(point.y_nm, view.minY, view.maxY, canvas.height - 44, 44);
    ctx.fillStyle = 'rgba(248, 250, 252, 0.95)';
    ctx.beginPath();
    ctx.arc(x, y, 2.8, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = 'rgba(248, 250, 252, 0.95)';
    ctx.font = '11px Arial';
    ctx.fillText(`+${idx + 1}m`, x + 6, y - 6);
  });
  ctx.restore();
}

function projectAircraftPosition(aircraft, secondsAhead, windVector) {
  const headingRad = ((Number(aircraft.heading_deg) - 90) * Math.PI) / 180;
  const distanceNm = (Number(aircraft.speed_kt) * secondsAhead) / 3600;
  let dx = Math.cos(headingRad) * distanceNm;
  let dy = Math.sin(headingRad) * distanceNm;
  if (windVector) {
    const windDistanceNm = (windVector.speedKt * secondsAhead) / 3600;
    dx += Math.cos(windVector.headingRad) * windDistanceNm;
    dy += Math.sin(windVector.headingRad) * windDistanceNm;
  }
  return {
    x_nm: Number(aircraft.x_nm) + dx,
    y_nm: Number(aircraft.y_nm) + dy
  };
}

function resolveWindVector(state) {
  const weather = state?.weather || {};
  const speedKt = Number(weather.wind_speed_kt ?? weather.wind_speed_kts ?? weather.wind_kt ?? weather.speed_kt);
  const fromDeg = Number(weather.wind_dir_deg ?? weather.wind_direction_deg ?? weather.wind_from_deg ?? weather.direction_deg);
  if (!Number.isFinite(speedKt) || speedKt <= 0 || !Number.isFinite(fromDeg)) return null;
  const toHeadingDeg = (fromDeg + 180) % 360;
  return { speedKt, headingRad: ((toHeadingDeg - 90) * Math.PI) / 180 };
}

function drawRadarGrid() {
  ctx.strokeStyle = 'rgba(148, 163, 184, 0.16)';
  ctx.lineWidth = 1;
  for (let x = 80; x < canvas.width; x += 80) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, canvas.height);
    ctx.stroke();
  }
  for (let y = 80; y < canvas.height; y += 80) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(canvas.width, y);
    ctx.stroke();
  }
}

function drawAirportLayout(layout, activeRunwayId) {
  if (!layout || typeof layout !== 'object') return false;
  const runways = Array.isArray(layout.runways) ? layout.runways : [];
  const taxiways = Array.isArray(layout.taxiways) ? layout.taxiways : [];
  const aprons = Array.isArray(layout.aprons) ? layout.aprons : [];
  const stands = Array.isArray(layout.stands) ? layout.stands : [];

  aprons.forEach(drawApron);
  taxiways.forEach(drawTaxiway);
  runways.forEach((runway) => drawLayoutRunway(runway, runway.id === activeRunwayId));
  stands.forEach(drawStand);

  return Boolean(findLayoutRunway(layout, activeRunwayId));
}

function drawApron(apron) {
  const polygon = validPointList(apron?.polygon, 3);
  if (!polygon) return;
  ctx.fillStyle = 'rgba(71, 85, 105, 0.28)';
  ctx.strokeStyle = 'rgba(148, 163, 184, 0.28)';
  ctx.lineWidth = 1;
  drawProjectedPath(polygon, true);
  if (apron.id) drawSurfaceLabel(apron.id, polygon[0], 'rgba(203, 213, 225, 0.72)');
}

function drawTaxiway(taxiway) {
  const points = validPointList(taxiway?.points, 2);
  if (!points) return;
  const view = getViewBounds();
  const scale = Math.min((canvas.width - 88) / (view.maxX - view.minX), (canvas.height - 88) / (view.maxY - view.minY));
  ctx.strokeStyle = 'rgba(125, 211, 252, 0.38)';
  ctx.lineWidth = Math.max(2, Math.min(8, Number(taxiway.width_nm || 0.03) * scale));
  ctx.lineCap = 'round';
  drawProjectedPath(points, false);
  ctx.lineCap = 'butt';
}

function drawLayoutRunway(runway, isActive) {
  const ends = validPointList(runway?.ends, 2, true);
  if (!ends) return;
  drawRunway(runway.id || 'RWY', ends, runway.width_nm, isActive);
}

function drawStand(stand) {
  if (!isWorldPoint(stand?.position)) return;
  const view = getViewBounds();
  const x = project(stand.position.x_nm, view.minX, view.maxX, 44, canvas.width - 44);
  const y = project(stand.position.y_nm, view.minY, view.maxY, canvas.height - 44, 44);
  ctx.fillStyle = 'rgba(226, 232, 240, 0.7)';
  ctx.strokeStyle = 'rgba(15, 23, 42, 0.8)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  if (stand.id) {
    ctx.fillStyle = 'rgba(203, 213, 225, 0.78)';
    ctx.font = '10px Arial';
    ctx.fillText(stand.id, x + 7, y + 4);
  }
}

function drawProjectedPath(points, closePath) {
  const view = getViewBounds();
  ctx.beginPath();
  points.forEach((point, idx) => {
    const x = project(point.x_nm, view.minX, view.maxX, 44, canvas.width - 44);
    const y = project(point.y_nm, view.minY, view.maxY, canvas.height - 44, 44);
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  if (closePath) {
    ctx.closePath();
    ctx.fill();
  }
  ctx.stroke();
}

function drawSurfaceLabel(label, point, color) {
  const view = getViewBounds();
  const x = project(point.x_nm, view.minX, view.maxX, 44, canvas.width - 44);
  const y = project(point.y_nm, view.minY, view.maxY, canvas.height - 44, 44);
  ctx.fillStyle = color;
  ctx.font = '10px Arial';
  ctx.fillText(label, x + 6, y - 6);
}

function drawRunway(runwayId, points = runwayWorldPoints(runwayId), widthNm = null, isActive = true) {
  if (!Array.isArray(points) || points.length < 2) return;
  const view = getViewBounds();
  const start = points[0];
  const end = points[1];
  const x1 = project(start.x_nm, view.minX, view.maxX, 44, canvas.width - 44);
  const y1 = project(start.y_nm, view.minY, view.maxY, canvas.height - 44, 44);
  const x2 = project(end.x_nm, view.minX, view.maxX, 44, canvas.width - 44);
  const y2 = project(end.y_nm, view.minY, view.maxY, canvas.height - 44, 44);
  const lengthPx = Math.hypot(x2 - x1, y2 - y1);
  if (!lengthPx) return;
  const scale = Math.min((canvas.width - 88) / (view.maxX - view.minX), (canvas.height - 88) / (view.maxY - view.minY));
  const width = _isFinitePositive(widthNm)
    ? Math.max(4, Math.min(20, (Number(widthNm) * scale) / 2))
    : Math.max(8, Math.min(18, lengthPx * 0.05));
  const dx = (x2 - x1) / lengthPx;
  const dy = (y2 - y1) / lengthPx;
  const px = -dy;
  const py = dx;

  ctx.fillStyle = isActive ? 'rgba(148, 163, 184, 0.16)' : 'rgba(100, 116, 139, 0.12)';
  ctx.strokeStyle = isActive ? 'rgba(226, 232, 240, 0.62)' : 'rgba(148, 163, 184, 0.34)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x1 + px * width, y1 + py * width);
  ctx.lineTo(x2 + px * width, y2 + py * width);
  ctx.lineTo(x2 - px * width, y2 - py * width);
  ctx.lineTo(x1 - px * width, y1 - py * width);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  if (lengthPx > 90) {
    ctx.strokeStyle = isActive ? 'rgba(248, 250, 252, 0.68)' : 'rgba(203, 213, 225, 0.42)';
    ctx.lineWidth = 2;
    ctx.setLineDash([18, 12]);
    ctx.beginPath();
    ctx.moveTo(x1 + dx * 20, y1 + dy * 20);
    ctx.lineTo(x2 - dx * 20, y2 - dy * 20);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.strokeStyle = isActive ? '#f8fafc' : 'rgba(203, 213, 225, 0.5)';
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(x1 + px * width * 0.8, y1 + py * width * 0.8);
  ctx.lineTo(x1 - px * width * 0.8, y1 - py * width * 0.8);
  ctx.moveTo(x2 + px * width * 0.8, y2 + py * width * 0.8);
  ctx.lineTo(x2 - px * width * 0.8, y2 - py * width * 0.8);
  ctx.stroke();

  ctx.fillStyle = isActive ? '#f8fafc' : 'rgba(203, 213, 225, 0.62)';
  ctx.font = '700 12px Arial';
  ctx.textAlign = 'center';
  ctx.fillText(runwayId, x1 - dx * 16, y1 - dy * 16);
  ctx.fillText(oppositeRunway(runwayId), x2 + dx * 16, y2 + dy * 16);
  ctx.textAlign = 'start';
}

function layoutWorldPoints(layout) {
  if (!layout || typeof layout !== 'object') return [];
  const points = [];
  (Array.isArray(layout.runways) ? layout.runways : []).forEach((runway) => points.push(...(validPointList(runway?.ends, 2, true) || [])));
  (Array.isArray(layout.taxiways) ? layout.taxiways : []).forEach((taxiway) => points.push(...(validPointList(taxiway?.points, 2) || [])));
  (Array.isArray(layout.aprons) ? layout.aprons : []).forEach((apron) => points.push(...(validPointList(apron?.polygon, 3) || [])));
  (Array.isArray(layout.stands) ? layout.stands : []).forEach((stand) => {
    if (isWorldPoint(stand?.position)) points.push(stand.position);
  });
  return points;
}

function findLayoutRunway(layout, runwayId) {
  if (!layout || !Array.isArray(layout.runways)) return null;
  return layout.runways.find((runway) => runway?.id === runwayId && validPointList(runway.ends, 2, true)) || null;
}

function validPointList(points, minimum, exact = false) {
  if (!Array.isArray(points)) return null;
  if (exact ? points.length !== minimum : points.length < minimum) return null;
  return points.every(isWorldPoint) ? points : null;
}

function isWorldPoint(point) {
  return point
    && Number.isFinite(Number(point.x_nm))
    && Number.isFinite(Number(point.y_nm));
}

function _isFinitePositive(value) {
  return Number.isFinite(Number(value)) && Number(value) > 0;
}

function runwayWorldPoints(runwayId) {
  if (!runwayId) return [];
  const heading = runwayHeading(runwayId);
  const angle = ((heading - 90) * Math.PI) / 180;
  const halfLengthNm = 2.25;
  const dx = Math.cos(angle) * halfLengthNm;
  const dy = Math.sin(angle) * halfLengthNm;
  return [
    { x_nm: -dx, y_nm: -dy },
    { x_nm: dx, y_nm: dy }
  ];
}

function runwayHeading(runwayId) {
  const match = String(runwayId || '').match(/\d{2}/);
  if (!match) return 90;
  const runwayNumber = Number(match[0]);
  return runwayNumber === 0 ? 360 : runwayNumber * 10;
}

function oppositeRunway(runwayId) {
  const match = String(runwayId || '').match(/\d{2}/);
  if (!match) return '';
  const n = Number(match[0]);
  if (!n) return '';
  const opposite = ((n + 17) % 36) + 1;
  return String(opposite).padStart(2, '0');
}

function drawConflictLinks(records, byCallsign, color, dash) {
  const view = getViewBounds();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.setLineDash(dash);
  for (const record of records) {
    if (!Array.isArray(record.aircraft) || record.aircraft.length < 2) continue;
    const a = byCallsign[record.aircraft[0]];
    const b = byCallsign[record.aircraft[1]];
    if (!a || !b) continue;
    ctx.beginPath();
    ctx.moveTo(project(a.x_nm, view.minX, view.maxX, 44, canvas.width - 44), project(a.y_nm, view.minY, view.maxY, canvas.height - 44, 44));
    ctx.lineTo(project(b.x_nm, view.minX, view.maxX, 44, canvas.width - 44), project(b.y_nm, view.minY, view.maxY, canvas.height - 44, 44));
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

function renderRadarLegend() {
  clearNode(radarLegend);
  [
    ['Normal aircraft', palette.normal, 'Aircraft without a conflict or emergency flag.'],
    ['Emergency', palette.emergency, 'Aircraft requiring priority handling.'],
    ['Predicted conflict', palette.predicted, 'Aircraft that may become too close soon.'],
    ['Active conflict', palette.conflict, 'Aircraft that are too close now.'],
    ['Landed or exited', palette.landed, 'Aircraft no longer active in the airspace.']
  ].forEach(([label, color, help]) => {
    const item = document.createElement('span');
    item.className = 'legend-item';
    item.title = help;
    const swatch = document.createElement('span');
    swatch.className = 'legend-swatch';
    swatch.style.background = color;
    item.append(swatch, label);
    radarLegend.appendChild(item);
  });
}

function handleRadarMove(event) {
  if (isPanning) return;
  if (!radarTargets.length) return;
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const x = (event.clientX - rect.left) * scaleX;
  const y = (event.clientY - rect.top) * scaleY;
  const target = radarTargets.find((item) => Math.hypot(item.x - x, item.y - y) <= 14);
  hoveredCallsign = target?.ac.callsign || null;
  drawCurrentRadar();
  if (!target) {
    hideRadarTooltip();
    return;
  }
  radarTooltip.hidden = false;
  radarTooltip.style.left = `${event.clientX - rect.left + 14}px`;
  radarTooltip.style.top = `${event.clientY - rect.top + 14}px`;
  radarTooltip.innerHTML = renderAircraftTooltip(target);
}

function handleRadarClick() {
  if (isPanning) return;
  if (!hoveredCallsign) return;
  selectedCallsign = hoveredCallsign;
  drawCurrentRadar();
  renderAircraftPanel(traceEvents[currentTickIndex], selectedCallsign);
}

function hideRadarTooltip() {
  hoveredCallsign = null;
  radarTooltip.hidden = true;
  drawCurrentRadar();
}

function drawCurrentRadar(frameTs = performance.now(), frameDeltaMs = 0) {
  const event = traceEvents[currentTickIndex];
  if (!event) return;
  const aircraft = currentMode === 'live'
    ? buildInterpolatedAircraft(event, frameTs, frameDeltaMs)
    : Object.values(event.state?.aircraft || {});
  drawRadar(event.state || {}, aircraft, aircraftSetFromRecords(event.conflicts || []), aircraftSetFromRecords(event.predicted_conflicts || []), event.conflicts || [], event.predicted_conflicts || []);
}

function buildInterpolatedAircraft(event, frameTs, frameDeltaMs) {
  const fallback = Object.values(event.state?.aircraft || {});
  if (!fallback.length) return fallback;
  const delayed = latestLiveArrivalMs && (frameTs - latestLiveArrivalMs) > LIVE_INTERPOLATION.maxHoldMs;
  return fallback.map((ac) => {
    const pair = liveSnapshotsByCallsign.get(ac.callsign);
    if (!pair?.target) return ac;
    const previous = pair.previous || pair.target;
    const target = pair.target;
    const span = Math.max(1, target.__arrivalMs - previous.__arrivalMs);
    let alpha = (frameTs - LIVE_INTERPOLATION.lagMs - previous.__arrivalMs) / span;
    if (delayed) {
      const slowdown = Math.max(0.08, 1 - (frameDeltaMs / LIVE_INTERPOLATION.maxFrameDeltaMs));
      alpha *= slowdown;
    }
    alpha = Math.max(0, Math.min(1, alpha));
    if (delayed && alpha > 0.995) alpha = 0.995;
    return {
      ...target,
      x_nm: lerpNumber(previous.x_nm, target.x_nm, alpha),
      y_nm: lerpNumber(previous.y_nm, target.y_nm, alpha),
      altitude_ft: lerpNumber(previous.altitude_ft, target.altitude_ft, alpha),
      speed_kt: lerpNumber(previous.speed_kt, target.speed_kt, alpha),
      heading_deg: lerpHeading(previous.heading_deg, target.heading_deg, alpha)
    };
  });
}

function lerpNumber(a, b, alpha) {
  const x = Number(a);
  const y = Number(b);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return Number.isFinite(y) ? y : x;
  return x + (y - x) * alpha;
}

function lerpHeading(a, b, alpha) {
  const from = Number(a);
  const to = Number(b);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return Number.isFinite(to) ? to : from;
  let delta = ((to - from + 540) % 360) - 180;
  return (from + delta * alpha + 360) % 360;
}

function startRadarPan(event) {
  if (!traceEvents.length || event.button !== 0) return;
  isPanning = true;
  lastPanPoint = { x: event.clientX, y: event.clientY };
  canvas.classList.add('is-panning');
  hideRadarTooltip();
}

function panRadar(event) {
  if (!isPanning || !radarView || !lastPanPoint) return;
  const view = getViewBounds();
  const dx = event.clientX - lastPanPoint.x;
  const dy = event.clientY - lastPanPoint.y;
  const worldPerPxX = (view.maxX - view.minX) / (canvas.width - 88);
  const worldPerPxY = (view.maxY - view.minY) / (canvas.height - 88);
  radarView.centerX -= dx * worldPerPxX;
  radarView.centerY += dy * worldPerPxY;
  lastPanPoint = { x: event.clientX, y: event.clientY };
  drawCurrentRadar();
}

function stopRadarPan() {
  if (!isPanning) return;
  isPanning = false;
  lastPanPoint = null;
  canvas.classList.remove('is-panning');
}

function handleRadarWheel(event) {
  if (!traceEvents.length) return;
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.18 : 1 / 1.18;
  zoomRadar(factor, event);
}

function zoomRadar(factor, pointerEvent = null) {
  if (!radarView) return;
  const oldView = getViewBounds();
  const pointBefore = pointerEvent ? screenToWorld(pointerEvent, oldView) : null;
  radarView.zoom = Math.max(0.65, Math.min(16, radarView.zoom * factor));
  if (pointBefore && pointerEvent) {
    const newView = getViewBounds();
    const pointAfter = screenToWorld(pointerEvent, newView);
    radarView.centerX += pointBefore.x - pointAfter.x;
    radarView.centerY += pointBefore.y - pointAfter.y;
  }
  drawCurrentRadar();
}

function resetRadarView(redraw = true) {
  if (!radarBounds) return;
  radarView = {
    centerX: (radarBounds.minX + radarBounds.maxX) / 2,
    centerY: (radarBounds.minY + radarBounds.maxY) / 2,
    zoom: 1
  };
  if (redraw) drawCurrentRadar();
}

function getViewBounds() {
  if (!radarBounds) return { minX: -10, maxX: 10, minY: -10, maxY: 10 };
  if (!radarView) resetRadarView(false);
  const width = (radarBounds.maxX - radarBounds.minX) / radarView.zoom;
  const height = (radarBounds.maxY - radarBounds.minY) / radarView.zoom;
  return {
    minX: radarView.centerX - width / 2,
    maxX: radarView.centerX + width / 2,
    minY: radarView.centerY - height / 2,
    maxY: radarView.centerY + height / 2
  };
}

function screenToWorld(event, view) {
  const rect = canvas.getBoundingClientRect();
  const x = (event.clientX - rect.left) * (canvas.width / rect.width);
  const y = (event.clientY - rect.top) * (canvas.height / rect.height);
  return {
    x: unproject(x, view.minX, view.maxX, 44, canvas.width - 44),
    y: unproject(y, view.minY, view.maxY, canvas.height - 44, 44)
  };
}

function renderAircraftTooltip(target) {
  const ac = target.ac;
  const status = target.conflict ? 'Active conflict' : target.predicted ? 'Predicted conflict' : ac.emergency ? 'Emergency' : humanize(ac.status || 'airborne');
  return `<b>${escapeHtml(ac.callsign)}</b><span>${escapeHtml(humanize(ac.role || 'aircraft'))} - ${escapeHtml(status)}</span><span>${Math.round(ac.altitude_ft)} ft - ${Math.round(ac.speed_kt)} kt - heading ${Math.round(ac.heading_deg)} deg</span>`;
}

function renderAircraftPanel(event, callsign) {
  clearNode(aircraftPanel);
  appendSectionTitle(aircraftPanel, 'Inspect', 'Aircraft');
  if (!event || !callsign) {
    appendText(aircraftPanel, 'p', 'Hover over an aircraft for a quick label, or click one to keep its details here.').className = 'muted';
    return;
  }
  const ac = event.state?.aircraft?.[callsign];
  if (!ac) {
    appendText(aircraftPanel, 'p', 'The selected aircraft is not present on this tick.').className = 'muted';
    return;
  }
  const conflictSet = aircraftSetFromRecords(event.conflicts || []);
  const predictedSet = aircraftSetFromRecords(event.predicted_conflicts || []);
  appendText(aircraftPanel, 'h3', ac.callsign);
  appendDefinitionList(aircraftPanel, {
    Role: humanize(ac.role || 'aircraft'),
    Status: humanize(ac.status || 'unknown'),
    Altitude: `${Math.round(ac.altitude_ft)} ft`,
    Speed: `${Math.round(ac.speed_kt)} kt`,
    Heading: `${Math.round(ac.heading_deg)} degrees`,
    Clearance: ac.clearance ? humanize(ac.clearance) : 'None',
    Emergency: ac.emergency ? 'Yes' : 'No',
    Conflict: conflictSet.has(callsign) ? 'Active now' : predictedSet.has(callsign) ? 'Predicted soon' : 'None detected'
  });
}

function renderTickDetails(e) {
  clearNode(tickPanel);
  appendSectionTitle(tickPanel, 'Now', 'Current Tick');
  renderCurrentSituation(tickPanel, e);
  renderControllerAction(tickPanel, e);
  renderOutcome(tickPanel, e);
  appendTechnicalDetails(tickPanel, 'Technical tick JSON', {
    decision_points: e.decision_points,
    actions: e.actions,
    invalid_actions: e.invalid_actions,
    conflicts: e.conflicts,
    predicted_conflicts: e.predicted_conflicts,
    triggered_events: e.triggered_events,
    tick_explanation: e.tick_explanation
  });
}

function renderCurrentSituation(parent, event) {
  const state = event.state || {};
  const aircraft = Object.values(state.aircraft || {});
  const conflicts = event.conflicts || [];
  const predicted = event.predicted_conflicts || [];
  const decisionPoints = event.decision_points || [];
  const panel = renderSubPanel('Current Situation');
  appendText(panel, 'p', `At ${event.time}s, ${countPhrase(aircraft.length, 'aircraft')} ${aircraft.length === 1 ? 'is' : 'are'} in the replay. ${describeConflicts(conflicts, predicted)}`);
  appendReadableList(panel, 'Why the controller was called', decisionPoints.map(describeDecisionPoint), 'No safety or scheduling issue triggered a controller call on this tick.');
  parent.appendChild(panel);
}

function renderControllerAction(parent, event) {
  const actions = event.tick_explanation?.action_chosen || event.actions || [];
  const invalid = event.invalid_actions || [];
  const panel = renderSubPanel('Controller Action');
  appendReadableList(panel, 'Issued command', actions.map(describeAction), 'The controller issued no command.');
  if (invalid.length) appendReadableList(panel, 'Rejected command', invalid.map((item) => describeInvalidAction(item)), 'No rejected commands.');
  parent.appendChild(panel);
}

function renderOutcome(parent, event) {
  const explanation = event.tick_explanation || {};
  const outcomeKind = explanation.outcome?.kind || 'unknown';
  const totalDelta = Number(explanation.score_after || 0) - Number(explanation.score_before || 0);
  const panel = renderSubPanel('Outcome');
  const badge = document.createElement('span');
  badge.className = `outcome-badge outcome-${outcomeKind}`;
  badge.textContent = humanize(outcomeKind);
  panel.appendChild(badge);
  appendText(panel, 'p', describeOutcome(outcomeKind, totalDelta, explanation));
  panel.appendChild(renderComponentTable(explanation, {}));
  parent.appendChild(panel);
}

function renderTimeline() {
  clearNode(timelineList);
  if (!traceEvents.length) {
    appendText(timelineList, 'p', 'Load a trace file to see replay events.').className = 'muted';
    return;
  }

  const componentTotals = {};
  let rendered = 0;
  traceEvents.forEach((event, idx) => {
    const explanation = event.tick_explanation || {};
    const outcome = explanation.outcome?.kind || 'unknown';
    const immediateDelta = Number(explanation.outcome?.immediate_delta || 0);
    const isSafetyTriggered = isSafetyTriggeredCall(event, explanation);
    if (filterHurt.checked && outcome !== 'hurt') return;
    if (filterSafety.checked && !isSafetyTriggered) return;
    if (filterLargeDelta.checked && Math.abs(immediateDelta) < 0.05) return;

    timelineList.appendChild(renderTimelineRow(idx, event, explanation, componentTotals));
    const deltas = explanation.score_delta_by_component || {};
    for (const [component, delta] of Object.entries(deltas)) {
      componentTotals[component] = Number(componentTotals[component] || 0) + Number(delta || 0);
    }
    rendered += 1;
  });

  if (!rendered) appendText(timelineList, 'p', 'No replay events match the selected filters.').className = 'muted';
}

function renderTimelineRow(idx, event, explanation, componentTotals) {
  const outcomeKind = explanation.outcome?.kind || 'unknown';
  const action = summarizeAction(explanation.action_chosen || event.actions || []);
  const immediateDelta = Number(explanation.outcome?.immediate_delta || 0);
  const totalDelta = Number(explanation.score_after || 0) - Number(explanation.score_before || 0);
  const details = document.createElement('details');
  details.className = 'timeline-item';

  const summary = document.createElement('summary');
  summary.className = 'timeline-summary';
  const left = document.createElement('span');
  left.className = 'timeline-summary-left';
  appendText(left, 'span', `#${idx + 1} - ${event.time}s`);
  const badge = document.createElement('span');
  badge.className = `outcome-badge outcome-${outcomeKind}`;
  badge.textContent = humanize(outcomeKind);
  left.appendChild(badge);
  appendText(left, 'span', primaryReason(event));
  appendText(left, 'span', action);
  const right = document.createElement('span');
  right.className = 'timeline-summary-right';
  right.textContent = scoreImpactLabel(totalDelta, immediateDelta);
  summary.append(left, right);

  const body = document.createElement('div');
  body.className = 'timeline-body';
  appendText(body, 'p', describeOutcome(outcomeKind, totalDelta, explanation));
  body.appendChild(renderComponentTable(explanation, componentTotals));
  const actions = document.createElement('div');
  actions.className = 'timeline-actions';
  const jump = document.createElement('button');
  jump.className = 'jump-link';
  jump.type = 'button';
  jump.textContent = 'Jump to this moment';
  jump.addEventListener('click', () => {
    renderAtTick(idx);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  actions.appendChild(jump);
  body.appendChild(actions);
  details.append(summary, body);
  return details;
}

function renderComponentTable(explanation, componentTotals) {
  const scoreBefore = Number(explanation.score_before || 0);
  const scoreAfter = Number(explanation.score_after || 0);
  const totalDelta = scoreAfter - scoreBefore;
  const deltaByComponent = explanation.score_delta_by_component || {};
  const nonZeroComponents = Object.entries(deltaByComponent).filter(([, delta]) => Number(delta) !== 0);
  if (!nonZeroComponents.length) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = 'No component-level score changes on this tick.';
    return p;
  }

  const table = document.createElement('table');
  table.className = 'component-table';
  const header = document.createElement('thead');
  const headerRow = document.createElement('tr');
  ['What changed', 'Before', 'After', 'Delta', 'Share'].forEach((label) => appendText(headerRow, 'th', label));
  header.appendChild(headerRow);
  const body = document.createElement('tbody');
  nonZeroComponents.forEach(([component, delta]) => {
    const deltaNum = Number(delta || 0);
    const before = Number(componentTotals[component] || 0);
    const pct = totalDelta === 0 ? 0 : (deltaNum / totalDelta) * 100;
    const row = document.createElement('tr');
    [humanizeLabel(component), formatNum(before), formatNum(before + deltaNum), formatSigned(deltaNum), `${formatNum(pct)}%`].forEach((value) => appendText(row, 'td', value));
    body.appendChild(row);
  });
  table.append(header, body);
  return table;
}

function isSafetyTriggeredCall(event, explanation) {
  const reasonType = explanation.call_reason?.type;
  if (reasonType === 'event') return true;
  const points = event.decision_points || [];
  return points.some((dp) => {
    const type = String(dp.type || '').toLowerCase();
    const severity = String(dp.severity || '').toLowerCase();
    return severity === 'critical' || type.includes('conflict') || type.includes('runway') || type.includes('emergency');
  });
}

function summarizeAction(actions) {
  if (!Array.isArray(actions) || !actions.length) return 'No action';
  return actions.map(describeAction).join('; ');
}

function describeAction(action) {
  if (!action) return 'No action';
  const type = humanizeLabel(action.type || 'unknown');
  const target = action.aircraft ? ` for ${action.aircraft}` : '';
  if (action.type === 'assign_heading') return `${type}${target} to ${action.heading} degrees`;
  if (action.type === 'assign_altitude') return `${type}${target} to ${action.altitude_ft} ft`;
  if (action.type === 'assign_speed') return `${type}${target} to ${action.speed_kt} kt`;
  if (action.type === 'no_op') return action.aircraft ? `No action for ${action.aircraft}` : 'No action';
  return `${type}${target}`;
}

function describeInvalidAction(item) {
  const action = item.action ? describeAction(item.action) : 'Malformed command';
  return `${action}: ${humanize(item.reason || item.error || 'rejected')}`;
}

function describeDecisionPoint(dp) {
  if (!dp) return 'Unknown reason';
  const base = humanizeLabel(dp.type || 'decision point');
  const aircraft = Array.isArray(dp.aircraft) && dp.aircraft.length ? ` involving ${dp.aircraft.join(', ')}` : '';
  const severity = dp.severity ? ` (${humanize(dp.severity)})` : '';
  return `${base}${aircraft}${severity}`;
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

function stepTick(delta) {
  if (currentMode === 'live') {
    liveFollowTail = false;
    if (delta > 0) {
      renderAtTick(traceEvents.length - 1);
      syncPlaybackButton();
      return;
    }
  }
  const next = Math.max(0, Math.min(traceEvents.length - 1, Number(slider.value) + delta));
  renderAtTick(next);
  if (next === traceEvents.length - 1) stopPlayback();
  else syncPlaybackButton();
}

function togglePlayback() {
  if (currentMode === 'live') {
    liveFollowTail = !liveFollowTail;
    if (liveFollowTail && traceEvents.length) renderAtTick(traceEvents.length - 1);
    syncPlaybackButton();
    loadStatus.textContent = liveFollowTail
      ? 'Live playback resumed at the newest tick.'
      : 'Live playback paused. Incoming ticks are buffered.';
    return;
  }
  if (playTimer) stopPlayback();
  else startPlayback();
}

function resetTimelineOrView() {
  resetRadarView(false);
  if (currentMode === 'live' && traceEvents.length) {
    liveFollowTail = false;
    renderAtTick(0);
    syncPlaybackButton();
    loadStatus.textContent = 'Live replay reset to the first buffered tick.';
    return;
  }
  drawCurrentRadar();
}

function startPlayback() {
  syncPlaybackButton(true);
  playTimer = setInterval(() => stepTick(1), Number(playSpeed.value));
}

function stopPlayback() {
  if (playTimer) clearInterval(playTimer);
  playTimer = null;
  syncPlaybackButton(false);
}

function syncPlaybackButton(forcePlaying = null) {
  const isPlaying = forcePlaying ?? (currentMode === 'live' ? liveFollowTail : Boolean(playTimer));
  playPause.textContent = isPlaying ? 'Pause' : 'Play';
  playPause.setAttribute('aria-label', isPlaying ? 'Pause replay' : 'Play replay');
}

function appendSectionTitle(parent, eyebrow, title) {
  const heading = document.createElement('div');
  heading.className = 'section-heading';
  const text = document.createElement('div');
  appendText(text, 'p', eyebrow).className = 'eyebrow';
  appendText(text, 'h2', title);
  heading.appendChild(text);
  parent.appendChild(heading);
}

function renderSubPanel(title) {
  const panel = document.createElement('section');
  panel.className = 'sub-panel';
  appendText(panel, 'h3', title);
  return panel;
}

function appendReadableList(parent, title, items, emptyText) {
  appendText(parent, 'h4', title);
  if (!items.length) {
    appendText(parent, 'p', emptyText).className = 'muted';
    return;
  }
  const list = document.createElement('ul');
  items.forEach((value) => appendText(list, 'li', value));
  parent.appendChild(list);
}

function appendReadableRecordList(parent, title, records, nonZeroOnly = false) {
  const entries = Object.entries(records).filter(([, value]) => !nonZeroOnly || Number(value) !== 0);
  if (!entries.length) return;
  appendText(parent, 'h3', title);
  const list = document.createElement('ul');
  entries.forEach(([key, value]) => {
    const item = document.createElement('li');
    const strong = document.createElement('b');
    strong.textContent = humanizeLabel(key);
    item.append(strong, `: ${formatSigned(Number(value))}`);
    list.appendChild(item);
  });
  parent.appendChild(list);
}

function appendDefinitionList(parent, records) {
  const list = document.createElement('dl');
  for (const [key, value] of Object.entries(records)) {
    appendText(list, 'dt', key);
    appendText(list, 'dd', value);
  }
  parent.appendChild(list);
}

function appendTechnicalDetails(parent, label, value) {
  const details = document.createElement('details');
  details.className = 'technical-details';
  const summary = document.createElement('summary');
  summary.textContent = label;
  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(value || {}, null, 2);
  details.append(summary, pre);
  parent.appendChild(details);
}

function appendText(parent, tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  parent.appendChild(node);
  return node;
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function project(v, min, max, outMin, outMax) {
  if (max - min === 0) return (outMin + outMax) / 2;
  return outMin + ((v - min) / (max - min)) * (outMax - outMin);
}

function unproject(v, min, max, outMin, outMax) {
  if (outMax - outMin === 0) return (min + max) / 2;
  return min + ((v - outMin) / (outMax - outMin)) * (max - min);
}

function formatNum(v) {
  return typeof v === 'number' && Number.isFinite(v) ? Number(v.toFixed(3)).toString() : String(v);
}

function formatSigned(v) {
  if (!Number.isFinite(v)) return String(v);
  if (v === 0) return '0';
  return `${v > 0 ? '+' : ''}${formatNum(v)}`;
}

function humanizeLabel(value) {
  return labelMap[value] || humanize(value);
}

function humanize(value) {
  return String(value || '')
    .replace(/\.[^.]+$/, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function prettifyScenarioName(value) {
  return humanize(String(value || 'Replay').replace(/\.(jsonl|json|txt)$/i, ''));
}

function countPhrase(count, singular) {
  return `${count} ${singular}${count === 1 ? '' : 's'}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[char]);
}
