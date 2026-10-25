import { SLACK_AFTER } from './state.js';

const DEMO_PROJECTS = ['quarterly-report', 'invoice-api', 'website-redesign'];
const DEMO_JOBS = ['Fix the invoice rounding bug', 'Draft the Q3 summary', 'Make the homepage less beige'];
const DEMO_DETAIL = {
  typing: ['invoice.py', 'summary.md', 'styles.css', 'server.py'], files: ['README.md', 'q3_numbers.csv', 'TODO.txt', 'config.yaml'],
  copier: ['npm test', 'pytest -q', 'git status', 'make build'], phone: ['fax etiquette', 'toner prices 1994', 'docs.python.org'],
  whiteboard: ['Plan'], delegate: ['Look into the flaky tests', 'Survey the CSS'],
};
const DEMO_TOOL = { typing: 'Edit', files: 'Read', copier: 'Bash', phone: 'WebSearch', whiteboard: 'TodoWrite', delegate: 'Agent' };
export class DemoShift {
  constructor() { this.agents = DEMO_PROJECTS.map((p, i) => this.make(`demo-${i}-${Date.now()}`, p, null, DEMO_JOBS[i], i * 1.5)); }
  make(id, project, parent, job, delay = 0) {
    const now = Date.now() / 1000;
    return { id, parent, temp: !!parent, project, cwd: `C:\\work\\${project}`, title: '', assignment: job, machine: project === 'invoice-api' ? 'Laptop' : 'Home PC', status: 'thinking', task: '', tool: '', detail: '',
      guess: false, since: now, last: now, tools: 0, log: [], _next: now + 3 + delay + Math.random() * 2, _end: parent ? now + 30 + Math.random() * 25 : Infinity };
  }
  set(a, status, task = '', detail = '') {
    const now = Date.now() / 1000;
    if (status !== a.status) a.since = now;
    Object.assign(a, { status, task, tool: DEMO_TOOL[task] || '', detail, last: now });
    if (task) { a.tools++; a.log = [...a.log, { t: now, text: `${DEMO_TOOL[task]} ${detail}` }].slice(-8); }
  }
  step() {
    const now = Date.now() / 1000;
    for (const a of [...this.agents]) {
      if (now < a._next) continue;
      if (now > a._end) {
        if (a.status !== 'idle') { this.set(a, 'idle'); a._next = now + 6; } else a._gone = true;
        continue;
      }
      const r = Math.random(), pick = (k) => DEMO_DETAIL[k][(Math.random() * DEMO_DETAIL[k].length) | 0];
      let wait = 3 + Math.random() * 5;
      if (r < 0.5) { const k = ['typing', 'typing', 'files', 'files', 'copier', 'phone', 'whiteboard'][(Math.random() * 7) | 0]; this.set(a, 'working', k, pick(k)); }
      else if (r < 0.63) this.set(a, 'thinking');
      else if (r < 0.7) this.set(a, 'replying');
      else if (r < 0.78) { this.set(a, 'waiting', 'copier', pick('copier')); wait = 7; }
      else if (r < 0.9 && !a.temp) { this.set(a, 'idle'); if (Math.random() < 0.5) a.last -= SLACK_AFTER + 5; wait = 14; }
      else if (!a.temp && this.agents.filter((x) => x.parent === a.id).length < 2) {
        this.set(a, 'working', 'delegate', pick('delegate'));
        this.agents.push(this.make(`demo-temp-${Math.random().toString(36).slice(2, 8)}`, a.project, a.id, a.detail, 4));
        wait = 12;
      } else this.set(a, 'thinking');
      a._next = now + wait;
    }
    this.agents = this.agents.filter((a) => !a._gone);
    return this.agents.map(({ _next, _end, _gone, ...rest }) => rest);
  }
}
