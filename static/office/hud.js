import { esc, hash } from './utils.js';
import { intent } from './state.js';
import { age, formatAge, metadata, timestamp } from './metadata.js';

const $ = (s) => document.querySelector(s);

// The HUD owns stable roster nodes and focus; actions cross explicit callbacks.
export function createHud({
  actors, state, nowSec, onSelect: select, onSourceChange: setSource,
  onHomeCamera, onToggleLabels, onLiteChange,
}) {
  const live = state.live;
  // ---------------------------------------------------------------- HUD
  const MOOD_LABEL = { work: 'Working', think: 'Thinking', wait: 'Needs you', break: 'On break', slack: 'Slacking' };
  const rosterNodes = new Map();
  function statusView(actor) {
    const a = actor.info, now = nowSec();
    const meta = metadata(a, state.source, now, actors);
    // Never fill an observed record with fields held for animation.
    const record = meta.observed ? { ...meta.observed, guess: meta.observed.confidence === 'inferred' } : a;
    const view = intent(record, age(now, a.last) ?? 0, age(now, record.since) ?? 0);
    const labels = { working: 'Working', thinking: 'Thinking', replying: 'Replying', waiting: 'Needs you', idle: 'Idle' };
    if (meta.observed && !Object.hasOwn(labels, record.status)) return { meta, mood: 'unknown', label: 'Unknown', line: 'Unknown status' };
    if (meta.observed && record.status === 'idle') return { meta, mood: 'break', label: 'Idle', line: 'Idle' };
    return { ...view, meta, label: meta.observed ? labels[record.status] : MOOD_LABEL[view.mood] || 'Unknown' };
  }
  function orderedActors() {
    const all = [...actors.values()].filter((a) => !a.boss && !a.leaving && a.info);
    const tops = all.filter((a) => !a.info.temp), out = [];
    for (const t of tops) { out.push([t, 0]); for (const c of all.filter((a) => a.info.temp && a.info.parent === t.id)) out.push([c, 1]); }
    for (const a of all) if (!out.some(([x]) => x === a)) out.push([a, a.info.temp ? 1 : 0]);
    return out;
  }
  function renderRoster() {
    const rows = orderedActors();
    const list = $('#roster-list');
    const oldIds = [...list.querySelectorAll('.row')].map((row) => row.dataset.id);
    const active = list.contains(document.activeElement) ? document.activeElement.closest('.row') : null;
    const focusedId = active?.dataset.id, focusedIndex = oldIds.indexOf(focusedId), scrollTop = list.scrollTop;
    const present = new Set(rows.map(([actor]) => actor.id));
    for (const [id, node] of rosterNodes) {
      if (!present.has(id)) { node.item.remove(); rosterNodes.delete(id); }
    }
    list.querySelector('.empty-row')?.remove();
    const counts = { work: 0, think: 0, wait: 0, break: 0, slack: 0 };
    rows.forEach(([a, depth], index) => {
      let node = rosterNodes.get(a.id);
      if (!node) {
        const item = document.createElement('div'), button = document.createElement('button');
        item.setAttribute('role', 'listitem');
        button.type = 'button'; button.dataset.id = a.id;
        button.setAttribute('aria-controls', 'file');
        button.innerHTML = '<span class="dot"></span><span class="nm"><span class="name"></span><span class="pill">Temp</span></span><span class="st"></span><span class="sub"></span><span class="meta"><span class="origin"></span><span class="quality"></span><span class="seen"></span><span class="parent"></span></span>';
        item.append(button);
        node = { item, button }; rosterNodes.set(a.id, node);
      }
      const { item, button } = node, view = statusView(a);
      button.className = `row${depth ? ' child' : ''}${a.id === state.selectedId ? ' sel' : ''}`;
      button.setAttribute('aria-pressed', String(a.id === state.selectedId));
      button.querySelector('.dot').style.background = a.look.shirt;
      button.querySelector('.name').textContent = a.name;
      button.querySelector('.pill').hidden = !a.info.temp;
      button.querySelector('.st').className = `st ${view.mood}`;
      button.querySelector('.st').textContent = view.label;
      button.querySelector('.sub').textContent = `${a.info.project || 'unknown project'} · ${view.line}`;
      const { meta } = view;
      const origin = button.querySelector('.origin'), quality = button.querySelector('.quality'), seen = button.querySelector('.seen'), parent = button.querySelector('.parent');
      origin.textContent = `${meta.provider} · ${meta.computer}`;
      origin.title = origin.textContent;
      quality.textContent = `Evidence: ${meta.qualityText}`;
      seen.textContent = `${meta.seenLabel}: ${meta.seenText}`;
      parent.hidden = !a.info.temp && !a.info.parent && a.info.identity_quality !== 'unattributed';
      parent.textContent = `Parent: ${meta.parent}`;
      parent.title = parent.textContent;
      if (list.children[index] !== item) list.insertBefore(item, list.children[index] || null);
      counts[view.mood] = (counts[view.mood] || 0) + 1;
    });
    if (!rows.length) {
      const empty = document.createElement('div'); empty.className = 'empty-row';
      empty.textContent = state.source === 'demo' ? 'Staff arriving…' : 'Nobody on shift.';
      list.append(empty);
    }
    if (focusedId) {
      const nextId = [focusedId, ...oldIds.slice(focusedIndex + 1), ...oldIds.slice(0, focusedIndex).reverse()].find((id) => rosterNodes.has(id));
      const target = rosterNodes.get(nextId)?.button || $('#roster-min');
      if (document.activeElement !== target) target.focus({ preventScroll: true });
    }
    list.scrollTop = scrollTop;
    $('#stats').innerHTML = `<span><b>${rows.length}</b> on shift</span><span><b>${counts.work + counts.think}</b> working</span>` +
      `<span style="color:${counts.wait ? 'var(--wait)' : 'inherit'}"><b>${counts.wait}</b> need you</span>` +
      `<span class="long"><b>${counts.break + counts.slack}</b> idle</span>`;
  }
  $('#roster-list').addEventListener('click', (e) => { const row = e.target.closest('.row'); if (row) select(row.dataset.id, true); });
  $('#roster-min').addEventListener('click', () => { const r = $('#roster'); r.classList.toggle('min'); $('#roster-min').textContent = r.classList.contains('min') ? '□' : '_'; });

  function fmtTime(ts) { return timestamp(ts) === null ? '—' : new Date(ts * 1000 - (state.source === 'live' ? live.offset * 1000 : 0)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
  function field(label, key, value, attributes = '') { return `<dt>${esc(label)}</dt><dd data-field="${key}"${attributes}>${esc(value)}</dd>`; }
  function renderFile() {
    const actor = state.selectedId && actors.get(state.selectedId);
    const win = $('#file');
    if (!actor || actor.leaving || !actor.info) { win.classList.add('hidden'); return; }
    win.classList.remove('hidden');
    const a = actor.info, view = statusView(actor), { meta } = view;
    $('#file-title').textContent = `Personnel File: ${actor.name}`;
    $('#file-body').innerHTML = `
      <dl>
        ${field('Position', 'position', a.identity_quality === 'unattributed' ? 'Unattributed worker' : a.temp ? 'Temp' : 'Specialist')}
        ${field('Provider', 'provider', meta.provider)}
        ${field('Computer', 'computer', meta.computer)}
        ${field('Parent', 'parent', meta.parent)}
        ${field('Identity', 'identity', meta.identity)}
        <dt>Project</dt><dd>${esc(a.project || '—')}</dd>
        ${a.title ? `<dt>Session</dt><dd>${esc(a.title)}</dd>` : ''}
        <dt>Assignment</dt><dd>${esc(a.assignment || '—')}</dd>
        ${field('Right now', 'observed-status', view.line)}
        ${field('Evidence', 'quality', meta.qualityText)}
        ${field('Status source', 'source', meta.statusSource)}
        ${field('Correlation', 'correlation', meta.correlation)}
        ${field('For', 'duration', formatAge(age(nowSec(), meta.since)))}
        ${field(meta.seenLabel, 'last-seen', meta.seenText, meta.seenAt === null ? '' : ` data-timestamp="${meta.seenAt}"`)}
        ${field('Tool calls', 'tools', typeof a.tools === 'number' && Number.isFinite(a.tools) ? a.tools : '—')}
        <dt>Badge no.</dt><dd data-field="badge"><code>${hash(a.id).toString(16).padStart(8, '0').toUpperCase()}</code></dd>
      </dl>
      <div class="log sunken">${(a.log || []).slice().reverse().map((l) => `<div>${fmtTime(l.t)}  ${esc(l.text)}</div>`).join('') || '<div>No entries yet.</div>'}</div>`;
  }
  function focusRoster(id) {
    const row = rosterNodes.get(id)?.button || rosterNodes.values().next().value?.button;
    const target = $('#roster').classList.contains('min') ? $('#roster-min') : row || $('#roster-min');
    target.focus({ preventScroll: true });
  }
  function closeFile() {
    const id = state.selectedId;
    select(null);
    if (id) focusRoster(id);
  }
  $('#file-close').addEventListener('click', closeFile);

  function memo(text, alert = false) {
    const box = $('#memos');
    const el = document.createElement('div');
    el.className = 'memo' + (alert ? ' alert' : '');
    el.textContent = text;
    box.prepend(el);
    while (box.children.length > 5) box.lastChild.remove();
    setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 700); }, alert ? 14000 : 8000);
  }

  function updateEmpty() {
    const show = state.source === 'live' && live.connected && !state.emptyDismissed && !orderedActors().length;
    $('#empty').classList.toggle('hidden', !show);
  }
  document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => { b.closest('.win').classList.add('hidden'); if (b.closest('#empty')) state.emptyDismissed = true; }));
  $('#empty-demo').addEventListener('click', () => { state.emptyDismissed = true; setSource('demo'); });

  function setConn() {
    const el = $('#conn');
    el.className = 'sunken ' + (state.source === 'demo' ? 'demo' : live.connected ? 'live' : 'off');
    el.querySelector('span').textContent = state.source === 'demo' ? 'Demo shift' : live.connected ? (live.hooks ? 'Live · hooks on' : 'Live') : 'Server offline';
    $('#demo').classList.toggle('on', state.source === 'demo');
    $('#demo').setAttribute('aria-pressed', String(state.source === 'demo'));
    document.querySelector('[data-cmd="demo"]').textContent = state.source === 'demo' ? 'Back to live office' : 'Demo shift';
  }
  $('#demo').addEventListener('click', () => setSource(state.source === 'demo' ? 'live' : 'demo'));

  const startMenu = $('#startmenu');
  $('#start').addEventListener('click', (e) => { e.stopPropagation(); startMenu.classList.toggle('hidden'); $('#start').classList.toggle('on', !startMenu.classList.contains('hidden')); });
  document.addEventListener('click', (e) => { if (!startMenu.contains(e.target)) { startMenu.classList.add('hidden'); $('#start').classList.remove('on'); } });
  startMenu.addEventListener('click', (e) => {
    const cmd = e.target.closest('button')?.dataset.cmd;
    if (!cmd) return;
    startMenu.classList.add('hidden'); $('#start').classList.remove('on');
    if (cmd === 'camera') { select(null); onHomeCamera(); }
    if (cmd === 'labels') { const off = onToggleLabels(); e.target.textContent = off ? 'Show name tags' : 'Hide name tags'; }
    if (cmd === 'demo') setSource(state.source === 'demo' ? 'live' : 'demo');
    if (cmd === 'lite') {
      onLiteChange();
    }
    if (cmd === 'about') $('#about').classList.remove('hidden');
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeFile(); startMenu.classList.add('hidden'); } });

  const clockEl = $('#clock');
  function updateClock(d) { clockEl.textContent = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
  function fileHasFocus() { return $('#file').contains(document.activeElement); }

  return { renderRoster, renderFile, focusRoster, memo, updateEmpty, setConn, updateClock, fileHasFocus };
}
