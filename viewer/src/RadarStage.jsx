import { useEffect, useMemo, useRef, useState } from 'react';
import { Arrow, Group, Layer, Line, Rect, Stage, Text } from 'react-konva';

const palette = {
  void: '#0b0f12',
  grid: 'rgba(180, 198, 210, 0.045)',
  text: '#f4f7f8',
  muted: '#8ea0aa',
  conflict: '#ff2f55'
};

const emptyFrame = {
  width: 900,
  height: 560,
  runway: 'RWY',
  targets: [],
  conflicts: [],
  predictedConflicts: []
};

export default function RadarStage() {
  const containerRef = useRef(null);
  const [size, setSize] = useState({ width: 900, height: 560 });
  const [frame, setFrame] = useState(emptyFrame);

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

  useEffect(() => {
    const onFrame = (event) => setFrame(event.detail || emptyFrame);
    window.addEventListener('atc:radar-frame', onFrame);
    return () => window.removeEventListener('atc:radar-frame', onFrame);
  }, []);

  const scale = useMemo(() => {
    const sx = size.width / Math.max(1, frame.width || size.width);
    const sy = size.height / Math.max(1, frame.height || size.height);
    return { sx, sy };
  }, [frame.height, frame.width, size.height, size.width]);

  const forward = (name) => (event) => {
    window.atcRadarInput?.[name]?.(event.evt);
  };

  return (
    <div ref={containerRef} className="konva-radar" aria-hidden="true">
      <Stage
        width={size.width}
        height={size.height}
        onMouseMove={forward('move')}
        onMouseLeave={forward('leave')}
        onClick={forward('click')}
        onWheel={forward('wheel')}
        onMouseDown={forward('down')}
      >
        <Layer listening={false}>
          <Rect x={0} y={0} width={size.width} height={size.height} fill={palette.void} />
          <RadarGrid width={size.width} height={size.height} />
          <Text x={18} y={18} text={`RUNWAY ${frame.runway || 'RWY'}`} fill={palette.text} fontFamily="JetBrains Mono, Fira Code, Consolas, monospace" fontSize={12} fontStyle="600" />
          <ConflictLines records={frame.predictedConflicts} targets={frame.targets} scale={scale} predicted />
          <ConflictLines records={frame.conflicts} targets={frame.targets} scale={scale} />
          {frame.targets.map((target) => (
            <AircraftTarget key={target.callsign} target={target} scale={scale} />
          ))}
        </Layer>
      </Stage>
    </div>
  );
}

function RadarGrid({ width, height }) {
  const lines = [];
  for (let x = 160; x < width; x += 160) lines.push(<Line key={`x-${x}`} points={[x, 0, x, height]} stroke={palette.grid} strokeWidth={1} />);
  for (let y = 160; y < height; y += 160) lines.push(<Line key={`y-${y}`} points={[0, y, width, y]} stroke={palette.grid} strokeWidth={1} />);
  return lines;
}

function ConflictLines({ records = [], targets = [], scale, predicted = false }) {
  const byCallsign = new Map(targets.map((target) => [target.callsign, target]));
  return records.map((record, index) => {
    const callsigns = Array.isArray(record.aircraft) ? record.aircraft : [];
    const a = byCallsign.get(callsigns[0]);
    const b = byCallsign.get(callsigns[1]);
    if (!a || !b) return null;
    return (
      <Line
        key={`${predicted ? 'p' : 'c'}-${index}`}
        points={[a.x * scale.sx, a.y * scale.sy, b.x * scale.sx, b.y * scale.sy]}
        stroke={predicted ? palette.muted : palette.conflict}
        strokeWidth={predicted ? 1 : 2}
        dash={predicted ? [6, 5] : undefined}
      />
    );
  });
}

function AircraftTarget({ target, scale }) {
  const x = target.x * scale.sx;
  const y = target.y * scale.sy;
  const color = target.color || palette.muted;
  const isDeparture = target.role === 'departure';
  const labelX = isDeparture ? x + 16 : x - 118;
  const labelY = y - 28;
  const headingRad = ((Number(target.headingDeg || 0) - 90) * Math.PI) / 180;
  const vectorX = x + 28 * Math.cos(headingRad);
  const vectorY = y + 28 * Math.sin(headingRad);

  return (
    <Group>
      {(target.selected || target.hovered) && (
        <Group>
          <Line points={[x - 17, y, x - 9, y]} stroke="#ffffff" strokeWidth={1.5} />
          <Line points={[x + 9, y, x + 17, y]} stroke="#ffffff" strokeWidth={1.5} />
          <Line points={[x, y - 17, x, y - 9]} stroke="#ffffff" strokeWidth={1.5} />
          <Line points={[x, y + 9, x, y + 17]} stroke="#ffffff" strokeWidth={1.5} />
        </Group>
      )}
      <Arrow points={[x, y, vectorX, vectorY]} stroke={color} fill={color} strokeWidth={target.conflict ? 2 : 1.5} pointerLength={7} pointerWidth={6} />
      <Rect x={x - 5} y={y - 5} width={10} height={10} stroke={color} strokeWidth={1.25} fill={palette.void} cornerRadius={5} />
      <Line points={[x, y, isDeparture ? labelX - 5 : labelX + 107, labelY + 10]} stroke={color} strokeWidth={1} />
      <Rect x={labelX} y={labelY} width={102} height={45} fill="rgba(3, 5, 6, 0.76)" stroke={target.conflict ? palette.conflict : color} strokeWidth={1} />
      <Rect x={labelX} y={labelY} width={3} height={45} fill={target.conflict ? palette.conflict : color} />
      <Text x={labelX + 8} y={labelY + 5} text={target.callsign} fill={target.conflict ? '#ffffff' : color} fontFamily="JetBrains Mono, Fira Code, Consolas, monospace" fontSize={10} fontStyle="600" />
      <Text x={labelX + 8} y={labelY + 19} text={`${target.altitude}  ${target.speed}`} fill={palette.text} fontFamily="JetBrains Mono, Fira Code, Consolas, monospace" fontSize={10} />
      <Text x={labelX + 8} y={labelY + 32} text={`${target.heading}  ${target.roleLabel}`} fill={palette.muted} fontFamily="JetBrains Mono, Fira Code, Consolas, monospace" fontSize={10} />
    </Group>
  );
}
