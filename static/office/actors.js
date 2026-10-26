import * as THREE from 'three';
import { CSS2DObject } from '../vendor/CSS2DRenderer.js';
import { esc, hash, mulberry32 } from './utils.js';
import { intent } from './state.js';

const MIN_STAY = 4;       // seconds a worker stays at a station before walking off again
const SPEED = 2.6;        // metres per second

// Actors own staff identities, desk reservations, poses and departure lifecycles.
export function createActors({
  scene, box, cyl, layout, setDeskLabel, clearDeskScreen, setEmployeeOfMonth,
  onSelect, onMemo, onDeparted,
}) {
  const { deskSlots, ST, SPOTS, westVia, breakVia } = layout;
  // ---------------------------------------------------------------- people
  // The built-in staff. A names.txt next to server.py replaces them live (see loadNames).
  const DEFAULT_NAMES = ['Gary', 'Linda', 'Trevor', 'Deborah', 'Kevin', 'Sandra', 'Nigel', 'Brenda', 'Darren', 'Tracy', 'Keith', 'Janet', 'Malcolm',
    'Sharon', 'Clive', 'Wendy', 'Barry', 'Denise', 'Graham', 'Lorraine', 'Derek', 'Pauline', 'Russell', 'Yvonne', 'Colin', 'Beverley'];
  let NAMES = DEFAULT_NAMES;
  const SHIRTS = ['#9db7d8', '#e2c46b', '#5f9e8f', '#a4504f', '#e2dccb', '#8a7fb3', '#e39b7b', '#6f8f4e', '#f0e6d2', '#4f6f9a', '#c77fa0'];
  const PANTS = ['#2d3346', '#4a4d55', '#6b5a43', '#3b3b3b', '#7a6d58'];
  const TIES = ['#7a1f2b', '#1f3a7a', '#2f6b3a', '#b08a2a', '#5a2a6b'];
  const SKIN = ['#f1c9a5', '#e0ac7e', '#c68a5e', '#8d5a3b', '#f5d6bd', '#a8714a'];
  const HAIR = ['#2b1d14', '#5a3a22', '#a8763e', '#d9c27a', '#7d7d7d', '#151515', '#8a3b1c'];
  const lensMat = new THREE.MeshLambertMaterial({ color: '#cfe8f2', transparent: true, opacity: 0.35 });

  const OVERALLS = '#3d6f9f', CAP = '#c8312f';
  function makePerson(seed, boss, temp, crew = false) {
    const r = mulberry32(seed);
    const pick = (a) => a[Math.floor(r() * a.length)];
    const skin = boss ? '#fbe4d3' : pick(SKIN);
    const hair = boss ? '#b4b4b4' : pick(HAIR);
    const shirt = boss ? '#f4f1ea' : crew ? OVERALLS : pick(SHIRTS);
    const jacket = boss ? '#2b2f3d' : crew ? null : r() < 0.2 ? pick(['#4a3b2f', '#3e4a5c', '#5b2f3a']) : null;
    const pants = boss ? '#2b2f3d' : crew ? OVERALLS : pick(PANTS);
    const tie = boss ? '#9b1d24' : crew ? null : r() < 0.6 ? pick(TIES) : null;
    const top = jacket || shirt;
    const shortSleeves = !jacket && !crew && r() < 0.35;
    const root = new THREE.Group(), body = new THREE.Group();
    root.add(body);
    const hips = [], knees = [], shoulders = [], elbows = [];
    for (const side of [-1, 1]) {
      const hip = new THREE.Group(); hip.position.set(side * 0.13, 0.85, 0); body.add(hip);
      box(0.19, 0.43, 0.21, pants, 0, -0.43, 0, hip);
      const knee = new THREE.Group(); knee.position.y = -0.43; hip.add(knee);
      box(0.18, 0.42, 0.2, pants, 0, -0.42, 0, knee);
      box(0.2, 0.08, 0.3, '#1d1b1a', 0, -0.43, 0.04, knee);
      hips.push(hip); knees.push(knee);
    }
    const upper = new THREE.Group(); upper.position.y = 0.85; body.add(upper);
    box(0.56, 0.62, 0.32, top, 0, 0, 0, upper);
    box(0.57, 0.07, 0.33, '#2a2522', 0, 0, 0, upper);
    if (jacket) box(0.2, 0.5, 0.02, shirt, 0, 0.12, 0.161, upper);
    if (tie) { box(0.09, 0.4, 0.02, tie, 0, 0.16, 0.175, upper); box(0.11, 0.07, 0.03, tie, 0, 0.54, 0.175, upper); }
    box(0.32, 0.05, 0.34, jacket ? shirt : top, 0, 0.6, 0, upper);
    if (crew) {
      // Overall straps and a bib pocket instead of an office badge.
      for (const s of [-1, 1]) box(0.07, 0.62, 0.02, '#2b557c', s * 0.14, 0, 0.165, upper);
      box(0.24, 0.16, 0.02, '#2b557c', 0, 0.2, 0.168, upper);
    } else {
      box(0.11, 0.13, 0.02, temp ? '#ffd23a' : '#ffffff', 0.16, 0.34, 0.17, upper);
    }
    for (const side of [-1, 1]) {
      const sh = new THREE.Group(); sh.position.set(side * 0.36, 0.56, 0); upper.add(sh);
      box(0.15, 0.31, 0.17, top, 0, -0.31, 0, sh);
      const el = new THREE.Group(); el.position.y = -0.31; sh.add(el);
      box(0.14, 0.27, 0.16, shortSleeves ? skin : top, 0, -0.27, 0, el);
      box(0.12, 0.11, 0.13, skin, 0, -0.38, 0, el);
      shoulders.push(sh); elbows.push(el);
    }
    const head = new THREE.Group(); head.position.y = 0.62; upper.add(head);
    box(0.14, 0.1, 0.14, skin, 0, 0, 0, head);
    box(0.42, 0.44, 0.4, skin, 0, 0.08, 0, head);
    for (const s of [-1, 1]) {
      box(0.06, 0.07, 0.02, '#1a1a1a', s * 0.09, 0.3, 0.2, head);
      box(0.04, 0.1, 0.08, skin, s * 0.22, 0.25, 0, head);
    }
    box(0.06, 0.09, 0.06, skin, 0, 0.19, 0.22, head);
    box(0.13, 0.025, 0.02, '#6e3428', 0, 0.14, 0.2, head);
    const style = boss || crew ? 0 : Math.floor(r() * 5);
    if (crew) { box(0.46, 0.09, 0.44, CAP, 0, 0.53, 0, head); box(0.32, 0.03, 0.2, CAP, 0, 0.53, 0.28, head); } // cap and peak
    if (style === 0) {
      box(0.44, 0.09, 0.42, hair, 0, 0.5, 0, head); box(0.44, 0.3, 0.06, hair, 0, 0.22, -0.2, head);
      if (boss) for (const s of [-1, 1]) box(0.03, 0.2, 0.24, hair, s * 0.215, 0.32, -0.07, head); // grey at the temples
    }
    if (style === 1) { box(0.44, 0.18, 0.42, hair, 0, 0.48, 0, head); box(0.44, 0.3, 0.06, hair, 0, 0.22, -0.2, head); }
    if (style === 2) { for (const s of [-1, 1]) box(0.03, 0.16, 0.3, hair, s * 0.215, 0.28, -0.04, head); box(0.44, 0.2, 0.05, hair, 0, 0.24, -0.2, head); }
    if (style === 3) { box(0.45, 0.1, 0.43, hair, 0, 0.5, 0, head); box(0.46, 0.62, 0.08, hair, 0, -0.04, -0.21, head); for (const s of [-1, 1]) box(0.05, 0.36, 0.32, hair, s * 0.23, 0.18, -0.04, head); }
    if (style === 4) { box(0.52, 0.24, 0.5, hair, 0, 0.42, -0.01, head); box(0.52, 0.4, 0.12, hair, 0, 0.1, -0.2, head); for (const s of [-1, 1]) box(0.07, 0.28, 0.38, hair, s * 0.25, 0.2, -0.02, head); }
    if ((boss || r() < 0.22) && style < 3) box(0.18, 0.04, 0.03, hair, 0, 0.165, 0.215, head);
    if (boss || r() < 0.35) {
      // Rims round each eye (open in the middle, so the eyes show), a bridge, and arms back to the ears.
      const rim = '#1e1e1e', t = 0.018, w = 0.14, h = 0.1, y0 = 0.285, z = 0.217;
      for (const s of [-1, 1]) {
        const cx = s * 0.095;
        box(w, t, 0.012, rim, cx, y0 + h - t, z, head);
        box(w, t, 0.012, rim, cx, y0, z, head);
        for (const e of [-1, 1]) box(t, h, 0.012, rim, cx + e * (w - t) / 2, y0, z, head);
        box(w - 2 * t, h - 2 * t, 0.004, lensMat, cx, y0 + t, z - 0.003, head);
        box(0.012, t, 0.21, rim, s * 0.216, y0 + h - t, 0.11, head);
      }
      box(0.05, t, 0.012, rim, 0, y0 + h * 0.55, z, head);
    }
    const props = {
      mug: cyl(0.05, 0.045, 0.1, '#f5f3ee', 0, -0.47, 0.05, elbows[1], 8),
      phone: box(0.06, 0.22, 0.07, '#c8312f', 0, -0.47, 0.06, elbows[1]),
      marker: cyl(0.015, 0.015, 0.14, '#c0262b', 0, -0.52, 0.05, elbows[1], 6),
      folder: box(0.34, 0.03, 0.26, '#e5c77f', 0, 0.2, 0.34, upper),
      clipboard: box(0.24, 0.32, 0.02, '#8a5a2b', 0, -0.5, 0.08, elbows[0]),
    };
    if (crew) {
      props.wrench = box(0.04, 0.26, 0.04, '#9aa0a6', 0, -0.62, 0.06, elbows[1]);
      props.toolbox = box(0.36, 0.2, 0.15, '#b8262b', 0, -0.62, 0.02, elbows[0]);
      box(0.2, 0.04, 0.03, '#2a2a2a', 0, 0.2, 0, props.toolbox); // handle
    }
    props.mug.rotation.x = 1.55;
    props.folder.rotation.x = -0.9;
    for (const p of Object.values(props)) p.visible = false;
    return { root, body, upper, head, hips, knees, shoulders, elbows, props, look: { shirt: top, skin, hair } };
  }

  // ---- poses: each returns joint targets; the actor blends towards them every frame
  const POSE_KEYS = ['y', 'lean', 'hipL', 'hipR', 'kneeL', 'kneeR', 'shL', 'shR', 'shLz', 'shRz', 'elL', 'elR', 'headX', 'headY'];
  const SIT = { y: -0.31, hipL: -1.55, hipR: -1.55, kneeL: 1.55, kneeR: 1.55 };
  const sitOr = (sit) => (sit ? { ...SIT } : {});
  const POSES = {
    stand: (t, sit) => ({ ...sitOr(sit), shL: 0.04 * Math.sin(t * 1.3), shR: -0.04 * Math.sin(t * 1.3), elL: -0.15, elR: -0.15, headY: 0.25 * Math.sin(t * 0.4) }),
    type: (t, sit) => ({ ...sitOr(sit), lean: 0.08, shL: -0.55, shR: -0.55, elL: -0.95 + 0.09 * Math.sin(t * 22), elR: -0.95 + 0.09 * Math.sin(t * 22 + 1.7), headX: 0.12 + 0.03 * Math.sin(t * 3) }),
    think: (t, sit) => ({ ...sitOr(sit), lean: sit ? -0.08 : 0, shR: -0.45, shRz: -0.3, elR: -2.3, shL: -0.3, shLz: 0.25, elL: -1.35, headX: -0.12, headY: 0.3 * Math.sin(t * 0.6) }),
    wait: (t, sit) => ({ ...sitOr(sit), shR: -2.95, shRz: 0.18 * Math.sin(t * 7), elR: -0.15, shL: -0.1, elL: -0.2, headX: -0.18, headY: 0.15 * Math.sin(t * 2) }),
    slack: (t, sit) => ({ ...sitOr(sit), lean: -0.28, shR: -0.55, elR: -0.75 + (Math.sin(t * 3.1) > 0.92 ? -0.08 : 0), shL: -0.1, shLz: 0.3, elL: -0.3, headX: 0.12, headY: 0.12 * Math.sin(t * 0.35) }),
    read: (t, sit) => ({ ...sitOr(sit), shL: -0.5, shR: -0.5, shLz: 0.25, shRz: -0.25, elL: -1.2, elR: -1.2, headX: 0.32 + 0.05 * Math.sin(t * 1.6), headY: 0.1 * Math.sin(t * 0.9) }),
    copy: (t, sit) => ({ ...sitOr(sit), lean: 0.12, shR: -0.95, elR: -0.45 + 0.3 * Math.max(0, Math.sin(t * 5)), shL: -0.2, elL: -0.3, headX: 0.35 }),
    phone: (t, sit) => ({ ...sitOr(sit), shR: -0.35, shRz: -0.3, elR: -2.6, shL: -0.15, shLz: -0.45, elL: -1.4, headX: -0.05, headY: 0.35 * Math.sin(t * 0.5) }),
    write: (t, sit) => ({ ...sitOr(sit), shR: -2.0 + 0.15 * Math.sin(t * 3), shRz: 0.12 * Math.sin(t * 4.3), elR: -0.4, shL: -0.15, elL: -0.2, headX: -0.18 }),
    talk: (t, sit) => ({ ...sitOr(sit), shR: -0.8 + 0.3 * Math.sin(t * 2.5), elR: -0.9, shL: -0.6 + 0.3 * Math.sin(t * 2.5 + 2), elL: -0.9, shLz: 0.2, shRz: -0.2, headY: 0.2 * Math.sin(t * 1.1) }),
    coffee: (t, sit) => { const sip = t % 6 < 1.4; return { ...sitOr(sit), shR: sip ? -0.55 : -0.3, shRz: -0.2, elR: sip ? -2.3 : -1.45, shL: -0.05, elL: -0.15, headX: sip ? -0.25 : 0.05, headY: sip ? 0 : 0.3 * Math.sin(t * 0.5) }; },
    arms: (t, sit) => ({ ...sitOr(sit), shL: -0.45, shR: -0.45, shLz: 0.55, shRz: -0.55, elL: -1.75, elR: -1.75, headX: 0.1, headY: 0.2 * Math.sin(t * 0.8) }),
  };
  POSES.fix = (t, sit) => ({ ...sitOr(sit), y: -0.07, lean: 0.25, hipL: -0.35, hipR: -0.35, kneeL: 0.7, kneeR: 0.7,
    shR: -1.1 + 0.35 * Math.sin(t * 9), elR: -0.5, shL: -0.75, elL: -0.6, headX: 0.45 });
  const PROPS_FOR = { coffee: 'mug', phone: 'phone', write: 'marker', read: 'folder', arms: 'clipboard', fix: 'wrench' };
  function walkPose(p) {
    const s = Math.sin(p);
    return { hipL: 0.55 * s, hipR: -0.55 * s, kneeL: 0.7 * Math.max(0, s), kneeR: 0.7 * Math.max(0, -s), shL: -0.45 * s, shR: 0.45 * s, elL: -0.3, elR: -0.3, y: 0.035 * Math.abs(Math.cos(p)) };
  }

  const actors = new Map();
  class Actor {
    constructor(id, { name, boss = false, temp = false, crew = false, seed }) {
      this.id = id; this.name = name; this.boss = boss; this.temp = temp; this.crew = crew;
      Object.assign(this, makePerson(seed, boss, temp, crew));
      this.pose = Object.fromEntries(POSE_KEYS.map((k) => [k, 0]));
      this.at = SPOTS.door; this.target = SPOTS.door; this.targetKey = 'door';
      this.root.position.set(SPOTS.door.x, 0, SPOTS.door.z);
      this.root.rotation.y = this.heading = SPOTS.door.face;
      this.walking = false; this.route = null; this.phase = Math.random() * 6; this.tOffset = Math.random() * 20;
      this.anim = 'stand'; this.stayUntil = 0; this.arrivedAt = 0; this.desk = null; this.info = null;
      this.line = ''; this.bubbleText = ''; this.mood = 'think';

      this.tagEl = document.createElement('div');
      this.tagEl.className = 'tag' + (temp ? ' temp' : '') + (boss ? ' boss' : '') + (crew ? ' crew' : '');
      this.tagEl.addEventListener('pointerdown', (e) => e.stopPropagation());
      this.tagEl.addEventListener('click', () => onSelect(this.id, true));
      this.tag = new CSS2DObject(this.tagEl);
      this.tag.position.set(0, 2.32, 0);
      this.body.add(this.tag);
      this.bubbleEl = document.createElement('div');
      this.bubbleEl.className = 'bubble hidden';
      this.bubble = new CSS2DObject(this.bubbleEl);
      this.bubble.position.set(0, 2.85, 0);
      this.body.add(this.bubble);
      this._tagHtml = ''; this._bubble = null;

      this.root.traverse((o) => { o.userData.actor = this; });
      scene.add(this.root);
    }

    goTo(spot, key) {
      this.targetKey = key;
      if (this.target === spot) return;
      if (this.target && this.target.taken === this.id) this.target.taken = null;
      const inward = [...spot.via].reverse().concat([{ x: spot.x, z: spot.z }]);
      let outward;
      if (!this.walking) outward = this.at ? this.at.via.slice() : [];
      else {
        const r = this.route, i = r.idx;
        if (i < r.out) outward = r.pts.slice(i, r.out);
        else if (i === r.out) outward = [];
        else outward = r.pts.slice(r.out, i).reverse();
      }
      this.route = { pts: outward.concat(inward), out: outward.length, idx: 0 };
      this.target = spot;
      this.walking = true;
      this.at = null;
    }

    update(dt, t) {
      let target;
      if (this.walking) {
        const r = this.route, p = r.pts[r.idx], pos = this.root.position;
        const dx = p.x - pos.x, dz = p.z - pos.z, d = Math.hypot(dx, dz), step = SPEED * dt;
        if (d <= step) {
          pos.x = p.x; pos.z = p.z; r.idx++;
          if (r.idx >= r.pts.length) {
            this.walking = false; this.at = this.target; this.arrivedAt = t; this.stayUntil = t + MIN_STAY;
            const cb = this.onArrive; this.onArrive = null; cb?.();
          }
        } else {
          pos.x += (dx / d) * step; pos.z += (dz / d) * step;
          this.heading = Math.atan2(dx, dz);
        }
        this.phase += dt * 9;
        target = walkPose(this.phase);
      } else {
        if (this.at?.face !== undefined) this.heading = this.at.face;
        target = (POSES[this.anim] || POSES.stand)(t + this.tOffset, !!this.at?.sit);
      }
      let dh = this.heading - this.root.rotation.y;
      dh = Math.atan2(Math.sin(dh), Math.cos(dh));
      this.root.rotation.y += dh * Math.min(1, dt * 10);

      const f = 1 - Math.exp(-dt * 14), p = this.pose;
      for (const k of POSE_KEYS) p[k] += ((target[k] || 0) - p[k]) * f;
      this.body.position.y = p.y;
      this.upper.rotation.x = p.lean;
      this.hips[0].rotation.x = p.hipL; this.hips[1].rotation.x = p.hipR;
      this.knees[0].rotation.x = p.kneeL; this.knees[1].rotation.x = p.kneeR;
      this.shoulders[0].rotation.x = p.shL; this.shoulders[1].rotation.x = p.shR;
      this.shoulders[0].rotation.z = p.shLz; this.shoulders[1].rotation.z = p.shRz;
      this.elbows[0].rotation.x = p.elL; this.elbows[1].rotation.x = p.elR;
      this.head.rotation.x = p.headX; this.head.rotation.y = p.headY;
      const prop = this.walking ? null : PROPS_FOR[this.anim];
      for (const [k, m] of Object.entries(this.props)) m.visible = k === prop || (k === 'toolbox' && this.crew);
    }

    label(line, bubble, bubbleClass = '') {
      const role = this.boss ? 'Manager' : this.crew ? 'Maintenance' : this.temp ? 'Temp' : '';
      const html = `<b>${esc(this.name)}${role ? ` · ${role}` : ''}</b><small>${esc(line)}</small>`;
      if (html !== this._tagHtml) { this.tagEl.innerHTML = html; this._tagHtml = html; }
      const key = bubble ? bubble + bubbleClass : '';
      if (key !== this._bubble) {
        this._bubble = key;
        this.bubbleEl.className = 'bubble' + (bubble ? '' : ' hidden') + (bubbleClass ? ' ' + bubbleClass : '');
        this.bubbleEl.textContent = bubble || '';
      }
    }

    dispose() {
      this.root.parent?.remove(this.root);
      this.root.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
      this.tagEl.remove(); this.bubbleEl.remove();
    }
  }

  // ---------------------------------------------------------------- behaviour
  const WORK_STATIONS = new Set(['files', 'copier', 'phone', 'whiteboard']);
  const STATION_ANIM = { desk: 'type', files: 'read', copier: 'copy', phone: 'phone', whiteboard: 'write', delegate: 'talk', coffee: 'coffee' };
  const TASK_WORDS = { typing: 'typing at the desk', files: 'off to the filing cabinets', copier: 'over at the copier', phone: 'on the phone', whiteboard: 'updating the whiteboard', delegate: 'briefing a temp' };

  function reserve(key, actor) {
    const list = ST[key];
    if (!list?.length) return null;
    const free = list.find((s) => !s.taken || s.taken === actor.id);
    if (free) { free.taken = actor.id; return free; }
    const s = list[hash(actor.id) % list.length], r = mulberry32(hash(actor.id + key));
    return { ...s, x: s.x + (r() - 0.5) * 0.9, z: s.z + (r() - 0.5) * 0.9, taken: null, sit: false };
  }
  function deskSpotFor(actor) {
    if (actor.desk) return actor.desk.spot;
    return reserve('coffee', actor);
  }
  function allocDesk(temp) {
    const cubes = deskSlots.filter((d) => d.kind === 'cubicle'), bench = deskSlots.filter((d) => d.kind === 'bench');
    return (temp ? [...bench, ...cubes] : [...cubes, ...bench]).find((d) => !d.owner) || null;
  }

  function think(actor, t, nowSec) {
    const a = actor.info;
    if (!a || actor.leaving) return;
    const idleFor = nowSec - a.last, sinceFor = nowSec - a.since;
    const it = intent(a, idleFor, sinceFor);
    let key = it.where;
    if (a.status === 'thinking' && WORK_STATIONS.has(actor.targetKey) && sinceFor < 25) key = actor.targetKey;
    if (key !== actor.targetKey && !actor.walking && t < actor.stayUntil) key = actor.targetKey;
    if (key !== actor.targetKey || (key === 'desk' && actor.desk && actor.target !== actor.desk.spot)) {
      const spot = key === 'desk' ? deskSpotFor(actor) : reserve(key, actor);
      if (spot) actor.goTo(spot, key);
    }
    actor.anim = key === it.where ? it.anim : a.status === 'thinking' ? 'think' : STATION_ANIM[key] || 'stand';
    actor.mood = it.mood;
    actor.line = it.line;
    const flustered = actor.flusteredUntil > t;
    actor.label(it.line, flustered ? '!?' : it.bubble, it.alert ? 'alert' : '');
  }

  // The manager wanders over to anyone playing Mines.
  let boss = null;
  function bossBrain(t) {
    if (!boss || boss.walking) return;
    if (boss.mode === 'inspect') {
      const v = boss.victim;
      const stillSlacking = v && actors.has(v.id) && !v.walking && v.anim === 'slack';
      boss.anim = 'arms';
      boss.label(`Keeping an eye on ${v?.name ?? 'someone'}`, boss.catchLine, 'boss');
      const stay = Math.max(7, 2 + boss.catchLine.length / 10); // long lines get time to be read
      if (t - boss.arrivedAt > stay || !stillSlacking) { boss.mode = 'home'; boss.goTo(SPOTS.boss, 'home'); boss.label('Back to the office', null); }
      return;
    }
    boss.anim = 'type';
    boss.label('Reviewing productivity reports', null);
    if (t < (boss.nextCheck || 0)) return;
    boss.nextCheck = t + 5;
    const victim = [...actors.values()].find((a) => a !== boss && !a.leaving && !a.walking && a.anim === 'slack' && a.desk && a.at === a.desk.spot && t - (a.lastInspected ?? -1e9) > 90);
    if (!victim) return;
    victim.lastInspected = t;
    const s = victim.desk.spot;
    const behind = { x: s.x - Math.sin(s.face) * 0.85, z: s.z - Math.cos(s.face) * 0.85, face: s.face, via: s.via };
    boss.mode = 'inspect';
    boss.victim = victim;
    boss.catchLine = MANAGER_SAYS[Math.floor(Math.random() * MANAGER_SAYS.length)];
    boss.goTo(behind, 'inspect');
    boss.label(`Walking over to ${victim.name}…`, null);
    boss.onArrive = () => {
      victim.flusteredUntil = t + 99;
      setTimeout(() => { victim.flusteredUntil = 0; }, 4000);
      onMemo(`${boss.name} caught ${victim.name} playing Mines: “${boss.catchLine}”`);
    };
  }

  // Maintenance comes by every quarter hour on the wall clock (so every screen sees the same visit),
  // fixes the copier and then the coffee machine, and leaves. He isn't an agent: no roster row.
  const VISIT_EVERY = 15 * 60 * 1000, FIX_FOR = 18;
  const DEFAULT_CREW = 'Stan';
  let crewName = DEFAULT_CREW, crew = null, crewSlot = Math.floor(Date.now() / VISIT_EVERY);
  const CREW_JOBS = [
    { thing: 'the copier', says: 'Paper jam again…', spot: { x: -12.2, z: 5.35, face: -Math.PI / 2, via: westVia(5.35) } },
    { thing: 'the coffee machine', says: 'Who keeps putting tea in here?', spot: { x: 12.75, z: 5.45, face: Math.PI / 2, via: breakVia(5.45) } },
  ];
  function startVisit() {
    if (crew) return;
    crew = new Actor('maintenance', { name: crewName, crew: true, seed: 4242 });
    crew.job = 0;
    crew.goTo(CREW_JOBS[0].spot, 'job');
    onMemo(`${crew.name} from maintenance is here for the copier and the coffee machine.`);
  }
  function crewBrain(t) {
    if (!crew) {
      const slot = Math.floor(Date.now() / VISIT_EVERY);
      if (slot !== crewSlot) { crewSlot = slot; startVisit(); }
      return;
    }
    const job = CREW_JOBS[crew.job];
    if (crew.walking || !job) {
      crew.anim = 'stand';
      crew.label(job ? `On the way to ${job.thing}` : 'Done for now', null);
      return;
    }
    crew.anim = 'fix';
    crew.label(`Fixing ${job.thing}`, job.says, 'boss');
    if (t - crew.arrivedAt < FIX_FOR) return;
    onMemo(`${crew.name} fixed ${job.thing}.`);
    crew.job += 1;
    if (CREW_JOBS[crew.job]) {
      crew.goTo(CREW_JOBS[crew.job].spot, 'job');
    } else {
      const leaving = crew;
      leaving.goTo(SPOTS.door, 'door');
      leaving.onArrive = () => { leaving.dispose(); if (crew === leaving) crew = null; };
    }
  }

  let eotmKey = '';
  const prev = new Map();
  const usedNames = () => new Set([...actors.values()].filter((a) => !a.leaving).map((a) => a.name));

  function nameFor(id) {
    const used = usedNames(), h = hash(id);
    for (let i = 0; i < NAMES.length; i++) { const n = NAMES[(h + i) % NAMES.length]; if (!used.has(n)) return n; }
    return NAMES[h % NAMES.length] + ' ' + String.fromCharCode(65 + (h % 26)) + '.';
  }

  // names.txt on the server can be edited at any time; the office checks it every 30 seconds
  // and renames everyone on shift when it changes.
  // It can also rename the Manager ("Manager: ...") and give him things to say ("Manager says: ...").
  const DEFAULT_MANAGER = 'Mr. Pemberton', DEFAULT_SAYS = ['Ahem.'];
  let MANAGER_SAYS = DEFAULT_SAYS;
  let namesKey = null, managerKey = null;
  function applyCast(cast, currentList) {
    if (Array.isArray(cast)) cast = { names: cast }; // an older server sends just the list
    crewName = cast?.maintenance ? String(cast.maintenance) : DEFAULT_CREW;
    if (crew) crew.name = crewName;
    const says = Array.isArray(cast?.says) && cast.says.length ? cast.says.map(String) : DEFAULT_SAYS;
    const managerName = cast?.manager ? String(cast.manager) : DEFAULT_MANAGER;
    const mKey = managerName + '\n' + says.join('\n');
    if (mKey !== managerKey) {
      const renamed = managerKey !== null && boss && boss.name !== managerName;
      managerKey = mKey;
      MANAGER_SAYS = says;
      if (boss) boss.name = managerName;
      if (renamed) onMemo(`${managerName} is the new manager.`);
    }
    const next = Array.isArray(cast?.names) && cast.names.length ? cast.names.map(String) : DEFAULT_NAMES;
    const key = next.join('\n');
    if (key === namesKey) return;
    const changed = namesKey !== null;
    namesKey = key;
    NAMES = next;
    const staff = [...actors.values()].filter((a) => a !== boss && !a.leaving);
    if (!staff.length) return;
    for (const a of staff) a.name = null; // free every name so they can be handed out again
    for (const a of staff) {
      a.name = nameFor(a.id);
      if (a.desk) setDeskLabel(a.desk, a.name.toUpperCase(), false);
    }
    eotmKey = '';
    updateEotm(currentList);
    return { changed };
  }
  function sync(list) {
    const seen = new Set(list.map((a) => a.id));
    // Release desks before arrivals, including when a full office replaces a worker.
    for (const [id, actor] of actors) {
      if (actor !== boss && !seen.has(id) && !actor.leaving) leave(actor);
    }
    for (const a of list) {
      let actor = actors.get(a.id);
      if (!actor || actor.leaving) {
        if (actor) { actor.dispose(); actors.delete(a.id); }
        actor = new Actor(a.id, { name: nameFor(a.id), temp: a.temp, seed: hash(a.id) });
        actors.set(a.id, actor);
        const parent = a.parent && actors.get(a.parent);
        onMemo(a.temp ? `A temp, ${actor.name}, has arrived to help ${parent?.name ?? 'out'}.` : `${actor.name} clocked in${a.project ? ` (${a.project})` : ''}.`);
      }
      actor.info = a;
      const before = prev.get(a.id);
      if (before && before.status !== a.status) {
        if (a.status === 'waiting') onMemo(`${actor.name} needs your sign-off${a.tool ? ` (${a.tool})` : ''}.`, true);
        else if (a.status === 'idle') onMemo(`${actor.name} finished up and went for a coffee.`);
      } else if (before && a.status === 'working' && before.task !== a.task && TASK_WORDS[a.task] && (actor.lastMemo ?? 0) < performance.now() - 8000) {
        actor.lastMemo = performance.now();
        onMemo(`${actor.name} is ${TASK_WORDS[a.task]}${a.detail ? `: ${a.detail}` : '.'}`);
      }
      prev.set(a.id, { status: a.status, task: a.task });
    }
    // Map insertion order gives existing overflow workers first refusal; keep all
    // current assignments stable and retain the cubicle/temp-desk preference.
    for (const actor of actors.values()) {
      if (actor === boss || actor.leaving || actor.desk) continue;
      actor.desk = allocDesk(actor.temp);
      if (actor.desk) {
        actor.desk.owner = actor.id;
        actor.desk.spot.taken = actor.id;
        setDeskLabel(actor.desk, actor.name.toUpperCase(), false);
      }
    }
    for (const id of [...prev.keys()]) if (!seen.has(id)) prev.delete(id);
    updateEotm(list);
  }

  function leave(actor) {
    actor.leaving = true;
    onMemo(`${actor.name} clocked out.`);
    if (actor.desk) { actor.desk.owner = null; actor.desk.spot.taken = null; setDeskLabel(actor.desk, 'VACANT', true); clearDeskScreen(actor.desk); actor.desk = null; }
    actor.label('Going home', null);
    actor.anim = 'stand';
    actor.goTo(SPOTS.door, 'door');
    const finishDeparture = () => { actor.dispose(); if (actors.get(actor.id) === actor) actors.delete(actor.id); };
    // A snapshot can remove a new actor before its first frame moves it off the door.
    if (actor.walking) actor.onArrive = finishDeparture;
    else finishDeparture();
    onDeparted(actor.id);
  }

  function updateEotm(list) {
    let best = null;
    for (const a of list) { const actor = actors.get(a.id); if (actor && (!best || a.tools > best.tools)) best = { ...actor.look, name: actor.name, tools: a.tools }; }
    const key = best ? `${best.name}|${best.tools}` : '';
    if (key === eotmKey) return;
    eotmKey = key;
    setEmployeeOfMonth(best && best.tools > 0 ? best : null);
  }

  boss = new Actor('manager', { name: DEFAULT_MANAGER, boss: true, seed: 1994 });
  boss.root.position.set(SPOTS.boss.x, 0, SPOTS.boss.z);
  boss.at = boss.target = SPOTS.boss;
  boss.root.rotation.y = boss.heading = 0;
  boss.mode = 'home';
  boss.anim = 'type';
  actors.set(boss.id, boss);

  function step(dt, t, ns) {
    for (const actor of actors.values()) { if (actor !== boss) think(actor, t, ns); actor.update(dt, t); }
    bossBrain(t);
    crewBrain(t);
    crew?.update(dt, t);

  }

  function setSelected(id) {
    for (const actor of actors.values()) actor.tagEl.classList.toggle('sel', actor.id === id);
  }

  return { actors, boss, get crew() { return crew; }, sync, applyCast, step, setSelected, startVisit };
}
