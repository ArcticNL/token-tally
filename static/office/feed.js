// Transport only. Source switching and visible state live in the composition root.
export function connectFeed({ onOpen, onSnapshot, onError }) {
  let es;
  try { es = new EventSource('/events'); } catch { return; }
  es.onopen = onOpen;
  es.onmessage = (e) => onSnapshot(JSON.parse(e.data));
  es.onerror = onError;
  return es;
}

export async function fetchNames() {
  let cast;
  try {
    const r = await fetch('names', { cache: 'no-store' });
    if (!r.ok) return;
    cast = await r.json();
  } catch { return; } // offline, or no server (opened as a file): keep the current names
  return cast;
}
