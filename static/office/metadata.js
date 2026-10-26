// Read only the public snapshot contract; opaque IDs never supply missing labels.
const text = (value) => typeof value === 'string' && value.trim() ? value : '';
const label = (values, key, fallback) => typeof key === 'string' && Object.hasOwn(values, key) ? values[key] : fallback;
export const timestamp = (value) => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
export function age(now, then) {
  return timestamp(then) === null || !Number.isFinite(now) ? null : Math.max(0, now - then);
}
export function formatAge(seconds) {
  if (seconds === null || !Number.isFinite(seconds)) return 'Unknown';
  const sec = Math.max(0, Math.round(seconds));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}
export function metadata(a, source, now, actors) {
  const demo = source === 'demo';
  const observed = a.observed && typeof a.observed === 'object' && !Array.isArray(a.observed) ? a.observed : null;
  const confidence = observed ? observed.confidence : a.guess === true ? 'inferred' : null;
  const quality = demo ? 'Simulated' : label({ observed: 'Observed', inferred: 'Inferred' }, confidence, 'Unknown');
  const limitations = demo ? [] : [observed?.correlation === 'ambiguous' && 'Ambiguous matching', observed?.limited === true && 'Limited history'].filter(Boolean);
  const provider = demo ? 'Demo' : label({ claude: 'Claude Code', codex: 'Codex' }, a.provider, text(a.provider) || 'Unknown provider');
  const computer = demo ? 'Simulated computer' : text(a.machine) || 'Unknown computer';
  const parentActor = a.parent && a.parent !== a.id && actors.get(a.parent);
  const parent = parentActor?.info && !parentActor.leaving && !parentActor.boss ? parentActor.name
    : a.parent ? 'Parent not on shift' : a.temp || a.identity_quality === 'unattributed' ? 'Unknown parent' : 'Not applicable';
  const seen = age(now, a.last);
  return {
    observed, provider, computer, parent, quality, limitations,
    qualityText: [quality, ...limitations].join(' · '),
    identity: demo ? 'Simulated actor' : label({ scoped: 'Scoped identity', legacy: 'Legacy identity', unattributed: 'Unattributed actor' }, a.identity_quality, 'Unknown identity'),
    statusSource: demo ? 'Simulation' : label({ hook: 'Hook', transcript: 'Transcript' }, observed?.source, text(observed?.source) || 'Unknown'),
    correlation: demo ? 'Simulated' : label({ native: 'Native turn', partial: 'Partial correlation', ambiguous: 'Ambiguous correlation' }, observed?.correlation, 'Unknown'),
    seenLabel: demo ? 'Demo activity' : observed ? 'Last seen' : 'Last activity',
    seenText: seen === null ? 'Unknown' : `${formatAge(seen)} ago`,
    seenAt: timestamp(a.last),
    since: observed ? observed.since : a.since,
  };
}
