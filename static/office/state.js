// Snapshot, source and selection state; no scene or DOM dependencies.
export function createState() {
  const state = {
    source: 'live',
    live: { agents: [], offset: 0, connected: false, everConnected: false, hooks: false },
    selectedId: null, follow: false, emptyDismissed: false,
  };
  state.nowSec = () => Date.now() / 1000 + (state.source === 'live' ? state.live.offset : 0);
  state.acceptSnapshot = (d) => {
    state.live.offset = d.now - Date.now() / 1000;
    state.live.agents = d.agents || [];
    state.live.hooks = !!d.hooks;
    state.live.connected = true;
  };
  return state;
}

export const SLACK_AFTER = 120;  // seconds idle before a coffee break turns into Mines

export function intent(a, idleFor, sinceFor) {
  const d = a.detail;
  switch (a.status) {
    case 'working':
      if (a.task === 'files') return { where: 'files', anim: 'read', mood: 'work', line: d ? `Filing: ${d}` : 'Digging through the files' };
      if (a.task === 'copier') return { where: 'copier', anim: 'copy', mood: 'work', line: d ? `Copier: ${d}` : 'Running a copy job' };
      if (a.task === 'phone') return { where: 'phone', anim: 'phone', mood: 'work', line: d ? `On the phone: ${d}` : 'On the phone' };
      if (a.task === 'whiteboard') return { where: 'whiteboard', anim: 'write', mood: 'work', line: 'Updating the whiteboard' };
      if (a.task === 'delegate') return sinceFor < 9
        ? { where: 'delegate', anim: 'talk', mood: 'work', line: d ? `Briefing a temp: ${d}` : 'Briefing a temp' }
        : { where: 'desk', anim: 'think', mood: 'work', bubble: '…', line: 'Waiting on the temps' };
      return { where: 'desk', anim: 'type', mood: 'work', line: d ? `Typing up ${d}` : 'Typing away' };
    case 'replying': return { where: 'desk', anim: 'type', mood: 'work', line: 'Writing a memo' };
    case 'waiting': return { where: 'desk', anim: 'wait', mood: 'wait', bubble: '!', alert: true, line: a.guess ? `Needs sign-off? ${a.tool || ''}`.trim() : `Needs your sign-off${a.tool ? ': ' + a.tool : ''}` };
    case 'idle': return idleFor < SLACK_AFTER
      ? { where: 'coffee', anim: 'coffee', mood: 'break', line: 'On a coffee break' }
      : { where: 'desk', anim: 'slack', mood: 'slack', line: 'Playing Mines' };
    default: return { where: 'desk', anim: 'think', mood: 'think', bubble: '…', line: 'Thinking it over' };
  }
}

