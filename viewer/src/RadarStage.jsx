import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Stage, Layer, Rect, Line, Circle, Text, Group } from 'react-konva';
import { humanize, runwayWorldPoints } from './useViewerState';

const MONO = '"JetBrains Mono", "Fira Code", Consolas, monospace';

// STARS-style presentation: dark scope, green datablocks, blue-white targets,
// red conflict alert, amber emergencies, dim gray-blue video map.
const palette = {
  void: '#06090b',
  fdb: '#2ee66b',
  fdbDim: 'rgba(46, 230, 107, 0.55)',
  target: '#b9dcff',
  history: '70, 160, 255',
  conflict: '#ff4545',
  caution: '#ffc94a',
  selected: '#f2f7f4',
  landed: '#46555f',
  map: 'rgba(140, 162, 178, 0.26)',
  mapStrong: 'rgba(168, 192, 208, 0.45)',
  mapText: 'rgba(150, 175, 190, 0.5)',
  rings: 'rgba(120, 150, 165, 0.12)',
  ringText: 'rgba(140, 170, 185, 0.32)',
  ssa: '#9fe8bb'
};

const STATUS_CODES = {
  waiting_departure: 'RDY',
  taking_off: 'DEP',
  on_final: 'FNL',
  final: 'FNL',
  approach: 'APP',
  holding: 'HLD',
  going_around: 'GA',
  go_around: 'GA',
  landed: 'LND',
  exited_airspace: 'EXT'
};

const PTL_MINUTES = 1;
const EXTENDED_CENTERLINE_NM = 12;

const LIVE_INTERPOLATION = {
  lagMs: 220,
  maxFrameDeltaMs: 120,
  maxHoldMs: 2200
};

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

// Altitude in hundreds of feet, three digits: 4500 -> "045".
function altHundreds(altitudeFt) {
  const altitude = Number(altitudeFt);
  if (!Number.isFinite(altitude)) return '---';
  return String(Math.max(0, Math.round(altitude / 100))).padStart(3, '0');
}

// Groundspeed in tens of knots, two digits: 210 -> "21".
function speedTens(speedKt) {
  const speed = Number(speedKt);
  if (!Number.isFinite(speed)) return '--';
  return String(Math.max(0, Math.round(speed / 10))).padStart(2, '0');
}

function verticalArrow(verticalRateFpm) {
  const rate = Number(verticalRateFpm);
  if (!Number.isFinite(rate)) return ' ';
  if (rate > 300) return '↑';
  if (rate < -300) return '↓';
  return ' ';
}

function formatClock(timeSec) {
  const total = Math.max(0, Math.round(Number(timeSec) || 0));
  const h = String(Math.floor(total / 3600)).padStart(2, '0');
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
  const s = String(total % 60).padStart(2, '0');
  return `${h}${m} ${s}`;
}

export default function RadarStage({
  traceEvents = [],
  currentTickIndex = 0,
  selectedCallsign = null,
  hoveredCallsign = null,
  setHoveredCallsign,
  selectAircraftForCommand,
  showPredictionOverlay = true,
  currentMode = 'replay',
  radarScopeRangeNm = 80,
  radarView,
  setRadarView,
  radarBounds,
  liveSnapshotsByCallsign,
  latestLiveArrivalMs
}) {
  const containerRef = useRef(null);
  const stageRef = useRef(null);
  const [size, setSize] = useState({ width: 900, height: 560 });
  const [interpolatedAircraft, setInterpolatedAircraft] = useState([]);
  const [tooltip, setTooltip] = useState(null);

  // Drives CA blinking (every phase step) and datablock field time-share (every 4 steps).
  const [displayPhase, setDisplayPhase] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setDisplayPhase((p) => (p + 1) % 8), 500);
    return () => clearInterval(id);
  }, []);
  const blinkOn = displayPhase % 2 === 0;
  const altLine2Page = Math.floor(displayPhase / 4) % 2;

  // Interactive Panning state
  const [isPanning, setIsPanning] = useState(false);
  const panRef = useRef({ isDown: false, start: null, last: null, dragged: false });

  // Keep track of current dimensions for ResizeObserver
  useEffect(() => {
    const resize = () => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect?.width || !rect?.height) return;
      setSize({ width: rect.width, height: rect.height });
    };
    resize();
    const observer = new ResizeObserver(resize);
    if (containerRef.current) observer.observe(containerRef.current);
    window.addEventListener('resize', resize);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', resize);
    };
  }, []);

  // Compute view bounds
  const getViewBounds = useCallback(() => {
    if (!radarBounds) return { minX: -10, maxX: 10, minY: -10, maxY: 10 };
    const width = (radarBounds.maxX - radarBounds.minX) / radarView.zoom;
    const height = (radarBounds.maxY - radarBounds.minY) / radarView.zoom;
    return {
      minX: radarView.centerX - width / 2,
      maxX: radarView.centerX + width / 2,
      minY: radarView.centerY - height / 2,
      maxY: radarView.centerY + height / 2
    };
  }, [radarBounds, radarView]);

  // Uniform px-per-nm scale so circles stay circular regardless of viewport aspect.
  const getScale = useCallback(() => {
    const view = getViewBounds();
    const spanX = Math.max(1e-6, view.maxX - view.minX);
    const spanY = Math.max(1e-6, view.maxY - view.minY);
    return Math.min((size.width - 88) / spanX, (size.height - 88) / spanY);
  }, [getViewBounds, size]);

  const projectPoint = useCallback((x_nm, y_nm) => {
    const view = getViewBounds();
    const scale = getScale();
    const cx = (view.minX + view.maxX) / 2;
    const cy = (view.minY + view.maxY) / 2;
    return {
      x: size.width / 2 + (Number(x_nm) - cx) * scale,
      y: size.height / 2 - (Number(y_nm) - cy) * scale
    };
  }, [getViewBounds, getScale, size]);

  const pixelRadiusForNm = useCallback((nm) => nm * getScale(), [getScale]);

  // Smooth live mode interpolation
  useEffect(() => {
    const event = traceEvents[currentTickIndex];
    if (!event) return;

    if (currentMode !== 'live') {
      setInterpolatedAircraft(Object.values(event.state?.aircraft || {}));
      return;
    }

    let active = true;
    let prevFrame = performance.now();

    const loop = (ts) => {
      if (!active) return;
      const dt = Math.min(LIVE_INTERPOLATION.maxFrameDeltaMs, Math.max(0, ts - prevFrame));
      prevFrame = ts;

      const fallback = Object.values(event.state?.aircraft || {});
      const delayed = latestLiveArrivalMs.current && (ts - latestLiveArrivalMs.current) > LIVE_INTERPOLATION.maxHoldMs;

      const interpolated = fallback.map((ac) => {
        const pair = liveSnapshotsByCallsign.current.get(ac.callsign);
        if (!pair?.target) return ac;
        const previous = pair.previous || pair.target;
        const target = pair.target;
        const span = Math.max(1, target.__arrivalMs - previous.__arrivalMs);
        let alpha = (ts - LIVE_INTERPOLATION.lagMs - previous.__arrivalMs) / span;
        if (delayed) {
          const slowdown = Math.max(0.08, 1 - (dt / LIVE_INTERPOLATION.maxFrameDeltaMs));
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

      setInterpolatedAircraft(interpolated);
      requestAnimationFrame(loop);
    };

    requestAnimationFrame(loop);
    return () => {
      active = false;
    };
  }, [currentMode, currentTickIndex, traceEvents, liveSnapshotsByCallsign, latestLiveArrivalMs]);

  // Stage Event Handlers
  const handleStageMouseDown = (e) => {
    const point = { x: e.evt.clientX, y: e.evt.clientY };
    panRef.current = { isDown: true, start: point, last: point, dragged: false };
    setIsPanning(true);
  };

  const handleStageMouseMove = (e) => {
    const currentEvent = traceEvents[currentTickIndex];
    if (!currentEvent) return;

    const rect = containerRef.current?.getBoundingClientRect();
    const mouseX = e.evt.clientX - rect.left;
    const mouseY = e.evt.clientY - rect.top;

    // Handle Panning
    if (panRef.current.isDown && panRef.current.last) {
      const dx = e.evt.clientX - panRef.current.last.x;
      const dy = e.evt.clientY - panRef.current.last.y;
      const totalDx = e.evt.clientX - panRef.current.start.x;
      const totalDy = e.evt.clientY - panRef.current.start.y;
      if (Math.hypot(totalDx, totalDy) > 3) panRef.current.dragged = true;
      const worldPerPx = 1 / getScale();

      setRadarView((prev) => ({
        ...prev,
        centerX: prev.centerX - dx * worldPerPx,
        centerY: prev.centerY + dy * worldPerPx
      }));
      const nextPoint = { x: e.evt.clientX, y: e.evt.clientY };
      panRef.current.last = nextPoint;
      return;
    }

    // Handle Hover & Tooltip
    let foundTarget = null;
    for (const ac of interpolatedAircraft) {
      const { x, y } = projectPoint(ac.x_nm, ac.y_nm);
      const dist = Math.hypot(x - mouseX, y - mouseY);
      if (dist <= 14) {
        foundTarget = ac;
        break;
      }
    }

    if (foundTarget) {
      setHoveredCallsign(foundTarget.callsign);
      const conflictSet = new Set(
        (currentEvent.conflicts || []).flatMap((c) => c.aircraft || [])
      );
      const predictedSet = new Set(
        (currentEvent.predicted_conflicts || []).flatMap((c) => c.aircraft || [])
      );
      const status = conflictSet.has(foundTarget.callsign)
        ? 'Active conflict'
        : predictedSet.has(foundTarget.callsign)
          ? 'Predicted conflict'
          : foundTarget.emergency
            ? 'Emergency'
            : humanize(foundTarget.status || 'airborne');

      setTooltip({
        x: mouseX + 14,
        y: mouseY + 14,
        callsign: foundTarget.callsign,
        role: humanize(foundTarget.role || 'aircraft'),
        status,
        altitude: Math.round(foundTarget.altitude_ft),
        speed: Math.round(foundTarget.speed_kt),
        heading: Math.round(foundTarget.heading_deg)
      });
    } else {
      setHoveredCallsign(null);
      setTooltip(null);
    }
  };

  const handleStageMouseUp = () => {
    setIsPanning(false);
    panRef.current.isDown = false;
  };

  const aircraftAtPoint = useCallback((clientX, clientY) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return null;
    const mouseX = clientX - rect.left;
    const mouseY = clientY - rect.top;
    for (const ac of interpolatedAircraft) {
      const { x, y } = projectPoint(ac.x_nm, ac.y_nm);
      if (Math.hypot(x - mouseX, y - mouseY) <= 14) return ac.callsign;
    }
    return null;
  }, [interpolatedAircraft, projectPoint]);

  const handleStageClick = (e) => {
    if (panRef.current.dragged) {
      panRef.current.dragged = false;
      return;
    }
    const clickedCallsign = aircraftAtPoint(e.evt.clientX, e.evt.clientY);
    if (clickedCallsign) {
      selectAircraftForCommand(selectedCallsign === clickedCallsign ? null : clickedCallsign);
    } else {
      selectAircraftForCommand(null);
    }
  };

  // Cursor-anchored wheel zoom.
  const handleStageWheel = (e) => {
    e.evt.preventDefault();
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mouseX = e.evt.clientX - rect.left;
    const mouseY = e.evt.clientY - rect.top;
    const scale = getScale();
    const view = getViewBounds();
    const cx = (view.minX + view.maxX) / 2;
    const cy = (view.minY + view.maxY) / 2;
    const worldX = cx + (mouseX - size.width / 2) / scale;
    const worldY = cy - (mouseY - size.height / 2) / scale;
    const factor = e.evt.deltaY < 0 ? 1.18 : 1 / 1.18;

    setRadarView((prev) => {
      const zoom = Math.max(0.3, Math.min(60, prev.zoom * factor));
      const shrink = prev.zoom / zoom;
      return {
        zoom,
        centerX: worldX - (worldX - prev.centerX) * shrink,
        centerY: worldY - (worldY - prev.centerY) * shrink
      };
    });
  };

  // Pre-compiled derived properties
  const currentEvent = traceEvents[currentTickIndex];
  const airport = currentEvent?.state?.airport || {};
  const weather = currentEvent?.state?.weather || {};
  const activeRunwayId = airport.active_runway || airport.runway_id || 'RWY';
  const layout = airport.layout || {};

  const byCallsign = useMemo(() => {
    return Object.fromEntries(interpolatedAircraft.map((ac) => [ac.callsign, ac]));
  }, [interpolatedAircraft]);

  const conflictSet = useMemo(
    () => new Set((currentEvent?.conflicts || []).flatMap((c) => c.aircraft || [])),
    [currentEvent]
  );
  const predictedSet = useMemo(
    () => new Set((currentEvent?.predicted_conflicts || []).flatMap((c) => c.aircraft || [])),
    [currentEvent]
  );

  // Position-history trails from the last few ticks.
  const trails = useMemo(() => {
    const map = new Map();
    const start = Math.max(0, currentTickIndex - 10);
    for (let i = start; i <= currentTickIndex; i += 1) {
      const tickAircraft = traceEvents[i]?.state?.aircraft || {};
      for (const [callsign, ac] of Object.entries(tickAircraft)) {
        const x = Number(ac.x_nm);
        const y = Number(ac.y_nm);
        if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
        if (!map.has(callsign)) map.set(callsign, []);
        map.get(callsign).push({ x_nm: x, y_nm: y });
      }
    }
    return map;
  }, [traceEvents, currentTickIndex]);

  // Pick a leader-line quadrant per datablock so blocks in dense traffic overlap less.
  const leaderDirections = useMemo(() => {
    const candidates = [
      { dx: 1, dy: -1 },
      { dx: -1, dy: -1 },
      { dx: 1, dy: 1 },
      { dx: -1, dy: 1 }
    ];
    const directions = new Map();
    const placedBlocks = [];
    for (const ac of interpolatedAircraft) {
      const { x, y } = projectPoint(ac.x_nm, ac.y_nm);
      let best = candidates[0];
      let bestScore = Infinity;
      for (const cand of candidates) {
        const blockX = x + cand.dx * 60;
        const blockY = y + cand.dy * 28;
        let score = cand === candidates[0] ? 0 : 4; // mild preference for the NE default
        for (const other of interpolatedAircraft) {
          if (other.callsign === ac.callsign) continue;
          const op = projectPoint(other.x_nm, other.y_nm);
          const dist = Math.hypot(op.x - blockX, op.y - blockY);
          if (dist < 80) score += 80 - dist;
        }
        for (const block of placedBlocks) {
          const dist = Math.hypot(block.x - blockX, block.y - blockY);
          if (dist < 90) score += (90 - dist) * 1.5;
        }
        if (score < bestScore) {
          bestScore = score;
          best = cand;
        }
      }
      directions.set(ac.callsign, best);
      placedBlocks.push({ x: x + best.dx * 60, y: y + best.dy * 28 });
    }
    return directions;
  }, [interpolatedAircraft, projectPoint]);

  const visibleRangeNm = useMemo(() => {
    const scale = getScale();
    return scale > 0 ? Math.round((size.width - 88) / scale) : 0;
  }, [getScale, size]);

  return (
    <div ref={containerRef} className="konva-radar" style={{ position: 'relative' }}>
      <Stage
        ref={stageRef}
        width={size.width}
        height={size.height}
        onMouseDown={handleStageMouseDown}
        onMouseMove={handleStageMouseMove}
        onMouseUp={handleStageMouseUp}
        onClick={handleStageClick}
        onWheel={handleStageWheel}
        style={{ cursor: isPanning ? 'grabbing' : 'grab' }}
      >
        <Layer>
          {/* Background void */}
          <Rect x={0} y={0} width={size.width} height={size.height} fill={palette.void} />

          {/* Range rings and compass rose around the field */}
          <RangeRings
            projectPoint={projectPoint}
            pixelRadiusForNm={pixelRadiusForNm}
            size={size}
          />

          {/* Airport Layout (video map: aprons, taxiways, stands, runways, centerlines) */}
          {layout && (
            <AirportLayout
              layout={layout}
              activeRunwayId={activeRunwayId}
              projectPoint={projectPoint}
              pixelRadiusForNm={pixelRadiusForNm}
              getScale={getScale}
            />
          )}

          {/* Conflict Links */}
          {currentEvent?.predicted_conflicts && (
            <ConflictLines
              records={currentEvent.predicted_conflicts}
              byCallsign={byCallsign}
              projectPoint={projectPoint}
              color={palette.caution}
              dash={[6, 5]}
            />
          )}
          {currentEvent?.conflicts && (
            <ConflictLines
              records={currentEvent.conflicts}
              byCallsign={byCallsign}
              projectPoint={projectPoint}
              color={palette.conflict}
            />
          )}

          {/* Position-history trails (single-color, STARS-style) */}
          {interpolatedAircraft.map((ac) => {
            if (ac.status === 'landed' || ac.status === 'exited_airspace') return null;
            const history = trails.get(ac.callsign);
            if (!history || history.length < 2) return null;
            return (
              <Group key={`trail-${ac.callsign}`}>
                {history.slice(0, -1).map((point, idx) => {
                  const { x, y } = projectPoint(point.x_nm, point.y_nm);
                  const alpha = 0.1 + 0.4 * (idx / history.length);
                  return (
                    <Circle
                      key={`trail-${ac.callsign}-${idx}`}
                      x={x}
                      y={y}
                      radius={1.5}
                      fill={`rgba(${palette.history}, ${alpha.toFixed(2)})`}
                    />
                  );
                })}
              </Group>
            );
          })}

          {/* Separation J-ring around the selected aircraft */}
          {selectedCallsign && byCallsign[selectedCallsign]
            && byCallsign[selectedCallsign].status !== 'landed'
            && byCallsign[selectedCallsign].status !== 'exited_airspace' && (
            <SeparationRing
              aircraft={byCallsign[selectedCallsign]}
              projectPoint={projectPoint}
              pixelRadiusForNm={pixelRadiusForNm}
            />
          )}

          {/* Aircraft Targets */}
          {interpolatedAircraft.map((ac) => {
            const isLanded = ac.status === 'landed' || ac.status === 'exited_airspace';
            const isSelected = ac.callsign === selectedCallsign;
            const isHovered = ac.callsign === hoveredCallsign;
            const isConflict = conflictSet.has(ac.callsign);
            const isPredicted = predictedSet.has(ac.callsign);

            const blockColor = isLanded
              ? palette.landed
              : isConflict
                ? palette.conflict
                : ac.emergency
                  ? palette.caution
                  : (isSelected || isHovered)
                    ? palette.selected
                    : palette.fdb;

            const { x, y } = projectPoint(ac.x_nm, ac.y_nm);
            const leader = leaderDirections.get(ac.callsign) || { dx: 1, dy: -1 };
            const symbolChar = String(ac.role || '').toLowerCase() === 'departure' ? 'D' : 'A';

            return (
              <Group key={ac.callsign}>
                {/* Selection ring */}
                {(isSelected || isHovered) && (
                  <Circle
                    x={x}
                    y={y}
                    radius={isSelected ? 11 : 9}
                    stroke={palette.selected}
                    strokeWidth={isSelected ? 1.5 : 1}
                  />
                )}

                {/* Position symbol */}
                <Text
                  x={x}
                  y={y}
                  text={isLanded ? '×' : symbolChar}
                  fill={isLanded ? palette.landed : isConflict ? palette.conflict : palette.target}
                  fontFamily={MONO}
                  fontSize={11}
                  fontStyle="700"
                  offsetX={3.5}
                  offsetY={5.5}
                />

                {/* Predicted track line: 1 minute of travel at current groundspeed */}
                {!isLanded && Number(ac.speed_kt) > 0 && (
                  <Line
                    points={(() => {
                      const headingRad = ((Number(ac.heading_deg || 0) - 90) * Math.PI) / 180;
                      const lengthPx = pixelRadiusForNm((Number(ac.speed_kt) * PTL_MINUTES) / 60);
                      return [
                        x + 6 * Math.cos(headingRad),
                        y + 6 * Math.sin(headingRad),
                        x + lengthPx * Math.cos(headingRad),
                        y + lengthPx * Math.sin(headingRad)
                      ];
                    })()}
                    stroke={isConflict ? palette.conflict : 'rgba(185, 220, 255, 0.55)'}
                    strokeWidth={1}
                  />
                )}

                {/* STARS-format datablock with leader line */}
                <Datablock
                  aircraft={ac}
                  x={x}
                  y={y}
                  leader={leader}
                  color={blockColor}
                  isLanded={isLanded}
                  isConflict={isConflict}
                  isPredicted={isPredicted}
                  blinkOn={blinkOn}
                  altLine2Page={altLine2Page}
                />
              </Group>
            );
          })}

          {/* Prediction Path Overlay for Selected Aircraft */}
          {showPredictionOverlay && selectedCallsign && byCallsign[selectedCallsign] && (
            <PredictionOverlay
              aircraft={byCallsign[selectedCallsign]}
              airportState={currentEvent?.state}
              projectPoint={projectPoint}
            />
          )}

          {/* System Status Area */}
          <SystemStatusArea
            event={currentEvent}
            weather={weather}
            activeRunwayId={activeRunwayId}
            rangeNm={visibleRangeNm}
            aircraftCount={interpolatedAircraft.filter((ac) => ac.status !== 'landed' && ac.status !== 'exited_airspace').length}
            blinkOn={blinkOn}
          />
        </Layer>
      </Stage>

      {/* Tooltip Popup */}
      {tooltip && (
        <div
          className="radar-tooltip"
          style={{
            position: 'absolute',
            left: tooltip.x,
            top: tooltip.y,
            zIndex: 30,
            pointerEvents: 'none'
          }}
        >
          <b>{tooltip.callsign}</b>
          <span>{tooltip.role} - {tooltip.status}</span>
          <span>{tooltip.altitude} ft - {tooltip.speed} kt - heading {tooltip.heading} deg</span>
        </div>
      )}
    </div>
  );
}

function RangeRings({ projectPoint, pixelRadiusForNm, size }) {
  const center = projectPoint(0, 0);
  const pxPerNm = pixelRadiusForNm(1);
  if (!Number.isFinite(pxPerNm) || pxPerNm <= 0) return null;

  // Adaptive ring spacing: smallest step in the 1/2/5 series at least ~70px apart.
  const steps = [1, 2, 5, 10, 20, 40];
  const step = steps.find((s) => s * pxPerNm >= 70) || 80;

  // Cover the whole viewport from the ring center.
  const corners = [
    Math.hypot(center.x, center.y),
    Math.hypot(size.width - center.x, center.y),
    Math.hypot(center.x, size.height - center.y),
    Math.hypot(size.width - center.x, size.height - center.y)
  ];
  const maxRadiusPx = Math.max(...corners);
  const ringCount = Math.min(10, Math.ceil(maxRadiusPx / (step * pxPerNm)));

  const rings = [];
  for (let i = 1; i <= ringCount; i += 1) {
    const nm = i * step;
    const radius = nm * pxPerNm;
    rings.push(
      <Group key={`ring-${nm}`}>
        <Circle x={center.x} y={center.y} radius={radius} stroke={palette.rings} strokeWidth={1} />
        <Text
          x={center.x + 4}
          y={center.y - radius + 4}
          text={`${nm}`}
          fill={palette.ringText}
          fontFamily={MONO}
          fontSize={9}
        />
      </Group>
    );
  }

  // Compass rose on the outermost fully visible ring.
  const visibleRadius = Math.min(
    center.x, size.width - center.x, center.y, size.height - center.y
  ) - 16;
  const roseRadius = visibleRadius > step * pxPerNm
    ? Math.floor(visibleRadius / (step * pxPerNm)) * step * pxPerNm
    : null;

  const roseTicks = [];
  if (roseRadius && roseRadius > 80) {
    for (let deg = 0; deg < 360; deg += 10) {
      const angle = ((deg - 90) * Math.PI) / 180;
      const major = deg % 30 === 0;
      const inner = roseRadius - (major ? 8 : 4);
      roseTicks.push(
        <Line
          key={`rose-tick-${deg}`}
          points={[
            center.x + inner * Math.cos(angle),
            center.y + inner * Math.sin(angle),
            center.x + roseRadius * Math.cos(angle),
            center.y + roseRadius * Math.sin(angle)
          ]}
          stroke={major ? palette.ringText : palette.rings}
          strokeWidth={1}
        />
      );
      if (major) {
        roseTicks.push(
          <Text
            key={`rose-label-${deg}`}
            x={center.x + (roseRadius + 12) * Math.cos(angle)}
            y={center.y + (roseRadius + 12) * Math.sin(angle)}
            text={String(deg === 0 ? 360 : deg).padStart(3, '0')}
            fill={palette.ringText}
            fontFamily={MONO}
            fontSize={9}
            offsetX={9}
            offsetY={4.5}
          />
        );
      }
    }
  }

  return (
    <Group>
      {rings}
      {roseTicks}
    </Group>
  );
}

function SeparationRing({ aircraft, projectPoint, pixelRadiusForNm }) {
  const { x, y } = projectPoint(aircraft.x_nm, aircraft.y_nm);
  const radius = pixelRadiusForNm(3);
  if (!Number.isFinite(radius) || radius < 6) return null;
  return (
    <Group>
      <Circle x={x} y={y} radius={radius} stroke={palette.fdbDim} strokeWidth={1} dash={[5, 6]} />
      <Text
        x={x + radius * 0.72}
        y={y - radius * 0.72}
        text="3"
        fill={palette.fdbDim}
        fontFamily={MONO}
        fontSize={9}
      />
    </Group>
  );
}

function ConflictLines({ records = [], byCallsign, projectPoint, color, dash }) {
  return (
    <>
      {records.map((record, index) => {
        const callsigns = record.aircraft || [record.a, record.b].filter(Boolean);
        if (callsigns.length < 2) return null;
        const a = byCallsign[callsigns[0]];
        const b = byCallsign[callsigns[1]];
        if (!a || !b) return null;
        const ptA = projectPoint(a.x_nm, a.y_nm);
        const ptB = projectPoint(b.x_nm, b.y_nm);
        const actual = Number(record.horizontal_nm);
        const required = Number(record.required_horizontal_nm);
        const separationLabel = Number.isFinite(actual)
          ? `${actual.toFixed(1)}${Number.isFinite(required) ? `/${required.toFixed(0)}` : ''}NM`
          : null;
        return (
          <Group key={`conflict-${index}`}>
            <Line
              points={[ptA.x, ptA.y, ptB.x, ptB.y]}
              stroke={color}
              strokeWidth={dash ? 1 : 1.5}
              dash={dash}
            />
            {separationLabel && (
              <Text
                x={(ptA.x + ptB.x) / 2 + 6}
                y={(ptA.y + ptB.y) / 2 - 12}
                text={separationLabel}
                fill={color}
                fontFamily={MONO}
                fontSize={10}
                fontStyle="600"
              />
            )}
          </Group>
        );
      })}
    </>
  );
}

function AirportLayout({ layout, activeRunwayId, projectPoint, pixelRadiusForNm, getScale }) {
  const aprons = layout.aprons || [];
  const taxiways = layout.taxiways || [];
  const stands = layout.stands || [];
  const runways = layout.runways || [];
  const scale = getScale();

  return (
    <Group>
      {/* Aprons */}
      {aprons.map((apron, i) => {
        const polygon = apron.polygon || [];
        if (polygon.length < 3) return null;
        const points = polygon.flatMap((pt) => {
          const projected = projectPoint(pt.x_nm, pt.y_nm);
          return [projected.x, projected.y];
        });
        const labelPt = projectPoint(polygon[0].x_nm, polygon[0].y_nm);

        return (
          <Group key={`apron-${i}`}>
            <Line
              points={points}
              closed
              fill="rgba(90, 105, 115, 0.06)"
              stroke={palette.map}
              strokeWidth={1}
            />
            {apron.id && (
              <Text
                x={labelPt.x + 6}
                y={labelPt.y - 6}
                text={apron.id}
                fill={palette.mapText}
                fontFamily={MONO}
                fontSize={9}
              />
            )}
          </Group>
        );
      })}

      {/* Taxiways */}
      {taxiways.map((tw, i) => {
        const twPoints = tw.points || [];
        if (twPoints.length < 2) return null;
        const points = twPoints.flatMap((pt) => {
          const projected = projectPoint(pt.x_nm, pt.y_nm);
          return [projected.x, projected.y];
        });
        const width = Math.max(1, Math.min(4, Number(tw.width_nm || 0.03) * scale));

        return (
          <Line
            key={`taxiway-${i}`}
            points={points}
            stroke={palette.map}
            strokeWidth={width}
            lineCap="round"
          />
        );
      })}

      {/* Runways */}
      {runways.map((rw) => {
        const ends = rw.ends || [];
        if (ends.length < 2) return null;
        const ptA = projectPoint(ends[0].x_nm, ends[0].y_nm);
        const ptB = projectPoint(ends[1].x_nm, ends[1].y_nm);

        return (
          <LayoutRunway
            key={`runway-${rw.id}`}
            runwayId={rw.id}
            ptA={ptA}
            ptB={ptB}
            widthNm={rw.width_nm}
            scale={scale}
            isActive={rw.id === activeRunwayId}
            pixelRadiusForNm={pixelRadiusForNm}
          />
        );
      })}

      {/* If layout runways list doesn't include the active runway, draw default */}
      {!runways.some((rw) => rw.id === activeRunwayId) && (
        <DefaultRunway
          runwayId={activeRunwayId}
          projectPoint={projectPoint}
          scale={scale}
          pixelRadiusForNm={pixelRadiusForNm}
        />
      )}

      {/* Stands */}
      {stands.map((stand, i) => {
        if (!stand?.position) return null;
        const { x, y } = projectPoint(stand.position.x_nm, stand.position.y_nm);

        return (
          <Group key={`stand-${i}`}>
            <Circle x={x} y={y} radius={3} fill={palette.map} stroke={palette.void} strokeWidth={1} />
            {stand.id && (
              <Text
                x={x + 7}
                y={y + 4}
                text={stand.id}
                fill={palette.mapText}
                fontFamily={MONO}
                fontSize={9}
              />
            )}
          </Group>
        );
      })}
    </Group>
  );
}

function DefaultRunway({ runwayId, projectPoint, scale, pixelRadiusForNm }) {
  const points = runwayWorldPoints(runwayId);
  if (points.length < 2) return null;
  const ptA = projectPoint(points[0].x_nm, points[0].y_nm);
  const ptB = projectPoint(points[1].x_nm, points[1].y_nm);
  return (
    <LayoutRunway
      runwayId={runwayId}
      ptA={ptA}
      ptB={ptB}
      widthNm={null}
      scale={scale}
      isActive={true}
      pixelRadiusForNm={pixelRadiusForNm}
    />
  );
}

// Extended runway centerline with 1nm tick marks: the final approach course.
function ExtendedCenterline({ ptA, ptB, pixelRadiusForNm }) {
  const lengthPx = Math.hypot(ptB.x - ptA.x, ptB.y - ptA.y);
  if (!lengthPx) return null;
  const dx = (ptB.x - ptA.x) / lengthPx;
  const dy = (ptB.y - ptA.y) / lengthPx;
  const px = -dy;
  const py = dx;

  const nmPx = pixelRadiusForNm(1);
  const extentPx = pixelRadiusForNm(EXTENDED_CENTERLINE_NM);
  const showTicks = nmPx > 8;

  const segments = [
    { origin: ptA, dirX: -dx, dirY: -dy },
    { origin: ptB, dirX: dx, dirY: dy }
  ];

  return (
    <Group>
      {segments.map((seg, i) => (
        <Group key={`centerline-${i}`}>
          <Line
            points={[
              seg.origin.x,
              seg.origin.y,
              seg.origin.x + seg.dirX * extentPx,
              seg.origin.y + seg.dirY * extentPx
            ]}
            stroke="rgba(168, 192, 208, 0.22)"
            strokeWidth={1}
            dash={[10, 8]}
          />
          {showTicks && Array.from({ length: EXTENDED_CENTERLINE_NM }, (_, n) => n + 1).map((nm) => {
            const cx = seg.origin.x + seg.dirX * nm * nmPx;
            const cy = seg.origin.y + seg.dirY * nm * nmPx;
            const half = nm % 5 === 0 ? 6 : 3;
            return (
              <Line
                key={`tick-${i}-${nm}`}
                points={[cx + px * half, cy + py * half, cx - px * half, cy - py * half]}
                stroke="rgba(168, 192, 208, 0.3)"
                strokeWidth={1}
              />
            );
          })}
        </Group>
      ))}
    </Group>
  );
}

function LayoutRunway({ runwayId, ptA, ptB, widthNm, scale, isActive, pixelRadiusForNm }) {
  const x1 = ptA.x;
  const y1 = ptA.y;
  const x2 = ptB.x;
  const y2 = ptB.y;

  const lengthPx = Math.hypot(x2 - x1, y2 - y1);
  if (!lengthPx) return null;

  const width = widthNm && Number(widthNm) > 0
    ? Math.max(4, Math.min(20, (Number(widthNm) * scale) / 2))
    : Math.max(8, Math.min(18, lengthPx * 0.05));

  const dx = (x2 - x1) / lengthPx;
  const dy = (y2 - y1) / lengthPx;
  const px = -dy;
  const py = dx;

  // Opposite runway name
  const opposite = () => {
    const match = String(runwayId || '').match(/\d{2}/);
    if (!match) return '';
    const n = Number(match[0]);
    if (!n) return '';
    const opp = ((n + 17) % 36) + 1;
    return String(opp).padStart(2, '0');
  };

  const points = [
    x1 + px * width, y1 + py * width,
    x2 + px * width, y2 + py * width,
    x2 - px * width, y2 - py * width,
    x1 - px * width, y1 - py * width
  ];

  return (
    <Group>
      {/* Final approach course off both runway ends */}
      {isActive && (
        <ExtendedCenterline ptA={ptA} ptB={ptB} pixelRadiusForNm={pixelRadiusForNm} />
      )}

      {/* Runway Fill/Border Rect */}
      <Line
        points={points}
        closed
        fill={isActive ? 'rgba(148, 173, 192, 0.07)' : 'rgba(100, 116, 139, 0.04)'}
        stroke={isActive ? palette.mapStrong : palette.map}
        strokeWidth={1}
      />

      {/* Centerline */}
      {lengthPx > 90 && (
        <Line
          points={[x1 + dx * 20, y1 + dy * 20, x2 - dx * 20, y2 - dy * 20]}
          stroke={isActive ? 'rgba(233, 241, 245, 0.4)' : 'rgba(203, 213, 225, 0.18)'}
          strokeWidth={1}
          dash={[18, 12]}
        />
      )}

      {/* Endmarks */}
      <Line
        points={[x1 + px * width * 0.8, y1 + py * width * 0.8, x1 - px * width * 0.8, y1 - py * width * 0.8]}
        stroke={isActive ? 'rgba(233, 241, 245, 0.7)' : 'rgba(203, 213, 225, 0.28)'}
        strokeWidth={2}
      />
      <Line
        points={[x2 + px * width * 0.8, y2 + py * width * 0.8, x2 - px * width * 0.8, y2 - py * width * 0.8]}
        stroke={isActive ? 'rgba(233, 241, 245, 0.7)' : 'rgba(203, 213, 225, 0.28)'}
        strokeWidth={2}
      />

      {/* Runway Labels */}
      <Text
        x={x1 - dx * 16}
        y={y1 - dy * 16}
        text={runwayId}
        fill={isActive ? '#e9f1f5' : 'rgba(203, 213, 225, 0.44)'}
        fontFamily={MONO}
        fontSize={11}
        fontStyle="700"
        align="center"
        offsetX={10} // approx text center centering
        offsetY={5}
      />
      <Text
        x={x2 + dx * 16}
        y={y2 + dy * 16}
        text={opposite()}
        fill={isActive ? '#e9f1f5' : 'rgba(203, 213, 225, 0.44)'}
        fontFamily={MONO}
        fontSize={11}
        fontStyle="700"
        align="center"
        offsetX={10}
        offsetY={5}
      />
    </Group>
  );
}

// STARS full datablock: bare monospace text with a leader line.
// Line 1: callsign. Line 2: altitude (hundreds) + climb/descent arrow + speed (tens),
// time-shared with assigned altitude / status code when present.
function Datablock({ aircraft, x, y, leader, color, isLanded, isConflict, isPredicted, blinkOn, altLine2Page }) {
  const callsign = String(aircraft.callsign || 'UNKNOWN').toUpperCase().slice(0, 10);

  const blockWidth = 66;
  const blockX = leader.dx > 0 ? x + 26 : x - 26 - blockWidth;
  const blockY = leader.dy > 0 ? y + 16 : y - 38;
  const leaderEndX = leader.dx > 0 ? blockX - 3 : blockX + blockWidth + 3;
  const leaderEndY = leader.dy > 0 ? blockY + 4 : blockY + 20;

  if (isLanded) {
    return (
      <Group>
        <Line points={[x + leader.dx * 7, y + leader.dy * 7, leaderEndX, leaderEndY]} stroke={palette.landed} strokeWidth={1} />
        <Text x={blockX} y={blockY + 6} text={callsign} fill={palette.landed} fontFamily={MONO} fontSize={11} />
      </Group>
    );
  }

  const arrow = verticalArrow(aircraft.vertical_rate_fpm);
  const primaryLine2 = `${altHundreds(aircraft.altitude_ft)}${arrow} ${speedTens(aircraft.speed_kt)}`;

  // Alternate page: assigned altitude (A-prefixed) and/or status code, when informative.
  const targetAlt = aircraft.target_altitude_ft == null ? NaN : Number(aircraft.target_altitude_ft);
  const currentAlt = Number(aircraft.altitude_ft);
  const hasAssigned = Number.isFinite(targetAlt) && Math.abs(targetAlt - currentAlt) > 200;
  const statusCode = STATUS_CODES[String(aircraft.status || '').toLowerCase()] || '';
  const altParts = [];
  if (hasAssigned) altParts.push(`A${altHundreds(targetAlt)}`);
  if (statusCode) altParts.push(statusCode);
  const alternateLine2 = altParts.join(' ');
  const line2 = altLine2Page === 1 && alternateLine2 ? alternateLine2 : primaryLine2;

  // Alert annunciator above the block: CA blinks for active conflicts,
  // steady for predicted; EM blinks for emergencies.
  const alertText = isConflict
    ? (blinkOn ? 'CA' : '')
    : aircraft.emergency
      ? (blinkOn ? 'EM' : '')
      : isPredicted
        ? 'CA'
        : '';
  const alertColor = isConflict ? palette.conflict : aircraft.emergency ? palette.caution : palette.caution;

  return (
    <Group>
      {/* Leader line */}
      <Line
        points={[x + leader.dx * 7, y + leader.dy * 7, leaderEndX, leaderEndY]}
        stroke={color}
        strokeWidth={1}
        opacity={0.8}
      />

      {alertText && (
        <Text
          x={blockX}
          y={blockY - 12}
          text={alertText}
          fill={alertColor}
          fontFamily={MONO}
          fontSize={10}
          fontStyle="700"
        />
      )}

      <Text
        x={blockX}
        y={blockY}
        text={callsign}
        fill={color}
        fontFamily={MONO}
        fontSize={11}
        fontStyle="600"
      />
      <Text
        x={blockX}
        y={blockY + 13}
        text={line2}
        fill={color}
        fontFamily={MONO}
        fontSize={11}
      />
    </Group>
  );
}

// Top-left System Status Area: clock, wind, visibility, runway, range, traffic count,
// plus conflict/emergency annunciator lines.
function SystemStatusArea({ event, weather, activeRunwayId, rangeNm, aircraftCount, blinkOn }) {
  if (!event) return null;

  const windDir = Number(weather.wind_dir_deg ?? weather.wind_direction_deg ?? weather.wind_from_deg);
  const windSpd = Number(weather.wind_speed_kt ?? weather.wind_speed_kts ?? weather.wind_kt);
  const wind = Number.isFinite(windSpd) && windSpd > 0 && Number.isFinite(windDir)
    ? `${String(Math.round(windDir)).padStart(3, '0')}/${String(Math.round(windSpd)).padStart(2, '0')}`
    : 'CALM';
  const vis = Number(weather.visibility_sm);

  const lines = [
    formatClock(event.time ?? event.state?.time_sec),
    `WND ${wind}${Number.isFinite(vis) ? `  VIS ${Math.round(vis)}SM` : ''}`,
    `RWY ${activeRunwayId}  RNG ${rangeNm}NM  ${aircraftCount}AC`
  ];

  const conflictPairs = (event.conflicts || [])
    .map((c) => (c.aircraft || [c.a, c.b].filter(Boolean)).join('/'))
    .filter(Boolean);
  const emergencies = Object.values(event.state?.aircraft || {})
    .filter((ac) => ac.emergency)
    .map((ac) => ac.callsign);

  return (
    <Group>
      {lines.map((line, i) => (
        <Text
          key={`ssa-${i}`}
          x={14}
          y={14 + i * 14}
          text={line}
          fill={palette.ssa}
          fontFamily={MONO}
          fontSize={11}
          fontStyle={i === 0 ? '700' : '500'}
        />
      ))}
      {conflictPairs.length > 0 && blinkOn && (
        <Text
          x={14}
          y={14 + lines.length * 14}
          text={`CA ${conflictPairs.join(' ')}`}
          fill={palette.conflict}
          fontFamily={MONO}
          fontSize={11}
          fontStyle="700"
        />
      )}
      {emergencies.length > 0 && (
        <Text
          x={14}
          y={14 + (lines.length + (conflictPairs.length > 0 ? 1 : 0)) * 14}
          text={`EM ${emergencies.join(' ')}`}
          fill={palette.caution}
          fontFamily={MONO}
          fontSize={11}
          fontStyle="700"
        />
      )}
    </Group>
  );
}

function PredictionOverlay({ aircraft, airportState = {}, projectPoint }) {
  if (!Number.isFinite(Number(aircraft.x_nm)) || !Number.isFinite(Number(aircraft.y_nm))) return null;
  const headingDeg = Number(aircraft.heading_deg);
  const speedKt = Number(aircraft.speed_kt);
  if (!Number.isFinite(headingDeg) || !Number.isFinite(speedKt)) return null;

  const weather = airportState.weather || {};
  const windSpeedKt = Number(weather.wind_speed_kt ?? weather.wind_speed_kts ?? weather.wind_kt ?? weather.speed_kt);
  const windFromDeg = Number(weather.wind_dir_deg ?? weather.wind_direction_deg ?? weather.wind_from_deg ?? weather.direction_deg);
  let windVector = null;
  if (Number.isFinite(windSpeedKt) && windSpeedKt > 0 && Number.isFinite(windFromDeg)) {
    const toHeadingDeg = (windFromDeg + 180) % 360;
    windVector = { speedKt: windSpeedKt, headingRad: ((toHeadingDeg - 90) * Math.PI) / 180 };
  }

  const projectPosition = (sec) => {
    const headingRad = ((headingDeg - 90) * Math.PI) / 180;
    const distanceNm = (speedKt * sec) / 3600;
    let dx = Math.cos(headingRad) * distanceNm;
    let dy = Math.sin(headingRad) * distanceNm;
    if (windVector) {
      const windDistanceNm = (windVector.speedKt * sec) / 3600;
      dx += Math.cos(windVector.headingRad) * windDistanceNm;
      dy += Math.sin(windVector.headingRad) * windDistanceNm;
    }
    return {
      x_nm: Number(aircraft.x_nm) + dx,
      y_nm: Number(aircraft.y_nm) + dy
    };
  };

  const points = [60, 120, 180].map(projectPosition);
  const all = [{ x_nm: Number(aircraft.x_nm), y_nm: Number(aircraft.y_nm) }, ...points];

  const linePoints = all.flatMap((pt) => {
    const projected = projectPoint(pt.x_nm, pt.y_nm);
    return [projected.x, projected.y];
  });

  return (
    <Group>
      {/* Path Line */}
      <Line
        points={linePoints}
        stroke={palette.fdbDim}
        strokeWidth={1}
        dash={[4, 5]}
      />

      {/* Markers */}
      {points.map((point, idx) => {
        const { x, y } = projectPoint(point.x_nm, point.y_nm);
        return (
          <Group key={`marker-${idx}`}>
            <Circle x={x} y={y} radius={2.5} fill={palette.fdbDim} />
            <Text
              x={x + 6}
              y={y - 6}
              text={`+${idx + 1}m`}
              fill={palette.fdbDim}
              fontFamily={MONO}
              fontSize={10}
            />
          </Group>
        );
      })}
    </Group>
  );
}
