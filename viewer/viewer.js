const traceFileInput = document.getElementById('traceFile');
const scoreFileInput = document.getElementById('scoreFile');
const slider = document.getElementById('tickSlider');
const tickLabel = document.getElementById('tickLabel');
const loadStatus = document.getElementById('loadStatus');
const scorePanel = document.getElementById('scorePanel');
const tickPanel = document.getElementById('tickPanel');
const canvas = document.getElementById('radar');
const ctx = canvas.getContext('2d');

let traceEvents = [];
let score = null;

const palette = {
  normal: '#7ec8ff',
  emergency: '#f39c12',
  predicted: '#e67ee2',
  conflict: '#ff4d4d',
  landed: '#7f8c8d'
};

traceFileInput.addEventListener('change', loadFiles);
scoreFileInput.addEventListener('change', loadFiles);
slider.addEventListener('input', () => renderAtTick(Number(slider.value)));

async function loadFiles() {
  if (!traceFileInput.files[0] || !scoreFileInput.files[0]) return;
  try {
    traceEvents = await parseJsonl(traceFileInput.files[0]);
    score = JSON.parse(await scoreFileInput.files[0].text());
    slider.disabled = traceEvents.length === 0;
    slider.min = 0;
    slider.max = Math.max(0, traceEvents.length - 1);
    slider.value = 0;
    loadStatus.textContent = `Loaded ${traceEvents.length} ticks.`;
    renderScore();
    renderAtTick(0);
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

function renderScore() {
  if (!score) return;
  const breakdown = Object.entries(score.score_breakdown || {})
    .map(([k, v]) => `<li><b>${k}</b>: ${formatNum(v)}</li>`)
    .join('');
  const metrics = Object.entries(score.metrics || {})
    .map(([k, v]) => `<li><b>${k}</b>: ${formatNum(v)}</li>`)
    .join('');

  scorePanel.innerHTML = `
    <h2>Score</h2>
    <p><b>Total:</b> ${formatNum(score.score)}</p>
    <h3>Score Breakdown</h3>
    <ul>${breakdown}</ul>
    <h3>Key Metrics</h3>
    <ul>${metrics}</ul>
  `;
}

function renderAtTick(index) {
  const e = traceEvents[index];
  if (!e) return;
  tickLabel.textContent = `${index + 1} / ${traceEvents.length} (t=${e.time}s)`;

  const state = e.state || {};
  const aircraftMap = state.aircraft || {};
  const aircraft = Object.values(aircraftMap);

  const conflictSet = new Set((e.conflicts || []).flatMap((c) => [c.a, c.b]));
  const predictedSet = new Set((e.predicted_conflicts || []).flatMap((c) => [c.a, c.b]));

  drawRadar(state, aircraft, conflictSet, predictedSet);
  renderTickDetails(e);
}

function drawRadar(state, aircraft, conflictSet, predictedSet) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#081018';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (!aircraft.length) return;
  const xs = aircraft.map((a) => a.x_nm);
  const ys = aircraft.map((a) => a.y_nm);
  const minX = Math.min(...xs) - 1;
  const maxX = Math.max(...xs) + 1;
  const minY = Math.min(...ys) - 1;
  const maxY = Math.max(...ys) + 1;

  const runway = state.airport?.active_runway || state.airport?.runway_id || 'RWY';
  ctx.fillStyle = '#ccd6dd';
  ctx.fillText(`Runway: ${runway}`, 10, 16);
  ctx.strokeStyle = '#95a5a6';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(canvas.width * 0.2, canvas.height * 0.5);
  ctx.lineTo(canvas.width * 0.8, canvas.height * 0.5);
  ctx.stroke();

  for (const ac of aircraft) {
    const x = project(ac.x_nm, minX, maxX, 40, canvas.width - 40);
    const y = project(ac.y_nm, minY, maxY, canvas.height - 40, 40);

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
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + 16 * Math.cos(headingRad), y + 16 * Math.sin(headingRad));
    ctx.stroke();

    ctx.fillStyle = '#e6edf3';
    ctx.font = '12px Arial';
    ctx.fillText(`${ac.callsign} ${Math.round(ac.altitude_ft)}ft ${Math.round(ac.speed_kt)}kt`, x + 8, y - 8);
  }

  renderLegend();
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
  tickPanel.innerHTML = `
    <h2>Tick Details</h2>
    <p><b>Decision Points:</b> ${fmtJson(e.decision_points)}</p>
    <p><b>Actions:</b> ${fmtJson(e.actions)}</p>
    <p><b>Invalid Actions:</b> ${fmtJson(e.invalid_actions)}</p>
    <p><b>Active Conflicts:</b> ${fmtJson(e.conflicts)}</p>
    <p><b>Predicted Conflicts:</b> ${fmtJson(e.predicted_conflicts)}</p>
    <p><b>Triggered Events:</b> ${fmtJson(e.triggered_events)}</p>
  `;
}

function fmtJson(v) {
  return `<pre>${escapeHtml(JSON.stringify(v || [], null, 2))}</pre>`;
}

function project(v, min, max, outMin, outMax) {
  if (max - min === 0) return (outMin + outMax) / 2;
  return outMin + ((v - min) / (max - min)) * (outMax - outMin);
}

function formatNum(v) {
  return typeof v === 'number' ? Number(v.toFixed(3)) : String(v);
}

function escapeHtml(str) {
  return str
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}
