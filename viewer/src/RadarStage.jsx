import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Stage, Layer, Rect, Line, Circle, Text, Arrow, Group } from 'react-konva';
import { humanize, runwayWorldPoints } from './useViewerState';

const palette = {
  arrival: '#00d8ff',
  departure: '#ffb02e',
  normal: '#95a9b5',
  emergency: '#ffffff',
  predicted: '#8fa7b5',
  conflict: '#ff4d6d',
  landed: '#46555f',
  runway: 'rgba(210, 226, 235, 0.5)',
  text: '#e9f1f5',
  mutedText: '#7e93a0',
  structure: 'rgba(148, 173, 192, 0.14)',
  structureStrong: 'rgba(175, 200, 218, 0.24)',
  void: '#060a0e',
  grid: 'rgba(148, 173, 192, 0.035)'
};

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

function formatFlightLevel(altitudeFt) {
  const altitude = Number(altitudeFt);
  if (!Number.isFinite(altitude)) return 'FL---';
  if (altitude >= 18000) return `FL${String(Math.round(altitude / 100)).padStart(3, '0')}`;
  return `${Math.round(altitude)}FT`;
}

function project(v, min, max, outMin, outMax) {
  if (max - min === 0) return (outMin + outMax) / 2;
  return outMin + ((v - min) / (max - min)) * (outMax - outMin);
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

  const projectPoint = useCallback((x_nm, y_nm) => {
    const view = getViewBounds();
    const x = project(x_nm, view.minX, view.maxX, 44, size.width - 44);
    const y = project(y_nm, view.minY, view.maxY, size.height - 44, 44);
    return { x, y };
  }, [getViewBounds, size]);

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
      const view = getViewBounds();
      const worldPerPxX = (view.maxX - view.minX) / (size.width - 88);
      const worldPerPxY = (view.maxY - view.minY) / (size.height - 88);

      setRadarView((prev) => ({
        ...prev,
        centerX: prev.centerX - dx * worldPerPxX,
        centerY: prev.centerY + dy * worldPerPxY
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

  const handleStageWheel = (e) => {
    e.evt.preventDefault();
  };

  // Pre-compiled derived properties
  const currentEvent = traceEvents[currentTickIndex];
  const airport = currentEvent?.state?.airport || {};
  const activeRunwayId = airport.active_runway || airport.runway_id || 'RWY';
  const layout = airport.layout || {};

  const byCallsign = useMemo(() => {
    return Object.fromEntries(interpolatedAircraft.map((ac) => [ac.callsign, ac]));
  }, [interpolatedAircraft]);

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

  // Pixel radius for a given world-space distance at the current zoom.
  const pixelRadiusForNm = useCallback((nm) => {
    const origin = projectPoint(0, 0);
    const offset = projectPoint(nm, 0);
    return Math.abs(offset.x - origin.x);
  }, [projectPoint]);

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

          {/* Grid lines */}
          <RadarGrid width={size.width} height={size.height} />

          {/* Range rings around the field */}
          <RangeRings projectPoint={projectPoint} pixelRadiusForNm={pixelRadiusForNm} />

          {/* Runway Header Info */}
          <Text
            x={18}
            y={18}
            text={`RUNWAY ${activeRunwayId}`}
            fill={palette.text}
            fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
            fontSize={12}
            fontStyle="600"
          />

          {/* Airport Layout (aprons, taxiways, stands, runways) */}
          {layout && (
            <AirportLayout
              layout={layout}
              activeRunwayId={activeRunwayId}
              projectPoint={projectPoint}
              getViewBounds={getViewBounds}
              size={size}
            />
          )}

          {/* Conflict Links */}
          {currentEvent?.predicted_conflicts && (
            <ConflictLines
              records={currentEvent.predicted_conflicts}
              byCallsign={byCallsign}
              projectPoint={projectPoint}
              color={palette.predicted}
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

          {/* Position-history trails */}
          {interpolatedAircraft.map((ac) => {
            if (ac.status === 'landed' || ac.status === 'exited_airspace') return null;
            const history = trails.get(ac.callsign);
            if (!history || history.length < 2) return null;
            const isDeparture = String(ac.role || '').toLowerCase() === 'departure';
            const trailColor = isDeparture ? '255, 176, 46' : '0, 216, 255';
            return (
              <Group key={`trail-${ac.callsign}`}>
                {history.slice(0, -1).map((point, idx) => {
                  const { x, y } = projectPoint(point.x_nm, point.y_nm);
                  const alpha = 0.08 + 0.32 * (idx / history.length);
                  return (
                    <Circle
                      key={`trail-${ac.callsign}-${idx}`}
                      x={x}
                      y={y}
                      radius={1.6}
                      fill={`rgba(${trailColor}, ${alpha.toFixed(2)})`}
                    />
                  );
                })}
              </Group>
            );
          })}

          {/* Separation ring around the selected aircraft */}
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
            const conflictSet = new Set((currentEvent?.conflicts || []).flatMap((c) => c.aircraft || []));
            const predictedSet = new Set((currentEvent?.predicted_conflicts || []).flatMap((c) => c.aircraft || []));
            
            const isLanded = ac.status === 'landed' || ac.status === 'exited_airspace';
            const isSelected = ac.callsign === selectedCallsign;
            const isHovered = ac.callsign === hoveredCallsign;

            const color = isLanded
              ? palette.landed
              : conflictSet.has(ac.callsign)
                ? palette.conflict
                : ac.emergency
                  ? palette.emergency
                  : ac.role === 'departure'
                    ? palette.departure
                    : palette.arrival;

            const { x, y } = projectPoint(ac.x_nm, ac.y_nm);

            return (
              <Group key={ac.callsign}>
                {/* Selected/Hovered target crosshair reticle */}
                {(isSelected || isHovered) && (
                  <Group>
                    <Circle x={x} y={y} radius={isSelected ? 13 : 10} stroke="#ffffff" strokeWidth={isSelected ? 2 : 1.5} />
                    <Line points={[x - 17, y, x - 9, y]} stroke="#ffffff" strokeWidth={isSelected ? 2 : 1.5} />
                    <Line points={[x + 9, y, x + 17, y]} stroke="#ffffff" strokeWidth={isSelected ? 2 : 1.5} />
                    <Line points={[x, y - 17, x, y - 9]} stroke="#ffffff" strokeWidth={isSelected ? 2 : 1.5} />
                    <Line points={[x, y + 9, x, y + 17]} stroke="#ffffff" strokeWidth={isSelected ? 2 : 1.5} />
                  </Group>
                )}

                {/* Target Dot */}
                <Circle
                  x={x}
                  y={y}
                  radius={isLanded ? 3.5 : 5}
                  stroke={color}
                  strokeWidth={1.25}
                  fill={isLanded ? color : palette.void}
                  shadowColor={color}
                  shadowBlur={isLanded ? 0 : 10}
                  shadowOpacity={0.55}
                />

                {/* Heading line vector */}
                {!isLanded && (
                  <Line
                    points={[
                      x,
                      y,
                      x + 28 * Math.cos(((Number(ac.heading_deg || 0) - 90) * Math.PI) / 180),
                      y + 28 * Math.sin(((Number(ac.heading_deg || 0) - 90) * Math.PI) / 180)
                    ]}
                    stroke={color}
                    strokeWidth={conflictSet.has(ac.callsign) ? 2 : 1.5}
                  />
                )}

                {/* Tag connecting line and tag info card */}
                <AircraftTagCard
                  aircraft={ac}
                  x={x}
                  y={y}
                  color={color}
                  isConflict={conflictSet.has(ac.callsign)}
                  isPredicted={predictedSet.has(ac.callsign)}
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

function RadarGrid({ width, height }) {
  const gridLines = [];
  for (let x = 80; x < width; x += 80) {
    gridLines.push(<Line key={`x-${x}`} points={[x, 0, x, height]} stroke={palette.grid} strokeWidth={1} />);
  }
  for (let y = 80; y < height; y += 80) {
    gridLines.push(<Line key={`y-${y}`} points={[0, y, width, y]} stroke={palette.grid} strokeWidth={1} />);
  }
  return <>{gridLines}</>;
}

function RangeRings({ projectPoint, pixelRadiusForNm }) {
  const center = projectPoint(0, 0);
  return (
    <Group>
      {[10, 20, 30, 40].map((nm) => {
        const radius = pixelRadiusForNm(nm);
        if (!Number.isFinite(radius) || radius < 12) return null;
        return (
          <Group key={`ring-${nm}`}>
            <Circle x={center.x} y={center.y} radius={radius} stroke="rgba(148, 173, 192, 0.07)" strokeWidth={1} />
            <Text
              x={center.x + 4}
              y={center.y - radius + 4}
              text={`${nm}`}
              fill="rgba(148, 173, 192, 0.25)"
              fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
              fontSize={9}
            />
          </Group>
        );
      })}
    </Group>
  );
}

function SeparationRing({ aircraft, projectPoint, pixelRadiusForNm }) {
  const { x, y } = projectPoint(aircraft.x_nm, aircraft.y_nm);
  const radius = pixelRadiusForNm(3);
  if (!Number.isFinite(radius) || radius < 6) return null;
  return (
    <Group>
      <Circle x={x} y={y} radius={radius} stroke="rgba(0, 216, 255, 0.28)" strokeWidth={1} dash={[5, 6]} />
      <Text
        x={x + radius * 0.72}
        y={y - radius * 0.72}
        text="3nm"
        fill="rgba(0, 216, 255, 0.45)"
        fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
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
          ? `${actual.toFixed(1)}${Number.isFinite(required) ? `/${required.toFixed(0)}` : ''}nm`
          : null;
        return (
          <Group key={`conflict-${index}`}>
            <Line
              points={[ptA.x, ptA.y, ptB.x, ptB.y]}
              stroke={color}
              strokeWidth={dash ? 1 : 2}
              dash={dash}
            />
            {separationLabel && (
              <Text
                x={(ptA.x + ptB.x) / 2 + 6}
                y={(ptA.y + ptB.y) / 2 - 12}
                text={separationLabel}
                fill={color}
                fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
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

function AirportLayout({ layout, activeRunwayId, projectPoint, getViewBounds, size }) {
  const aprons = layout.aprons || [];
  const taxiways = layout.taxiways || [];
  const stands = layout.stands || [];
  const runways = layout.runways || [];

  const view = getViewBounds();
  const scale = Math.min((size.width - 88) / (view.maxX - view.minX), (size.height - 88) / (view.maxY - view.minY));

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
              fill="rgba(80, 96, 105, 0.07)"
              stroke="rgba(148, 173, 192, 0.12)"
              strokeWidth={1}
            />
            {apron.id && (
              <Text
                x={labelPt.x + 6}
                y={labelPt.y - 6}
                text={apron.id}
                fill="rgba(148, 173, 192, 0.4)"
                fontFamily="JetBrains Mono, Fira Code, Consolas, monospace"
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
            stroke="rgba(148, 173, 192, 0.14)"
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
          />
        );
      })}
      
      {/* If layout runways list doesn't include the active runway, draw default */}
      {!runways.some((rw) => rw.id === activeRunwayId) && (
        <DefaultRunway
          runwayId={activeRunwayId}
          projectPoint={projectPoint}
          scale={scale}
        />
      )}

      {/* Stands */}
      {stands.map((stand, i) => {
        if (!stand?.position) return null;
        const { x, y } = projectPoint(stand.position.x_nm, stand.position.y_nm);

        return (
          <Group key={`stand-${i}`}>
            <Circle x={x} y={y} radius={4} fill="rgba(148, 173, 192, 0.32)" stroke="#060a0e" strokeWidth={1} />
            {stand.id && (
              <Text
                x={x + 7}
                y={y + 4}
                text={stand.id}
                fill="rgba(148, 173, 192, 0.44)"
                fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
                fontSize={9}
              />
            )}
          </Group>
        );
      })}
    </Group>
  );
}

function DefaultRunway({ runwayId, projectPoint, scale }) {
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
    />
  );
}

function LayoutRunway({ runwayId, ptA, ptB, widthNm, scale, isActive }) {
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
      {/* Runway Fill/Border Rect */}
      <Line
        points={points}
        closed
        fill={isActive ? 'rgba(148, 173, 192, 0.07)' : 'rgba(100, 116, 139, 0.04)'}
        stroke={isActive ? 'rgba(210, 226, 235, 0.38)' : 'rgba(148, 163, 184, 0.16)'}
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
        fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
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
        fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
        fontSize={11}
        fontStyle="700"
        align="center"
        offsetX={10}
        offsetY={5}
      />
    </Group>
  );
}

function AircraftTagCard({ aircraft, x, y, color, isConflict, isPredicted }) {
  const role = String(aircraft.role || '').toLowerCase();
  const isDeparture = role === 'departure';
  const labelX = isDeparture ? x + 16 : x - 118;
  const labelY = y - 28;
  const tagWidth = 102;
  const tagHeight = 45;

  const callsign = String(aircraft.callsign || 'UNKNOWN').toUpperCase();
  const altitude = formatFlightLevel(aircraft.altitude_ft);
  const speed = `${Math.round(Number(aircraft.speed_kt) || 0)}KT`;
  const heading = `${String(Math.round(Number(aircraft.heading_deg) || 0)).padStart(3, '0')}H`;

  return (
    <Group>
      {/* Connector line */}
      <Line
        points={[x, y, isDeparture ? labelX - 5 : labelX + tagWidth + 5, labelY + 10]}
        stroke={color}
        strokeWidth={1}
      />

      {/* Tag Card Background */}
      <Rect
        x={labelX}
        y={labelY}
        width={tagWidth}
        height={tagHeight}
        cornerRadius={4}
        fill="rgba(4, 7, 10, 0.84)"
        stroke={isConflict ? palette.conflict : color}
        strokeWidth={1}
        opacity={isConflict ? 1 : 0.92}
      />

      {/* Alert strip */}
      <Rect
        x={labelX}
        y={labelY}
        width={3}
        height={tagHeight}
        cornerRadius={[4, 0, 0, 4]}
        fill={isConflict ? palette.conflict : color}
      />

      {/* Callsign */}
      <Text
        x={labelX + 8}
        y={labelY + 5}
        text={callsign.slice(0, 10)}
        fill={isConflict ? '#ffffff' : color}
        fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
        fontSize={10}
        fontStyle="600"
      />

      {/* Altitude & Speed */}
      <Text
        x={labelX + 8}
        y={labelY + 19}
        text={`${altitude}  ${speed}`}
        fill={isPredicted && !isConflict ? '#dbe8ee' : palette.text}
        fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
        fontSize={10}
        fontStyle="500"
      />

      {/* Heading & Role */}
      <Text
        x={labelX + 8}
        y={labelY + 32}
        text={`${heading}  ${role ? role.slice(0, 3).toUpperCase() : 'UNK'}`}
        fill={palette.mutedText}
        fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
        fontSize={10}
      />
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
        stroke="rgba(233, 241, 245, 0.7)"
        strokeWidth={1}
        dash={[4, 5]}
      />

      {/* Markers */}
      {points.map((point, idx) => {
        const { x, y } = projectPoint(point.x_nm, point.y_nm);
        return (
          <Group key={`marker-${idx}`}>
            <Circle x={x} y={y} radius={2.8} fill="rgba(233, 241, 245, 0.9)" />
            <Text
              x={x + 6}
              y={y - 6}
              text={`+${idx + 1}m`}
              fill="rgba(233, 241, 245, 0.9)"
              fontFamily='"JetBrains Mono", "Fira Code", Consolas, monospace'
              fontSize={10}
            />
          </Group>
        );
      })}
    </Group>
  );
}
