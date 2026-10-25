// Token & Tally office: native module composition, source switching and frame loop.
import * as THREE from 'three';
import { createState } from './office/state.js';
import { createOfficeScene } from './office/scene.js';
import { createActors } from './office/actors.js';
import { createHud } from './office/hud.js';
import { connectFeed, fetchNames } from './office/feed.js';
import { DemoShift } from './office/demo.js';

const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
let savedLite = null;
try { savedLite = localStorage.getItem('tt-lite'); } catch { /* storage blocked: fall back to the URL */ }
const LITE = params.has('lite') ? params.get('lite') !== '0' : savedLite === '1';
const host = $('#scene');
const state = createState(), live = state.live;
const nowSec = state.nowSec;
const room = createOfficeScene({ host, lite: LITE });
const { renderer, camera, controls } = room;
const { deskSlots } = room.layout;
const staff = createActors({
  scene: room.scene, box: room.box, cyl: room.cyl, layout: room.layout,
  setDeskLabel: room.setDeskLabel, clearDeskScreen: room.clearDeskScreen,
  setEmployeeOfMonth: room.setEmployeeOfMonth,
  onSelect: select, onMemo: (...args) => hud.memo(...args),
  onDeparted(id) {
    if (state.selectedId === id) {
      state.selectedId = null; state.follow = false;
      staff.setSelected(null);
    }
  },
});
const actors = staff.actors;
const hud = createHud({
  actors, state, nowSec, onSelect: select, onSourceChange: setSource,
  onHomeCamera() { camera.position.copy(room.HOME_CAM.pos); controls.target.copy(room.HOME_CAM.target); },
  onToggleLabels() { return room.labelRenderer.domElement.classList.toggle('off'); },
  onLiteChange() {
    try { localStorage.setItem('tt-lite', LITE ? '0' : '1'); } catch { /* ignore */ }
    params.set('lite', LITE ? '0' : '1');
    if (state.source === 'demo') params.set('demo', '');
    else params.delete('demo');
    location.search = params.toString();
  },
});

function sync(list) {
  const restoreFileFocus = state.selectedId && !list.some((a) => a.id === state.selectedId) && hud.fileHasFocus();
  staff.sync(list);
  hud.renderRoster();
  hud.renderFile();
  if (restoreFileFocus) hud.focusRoster();
  hud.updateEmpty();
}

function select(id, focus = false) {
  const actor = actors.get(id);
  if (!actor?.info || actor.leaving || actor.boss) id = null;
  state.selectedId = id;
  state.follow = !!id && focus;
  staff.setSelected(id);
  hud.renderRoster();
  hud.renderFile();
}

async function loadNames() {
  const cast = await fetchNames();
  if (cast === undefined) return;
  const list = state.source === 'demo' ? [...actors.values()].filter((a) => a.info).map((a) => a.info) : live.agents;
  const result = staff.applyCast(cast, list);
  if (!result) return;
  hud.renderRoster();
  hud.renderFile();
  if (result.changed) hud.memo('New name tags have been handed out.');
}

function connect() {
  connectFeed({
    onOpen() { live.connected = true; live.everConnected = true; hud.setConn(); },
    onSnapshot(d) {
      state.acceptSnapshot(d);
      hud.setConn();
      if (state.source === 'live') sync(live.agents);
    },
    onError() { live.connected = false; hud.setConn(); hud.updateEmpty(); },
  });
}

let demo = null, demoTimer = null;
function setSource(next) {
  state.source = next;
  clearInterval(demoTimer);
  if (next === 'demo') {
    demo = new DemoShift();
    sync(demo.step());
    demoTimer = setInterval(() => sync(demo.step()), 1000);
    hud.memo('Demo shift started. These are pretend employees.');
  } else {
    demo = null;
    sync(live.agents);
  }
  hud.setConn();
  hud.updateEmpty();
}

const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
let downAt = null;
renderer.domElement.addEventListener('pointerdown', (e) => { downAt = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener('pointerup', (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return;
  const rect = renderer.domElement.getBoundingClientRect();
  ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
  ray.setFromCamera(ndc, camera);
  const hit = ray.intersectObjects([...actors.values()].map((a) => a.root), true)[0];
  const actor = hit?.object.userData.actor;
  select(actor && !actor.boss ? actor.id : null, !!actor);
});
controls.addEventListener('start', () => { state.follow = false; });

addEventListener('resize', room.resize);
room.resize();

const clock = new THREE.Clock();
let lastHud = 0;
const tmp = new THREE.Vector3();
let simT = 0;
function step(dt, draw = true) {
  simT += dt;
  const t = simT, ns = nowSec();
  staff.step(dt, t, ns);

  room.updateScreens(actors, staff.boss);
  room.updateWalls();

  if (state.follow && state.selectedId && actors.get(state.selectedId)) {
    actors.get(state.selectedId).root.getWorldPosition(tmp);
    tmp.y = 1;
    controls.target.lerp(tmp, 1 - Math.exp(-dt * 4));
  }
  if (!draw) return;
  room.render();

  if (t - lastHud > 1) {
    lastHud = t;
    const d = new Date();
    hud.updateClock(d);
    room.updateClock(d);
    hud.renderRoster();
    hud.renderFile();
  }
}
let pending = 0;
function frame() {
  pending += clock.getDelta();
  if (!LITE || pending >= 1 / 31) { step(Math.min(pending, 0.25)); pending = 0; }
  requestAnimationFrame(frame);
}
// Handy from the dev console; advance() fast-forwards the floor when the tab is in the background.
window.tokenTally = { actors, deskSlots, camera, controls, loadNames, maintenance: staff.startVisit, get crew() { return staff.crew; }, advance(sec) { for (let i = 0; i < sec * 30; i++) step(1 / 30, false); step(0); } };

hud.setConn();
hud.renderRoster();
document.querySelector('[data-cmd="lite"]').textContent = LITE ? 'High quality mode' : 'Low quality mode';
if (params.has('demo')) setSource('demo');
loadNames();
setInterval(loadNames, 30000);
connect();
setTimeout(() => { if (!live.everConnected && state.source === 'live') { hud.memo('Can’t reach the office server, so here’s a demo shift.'); setSource('demo'); } }, 3000);
requestAnimationFrame(frame);
