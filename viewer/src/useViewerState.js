import { useState, useEffect, useRef, useMemo, useCallback } from 'react';

const DEFAULT_LIVE_ENDPOINT = 'ws://localhost:8080/live';

const LAST_TRACE_KEY = 'atc_last_trace_jsonl';
const LAST_SCORE_KEY = 'atc_last_score_json';
const LEVEL_RESULTS_KEY = 'atc_level_results';
const MAX_PERSISTED_TRACE_CHARS = 4_000_000;

function safeStorageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeStorageSet(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function readStoredLevelResults() {
  try {
    return JSON.parse(safeStorageGet(LEVEL_RESULTS_KEY) || '{}') || {};
  } catch {
    return {};
  }
}

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

// Helper utility functions
export function humanize(value) {
  return String(value || '')
    .replace(/\.[^.]+$/, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function humanizeLabel(value) {
  return labelMap[value] || humanize(value);
}

export function formatNum(v) {
  return typeof v === 'number' && Number.isFinite(v) ? Number(v.toFixed(3)).toString() : String(v);
}

export function formatSigned(v) {
  if (!Number.isFinite(v)) return String(v);
  if (v === 0) return '0';
  return `${v > 0 ? '+' : ''}${formatNum(v)}`;
}

export function prettifyScenarioName(value) {
  return humanize(String(value || 'Replay').replace(/\.(jsonl|json|txt)$/i, ''));
}

export function countPhrase(count, singular) {
  return `${count} ${singular}${count === 1 ? '' : 's'}`;
}

export function isWorldPoint(point) {
  return point && Number.isFinite(Number(point.x_nm)) && Number.isFinite(Number(point.y_nm));
}

export function validPointList(points, minimum, exact = false) {
  if (!Array.isArray(points)) return null;
  if (exact ? points.length !== minimum : points.length < minimum) return null;
  return points.every(isWorldPoint) ? points : null;
}

export function layoutWorldPoints(layout) {
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

export function findLayoutRunway(layout, runwayId) {
  if (!layout || !Array.isArray(layout.runways)) return null;
  return layout.runways.find((runway) => runway?.id === runwayId && validPointList(runway.ends, 2, true)) || null;
}

export function runwayMidpoint(points) {
  const valid = validPointList(points, 2, true);
  if (!valid) return null;
  return {
    x_nm: (Number(valid[0].x_nm) + Number(valid[1].x_nm)) / 2,
    y_nm: (Number(valid[0].y_nm) + Number(valid[1].y_nm)) / 2
  };
}

export function pointListCenter(points) {
  if (!Array.isArray(points) || !points.length) return null;
  const valid = points.filter(isWorldPoint);
  if (!valid.length) return null;
  const xs = valid.map((point) => Number(point.x_nm));
  const ys = valid.map((point) => Number(point.y_nm));
  return {
    x_nm: (Math.min(...xs) + Math.max(...xs)) / 2,
    y_nm: (Math.min(...ys) + Math.max(...ys)) / 2
  };
}

export function normalizeWorldPoint(point) {
  return { x_nm: Number(point.x_nm), y_nm: Number(point.y_nm) };
}

export function runwayHeading(runwayId) {
  const match = String(runwayId || '').match(/\d{2}/);
  if (!match) return 90;
  const runwayNumber = Number(match[0]);
  return runwayNumber === 0 ? 360 : runwayNumber * 10;
}

export function runwayWorldPoints(runwayId) {
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

export function calculateBounds(events) {
  const displayCenter = resolveDisplayCenter(events);
  const defaultRangeNm = resolveDefaultRangeNm(events);
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
  if (!aircraft.length && !surfacePoints.length) return { minX: -10, maxX: 10, minY: -10, maxY: 10, displayCenter, defaultRangeNm };
  const xs = [...aircraft.map((a) => a.x_nm), ...surfacePoints.map((point) => point.x_nm)];
  const ys = [...aircraft.map((a) => a.y_nm), ...surfacePoints.map((point) => point.y_nm)];
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const pad = Math.max(2, (Math.max(maxX - minX, maxY - minY) || 10) * 0.12);
  return { minX: minX - pad, maxX: maxX + pad, minY: minY - pad, maxY: maxY + pad, displayCenter, defaultRangeNm };
}

function resolveDefaultRangeNm(events) {
  for (const event of events) {
    const range = Number(event.state?.airport?.default_range_nm);
    if (Number.isFinite(range) && range > 0) return range;
  }
  return null;
}

function resolveDisplayCenter(events) {
  for (const event of events) {
    const airport = event.state?.airport || {};
    if (isWorldPoint(airport.display_center)) return normalizeWorldPoint(airport.display_center);
  }
  for (const event of events) {
    const airport = event.state?.airport || {};
    if (isWorldPoint(airport.reference_point)) return normalizeWorldPoint(airport.reference_point);
  }
  for (const event of events) {
    const airport = event.state?.airport || {};
    const runway = findLayoutRunway(airport.layout, airport.active_runway || airport.runway_id);
    const midpoint = runwayMidpoint(runway?.ends);
    if (midpoint) return midpoint;
  }
  for (const event of events) {
    const center = pointListCenter(layoutWorldPoints(event.state?.airport?.layout));
    if (center) return center;
  }
  return null;
}

export function isSafetyTriggeredCall(event, explanation) {
  const reasonType = explanation.call_reason?.type;
  if (reasonType === 'event') return true;
  const points = event.decision_points || [];
  return points.some((dp) => {
    const type = String(dp.type || '').toLowerCase();
    const severity = String(dp.severity || '').toLowerCase();
    return severity === 'critical' || type.includes('conflict') || type.includes('runway') || type.includes('emergency');
  });
}

export function describeAction(action) {
  if (!action) return 'No action';
  const type = humanizeLabel(action.type || 'unknown');
  const target = action.aircraft ? ` for ${action.aircraft}` : '';
  if (action.type === 'assign_heading') return `${type}${target} to ${action.heading} degrees`;
  if (action.type === 'assign_altitude') return `${type}${target} to ${action.altitude_ft} ft`;
  if (action.type === 'assign_speed') return `${type}${target} to ${action.speed_kt} kt`;
  if (action.type === 'no_op') return action.aircraft ? `No action for ${action.aircraft}` : 'No action';
  return `${type}${target}`;
}

export function describeInvalidAction(item) {
  const action = item.action ? describeAction(item.action) : 'Malformed command';
  return `${action}: ${humanize(item.reason || item.error || 'rejected')}`;
}

export function describeDecisionPoint(dp) {
  if (!dp) return 'Unknown reason';
  const base = humanizeLabel(dp.type || 'decision point');
  const aircraft = Array.isArray(dp.aircraft) && dp.aircraft.length ? ` involving ${dp.aircraft.join(', ')}` : '';
  const severity = dp.severity ? ` (${humanize(dp.severity)})` : '';
  return `${base}${aircraft}${severity}`;
}

export function describeCommandAck(payload) {
  const action = payload.details?.accepted_action || payload.details?.rejected_action;
  const actionText = action ? describeAction(action) : 'Command';
  if (payload.ok) return `Accepted: ${actionText}.`;
  return `Rejected: ${actionText} (${humanize(payload.reason || 'invalid command')}).`;
}

export default function useViewerState() {
  const [traceEvents, setTraceEvents] = useState([]);
  const [score, setScore] = useState(null);
  const [currentTickIndex, setCurrentTickIndex] = useState(0);
  const [selectedCallsign, setSelectedCallsign] = useState(null);
  const [hoveredCallsign, setHoveredCallsign] = useState(null);
  const [showPredictionOverlay, setShowPredictionOverlay] = useState(true);
  const [currentMode, setCurrentMode] = useState('replay');
  const [radarScopeRangeNm, setRadarScopeRangeNm] = useState(80);

  // Live simulation states
  const [liveSessionId, setLiveSessionId] = useState(null);
  const [livePaused, setLivePaused] = useState(false);
  const [liveLogEntries, setLiveLogEntries] = useState([]);
  const [liveConnectionState, setLiveConnectionState] = useState(false);
  const [liveRunState, setLiveRunState] = useState('Disconnected');
  const [liveFollowTail, setLiveFollowTail] = useState(true);

  // Game flow state
  const [levels, setLevels] = useState(null);
  const [currentLevel, setCurrentLevel] = useState(null);
  const [debrief, setDebrief] = useState(null);
  const [bestResults, setBestResults] = useState(readStoredLevelResults);
  const [hasSavedRun, setHasSavedRun] = useState(() => Boolean(safeStorageGet(LAST_TRACE_KEY)));

  // Command panel state
  const [commandText, setCommandText] = useState('');
  const [commandType, setCommandType] = useState('no_op');
  const [commandValue, setCommandValue] = useState('');
  const [commandFeedback, setCommandFeedback] = useState({ status: null, message: '' });

  const [loadStatus, setLoadStatus] = useState('Load a trace file to begin. Score file optional.');
  const [isPlaying, setIsPlaying] = useState(false);
  const [playSpeed, setPlaySpeed] = useState(1000); // interval ms

  // Interactive scope panning/zooming state
  const [radarBounds, setRadarBounds] = useState(null);
  const [radarView, setRadarView] = useState({ centerX: 0, centerY: 0, zoom: 1 });

  // Refs for animation loop and networking to avoid stale state in callbacks
  const liveSocketRef = useRef(null);
  const livePollTimerRef = useRef(null);
  const playTimerRef = useRef(null);
  const liveSnapshotsByCallsign = useRef(new Map());
  const latestLiveArrivalMs = useRef(0);
  const liveResetPending = useRef(false);
  const traceEventsRef = useRef([]);
  const currentModeRef = useRef(currentMode);
  const liveFollowTailRef = useRef(liveFollowTail);
  const liveLogIdRef = useRef(0);

  // Sync ref for WebSocket handler
  useEffect(() => {
    traceEventsRef.current = traceEvents;
  }, [traceEvents]);

  useEffect(() => {
    currentModeRef.current = currentMode;
  }, [currentMode]);

  useEffect(() => {
    liveFollowTailRef.current = liveFollowTail;
  }, [liveFollowTail]);

  // Timeline filters
  const [filterHurt, setFilterHurt] = useState(false);
  const [filterSafety, setFilterSafety] = useState(false);
  const [filterLargeDelta, setFilterLargeDelta] = useState(false);

  // Parse JSONL file helper
  const parseJsonl = async (file) => {
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
  };

  const applyParsedTrace = useCallback((parsedTrace, statusText) => {
    setIsPlaying(false);
    if (playTimerRef.current) clearInterval(playTimerRef.current);
    playTimerRef.current = null;

    const computedBounds = calculateBounds(parsedTrace);
    setRadarBounds(computedBounds);

    const defaultRange = computedBounds.defaultRangeNm || 80;
    setRadarScopeRangeNm(defaultRange);

    const center = computedBounds.displayCenter || {
      x_nm: (computedBounds.minX + computedBounds.maxX) / 2,
      y_nm: (computedBounds.minY + computedBounds.maxY) / 2
    };
    const boundsWidth = Math.max(1, computedBounds.maxX - computedBounds.minX);
    const boundsHeight = Math.max(1, computedBounds.maxY - computedBounds.minY);
    const boundsSpan = Math.max(boundsWidth, boundsHeight);
    const zoom = Math.max(0.65, Math.min(16, boundsSpan / Math.max(1, defaultRange)));

    setRadarView({ centerX: center.x_nm, centerY: center.y_nm, zoom });
    setSelectedCallsign(null);
    setHoveredCallsign(null);
    setTraceEvents(parsedTrace);
    setCurrentTickIndex(0);
    setLoadStatus(statusText || `Loaded ${parsedTrace.length} ticks. Score file optional.`);
  }, []);

  // Load trace files
  const loadTraceFile = useCallback(async (file) => {
    if (!file) return;
    try {
      const parsedTrace = await parseJsonl(file);
      applyParsedTrace(parsedTrace);
    } catch (err) {
      setLoadStatus(`Failed to parse files: ${err.message}`);
    }
  }, [applyParsedTrace]);

  const loadLastRun = useCallback(() => {
    const text = safeStorageGet(LAST_TRACE_KEY);
    if (!text) {
      setLoadStatus('No saved run found.');
      return;
    }
    try {
      const events = text.split('\n').filter(Boolean).map((line) => JSON.parse(line));
      let savedScore = null;
      try {
        savedScore = JSON.parse(safeStorageGet(LAST_SCORE_KEY) || 'null');
      } catch {
        savedScore = null;
      }
      setCurrentMode('replay');
      applyParsedTrace(events, `Loaded last saved run (${events.length} ticks).`);
      setScore(savedScore && Object.keys(savedScore).length ? savedScore : null);
    } catch (err) {
      setLoadStatus(`Failed to load saved run: ${err.message}`);
    }
  }, [applyParsedTrace]);

  const loadScoreFile = useCallback(async (file) => {
    if (!file) return;
    try {
      const parsedScore = JSON.parse(await file.text());
      setScore(parsedScore);
      if (traceEvents.length > 0) {
        setLoadStatus(`Loaded ${traceEvents.length} ticks and matching score.`);
      }
    } catch (err) {
      setLoadStatus(`Failed to parse score file: ${err.message}`);
    }
  }, [traceEvents.length]);

  // Recenter scope view helper
  const resetScopeView = useCallback((rangeNm = radarScopeRangeNm, bounds = radarBounds) => {
    if (!bounds) return;
    const center = bounds.displayCenter || {
      x_nm: (bounds.minX + bounds.maxX) / 2,
      y_nm: (bounds.minY + bounds.maxY) / 2
    };
    const boundsWidth = Math.max(1, bounds.maxX - bounds.minX);
    const boundsHeight = Math.max(1, bounds.maxY - bounds.minY);
    const boundsSpan = Math.max(boundsWidth, boundsHeight);
    const zoom = Math.max(0.65, Math.min(16, boundsSpan / Math.max(1, rangeNm)));
    setRadarView({
      centerX: center.x_nm,
      centerY: center.y_nm,
      zoom
    });
  }, [radarScopeRangeNm, radarBounds]);

  // Playback timer implementation
  useEffect(() => {
    if (isPlaying && currentMode === 'replay') {
      playTimerRef.current = setInterval(() => {
        setCurrentTickIndex((prev) => {
          if (prev >= traceEvents.length - 1) {
            setIsPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, playSpeed);
    } else {
      if (playTimerRef.current) clearInterval(playTimerRef.current);
      playTimerRef.current = null;
    }
    return () => {
      if (playTimerRef.current) clearInterval(playTimerRef.current);
    };
  }, [isPlaying, playSpeed, traceEvents.length, currentMode]);

  const togglePlayback = useCallback(() => {
    if (currentMode === 'live') {
      setLiveFollowTail((prev) => {
        const next = !prev;
        if (next && traceEvents.length > 0) {
          setCurrentTickIndex(traceEvents.length - 1);
          resetScopeView(radarScopeRangeNm);
        }
        setLoadStatus(next
          ? 'Live playback resumed at the newest tick.'
          : 'Live playback paused. Incoming ticks are buffered.'
        );
        return next;
      });
      return;
    }
    setIsPlaying((prev) => {
      if (!prev && currentTickIndex >= traceEvents.length - 1) {
        setCurrentTickIndex(0);
      }
      return !prev;
    });
  }, [currentMode, currentTickIndex, traceEvents.length, resetScopeView, radarScopeRangeNm]);

  const stepTick = useCallback((delta) => {
    if (currentMode === 'live') {
      setLiveFollowTail(false);
      if (delta > 0) {
        setCurrentTickIndex(traceEvents.length - 1);
        return;
      }
    }
    setCurrentTickIndex((prev) => {
      const next = Math.max(0, Math.min(traceEvents.length - 1, prev + delta));
      if (next === traceEvents.length - 1) {
        setIsPlaying(false);
      }
      return next;
    });
  }, [currentMode, traceEvents.length]);

  const handleZoom = useCallback((direction) => {
    const range = direction === 'in' ? 80 : 40;
    setRadarScopeRangeNm(range);
    resetScopeView(range);
  }, [resetScopeView]);

  const handleResetView = useCallback(() => {
    if (currentMode === 'live' && traceEvents.length > 0) {
      setLiveFollowTail(true);
      resetScopeView(radarScopeRangeNm);
      setCurrentTickIndex(traceEvents.length - 1);
      setLoadStatus('Live scope returned to the latest traffic.');
      return;
    }
    resetScopeView(radarScopeRangeNm);
  }, [currentMode, traceEvents.length, resetScopeView, radarScopeRangeNm]);

  const handleModeChange = useCallback((mode) => {
    setCurrentMode(mode);
    const isLive = mode === 'live';
    setLoadStatus(isLive
      ? 'Live mode uses a stable tactical scope with preset range rings.'
      : 'Replay mode loads files from disk.'
    );
    if (!isLive) {
      // Disconnect live transport
      if (liveSocketRef.current) liveSocketRef.current.close();
      liveSocketRef.current = null;
      if (livePollTimerRef.current) clearInterval(livePollTimerRef.current);
      livePollTimerRef.current = null;
      setLiveFollowTail(false);
      setLivePaused(false);
      setIsPlaying(false);
      setLiveConnectionState(false);
      setLiveRunState('Disconnected');
    }
  }, []);

  const selectAircraftForCommand = useCallback((callsign) => {
    setSelectedCallsign(callsign || null);
  }, []);

  // WebSocket Live transport integration
  const resolveLiveEndpoint = () => {
    return window.atcLiveEndpoint || DEFAULT_LIVE_ENDPOINT;
  };

  const appendLiveLog = (message) => {
    if (!message) return;
    setLiveLogEntries((prev) => {
      const next = [{ id: `live-log-${liveLogIdRef.current += 1}`, time: new Date().toLocaleTimeString(), message }, ...prev];
      return next.slice(0, 12);
    });
  };

  const handleLiveControlStatus = (payload) => {
    const status = payload.status || 'running';
    if (status === 'paused') setLivePaused(true);
    if (status === 'running' || status === 'reset' || status === 'level_started') setLivePaused(false);
    if (status === 'ended') setLivePaused(true);
    if (status === 'unknown_level' || status === 'no_level') {
      appendLiveLog(`Level request rejected (${humanize(status)}).`);
      return;
    }
    if (status !== 'level_started') {
      setLiveRunState(humanize(status));
      appendLiveLog(`Simulation ${humanize(status)}.`);
    }
    if (status === 'reset') {
      setLiveFollowTail(true);
    }
  };

  const recordLevelResult = (payload) => {
    const id = payload.scenario;
    if (!id) return;
    const value = Number(payload.score?.score ?? 0);
    setBestResults((prev) => {
      const existing = prev[id];
      const merged = {
        bestScore: Math.max(existing?.bestScore ?? 0, value),
        stars: Math.max(existing?.stars ?? 0, payload.stars ?? 0),
        lastOutcome: payload.debrief?.outcome || payload.outcome || 'unknown',
        playedAt: new Date().toISOString()
      };
      const next = { ...prev, [id]: merged };
      safeStorageSet(LEVEL_RESULTS_KEY, JSON.stringify(next));
      return next;
    });
  };

  const persistLastRun = (payload) => {
    const traceJsonl = traceEventsRef.current.map((event) => JSON.stringify(event)).join('\n');
    if (!traceJsonl || traceJsonl.length > MAX_PERSISTED_TRACE_CHARS) return;
    const traceOk = safeStorageSet(LAST_TRACE_KEY, traceJsonl);
    const scoreOk = safeStorageSet(LAST_SCORE_KEY, JSON.stringify(payload.score || {}, null, 2));
    if (traceOk && scoreOk) setHasSavedRun(true);
  };

  const handleLevelComplete = (payload) => {
    recordLevelResult(payload);
    persistLastRun(payload);
    setScore(payload.score || null);
    setDebrief(payload);
    setLiveRunState('Level complete');
    const outcome = payload.debrief?.outcome || payload.outcome || 'complete';
    appendLiveLog(`Level complete: ${humanize(outcome)} (${payload.stars ?? 0} stars).`);
    setLoadStatus(`Level complete: ${humanize(outcome)}.`);
  };

  const clearLiveView = () => {
    setTraceEvents([]);
    setRadarBounds(null);
    setCurrentTickIndex(0);
    setSelectedCallsign(null);
    setHoveredCallsign(null);
    liveSnapshotsByCallsign.current.clear();
    latestLiveArrivalMs.current = 0;
    setLiveFollowTail(true);
  };

  const ingestLiveSnapshots = (event) => {
    const now = performance.now();
    latestLiveArrivalMs.current = now;
    const aircraft = Object.values(event.state?.aircraft || {});
    const seen = new Set();
    aircraft.forEach((ac) => {
      const callsign = ac.callsign;
      if (!callsign) return;
      seen.add(callsign);
      const snapshot = { ...ac, __arrivalMs: now };
      const prev = liveSnapshotsByCallsign.current.get(callsign);
      liveSnapshotsByCallsign.current.set(callsign, { previous: prev?.target || snapshot, target: snapshot });
    });
    for (const callsign of liveSnapshotsByCallsign.current.keys()) {
      if (!seen.has(callsign)) liveSnapshotsByCallsign.current.delete(callsign);
    }
  };

  const extractCommandRejectionReason = (payload) => {
    const reasonCode = payload?.reason || payload?.error_code || payload?.invalid?.[0]?.reason;
    if (reasonCode && VALIDATOR_REASON_MESSAGES[reasonCode]) return VALIDATOR_REASON_MESSAGES[reasonCode];
    if (typeof payload?.message === 'string' && payload.message) return payload.message;
    return reasonCode || null;
  };

  const handleLiveEnvelope = (payload) => {
    if (!payload) return;
    if (payload.session_id) setLiveSessionId(payload.session_id);
    if (payload.type === 'level_list') {
      setLevels(Array.isArray(payload.levels) ? payload.levels : []);
      return;
    }
    if (payload.type === 'level_started') {
      clearLiveView();
      liveResetPending.current = false;
      setCurrentLevel(payload.level || null);
      setDebrief(null);
      setLivePaused(false);
      setLiveRunState('Running');
      appendLiveLog(`Level started: ${payload.level?.name || 'unknown level'}.`);
      return;
    }
    if (payload.type === 'control_ack' || payload.type === 'control_status') {
      handleLiveControlStatus(payload);
      return;
    }
    if (payload.type === 'command_ack') {
      appendLiveLog(describeCommandAck(payload));
      const reason = extractCommandRejectionReason(payload);
      setCommandFeedback({
        status: payload.ok ? 'accepted' : 'rejected',
        message: payload.ok ? 'Accepted: command read back.' : `Rejected: ${reason || 'command rejected'}.`
      });
      return;
    }
    if (payload.type === 'level_complete') {
      handleLevelComplete(payload);
      return;
    }
    const event = payload.tick || payload;
    if (!event?.state) return;
    if (liveResetPending.current && Number(event.time) > 1) return;
    liveResetPending.current = false;
    ingestLiveSnapshots(event);

    setTraceEvents((prevTrace) => {
      const nextTrace = [...prevTrace, event];
      const computedBounds = calculateBounds(nextTrace);
      setRadarBounds(computedBounds);

      // recaculate view centering if first tick
      if (nextTrace.length === 1) {
        const defaultRange = computedBounds.defaultRangeNm || 80;
        setRadarScopeRangeNm(defaultRange);
        resetScopeView(defaultRange, computedBounds);
      }

      return nextTrace;
    });

    if (currentModeRef.current !== 'live' || liveFollowTailRef.current) {
      setTraceEvents((prev) => {
        if (prev.length > 0) {
          setCurrentTickIndex(prev.length - 1);
        }
        return prev;
      });
    }

    // select aircraft updates
    const options = Object.keys(event.state.aircraft || {});
    setSelectedCallsign((prevSelected) => (prevSelected && !options.includes(prevSelected) ? null : prevSelected));
  };

  const connectLiveTransport = useCallback(() => {
    const endpoint = resolveLiveEndpoint();
    if (!endpoint) return;
    
    // Disconnect existing
    if (liveSocketRef.current) liveSocketRef.current.close();
    liveSocketRef.current = null;
    if (livePollTimerRef.current) clearInterval(livePollTimerRef.current);
    livePollTimerRef.current = null;

    // Reset live run state
    setIsPlaying(false);
    setTraceEvents([]);
    setScore(null);
    setRadarBounds(null);
    setCurrentTickIndex(0);
    setSelectedCallsign(null);
    setHoveredCallsign(null);
    liveSnapshotsByCallsign.current.clear();
    latestLiveArrivalMs.current = 0;
    setLiveFollowTail(true);
    liveResetPending.current = false;
    setLiveSessionId(null);
    setLivePaused(false);
    setLiveLogEntries([]);
    liveLogIdRef.current = 0;
    setRadarScopeRangeNm(80);
    setLevels(null);
    setCurrentLevel(null);
    setDebrief(null);

    if (endpoint.startsWith('ws://') || endpoint.startsWith('wss://')) {
      const socket = new WebSocket(endpoint);
      liveSocketRef.current = socket;
      
      socket.onopen = () => {
        setLiveConnectionState(true);
        setLiveFollowTail(true);
        setLivePaused(false);
        setLiveRunState('Connected');
        appendLiveLog('Session started.');
        setLoadStatus(`Live connected: ${endpoint}`);
        socket.send(JSON.stringify({ type: 'subscribe_tick_stream' }));
        socket.send(JSON.stringify({ type: 'list_levels' }));
      };
      
      socket.onmessage = (event) => {
        try {
          const payload = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
          handleLiveEnvelope(payload);
        } catch {
          // ignore parsing error
        }
      };
      
      socket.onerror = () => {
        setLoadStatus('Live transport error.');
      };
      
      socket.onclose = () => {
        setLiveConnectionState(false);
      };
      return;
    }

    setLiveConnectionState(true);
    livePollTimerRef.current = setInterval(async () => {
      try {
        const state = await fetch(`${endpoint.replace(/\/$/, '')}/state`).then((res) => res.json());
        if (state) handleLiveEnvelope(state);
      } catch {
        // ignore fetch error
      }
    }, 1000);
  }, [resetScopeView]);

  const disconnectLiveTransport = useCallback(() => {
    if (liveSocketRef.current) liveSocketRef.current.close();
    liveSocketRef.current = null;
    if (livePollTimerRef.current) clearInterval(livePollTimerRef.current);
    livePollTimerRef.current = null;
    setLiveFollowTail(false);
    setLivePaused(false);
    setIsPlaying(false);
    setLiveConnectionState(false);
    setLiveRunState('Disconnected');
  }, []);

  const sendLiveControl = useCallback((type) => {
    if (!liveSocketRef.current || liveSocketRef.current.readyState !== WebSocket.OPEN) {
      setCommandFeedback({ status: 'rejected', message: 'Rejected: live transport is not connected.' });
      return;
    }
    liveSocketRef.current.send(JSON.stringify({ type, session_id: liveSessionId }));
    if (type === 'pause') {
      setLivePaused(true);
      setLiveRunState('Paused');
      appendLiveLog('Pause requested.');
    } else if (type === 'resume') {
      setLivePaused(false);
      setLiveFollowTail(true);
      setLiveRunState('Running');
      appendLiveLog('Resume requested.');
    } else if (type === 'reset') {
      setLiveRunState('Resetting');
      appendLiveLog('Scenario reset requested.');
    } else if (type === 'end_session') {
      setLiveRunState('Ending');
      appendLiveLog('End session requested.');
    }
  }, [liveSessionId]);

  const startLevel = useCallback((levelId) => {
    if (!levelId) return;
    if (!liveSocketRef.current || liveSocketRef.current.readyState !== WebSocket.OPEN) {
      setLoadStatus('Connect to the live server before starting a level.');
      return;
    }
    setDebrief(null);
    setScore(null);
    liveResetPending.current = true;
    liveSocketRef.current.send(JSON.stringify({ type: 'start_level', scenario: levelId, session_id: liveSessionId }));
    appendLiveLog(`Requested level: ${levelId}.`);
  }, [liveSessionId]);

  const returnToLevelSelect = useCallback(() => {
    setDebrief(null);
    setCurrentLevel(null);
    setScore(null);
    clearLiveView();
    if (liveSocketRef.current && liveSocketRef.current.readyState === WebSocket.OPEN) {
      liveSocketRef.current.send(JSON.stringify({ type: 'list_levels', session_id: liveSessionId }));
    }
  }, [liveSessionId]);

  const watchReplay = useCallback(() => {
    if (debrief?.score) setScore(debrief.score);
    setDebrief(null);
    setCurrentLevel(null);
    handleModeChange('replay');
    setCurrentTickIndex(0);
    setLoadStatus('Reviewing the finished level in replay mode.');
  }, [debrief, handleModeChange]);

  // Command processing
  const parseCommandText = (rawText) => {
    const raw = String(rawText || '').trim();
    if (!raw) return { ok: false, reason: 'Enter a command.' };
    const normalized = raw.toUpperCase().replace(/[,:/]+/g, ' ');
    const tokens = normalized.split(/\s+/).filter(Boolean);
    
    const currentEvent = traceEvents[currentTickIndex] || {};
    const aircraftIds = new Set(Object.keys(currentEvent.state?.aircraft || {}).map((item) => item.toUpperCase()));
    
    const first = tokens[0] || '';
    const hasExplicitAircraft = aircraftIds.has(first);
    const aircraft = hasExplicitAircraft ? first : null;
    const actionTokens = hasExplicitAircraft ? tokens.slice(1) : tokens;
    if (!aircraft) return { ok: false, reason: 'Start with a callsign.' };
    if (!actionTokens.length) return { ok: false, reason: 'Enter a command action.' };

    const joined = actionTokens.join(' ');
    const value = Number(actionTokens.find((token) => /^-?\d+(\.\d+)?$/.test(token)));
    let type = null;
    if (/^(NOOP|NO-OP|NONE|NO ACTION)$/.test(joined)) type = 'no_op';
    else if (/^(HDG|HEADING)\b/.test(joined)) type = 'assign_heading';
    else if (/^(ALT|ALTITUDE|CLIMB|DESCEND)\b/.test(joined)) type = 'assign_altitude';
    else if (/^(SPD|SPEED)\b/.test(joined)) type = 'assign_speed';
    else if (/^(LAND|CLEAR LAND|CLEARED LAND|CLEAR TO LAND)$/.test(joined)) type = 'clear_to_land';
    else if (/^(TAKEOFF|TAKE OFF|CLEAR TAKEOFF|CLEAR FOR TAKEOFF)$/.test(joined)) type = 'clear_for_takeoff';
    else if (/^(GA|GO AROUND|GOAROUND)$/.test(joined)) type = 'go_around';
    else if (/^(HOLD SHORT|SHORT)$/.test(joined)) type = 'hold_short';
    else if (/^(HOLD|HOLD POS|HOLD POSITION|POSITION)$/.test(joined)) type = 'hold_position';
    if (!type) return { ok: false, reason: 'Unsupported command action.' };

    const schema = COMMAND_SCHEMA[type];
    const command = { aircraft, type };
    if (schema.field) {
      if (!Number.isFinite(value)) return { ok: false, reason: `Provide ${schema.unitHint}.` };
      if (value < schema.min || value > schema.max) return { ok: false, reason: `${schema.label} must be ${schema.unitHint}.` };
      command[schema.field] = value;
    }
    return { ok: true, command };
  };

  const buildLiveCommandEnvelope = () => {
    const parsed = parseCommandText(commandText);
    if (parsed.ok) {
      return { ok: true, envelope: { type: 'command', session_id: liveSessionId, command: parsed.command } };
    }
    if ((commandText || '').trim()) return parsed;

    const callsign = selectedCallsign;
    const actionType = commandType;
    const schema = COMMAND_SCHEMA[actionType];
    if (!callsign) return { ok: false, reason: 'Select an aircraft from the radar or flight strips.' };
    if (!schema) return { ok: false, reason: 'Select a valid action type.' };
    const command = { aircraft: callsign, type: actionType };
    if (schema.field) {
      const raw = commandValue;
      const numericValue = Number(raw);
      if (!raw || Number.isNaN(numericValue)) return { ok: false, reason: `Provide ${schema.unitHint}.` };
      if (numericValue < schema.min || numericValue > schema.max) {
        return { ok: false, reason: `${schema.label} must be ${schema.unitHint}.` };
      }
      command[schema.field] = numericValue;
    }
    return { ok: true, envelope: { type: 'command', session_id: liveSessionId, command } };
  };

  const sendLiveCommand = useCallback(() => {
    const envelopeResult = buildLiveCommandEnvelope();
    if (!envelopeResult.ok) {
      setCommandFeedback({ status: 'rejected', message: `Rejected: ${envelopeResult.reason}` });
      return;
    }
    const { envelope } = envelopeResult;
    setCommandFeedback({ status: null, message: 'Sending command…' });
    if (liveSocketRef.current && liveSocketRef.current.readyState === WebSocket.OPEN) {
      liveSocketRef.current.send(JSON.stringify(envelope));
      setCommandFeedback({ status: null, message: 'Command sent. Waiting for controller response.' });
      return;
    }
    const endpoint = resolveLiveEndpoint().replace(/\/$/, '');
    fetch(`${endpoint}/command`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(envelope) })
      .then(async (res) => {
        const payload = await res.json().catch(() => ({}));
        const responseReason = extractCommandRejectionReason(payload);
        if (!res.ok || responseReason) {
          const reason = responseReason || payload?.error || `HTTP ${res.status}`;
          setCommandFeedback({ status: 'rejected', message: `Rejected: ${reason}` });
          return;
        }
        setCommandFeedback({ status: 'accepted', message: 'Accepted: command delivered to backend.' });
      })
      .catch((err) => {
        setCommandFeedback({ status: 'rejected', message: `Rejected: transport error (${err.message}).` });
      });
  }, [commandText, selectedCallsign, commandType, commandValue, liveSessionId, traceEvents, currentTickIndex]);

  const selectCommandType = useCallback((actionType) => {
    if (!COMMAND_SCHEMA[actionType]) return;
    setCommandType(actionType);
    setCommandFeedback({ status: null, message: '' });
  }, []);

  const handleCommandTextChange = useCallback((text) => {
    setCommandText(text);
    setCommandFeedback({ status: null, message: '' });
  }, []);

  const handleCommandValueChange = useCallback((val) => {
    setCommandValue(val);
    setCommandFeedback({ status: null, message: '' });
  }, []);

  return {
    traceEvents,
    score,
    currentTickIndex,
    selectedCallsign,
    hoveredCallsign,
    showPredictionOverlay,
    currentMode,
    radarScopeRangeNm,
    liveSessionId,
    livePaused,
    liveLogEntries,
    liveConnectionState,
    liveRunState,
    liveFollowTail,
    commandText,
    commandType,
    commandValue,
    commandFeedback,
    levels,
    currentLevel,
    debrief,
    bestResults,
    hasSavedRun,
    loadStatus,
    isPlaying,
    playSpeed,
    radarView,
    radarBounds,
    liveSnapshotsByCallsign,
    latestLiveArrivalMs,
    filterHurt,
    filterSafety,
    filterLargeDelta,
    
    // Actions
    setHoveredCallsign,
    setSelectedCallsign,
    setCurrentTickIndex,
    setShowPredictionOverlay,
    setFilterHurt,
    setFilterSafety,
    setFilterLargeDelta,
    setRadarView,
    loadTraceFile,
    loadScoreFile,
    loadLastRun,
    startLevel,
    returnToLevelSelect,
    watchReplay,
    stepTick,
    togglePlayback,
    setPlaySpeed,
    handleZoom,
    handleResetView,
    handleModeChange,
    selectAircraftForCommand,
    connectLiveTransport,
    disconnectLiveTransport,
    sendLiveControl,
    sendLiveCommand,
    selectCommandType,
    handleCommandTextChange,
    handleCommandValueChange
  };
}
