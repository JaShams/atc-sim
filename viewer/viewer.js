const traceFileInput = document.getElementById('traceFile');
const scoreFileInput = document.getElementById('scoreFile');
const slider = document.getElementById('tickSlider');
const tickLabel = document.getElementById('tickLabel');
const loadStatus = document.getElementById('loadStatus');
const scorePanel = document.getElementById('scorePanel');
const tickPanel = document.getElementById('tickPanel');
const timelineList = document.getElementById('timelineList');
const filterHurt = document.getElementById('filterHurt');
const filterSafety = document.getElementById('filterSafety');
const filterLargeDelta = document.getElementById('filterLargeDelta');
const canvas = document.getElementById('radar');
const ctx = canvas.getContext('2d');
const playPause = document.getElementById('playPause');
const stepBack = document.getElementById('stepBack');
const stepForward = document.getElementById('stepForward');
const playSpeed = document.getElementById('playSpeed');

let traceEvents = [];
let score = null;
let radarBounds = null;
let playTimer = null;

const palette = {
  normal: '#7ec8ff',
  emergency: '#f39c12',
  predicted: '#e67ee2',
  conflict: '#ff4d4d',
  landed: '#7f8c8d',
  runway: '#95a5a6',
  text: '#e6edf3'
};

traceFileInput.addEventListener('change', loadFiles);
scoreFileInput.addEventListener('change', loadFiles);
slider.addEventListener('input', () => renderAtTick(Number(slider.value)));
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
filterHurt.addEventListener('change', renderTimeline);
filterSafety.addEventListener('change', renderTimeline);
filterLargeDelta.addEventListener('change', renderTimeline);

async function loadFiles() {
  if (!traceFileInput.files[0]) return;
  try {
    stopPlayback();
    traceEvents = await parseJsonl(traceFileInput.files[0]);
    score = scoreFileInput.files[0] ? JSON.parse(await scoreFileInput.files[0].text()) : null;
    radarBounds = calculateBounds(traceEvents);
    const hasTrace = traceEvents.length > 0;
    slider.disabled = !hasTrace;
    playPause.disabled = !hasTrace;
    stepBack.disabled = !hasTrace;
    stepForward.disabled = !hasTrace;
    slider.min = 0;
    slider.max = Math.max(0, traceEvents.length - 1);
    slider.value = 0;
    loadStatus.textContent = score
      ? `Loaded ${traceEvents.length} ticks and score.`
      : `Loaded ${traceEvents.length} ticks. Score file optional.`;
    renderScore();
    renderAtTick(0);
    renderTimeline();
  } catch (err) {
    loadStatus.textContent = `Failed to parse files: ${err.message}`;
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
  if (!aircraft.length) return { minX: -10, maxX: 10, minY: -10, maxY: 10 };
  const xs = aircraft.map((a) => a.x_nm);
  const ys = aircraft.map((a) => a.y_nm);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const pad = Math.max(2, (Math.max(maxX - minX, maxY - minY) || 10) * 0.12);
  return { minX: minX - pad, maxX: maxX + pad, minY: minY - pad, maxY: maxY + pad };
}

function renderScore() {
  clearNode(scorePanel);
  appendHeading(scorePanel, 'Score', 2);
  if (!score) {
    appendText(scorePanel, 'p', 'No score file loaded.');
    return;
  }
  appendText(scorePanel, 'p', `Total: ${formatNum(score.score)}`);
  appendRecordList(scorePanel, 'Score Breakdown', score.score_breakdown || {});
  appendRecordList(scorePanel, 'Key Metrics', score.metrics || {});
  if (score.run_manifest) {
    appendRecordList(scorePanel, 'Run Manifest', flattenManifest(score.run_manifest));
  }
}

function renderAtTick(index) {
  const e = traceEvents[index];
  if (!e) return;
  slider.value = index;
  tickLabel.textContent = `${index + 1} / ${traceEvents.length} (t=${e.time}s)`;

  const state = e.state || {};
  const aircraftMap = state.aircraft || {};
  const aircraft = Object.values(aircraftMap);
  const conflictSet = aircraftSetFromRecords(e.conflicts || []);
  const predictedSet = aircraftSetFromRecords(e.predicted_conflicts || []);

  drawRadar(state, aircraft, conflictSet, predictedSet, e.conflicts || [], e.predicted_conflicts || []);
  renderTickDetails(e);
}

function aircraftSetFromRecords(records) {
  return new Set(records.flatMap((record) => (Array.isArray(record.aircraft) ? record.aircraft : [record.a, record.b].filter(Boolean))));
}

function drawRadar(state, aircraft, conflictSet, predictedSet, conflicts, predictedConflicts) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#081018';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const runway = state.airport?.active_runway || state.airport?.runway_id || 'RWY';
  ctx.fillStyle = palette.text;
  ctx.font = '12px Arial';
  ctx.fillText(`Runway: ${runway}`, 10, 16);
  ctx.strokeStyle = palette.runway;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(canvas.width * 0.2, canvas.height * 0.5);
  ctx.lineTo(canvas.width * 0.8, canvas.height * 0.5);
  ctx.stroke();

  const byCallsign = Object.fromEntries(aircraft.map((ac) => [ac.callsign, ac]));
  drawConflictLinks(predictedConflicts, byCallsign, palette.predicted, [4, 4]);
  drawConflictLinks(conflicts, byCallsign, palette.conflict, []);

  for (const ac of aircraft) {
    const x = project(ac.x_nm, radarBounds.minX, radarBounds.maxX, 40, canvas.width - 40);
    const y = project(ac.y_nm, radarBounds.minY, radarBounds.maxY, canvas.height - 40, 40);
    const isLanded = ac.status === 'landed' || ac.status === 'exited_airspace';
    const color = isLanded
      ? palette.landed
      : conflictSet.has(ac.callsign)
        ? palette.conflict
        : predictedSet.has(ac.callsign)
          ? palette.predicted
          : ac.emergency
            ? palette.emergency
            : palette.normal;

    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, 6, 0, Math.PI * 2);
    ctx.fill();

    const headingRad = ((ac.heading_deg - 90) * Math.PI) / 180;
    ctx.strokeStyle = color;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + 16 * Math.cos(headingRad), y + 16 * Math.sin(headingRad));
    ctx.stroke();

    ctx.fillStyle = palette.text;
    ctx.fillText(`${ac.callsign} ${Math.round(ac.altitude_ft)}ft ${Math.round(ac.speed_kt)}kt`, x + 8, y - 8);
  }

  renderLegend();
}

function drawConflictLinks(records, byCallsign, color, dash) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.setLineDash(dash);
  for (const record of records) {
    if (!Array.isArray(record.aircraft) || record.aircraft.length < 2) continue;
    const a = byCallsign[record.aircraft[0]];
    const b = byCallsign[record.aircraft[1]];
    if (!a || !b) continue;
    ctx.beginPath();
    ctx.moveTo(project(a.x_nm, radarBounds.minX, radarBounds.maxX, 40, canvas.width - 40), project(a.y_nm, radarBounds.minY, radarBounds.maxY, canvas.height - 40, 40));
    ctx.lineTo(project(b.x_nm, radarBounds.minX, radarBounds.maxX, 40, canvas.width - 40), project(b.y_nm, radarBounds.minY, radarBounds.maxY, canvas.height - 40, 40));
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

function renderLegend() {
  const legend = [
    ['normal', palette.normal],
    ['emergency', palette.emergency],
    ['predicted conflict', palette.predicted],
    ['active conflict', palette.conflict],
    ['landed/exited', palette.landed]
  ];
  let x = 10;
  for (const [label, color] of legend) {
    ctx.fillStyle = color;
    ctx.fillRect(x, canvas.height - 20, 10, 10);
    ctx.fillStyle = '#dbe6ef';
    ctx.fillText(label, x + 14, canvas.height - 11);
    x += 130;
  }
}

function renderTickDetails(e) {
  clearNode(tickPanel);
  appendHeading(tickPanel, 'Tick Details', 2);
  appendJsonBlock(tickPanel, 'Decision Points', e.decision_points);
  appendJsonBlock(tickPanel, 'Actions', e.actions);
  appendJsonBlock(tickPanel, 'Invalid Actions', e.invalid_actions);
  appendJsonBlock(tickPanel, 'Active Conflicts', e.conflicts);
  appendJsonBlock(tickPanel, 'Predicted Conflicts', e.predicted_conflicts);
  appendJsonBlock(tickPanel, 'Triggered Events', e.triggered_events);
}

function renderTimeline() {
  clearNode(timelineList);
  if (!traceEvents.length) {
    appendText(timelineList, 'p', 'Load a trace file to see timeline entries.');
    timelineList.lastChild.className = 'muted';
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

  if (!rendered) {
    appendText(timelineList, 'p', 'No ticks match the selected filters.');
    timelineList.lastChild.className = 'muted';
  }
}

function renderTimelineRow(idx, event, explanation, componentTotals) {
  const outcomeKind = explanation.outcome?.kind || 'unknown';
  const callReason = explanation.call_reason?.type || 'none';
  const action = summarizeAction(explanation.action_chosen || event.actions || []);
  const immediateDelta = Number(explanation.outcome?.immediate_delta || 0);
  const totalDelta = Number(explanation.score_after || 0) - Number(explanation.score_before || 0);
  const details = document.createElement('details');
  details.className = 'timeline-item';

  const summary = document.createElement('summary');
  summary.className = 'timeline-summary';
  const left = document.createElement('span');
  left.className = 'timeline-summary-left';
  appendText(left, 'span', `#${idx + 1}`);
  const badge = document.createElement('span');
  badge.className = `outcome-badge outcome-${outcomeKind}`;
  badge.textContent = outcomeKind;
  left.appendChild(badge);
  appendText(left, 'span', `Reason: ${callReason}`);
  appendText(left, 'span', `Action: ${action}`);
  const right = document.createElement('span');
  right.className = 'timeline-summary-right';
  right.textContent = `Delta ${formatNum(totalDelta)} (norm ${formatNum(immediateDelta)})`;
  summary.append(left, right);

  const body = document.createElement('div');
  appendText(body, 'p', `Outcome label: ${outcomeKind}`);
  body.appendChild(renderComponentTable(explanation, componentTotals));
  const actions = document.createElement('div');
  actions.className = 'timeline-actions';
  const jump = document.createElement('button');
  jump.className = 'jump-link';
  jump.type = 'button';
  jump.textContent = 'Jump to replay/state inspector';
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
  ['Component', 'Before', 'After', 'Delta', 'Contribution'].forEach((label) => appendText(headerRow, 'th', label));
  header.appendChild(headerRow);
  const body = document.createElement('tbody');
  nonZeroComponents.forEach(([component, delta]) => {
    const deltaNum = Number(delta || 0);
    const before = Number(componentTotals[component] || 0);
    const pct = totalDelta === 0 ? 0 : (deltaNum / totalDelta) * 100;
    const row = document.createElement('tr');
    [component, formatNum(before), formatNum(before + deltaNum), formatNum(deltaNum), `${formatNum(pct)}%`].forEach((value) => appendText(row, 'td', value));
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
  if (!Array.isArray(actions) || !actions.length) return 'no_op';
  const first = actions[0] || {};
  const type = first.type || 'unknown';
  const target = first.aircraft ? `(${first.aircraft})` : '';
  return `${type}${target}${actions.length > 1 ? ` +${actions.length - 1}` : ''}`;
}

function stepTick(delta) {
  const next = Math.max(0, Math.min(traceEvents.length - 1, Number(slider.value) + delta));
  renderAtTick(next);
  if (next === traceEvents.length - 1) stopPlayback();
}

function togglePlayback() {
  if (playTimer) stopPlayback();
  else startPlayback();
}

function startPlayback() {
  playPause.textContent = 'Pause';
  playTimer = setInterval(() => stepTick(1), Number(playSpeed.value));
}

function stopPlayback() {
  if (playTimer) clearInterval(playTimer);
  playTimer = null;
  playPause.textContent = 'Play';
}

function appendRecordList(parent, title, records) {
  appendHeading(parent, title, 3);
  const list = document.createElement('ul');
  for (const [key, value] of Object.entries(records)) {
    const item = document.createElement('li');
    const strong = document.createElement('b');
    strong.textContent = key;
    item.append(strong, `: ${formatNum(value)}`);
    list.appendChild(item);
  }
  parent.appendChild(list);
}

function appendJsonBlock(parent, label, value) {
  appendText(parent, 'p', `${label}:`);
  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(value || [], null, 2);
  parent.appendChild(pre);
}

function appendHeading(parent, text, level) {
  appendText(parent, `h${level}`, text);
}

function appendText(parent, tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  parent.appendChild(node);
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function flattenManifest(manifest) {
  const out = {};
  for (const [key, value] of Object.entries(manifest)) {
    out[key] = typeof value === 'object' && value !== null ? JSON.stringify(value) : value;
  }
  return out;
}

function project(v, min, max, outMin, outMax) {
  if (max - min === 0) return (outMin + outMax) / 2;
  return outMin + ((v - min) / (max - min)) * (outMax - outMin);
}

function formatNum(v) {
  return typeof v === 'number' ? Number(v.toFixed(3)) : String(v);
}
