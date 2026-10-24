import { useEffect } from 'react';
import { MapContainer, TileLayer, Polyline, CircleMarker, Tooltip, useMap, useMapEvents } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import { CONFIG } from '../config.js';
import { daysText } from '../lib/view.js';

function Fit({ analyses }) {
  const map = useMap();
  useEffect(() => {
    const pts = analyses.flatMap((a) => a.route.coords);
    if (pts.length) map.fitBounds(pts, { padding: [40, 40] });
  }, [analyses, map]);
  return null;
}

function Clicks({ onPick }) {
  useMapEvents({ click: (e) => onPick(e.latlng) });
  return null;
}

// Solid line = chunk has recent evidence. Dashed thin line = no evidence, so the condition is unknown.
function RouteLines({ a, i, selected, onSelect }) {
  const on = i === selected;
  return a.chunks.map((c) => (
    <Polyline
      key={`${i}-${c.i}`}
      positions={c.coords}
      pathOptions={{
        color: CONFIG.routeColors[i],
        weight: on ? (c.observed ? 7 : 4) : 3,
        opacity: on ? 0.95 : 0.5,
        dashArray: c.observed ? null : '3 9',
      }}
      eventHandlers={{ click: () => onSelect(i) }}
    />
  ));
}

// Public reports and route evidence both carry id, lat, lng, label, status and age, so one marker serves both.
function ReportDot({ r, focus, onFocus, extra }) {
  const verified = r.status === 'verified' || r.basis === 'verified';
  const corroborated = r.basis === 'corroborated';
  return (
    <CircleMarker
      center={[r.lat, r.lng]}
      radius={focus ? 10 : 7}
      pathOptions={{
        color: focus ? '#0F7C8A' : r.counted === false ? '#5B6B7A' : '#1B2733',
        fillColor: '#1B2733',
        fillOpacity: verified ? 0.9 : corroborated ? 0.45 : 0.05,
        weight: focus ? 4 : 2,
        dashArray: r.simulated ? '2 3' : null,
      }}
      eventHandlers={{ click: () => onFocus && onFocus(r.id) }}
    >
      <Tooltip>
        {r.label}, {extra}, {daysText(r.age_days)}
        {r.simulated ? ' (simulated)' : ''}
      </Tooltip>
    </CircleMarker>
  );
}

// `loose` = saved public reports to show when there is no route yet (so a new report appears on the map at once).
export default function MapView({ analyses, selected, onSelect, pins, draft, onPick, loose = [], focusId = null, onFocus }) {
  const ordered = analyses.map((a, i) => ({ a, i })).sort((x, y) => (x.i === selected) - (y.i === selected));
  const current = analyses[selected];
  return (
    <MapContainer center={CONFIG.center} zoom={CONFIG.zoom} className="map">
      <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
      <Clicks onPick={onPick} />
      <Fit analyses={analyses} />
      {ordered.map(({ a, i }) => <RouteLines key={i} a={a} i={i} selected={selected} onSelect={onSelect} />)}
      {current && current.gaps.map((g) => (
        <CircleMarker key={`gap-${g.from_m}`} center={[g.lat, g.lng]} radius={12} pathOptions={{ color: '#5B6B7A', fillOpacity: 0, weight: 2, dashArray: '2 4' }}>
          <Tooltip>No recent evidence for {g.length_m} m. Unknown, not clear.</Tooltip>
        </CircleMarker>
      ))}
      {current && current.evidence.map((e) => <ReportDot key={e.id} r={e} focus={e.id === focusId} onFocus={onFocus} extra={e.basis} />)}
      {loose.map((r) => <ReportDot key={r.id} r={r} focus={r.id === focusId} onFocus={onFocus} extra={r.status} />)}
      {[['from', 'Start'], ['to', 'End']].map(([k, label]) =>
        pins[k] ? (
          <CircleMarker key={k} center={pins[k]} radius={9} pathOptions={{ color: '#1B2733', fillColor: '#fff', fillOpacity: 1, weight: 3 }}>
            <Tooltip permanent direction="top">{label}</Tooltip>
          </CircleMarker>
        ) : null,
      )}
      {draft && (
        <CircleMarker center={draft} radius={11} pathOptions={{ color: '#1B2733', fillColor: '#fff', fillOpacity: 0.7, weight: 3, dashArray: '4 4' }}>
          <Tooltip permanent direction="top">New report</Tooltip>
        </CircleMarker>
      )}
    </MapContainer>
  );
}
