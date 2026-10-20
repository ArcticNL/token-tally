// Token & Tally Stationery Supplies — a 1990s office floor for Claude Code agents.
// Server-Sent Events from server.py tell us what every agent is doing; each one
// becomes an office worker who walks to the right part of the floor for the job.

import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from './vendor/CSS2DRenderer.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function mulberry32(a) { return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
function hash(s) { let h = 2166136261 >>> 0; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }

const SLACK_AFTER = 120;  // seconds idle before a coffee break turns into Mines
const MIN_STAY = 4;       // seconds a worker stays at a station before walking off again
const SPEED = 2.6;        // metres per second

// ---------------------------------------------------------------- renderer
// Low quality mode (?lite, or the Start menu) is for budget tablets: no shadows,
// no antialiasing, 1x resolution and at most 30 frames a second.
const params = new URLSearchParams(location.search);
let savedLite = null;
try { savedLite = localStorage.getItem('tt-lite'); } catch { /* storage blocked: fall back to the URL */ }
const LITE = params.has('lite') ? params.get('lite') !== '0' : savedLite === '1';
const host = $('#scene');
const renderer = new THREE.WebGLRenderer({ antialias: !LITE, powerPreference: LITE ? 'low-power' : 'default' });
renderer.setPixelRatio(LITE ? 1 : Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = !LITE;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
host.appendChild(renderer.domElement);

const labelRenderer = new CSS2DRenderer();
labelRenderer.domElement.className = 'labels';
host.appendChild(labelRenderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color('#23252f');

const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 200);
const HOME_CAM = { pos: new THREE.Vector3(5, 25, 27), target: new THREE.Vector3(0, 0, 0.5) };
camera.position.copy(HOME_CAM.pos);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.copy(HOME_CAM.target);
controls.enableDamping = true;
controls.minPolarAngle = 0.25;
controls.maxPolarAngle = 1.22;
controls.minDistance = 6;
controls.maxDistance = 60;

scene.add(new THREE.HemisphereLight('#fff6e3', '#59606f', 2.1));
const sun = new THREE.DirectionalLight('#fff1d2', 1.9);
sun.position.set(-9, 22, 12);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -19, right: 19, top: 15, bottom: -15, near: 1, far: 70 });
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.03;
scene.add(sun);

// ---------------------------------------------------------------- building blocks
const C = {
  carpet: '#59677d', runner: '#6b7589', wall: '#dacfb2', stripe: '#2f6d70', skirting: '#4f4134', trim: '#beb8a4',
  fabric: '#7b879a', desk: '#cbb087', deskDark: '#9d845f', beige: '#e4dcc6', beigeDark: '#c3b99e', chair: '#30333f',
  metal: '#a0a4a7', metalDark: '#575b60', cabinet: '#a9ab9f', wood: '#7a4c2b', woodDark: '#5a361f', leaf: '#3f7b3c', pot: '#9a5634',
};
const mats = new Map();
function mat(color) {
  if (!mats.has(color)) mats.set(color, new THREE.MeshLambertMaterial({ color, flatShading: true }));
  return mats.get(color);
}
function box(w, h, d, color, x = 0, y = 0, z = 0, parent = scene) {
  const g = new THREE.BoxGeometry(w, h, d);
  g.translate(0, h / 2, 0); // origin at the bottom so things stack by height
  const m = new THREE.Mesh(g, typeof color === 'string' ? mat(color) : color);
  m.position.set(x, y, z);
  m.castShadow = m.receiveShadow = true;
  parent.add(m);
  return m;
}
function cyl(rt, rb, h, color, x = 0, y = 0, z = 0, parent = scene, seg = 12) {
  const g = new THREE.CylinderGeometry(rt, rb, h, seg);
  g.translate(0, h / 2, 0);
  const m = new THREE.Mesh(g, typeof color === 'string' ? mat(color) : color);
  m.position.set(x, y, z);
  m.castShadow = m.receiveShadow = true;
  parent.add(m);
  return m;
}
function group(x = 0, z = 0, rotY = 0, parent = scene) {
  const g = new THREE.Group();
  g.position.set(x, 0, z);
  g.rotation.y = rotY;
  parent.add(g);
  return g;
}
function canvasTex(w, h, draw, pixel = false) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  draw(ctx, w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  if (pixel) { t.magFilter = THREE.NearestFilter; t.generateMipmaps = false; t.minFilter = THREE.LinearFilter; }
  t.userData = { ctx, w, h, draw };
  return t;
}
function redraw(tex, draw) { const { ctx, w, h } = tex.userData; ctx.clearRect(0, 0, w, h); draw(ctx, w, h); tex.needsUpdate = true; }
function textLines(g, w, h, lines) {
  g.textAlign = 'center'; g.textBaseline = 'middle';
  const total = lines.reduce((s, l) => s + l.size * 1.22, 0);
  let y = h / 2 - total / 2;
  for (const l of lines) {
    y += (l.size * 1.22) / 2;
    g.fillStyle = l.color || '#1f2c46';
    g.font = `${l.weight || 'bold'} ${l.size}px ${l.font || 'Tahoma, Verdana, sans-serif'}`;
    g.fillText(l.text, w / 2, y, w * 0.92);
    y += (l.size * 1.22) / 2;
  }
}
function textTex(w, h, lines, { bg = '#f4efdd', border = '#1f2c46' } = {}) {
  return canvasTex(w, h, (g) => {
    if (bg) { g.fillStyle = bg; g.fillRect(0, 0, w, h); }
    if (border) { const lw = Math.max(4, Math.round(h * 0.05)); g.strokeStyle = border; g.lineWidth = lw; g.strokeRect(lw / 2, lw / 2, w - lw, h - lw); }
    textLines(g, w, h, lines);
  });
}
// A thin board with the picture on both big faces (reads correctly from either side).
function board(tex, w, h, x, y, z, rotY = 0, parent = scene, depth = 0.04) {
  const edge = mat('#2b2b2b'), face = new THREE.MeshLambertMaterial({ map: tex });
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, depth), [edge, edge, edge, edge, face, face]);
  m.position.set(x, y, z);
  m.rotation.y = rotY;
  m.castShadow = true;
  parent.add(m);
  return m;
}
function plant(x, z, s = 1, parent = scene) {
  cyl(0.22 * s, 0.17 * s, 0.38 * s, C.pot, x, 0, z, parent, 8);
  const r = mulberry32(hash(`plant${x},${z}`));
  for (let i = 0; i < 5; i++) {
    const leaf = new THREE.Mesh(new THREE.IcosahedronGeometry(0.26 * s * (0.8 + r() * 0.5), 0), mat(i % 2 ? C.leaf : '#4f8f46'));
    leaf.position.set(x + (r() - 0.5) * 0.35 * s, (0.55 + r() * 0.75) * s, z + (r() - 0.5) * 0.35 * s);
    leaf.rotation.set(r() * 3, r() * 3, r() * 3);
    leaf.castShadow = true;
    parent.add(leaf);
  }
}
function chair(parent, x, z, rotY, color = C.chair) {
  const g = new THREE.Group();
  g.position.set(x, 0, z);
  g.rotation.y = rotY;
  parent.add(g);
  for (let i = 0; i < 5; i++) {
    const arm = new THREE.Group(); arm.rotation.y = (i * Math.PI * 2) / 5; g.add(arm);
    box(0.05, 0.04, 0.3, C.metalDark, 0, 0.03, 0.15, arm);
  }
  cyl(0.03, 0.03, 0.42, C.metal, 0, 0.05, 0, g, 6);
  box(0.5, 0.08, 0.48, color, 0, 0.44, 0, g);
  box(0.48, 0.52, 0.07, color, 0, 0.56, -0.25, g);
  return g;
}
function stoolChair(parent, x, z, rotY, color) {
  const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = rotY; parent.add(g);
  for (const [dx, dz] of [[-0.18, -0.18], [0.18, -0.18], [-0.18, 0.18], [0.18, 0.18]]) box(0.04, 0.44, 0.04, C.metalDark, dx, 0, dz, g);
  box(0.46, 0.05, 0.44, color, 0, 0.44, 0, g);
  box(0.44, 0.4, 0.05, color, 0, 0.5, -0.22, g);
  return g;
}

// ---------------------------------------------------------------- CRT screens
const screens = {
  off: canvasTex(128, 96, (g, w, h) => {
    g.fillStyle = '#0b1511'; g.fillRect(0, 0, w, h);
    g.fillStyle = 'rgba(80,255,140,.5)';
    for (let i = 0; i < 14; i++) g.fillRect((i * 37) % w, (i * 23) % h, 2, 2);
  }, true),
  work: canvasTex(128, 96, (g, w, h) => {
    g.fillStyle = '#008080'; g.fillRect(0, 0, w, h);
    g.fillStyle = '#c0c0c0'; g.fillRect(8, 6, 112, 80);
    g.fillStyle = '#000080'; g.fillRect(10, 8, 108, 9);
    g.fillStyle = '#fff'; g.fillRect(11, 19, 106, 64);
    const cols = ['#000080', '#800000', '#007a00', '#555555'];
    for (let y = 22, i = 0; y < 80; y += 6, i++) { g.fillStyle = cols[i % 4]; g.fillRect(14 + (i % 3) * 6, y, 24 + ((i * 37) % 64), 3); }
    g.fillStyle = '#c0c0c0'; g.fillRect(0, 88, w, 8); g.fillStyle = '#3a9b3c'; g.fillRect(2, 89, 14, 6);
  }, true),
  mines: canvasTex(128, 96, (g, w, h) => {
    g.fillStyle = '#008080'; g.fillRect(0, 0, w, h);
    g.fillStyle = '#c0c0c0'; g.fillRect(26, 3, 76, 90);
    g.fillStyle = '#000080'; g.fillRect(28, 5, 72, 7);
    g.fillStyle = '#ffd800'; g.beginPath(); g.arc(64, 21, 6, 0, 7); g.fill();
    g.fillStyle = '#000'; g.fillRect(61, 19, 2, 2); g.fillRect(66, 19, 2, 2); g.fillRect(61, 24, 7, 1);
    const nums = ['', '#0000ff', '#008000', '#ff0000'];
    for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) {
      const x = 30 + c * 8.6, y = 31 + r * 7.5, open = (r * 3 + c * 5) % 7 > 2;
      g.fillStyle = open ? '#bdbdbd' : '#ececec'; g.fillRect(x, y, 8, 7);
      if (!open) { g.fillStyle = '#7b7b7b'; g.fillRect(x + 7, y, 1, 7); g.fillRect(x, y + 6, 8, 1); }
      else if ((r + c) % 4) { g.fillStyle = nums[(r + c) % 4]; g.font = 'bold 7px monospace'; g.fillText(String((r + c) % 4), x + 2, y + 6); }
    }
  }, true),
  wait: canvasTex(128, 96, (g, w, h) => {
    g.fillStyle = '#008080'; g.fillRect(0, 0, w, h);
    g.fillStyle = '#c0c0c0'; g.fillRect(14, 20, 100, 56);
    g.fillStyle = '#000080'; g.fillRect(16, 22, 96, 9);
    g.fillStyle = '#ffd800'; g.beginPath(); g.moveTo(30, 58); g.lineTo(38, 42); g.lineTo(46, 58); g.fill();
    g.fillStyle = '#000'; g.fillRect(37, 47, 2, 6); g.fillRect(37, 55, 2, 2);
    g.fillRect(54, 42, 48, 3); g.fillRect(54, 49, 36, 3);
    for (const x of [52, 82]) { g.fillStyle = '#e6e6e6'; g.fillRect(x, 62, 24, 10); g.strokeStyle = '#000'; g.strokeRect(x + 0.5, 62.5, 24, 10); }
  }, true),
};
const screenMats = Object.fromEntries(Object.entries(screens).map(([k, t]) => [k, new THREE.MeshBasicMaterial({ map: t })]));

function monitor(parent, x, y, z, rotY = 0) {
  const g = new THREE.Group();
  g.position.set(x, y, z);
  g.rotation.y = rotY;
  parent.add(g);
  box(0.36, 0.03, 0.3, C.beigeDark, 0, 0, 0, g);
  box(0.12, 0.05, 0.1, C.beigeDark, 0, 0.03, 0, g);
  box(0.52, 0.44, 0.36, C.beige, 0, 0.07, 0.02, g);
  box(0.38, 0.32, 0.22, C.beigeDark, 0, 0.12, -0.25, g);
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(0.42, 0.32), screenMats.off);
  screen.position.set(0, 0.29, 0.202);
  g.add(screen);
  return { g, screen };
}

// ---------------------------------------------------------------- the floor plan
// Room: x -14..14, z -9..9. Main aisle runs along z = 0. Camera looks in from the open south side.
const AISLE = 0;
const walls = [];

function buildFloor() {
  const tex = canvasTex(128, 128, (g, w, h) => {
    g.fillStyle = C.carpet; g.fillRect(0, 0, w, h);
    const r = mulberry32(7);
    for (let i = 0; i < 1500; i++) { g.fillStyle = r() < 0.5 ? 'rgba(255,255,255,.07)' : 'rgba(0,0,0,.12)'; g.fillRect((r() * w) | 0, (r() * h) | 0, 2, 2); }
    g.strokeStyle = 'rgba(0,0,0,.16)'; g.lineWidth = 2; g.strokeRect(1, 1, w - 2, h - 2);
  });
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(14, 9);
  const top = new THREE.MeshLambertMaterial({ map: tex }), side = mat('#3a3f4b');
  const floor = new THREE.Mesh(new THREE.BoxGeometry(28.5, 0.4, 18.5), [side, side, top, side, side, side]);
  floor.position.y = -0.2;
  floor.receiveShadow = true;
  scene.add(floor);
  box(26.6, 0.012, 2.4, C.runner, 0.7, 0, AISLE).castShadow = false;
  box(2.4, 0.012, 5.6, C.runner, -1.5, 0, -4.0).castShadow = false;

  const checker = canvasTex(64, 64, (g) => { g.fillStyle = '#e9e2cf'; g.fillRect(0, 0, 64, 64); g.fillStyle = '#9b3b3b'; g.fillRect(0, 0, 32, 32); g.fillRect(32, 32, 32, 32); }, true);
  checker.wrapS = checker.wrapT = THREE.RepeatWrapping;
  checker.repeat.set(5.4 / 1.2, 6.4 / 1.2);
  box(5.4, 0.014, 6.4, new THREE.MeshLambertMaterial({ map: checker }), 11.25, 0, 5.75).castShadow = false;
  box(3.4, 0.014, 2.6, '#7b2f35', 11.6, 0, -6.4).castShadow = false;
}

function buildWall(len, x, z, rotY, nx, nz, edge) {
  const g = group(x, z, rotY);
  box(len, 3.0, 0.25, C.wall, 0, 0, 0, g);
  box(len, 0.16, 0.29, C.skirting, 0, 0, 0, g);
  box(len, 0.12, 0.27, C.stripe, 0, 1.0, 0, g);
  box(len + 0.02, 0.08, 0.31, C.trim, 0, 2.95, 0, g);
  walls.push({ g, nx, nz, edge });
  return g;
}
function windowOn(g, lx) {
  box(2.3, 1.45, 0.06, '#f1ece0', lx, 1.2, 0.13, g);
  const glass = new THREE.Mesh(new THREE.PlaneGeometry(2.1, 1.25), new THREE.MeshBasicMaterial({ color: '#a8d2ea' }));
  glass.position.set(lx, 1.925, 0.165);
  g.add(glass);
  for (let i = 0; i < 9; i++) box(2.12, 0.035, 0.03, '#ece7da', lx, 1.36 + i * 0.135, 0.19, g);
  box(2.4, 0.06, 0.18, '#f1ece0', lx, 1.17, 0.2, g);
}

let eotmTex = null, clockHands = null;
function buildWalls() {
  const north = buildWall(28.5, 0, -9.12, 0, 0, -1, 9);
  const west = buildWall(18.0, -14.12, 0, Math.PI / 2, -1, 0, 14);   // local x = -world z
  const east = buildWall(18.0, 14.12, 0, -Math.PI / 2, 1, 0, 14);    // local x = world z

  windowOn(north, -11.5); windowOn(north, 2.5); windowOn(north, 11.5);
  board(textTex(512, 110, [{ text: 'TEMP DESKS', size: 62, color: '#7a4b00' }], { bg: '#ffe9a6', border: '#7a4b00' }), 2.2, 0.48, -6, 2.15, 0.16, 0, north);

  // whiteboard
  box(2.75, 1.42, 0.05, '#b9bdc0', -1.5, 1.0, 0.13, north);
  const wb = canvasTex(512, 256, (g, w, h) => {
    g.fillStyle = '#fbfbf8'; g.fillRect(0, 0, w, h);
    g.lineWidth = 4; g.lineCap = 'round';
    g.fillStyle = '#1d3f9a'; g.font = 'bold 34px "Comic Sans MS", "Marker Felt", cursive'; g.fillText('Q3 GOALS', 22, 46);
    g.font = '24px "Comic Sans MS", cursive';
    ['ship the thing', 'fix flaky tests', 'order more toner'].forEach((t, i) => { g.fillStyle = '#222'; g.fillText('- ' + t, 30, 92 + i * 36); });
    g.strokeStyle = '#2f8a3a'; [[300, 190, 40], [350, 190, 80], [400, 190, 120], [450, 190, 150]].forEach(([x, y, hgt]) => g.strokeRect(x, y - hgt, 34, hgt));
    g.fillStyle = '#c0262b'; g.font = 'bold 30px "Comic Sans MS", cursive'; g.fillText('SHIP IT!', 300, 232);
    g.strokeStyle = '#c0262b'; g.beginPath(); g.moveTo(250, 120); g.quadraticCurveTo(280, 60, 330, 70); g.stroke();
  });
  const wbPlane = new THREE.Mesh(new THREE.PlaneGeometry(2.6, 1.28), new THREE.MeshLambertMaterial({ map: wb }));
  wbPlane.position.set(-1.5, 1.71, 0.162);
  north.add(wbPlane);
  box(2.4, 0.04, 0.12, '#9a9ea2', -1.5, 1.0, 0.2, north);

  // employee of the month + motivational poster
  box(1.08, 1.34, 0.05, '#6b4a1c', 5.6, 1.18, 0.13, north);
  eotmTex = canvasTex(256, 320, drawEotm(null));
  const eotm = new THREE.Mesh(new THREE.PlaneGeometry(0.94, 1.18), new THREE.MeshLambertMaterial({ map: eotmTex }));
  eotm.position.set(5.6, 1.85, 0.162);
  north.add(eotm);
  box(1.08, 1.34, 0.05, '#151515', 7.6, 1.18, 0.13, north);
  const poster = canvasTex(256, 320, (g, w, h) => {
    const grad = g.createLinearGradient(0, 0, 0, h); grad.addColorStop(0, '#13265a'); grad.addColorStop(1, '#4b2e6b');
    g.fillStyle = grad; g.fillRect(0, 0, w, h);
    g.fillStyle = '#e9b44c'; g.beginPath(); g.arc(180, 70, 26, 0, 7); g.fill();
    g.fillStyle = '#0c1634'; g.beginPath(); g.moveTo(0, 210); g.lineTo(70, 120); g.lineTo(120, 170); g.lineTo(170, 100); g.lineTo(256, 200); g.lineTo(256, 230); g.lineTo(0, 230); g.fill();
    g.fillStyle = '#fff'; g.font = 'bold 34px Georgia, serif'; g.textAlign = 'center'; g.fillText('TEAMWORK', w / 2, 270);
    g.font = 'italic 15px Georgia, serif'; g.fillText('Together, we file more.', w / 2, 298);
  });
  const posterPlane = new THREE.Mesh(new THREE.PlaneGeometry(0.94, 1.18), new THREE.MeshLambertMaterial({ map: poster }));
  posterPlane.position.set(7.6, 1.85, 0.162);
  north.add(posterPlane);

  // front door, exit sign, company sign, wall clock (west wall)
  box(1.35, 2.35, 0.05, C.woodDark, 0, 0, 0.13, west);
  box(1.15, 2.22, 0.06, C.wood, 0, 0, 0.15, west);
  box(0.09, 0.09, 0.08, '#d4b04a', 0.42, 1.05, 0.2, west);
  board(textTex(256, 96, [{ text: 'EXIT', size: 64, color: '#eaffea' }], { bg: '#1f8a3a', border: '#0c3a18' }), 0.62, 0.23, 0, 2.58, 0.18, 0, west, 0.07);
  board(textTex(512, 210, [
    { text: 'TOKEN & TALLY', size: 70, color: '#1b3a6b' },
    { text: 'STATIONERY SUPPLIES', size: 34, color: '#8a2b2b' },
    { text: 'est. 1994', size: 24, weight: 'italic', color: '#444' },
  ], { bg: '#f6f0dc', border: '#1b3a6b' }), 2.5, 1.02, 2.8, 1.95, 0.16, 0, west);
  board(textTex(400, 110, [{ text: 'COPY ROOM', size: 56 }], { bg: '#dfe8f1' }), 1.5, 0.42, -5.7, 2.3, 0.16, 0, west);
  board(textTex(400, 110, [{ text: 'RECORDS', size: 56 }], { bg: '#dfe8f1' }), 1.4, 0.42, 6.3, 2.3, 0.16, 0, west);

  const clock = new THREE.Group(); clock.position.set(-2.7, 2.25, 0.16); west.add(clock);
  const face = canvasTex(128, 128, (g) => {
    g.fillStyle = '#fbfaf4'; g.beginPath(); g.arc(64, 64, 62, 0, 7); g.fill();
    g.fillStyle = '#222'; for (let i = 0; i < 12; i++) { g.save(); g.translate(64, 64); g.rotate((i * Math.PI) / 6); g.fillRect(-2, -58, 4, i % 3 ? 8 : 14); g.restore(); }
  });
  const faceMesh = new THREE.Mesh(new THREE.CircleGeometry(0.3, 24), new THREE.MeshLambertMaterial({ map: face }));
  clock.add(faceMesh);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(0.31, 0.03, 6, 24), mat('#222'));
  clock.add(rim);
  const hand = (len, wdt) => { const p = new THREE.Group(); p.position.z = 0.02; clock.add(p); box(wdt, len, 0.01, '#111', 0, 0, 0, p); return p; };
  clockHands = { h: hand(0.15, 0.03), m: hand(0.24, 0.02) };

  // break room bulletin board (east wall)
  box(1.7, 0.95, 0.05, '#b98a52', 5.6, 1.35, 0.13, east);
  const notes = ['#fff7a8', '#ffd0d0', '#d2f0ff', '#e3ffd0', '#fff'];
  notes.forEach((c, i) => box(0.3, 0.26, 0.02, c, 5.0 + (i % 3) * 0.55 + (i > 2 ? 0.25 : 0), 1.45 + (i > 2 ? 0.42 : 0.05), 0.16, east));
  board(textTex(256, 96, [{ text: 'NO FOOD IN', size: 26, color: '#a1191c' }, { text: 'THE COPIER', size: 26, color: '#a1191c' }], { bg: '#fff', border: '#a1191c' }), 0.62, 0.23, 7.5, 1.7, 0.16, 0, east);
  box(0.9, 0.6, 0.05, '#7a5a2a', -6.6, 1.5, 0.13, east);
  board(textTex(256, 180, [{ text: 'M.B.A.', size: 46, weight: 'bold', font: 'Georgia, serif' }, { text: 'Business School', size: 22, weight: 'italic', font: 'Georgia, serif' }], { bg: '#f8f2de', border: '#a0822d' }), 0.78, 0.5, -6.6, 1.8, 0.17, 0, east, 0.02);
}

function drawEotm(winner) {
  return (g, w, h) => {
    g.fillStyle = '#f7f1dd'; g.fillRect(0, 0, w, h);
    g.fillStyle = '#7a1f2b'; g.fillRect(0, 0, w, 64);
    g.fillStyle = '#ffd66b'; g.textAlign = 'center'; g.font = 'bold 22px Georgia, serif';
    g.fillText('EMPLOYEE', w / 2, 28); g.fillText('OF THE MONTH', w / 2, 54);
    if (winner) {
      g.fillStyle = winner.shirt; g.fillRect(78, 196, 100, 46);
      g.fillStyle = winner.skin; g.fillRect(92, 96, 72, 92);
      g.fillStyle = winner.hair; g.fillRect(90, 88, 76, 20);
      g.fillStyle = '#111'; g.fillRect(108, 130, 9, 10); g.fillRect(140, 130, 9, 10);
      g.fillStyle = '#7a3a2a'; g.fillRect(114, 162, 28, 5);
      g.fillStyle = '#1f2c46'; g.font = 'bold 26px Tahoma, sans-serif'; g.fillText(winner.name, w / 2, 274, w - 20);
      g.font = '15px Tahoma, sans-serif'; g.fillStyle = '#555'; g.fillText(`${winner.tools} tasks done`, w / 2, 300, w - 20);
    } else {
      g.fillStyle = '#888'; g.font = 'italic 20px Georgia, serif'; g.fillText('Position open', w / 2, 190);
    }
    g.strokeStyle = '#b8902f'; g.lineWidth = 6; g.strokeRect(3, 3, w - 6, h - 6);
  };
}

// ---- desks
const deskSlots = []; // { kind, spot, screen, plaque, owner }
function nameplate(parent, x, y, z) {
  const tex = canvasTex(256, 64, drawPlate('VACANT', true));
  const m = new THREE.Mesh(new THREE.PlaneGeometry(0.56, 0.14), new THREE.MeshLambertMaterial({ map: tex }));
  m.position.set(x, y, z);
  parent.add(m);
  return tex;
}
function drawPlate(text, vacant) {
  return (g, w, h) => {
    g.fillStyle = vacant ? '#d7d2c4' : '#d9b85a'; g.fillRect(0, 0, w, h);
    g.strokeStyle = '#5a4a20'; g.lineWidth = 4; g.strokeRect(2, 2, w - 4, h - 4);
    g.fillStyle = vacant ? '#77705f' : '#2b220c'; g.font = `bold ${vacant ? 28 : 34}px Tahoma, sans-serif`;
    g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(text, w / 2, h / 2 + 1, w - 16);
  };
}
function clutter(g, seed) {
  const r = mulberry32(seed);
  if (r() < 0.8) for (let i = 0, n = 1 + ((r() * 4) | 0); i < n; i++) box(0.3, 0.02, 0.22, i % 2 ? '#f6f6f2' : '#e9e6da', -0.8 + r() * 0.04, 0.75 + i * 0.022, -1.15, g);
  if (r() < 0.7) cyl(0.045, 0.04, 0.1, ['#f5f3ee', '#2f5f9e', '#b33a3a', '#e3b23c'][(r() * 4) | 0], 0.72, 0.75, -0.92, g, 8);
  if (r() < 0.5) plant(-1.2, -1.28, 0.42, g);
  if (r() < 0.5) { box(0.14, 0.18, 0.02, '#6b4a1c', -0.42, 0.75, -1.35, g).rotation.x = -0.2; }
  if (r() < 0.45) box(0.18, 0.06, 0.06, '#b8262b', -0.55, 0.75, -0.85, g);
  if (r() < 0.4) { cyl(0.07, 0.09, 0.03, '#333', 1.05, 0.75, -1.3, g, 8); cyl(0.012, 0.012, 0.35, '#333', 1.05, 0.78, -1.3, g, 6); cyl(0.07, 0.11, 0.1, '#2f6d70', 1.05, 1.1, -1.24, g, 8); }
}
function cubicleRow(xs, cz, south) {
  const s = south ? 1 : -1, H = 1.35;
  const backZ = cz + s * 1.5, midZ = cz + s * 0.3;
  for (const x of xs) { box(3.0, H, 0.08, C.fabric, x, 0, backZ); box(3.04, 0.05, 0.12, C.trim, x, H, backZ); }
  for (const sx of [...new Set(xs.flatMap((x) => [x - 1.5, x + 1.5]))]) { box(0.08, H, 2.4, C.fabric, sx, 0, midZ); box(0.12, 0.05, 2.44, C.trim, sx, H, midZ); }
  for (const x of xs) {
    const g = group(x, cz, south ? Math.PI : 0);
    box(2.8, 0.05, 0.85, C.desk, 0, 0.7, -1.03, g);
    box(0.55, 0.7, 0.75, C.deskDark, 1.05, 0, -1.03, g);
    box(0.06, 0.7, 0.75, C.deskDark, -1.36, 0, -1.03, g);
    const mon = monitor(g, 0.1, 0.75, -1.2);
    box(0.5, 0.03, 0.17, C.beige, 0.05, 0.75, -0.78, g);
    box(0.07, 0.03, 0.1, C.beige, 0.45, 0.75, -0.8, g);
    clutter(g, hash(`${x},${cz}`));
    chair(g, 0.05, -0.33, Math.PI);
    const plaque = nameplate(g, 1.12, 1.16, 0.93);
    g.updateMatrixWorld(true);
    const seat = new THREE.Vector3(0.05, 0, -0.33).applyMatrix4(g.matrixWorld);
    deskSlots.push({ kind: 'cubicle', screen: mon.screen, plaque, owner: null,
      spot: { x: seat.x, z: seat.z, face: g.rotation.y + Math.PI, sit: true, via: [{ x, z: AISLE }] } });
  }
}
function buildBench() {
  const z = -8.38;
  box(5.8, 0.05, 0.8, C.desk, -6, 0.7, z);
  for (const x of [-8.8, -3.2]) box(0.06, 0.7, 0.7, C.deskDark, x, 0, z);
  for (const x of [-8.1, -6.7, -5.3, -3.9]) {
    const mon = monitor(scene, x, 0.75, z - 0.12);
    box(0.46, 0.03, 0.16, C.beige, x, 0.75, -8.05);
    chair(scene, x, -7.5, Math.PI, '#4a4f63');
    deskSlots.push({ kind: 'bench', screen: mon.screen, plaque: null, owner: null,
      spot: { x, z: -7.5, face: Math.PI, sit: true, via: [{ x, z: -6.4 }, { x: -1.5, z: -6.4 }, { x: -1.5, z: AISLE }] } });
  }
}

// ---- work stations
const ST = { files: [], copier: [], phone: [], whiteboard: [], delegate: [], coffee: [] };
const westVia = (z) => [{ x: -11.2, z }, { x: -11.2, z: AISLE }];
const northVia = () => [{ x: -1.5, z: -6.4 }, { x: -1.5, z: AISLE }];
const breakVia = (z) => [{ x: 11.6, z }, { x: 11.6, z: AISLE }];
const SPOTS = {
  door: { x: -13.3, z: AISLE, face: Math.PI / 2, via: [{ x: -12.4, z: AISLE }] },
  boss: { x: 11.6, z: -7.45, face: 0, sit: true, via: [{ x: 10.1, z: -7.45 }, { x: 10.1, z: -4.2 }, { x: 11.1, z: -2.5 }, { x: 11.1, z: AISLE }] },
};
let bossScreen = null;

function buildStations() {
  // filing cabinets on the west wall
  for (let i = 0; i < 5; i++) {
    const z = -8.3 + i * 0.9;
    box(0.75, 1.32, 0.85, C.cabinet, -13.58, 0, z);
    for (let d = 0; d < 4; d++) { box(0.02, 0.28, 0.76, '#97998d', -13.2, 0.06 + d * 0.32, z); box(0.04, 0.03, 0.18, '#d8d8d0', -13.18, 0.25 + d * 0.32, z); }
  }
  cyl(0.18, 0.16, 0.38, '#5a5f66', -13.4, 1.32, -8.0, scene, 8);
  ST.files.push(...[-7.6, -6.1, -4.7].map((z) => ({ x: -12.75, z, face: -Math.PI / 2, via: westVia(z) })));

  // copy room
  box(1.05, 0.92, 1.3, '#dcd7c7', -13.25, 0, 4.6);
  box(0.98, 0.07, 1.15, '#55585e', -13.25, 0.92, 4.6);
  box(0.25, 0.08, 0.5, '#55585e', -12.62, 0.86, 4.4);
  box(0.3, 0.05, 0.2, '#2c2f33', -12.7, 0.94, 5.0).rotation.z = 0.3;
  box(0.05, 0.04, 0.05, '#3ae36a', -12.6, 0.99, 5.06);
  box(0.4, 0.04, 0.5, '#f4f4f0', -12.62, 0.66, 4.3);
  box(0.8, 0.72, 0.9, C.cabinet, -13.4, 0, 6.8);
  box(0.6, 0.32, 0.55, '#d6d1c1', -13.4, 0.72, 6.8);
  box(0.5, 0.04, 0.38, '#f4f4f0', -13.4, 1.04, 6.8);
  for (let i = 0; i < 4; i++) box(0.5, 0.28, 0.38, i % 2 ? '#e9d9a8' : '#d8c890', -13.4 + (i % 2) * 0.06, i * 0.28, 8.3);
  ST.copier.push({ x: -12.15, z: 4.6, face: -Math.PI / 2, via: westVia(4.6) }, { x: -12.45, z: 6.8, face: -Math.PI / 2, via: westVia(6.8) },
    { x: -11.9, z: 3.5, face: -Math.PI / 2 - 0.4, via: westVia(3.5) });

  // phones & fax by the north window
  box(2.6, 0.74, 0.8, '#8f7a5c', 2.5, 0, -8.45);
  box(0.5, 0.18, 0.4, '#dcd6c4', 1.7, 0.74, -8.5); box(0.4, 0.02, 0.3, '#fff', 1.7, 0.92, -8.6);
  box(0.26, 0.09, 0.22, '#c8312f', 2.6, 0.74, -8.35); box(0.24, 0.05, 0.07, '#c8312f', 2.6, 0.83, -8.43);
  box(0.26, 0.09, 0.22, '#e4dcc6', 3.4, 0.74, -8.35); box(0.24, 0.05, 0.07, '#e4dcc6', 3.4, 0.83, -8.43);
  cyl(0.09, 0.09, 0.14, '#333', 3.85, 0.74, -8.55, scene, 10);
  ST.phone.push(...[1.8, 3.2, 4.5].map((x) => ({ x, z: -7.55, face: Math.PI, via: northVia() })));
  ST.whiteboard.push({ x: -2.2, z: -7.75, face: Math.PI, via: northVia() }, { x: -0.8, z: -7.75, face: Math.PI, via: northVia() });

  // manager's office
  const glass = new THREE.MeshLambertMaterial({ color: '#bfe2ee', transparent: true, opacity: 0.25, depthWrite: false });
  const glassX = (z, x1, x2) => {
    const L = x2 - x1, cx = (x1 + x2) / 2;
    box(L, 0.1, 0.12, C.trim, cx, 0, z); box(L, 0.08, 0.12, C.trim, cx, 2.4, z);
    box(L, 2.3, 0.03, glass, cx, 0.1, z).castShadow = false;
    const n = Math.max(1, Math.round(L / 1.4));
    for (let i = 0; i <= n; i++) box(0.08, 2.48, 0.12, C.trim, x1 + (L * i) / n, 0, z);
  };
  const glassZ = (x, z1, z2) => {
    const L = z2 - z1, cz = (z1 + z2) / 2;
    box(0.12, 0.1, L, C.trim, x, 0, cz); box(0.12, 0.08, L, C.trim, x, 2.4, cz);
    box(0.03, 2.3, L, glass, x, 0.1, cz).castShadow = false;
    const n = Math.max(1, Math.round(L / 1.4));
    for (let i = 0; i <= n; i++) box(0.12, 2.48, 0.08, C.trim, x, 0, z1 + (L * i) / n);
  };
  glassZ(9, -9, -2.5); glassX(-2.5, 9, 10.6); glassX(-2.5, 11.6, 14);
  box(0.05, 2.2, 0.92, C.wood, 11.64, 0, -2.98);
  board(textTex(256, 80, [{ text: 'MANAGER', size: 44 }], { bg: '#d9b85a', border: '#5a4a20' }), 0.72, 0.22, 10.0, 1.6, -2.46, 0, scene, 0.02);
  box(2.0, 0.06, 0.95, C.wood, 11.6, 0.7, -6.6);
  box(2.0, 0.7, 0.06, C.woodDark, 11.6, 0, -6.15);
  box(0.06, 0.7, 0.9, C.woodDark, 10.63, 0, -6.6); box(0.06, 0.7, 0.9, C.woodDark, 12.57, 0, -6.6);
  bossScreen = monitor(scene, 12.1, 0.76, -6.8, Math.PI).screen;
  bossScreen.material = screenMats.work;
  cyl(0.045, 0.04, 0.1, '#ffffff', 11.0, 0.76, -6.4, scene, 8);
  box(0.4, 0.05, 0.3, '#f6f6f2', 11.3, 0.76, -6.75);
  board(textTex(256, 64, [{ text: "WORLD'S OK-EST BOSS", size: 22, color: '#2b220c' }], { bg: '#d9b85a', border: '#5a4a20' }), 0.5, 0.12, 11.6, 0.88, -6.3, 0, scene, 0.02);
  chair(scene, 11.6, -7.45, 0, '#4a2a1c');
  stoolChair(scene, 11.0, -5.5, Math.PI, '#7a2f35');
  stoolChair(scene, 12.2, -5.5, Math.PI, '#7a2f35');
  box(0.42, 2.0, 1.6, C.woodDark, 13.75, 0, -5.0);
  for (let s = 0; s < 4; s++) {
    box(0.4, 0.03, 1.5, C.wood, 13.72, 0.12 + s * 0.48, -5.0);
    const r = mulberry32(s + 9);
    for (let z = -5.65; z < -4.3;) { const t = 0.06 + r() * 0.06; box(0.28, 0.28 + r() * 0.1, t, ['#8a2b2b', '#1f3a6b', '#2f6b3a', '#c9a227', '#444'][(r() * 5) | 0], 13.68, 0.15 + s * 0.48, z); z += t + 0.01; }
  }
  plant(13.4, -8.5, 1.2);
  ST.delegate.push({ x: 11.0, z: -5.5, face: Math.PI, sit: true, via: [{ x: 11.1, z: -4.2 }, { x: 11.1, z: -2.5 }, { x: 11.1, z: AISLE }] },
    { x: 12.2, z: -5.5, face: Math.PI, sit: true, via: [{ x: 11.1, z: -4.2 }, { x: 11.1, z: -2.5 }, { x: 11.1, z: AISLE }] });

  // break room
  const partition = (x, z, w, d, h = 1.1) => { box(w, h, d, C.fabric, x, 0, z); box(w + 0.02, 0.05, d + 0.04, C.trim, x, h, z); };
  partition(9.8, 2.5, 2.6, 0.08); partition(13.05, 2.5, 1.9, 0.08); partition(8.5, 5.75, 0.08, 6.5);
  for (const x of [11.05, 12.15]) box(0.08, 2.45, 0.08, C.trim, x, 0, 2.5);
  board(textTex(400, 100, [{ text: 'BREAK ROOM', size: 52, color: '#7a1f2b' }], { bg: '#f6efd8', border: '#7a1f2b' }), 1.2, 0.3, 11.6, 2.25, 2.5, 0, scene, 0.05);
  box(0.36, 0.95, 0.36, '#e9e9e6', 13.45, 0, 3.2);
  cyl(0.15, 0.15, 0.5, new THREE.MeshLambertMaterial({ color: '#7fb9e6', transparent: true, opacity: 0.75 }), 13.45, 0.95, 3.2, scene, 10);
  box(0.7, 0.9, 2.6, '#e6dfcc', 13.6, 0, 5.6);
  box(0.75, 0.04, 2.65, '#b9a27c', 13.6, 0.9, 5.6);
  box(0.32, 0.44, 0.3, '#222', 13.6, 0.94, 4.8);
  box(0.04, 0.04, 0.02, '#ff3b2b', 13.43, 1.25, 4.8);
  cyl(0.08, 0.07, 0.16, new THREE.MeshLambertMaterial({ color: '#4a2b16', transparent: true, opacity: 0.85 }), 13.52, 0.94, 4.8, scene, 8);
  box(0.5, 0.3, 0.36, '#dcdcdc', 13.6, 0.94, 6.3);
  for (let i = 0; i < 3; i++) cyl(0.045, 0.04, 0.1, ['#fff', '#2f5f9e', '#c9a227'][i], 13.55, 0.94, 5.3 + i * 0.15, scene, 8);
  box(0.85, 1.95, 0.9, '#a1262b', 13.5, 0, 8.25);
  box(0.03, 1.2, 0.6, '#1d1d24', 13.07, 0.55, 8.15);
  for (let r = 0; r < 4; r++) for (let c = 0; c < 3; c++) box(0.02, 0.12, 0.12, ['#f2c21b', '#3a9b3c', '#d4382c', '#2b5fc1'][(r + c) % 4], 13.05, 0.65 + r * 0.28, 7.95 + c * 0.16);
  cyl(0.7, 0.7, 0.04, '#efe9da', 10.0, 0.72, 6.4, scene, 16);
  cyl(0.06, 0.06, 0.72, C.metalDark, 10.0, 0, 6.4, scene, 8);
  cyl(0.35, 0.35, 0.03, C.metalDark, 10.0, 0, 6.4, scene, 12);
  stoolChair(scene, 9.15, 6.4, Math.PI / 2, '#d0702c');
  stoolChair(scene, 10.85, 6.4, -Math.PI / 2, '#d0702c');
  stoolChair(scene, 10.0, 7.25, Math.PI, '#d0702c');
  box(0.24, 0.03, 0.18, '#fff', 9.9, 0.76, 6.3);
  ST.coffee.push(
    { x: 12.75, z: 3.2, face: Math.PI / 2, via: breakVia(3.2) },
    { x: 12.75, z: 4.8, face: Math.PI / 2, via: breakVia(4.8) },
    { x: 12.75, z: 6.2, face: Math.PI / 2, via: breakVia(6.2) },
    { x: 9.15, z: 6.4, face: Math.PI / 2, sit: true, via: [{ x: 9.15, z: 5.3 }, { x: 11.6, z: 5.3 }, { x: 11.6, z: AISLE }] },
    { x: 10.85, z: 6.4, face: -Math.PI / 2, sit: true, via: breakVia(6.4) },
    { x: 10.0, z: 7.25, face: Math.PI, sit: true, via: breakVia(7.25) },
  );

  // stock shelves along the south edge — it is a stationery supplier, after all
  const BOXES = ['#f2f0ea', '#e9d9a8', '#c9dbe8', '#e8c0c0', '#cfe3c4', '#b38a5a', '#f2f0ea'];
  const shelf = (cx, cz, w) => {
    const r = mulberry32(hash('shelf' + cx));
    for (const dx of [-w / 2, w / 2]) box(0.06, 1.7, 0.6, C.metalDark, cx + dx, 0, cz);
    for (const y of [0.1, 0.65, 1.2, 1.68]) box(w, 0.04, 0.62, C.metal, cx, y, cz);
    for (const y of [0.14, 0.69, 1.24]) {
      let x = cx - w / 2 + 0.08;
      while (x < cx + w / 2 - 0.4) { const bw = 0.3 + r() * 0.25; box(bw - 0.04, 0.28 + r() * 0.18, 0.45, BOXES[(r() * BOXES.length) | 0], x + bw / 2, y, cz); x += bw; }
    }
  };
  shelf(-6, 7.9, 4.4);
  shelf(3, 7.9, 4.4);
  for (const [x, z, s] of [[-9.5, -2.0, 1], [7.0, -2.0, 1], [-9.5, 2.0, 0.9], [7.0, 2.0, 0.9], [-13.4, -1.6, 1.1], [-13.4, 1.6, 1.1], [7.8, -8.4, 1.1], [-0.3, 8.4, 1]]) plant(x, z, s);
}

buildFloor();
buildWalls();
cubicleRow([-7.5, -4.5], -3, false); cubicleRow([1.5, 4.5], -3, false);
cubicleRow([-7.5, -4.5], 3, true); cubicleRow([1.5, 4.5], 3, true);
buildBench();
buildStations();

// ---------------------------------------------------------------- people
const NAMES = ['Gary', 'Linda', 'Trevor', 'Deborah', 'Kevin', 'Sandra', 'Nigel', 'Brenda', 'Darren', 'Tracy', 'Keith', 'Janet', 'Malcolm',
  'Sharon', 'Clive', 'Wendy', 'Barry', 'Denise', 'Graham', 'Lorraine', 'Derek', 'Pauline', 'Russell', 'Yvonne', 'Colin', 'Beverley'];
const SHIRTS = ['#9db7d8', '#e2c46b', '#5f9e8f', '#a4504f', '#e2dccb', '#8a7fb3', '#e39b7b', '#6f8f4e', '#f0e6d2', '#4f6f9a', '#c77fa0'];
const PANTS = ['#2d3346', '#4a4d55', '#6b5a43', '#3b3b3b', '#7a6d58'];
const TIES = ['#7a1f2b', '#1f3a7a', '#2f6b3a', '#b08a2a', '#5a2a6b'];
const SKIN = ['#f1c9a5', '#e0ac7e', '#c68a5e', '#8d5a3b', '#f5d6bd', '#a8714a'];
const HAIR = ['#2b1d14', '#5a3a22', '#a8763e', '#d9c27a', '#7d7d7d', '#151515', '#8a3b1c'];
const lensMat = new THREE.MeshLambertMaterial({ color: '#cfe8f2', transparent: true, opacity: 0.35 });

function makePerson(seed, boss, temp) {
  const r = mulberry32(seed);
  const pick = (a) => a[Math.floor(r() * a.length)];
  const skin = pick(SKIN);
  const hair = boss ? '#8a8a8a' : pick(HAIR);
  const shirt = boss ? '#f4f1ea' : pick(SHIRTS);
  const jacket = boss ? '#2b2f3d' : r() < 0.2 ? pick(['#4a3b2f', '#3e4a5c', '#5b2f3a']) : null;
  const pants = boss ? '#2b2f3d' : pick(PANTS);
  const tie = boss ? '#9b1d24' : r() < 0.6 ? pick(TIES) : null;
  const top = jacket || shirt;
  const shortSleeves = !jacket && r() < 0.35;
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
  box(0.11, 0.13, 0.02, temp ? '#ffd23a' : '#ffffff', 0.16, 0.34, 0.17, upper);
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
  const style = boss ? 2 : Math.floor(r() * 5);
  if (style === 0) { box(0.44, 0.09, 0.42, hair, 0, 0.5, 0, head); box(0.44, 0.3, 0.06, hair, 0, 0.22, -0.2, head); }
  if (style === 1) { box(0.44, 0.18, 0.42, hair, 0, 0.48, 0, head); box(0.44, 0.3, 0.06, hair, 0, 0.22, -0.2, head); }
  if (style === 2) { for (const s of [-1, 1]) box(0.03, 0.16, 0.3, hair, s * 0.215, 0.28, -0.04, head); box(0.44, 0.2, 0.05, hair, 0, 0.24, -0.2, head); }
  if (style === 3) { box(0.45, 0.1, 0.43, hair, 0, 0.5, 0, head); box(0.46, 0.62, 0.08, hair, 0, -0.04, -0.21, head); for (const s of [-1, 1]) box(0.05, 0.36, 0.32, hair, s * 0.23, 0.18, -0.04, head); }
  if (style === 4) { box(0.52, 0.24, 0.5, hair, 0, 0.42, -0.01, head); box(0.52, 0.4, 0.12, hair, 0, 0.1, -0.2, head); for (const s of [-1, 1]) box(0.07, 0.28, 0.38, hair, s * 0.25, 0.2, -0.02, head); }
  if ((boss || r() < 0.22) && style < 3) box(0.18, 0.04, 0.03, hair, 0, 0.165, 0.215, head);
  if (boss || r() < 0.35) {
    for (const s of [-1, 1]) { box(0.15, 0.11, 0.015, '#2b2b2b', s * 0.1, 0.25, 0.21, head); box(0.11, 0.07, 0.02, lensMat, s * 0.1, 0.27, 0.218, head); }
  }
  const props = {
    mug: cyl(0.05, 0.045, 0.1, '#f5f3ee', 0, -0.47, 0.05, elbows[1], 8),
    phone: box(0.06, 0.22, 0.07, '#c8312f', 0, -0.47, 0.06, elbows[1]),
    marker: cyl(0.015, 0.015, 0.14, '#c0262b', 0, -0.52, 0.05, elbows[1], 6),
    folder: box(0.34, 0.03, 0.26, '#e5c77f', 0, 0.2, 0.34, upper),
    clipboard: box(0.24, 0.32, 0.02, '#8a5a2b', 0, -0.5, 0.08, elbows[0]),
  };
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
const PROPS_FOR = { coffee: 'mug', phone: 'phone', write: 'marker', read: 'folder', arms: 'clipboard' };
function walkPose(p) {
  const s = Math.sin(p);
  return { hipL: 0.55 * s, hipR: -0.55 * s, kneeL: 0.7 * Math.max(0, s), kneeR: 0.7 * Math.max(0, -s), shL: -0.45 * s, shR: 0.45 * s, elL: -0.3, elR: -0.3, y: 0.035 * Math.abs(Math.cos(p)) };
}

const actors = new Map();
class Actor {
  constructor(id, { name, boss = false, temp = false, seed }) {
    this.id = id; this.name = name; this.boss = boss; this.temp = temp;
    Object.assign(this, makePerson(seed, boss, temp));
    this.pose = Object.fromEntries(POSE_KEYS.map((k) => [k, 0]));
    this.at = SPOTS.door; this.target = SPOTS.door; this.targetKey = 'door';
    this.root.position.set(SPOTS.door.x, 0, SPOTS.door.z);
    this.root.rotation.y = this.heading = SPOTS.door.face;
    this.walking = false; this.route = null; this.phase = Math.random() * 6; this.tOffset = Math.random() * 20;
    this.anim = 'stand'; this.stayUntil = 0; this.arrivedAt = 0; this.desk = null; this.info = null;
    this.line = ''; this.bubbleText = ''; this.mood = 'think';

    this.tagEl = document.createElement('div');
    this.tagEl.className = 'tag' + (temp ? ' temp' : '') + (boss ? ' boss' : '');
    this.tagEl.addEventListener('pointerdown', (e) => e.stopPropagation());
    this.tagEl.addEventListener('click', () => select(this.id, true));
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
    for (const [k, m] of Object.entries(this.props)) m.visible = k === prop;
  }

  label(line, bubble, bubbleClass = '') {
    const role = this.boss ? 'Manager' : this.temp ? 'Temp' : '';
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

function intent(a, idleFor, sinceFor) {
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
  if (key !== actor.targetKey) {
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
    boss.label(`Keeping an eye on ${v?.name ?? 'someone'}`, 'Ahem.', 'boss');
    if (t - boss.arrivedAt > 7 || !stillSlacking) { boss.mode = 'home'; boss.goTo(SPOTS.boss, 'home'); boss.label('Back to the office', null); }
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
  boss.goTo(behind, 'inspect');
  boss.label(`Walking over to ${victim.name}…`, null);
  boss.onArrive = () => { victim.flusteredUntil = t + 99; setTimeout(() => { victim.flusteredUntil = 0; }, 4000); memo(`The Manager caught ${victim.name} playing Mines.`); };
}

// ---------------------------------------------------------------- syncing state
let source = 'live';
const live = { agents: [], offset: 0, connected: false, everConnected: false, hooks: false };
let selectedId = null, follow = false, emptyDismissed = false, eotmKey = '';
const prev = new Map();
const usedNames = () => new Set([...actors.values()].filter((a) => !a.leaving).map((a) => a.name));

function nameFor(id) {
  const used = usedNames(), h = hash(id);
  for (let i = 0; i < NAMES.length; i++) { const n = NAMES[(h + i) % NAMES.length]; if (!used.has(n)) return n; }
  return NAMES[h % NAMES.length] + ' ' + String.fromCharCode(65 + (h % 26)) + '.';
}

function sync(list) {
  const seen = new Set();
  for (const a of list) {
    seen.add(a.id);
    let actor = actors.get(a.id);
    if (!actor || actor.leaving) {
      if (actor) { actor.dispose(); actors.delete(a.id); }
      actor = new Actor(a.id, { name: nameFor(a.id), temp: a.temp, seed: hash(a.id) });
      actor.desk = allocDesk(a.temp);
      if (actor.desk) { actor.desk.owner = actor.id; actor.desk.spot.taken = actor.id; if (actor.desk.plaque) redraw(actor.desk.plaque, drawPlate(actor.name.toUpperCase(), false)); }
      actors.set(a.id, actor);
      const parent = a.parent && actors.get(a.parent);
      memo(a.temp ? `A temp, ${actor.name}, has arrived to help ${parent?.name ?? 'out'}.` : `${actor.name} clocked in${a.project ? ` (${a.project})` : ''}.`);
    }
    actor.info = a;
    const before = prev.get(a.id);
    if (before && before.status !== a.status) {
      if (a.status === 'waiting') memo(`${actor.name} needs your sign-off${a.tool ? ` (${a.tool})` : ''}.`, true);
      else if (a.status === 'idle') memo(`${actor.name} finished up and went for a coffee.`);
    } else if (before && a.status === 'working' && before.task !== a.task && TASK_WORDS[a.task] && (actor.lastMemo ?? 0) < performance.now() - 8000) {
      actor.lastMemo = performance.now();
      memo(`${actor.name} is ${TASK_WORDS[a.task]}${a.detail ? `: ${a.detail}` : '.'}`);
    }
    prev.set(a.id, { status: a.status, task: a.task });
  }
  for (const [id, actor] of actors) {
    if (actor === boss || seen.has(id) || actor.leaving) continue;
    leave(actor);
  }
  for (const id of [...prev.keys()]) if (!seen.has(id)) prev.delete(id);
  updateEotm(list);
  renderRoster();
  renderFile();
  updateEmpty();
}

function leave(actor) {
  actor.leaving = true;
  memo(`${actor.name} clocked out.`);
  if (actor.desk) { actor.desk.owner = null; actor.desk.spot.taken = null; if (actor.desk.plaque) redraw(actor.desk.plaque, drawPlate('VACANT', true)); actor.desk.screen.material = screenMats.off; actor.desk = null; }
  actor.label('Going home', null);
  actor.anim = 'stand';
  actor.goTo(SPOTS.door, 'door');
  actor.onArrive = () => { actor.dispose(); if (actors.get(actor.id) === actor) actors.delete(actor.id); if (selectedId === actor.id) select(null); };
}

function updateEotm(list) {
  let best = null;
  for (const a of list) { const actor = actors.get(a.id); if (actor && (!best || a.tools > best.tools)) best = { ...actor.look, name: actor.name, tools: a.tools }; }
  const key = best ? `${best.name}|${best.tools}` : '';
  if (key === eotmKey) return;
  eotmKey = key;
  redraw(eotmTex, drawEotm(best && best.tools > 0 ? best : null));
}

// ---------------------------------------------------------------- HUD
const MOOD_LABEL = { work: 'Working', think: 'Thinking', wait: 'Needs you', break: 'On break', slack: 'Slacking' };
function orderedActors() {
  const all = [...actors.values()].filter((a) => a !== boss && !a.leaving && a.info);
  const tops = all.filter((a) => !a.temp), out = [];
  for (const t of tops) { out.push([t, 0]); for (const c of all.filter((a) => a.temp && a.info.parent === t.id)) out.push([c, 1]); }
  for (const a of all) if (!out.some(([x]) => x === a)) out.push([a, a.temp ? 1 : 0]);
  return out;
}
function renderRoster() {
  const rows = orderedActors();
  const list = $('#roster-list');
  if (!rows.length) {
    list.innerHTML = `<div class="empty-row">${source === 'demo' ? 'Staff arriving…' : 'Nobody on shift.'}</div>`;
  } else {
    list.innerHTML = rows.map(([a, depth]) => `
      <div class="row${depth ? ' child' : ''}${a.id === selectedId ? ' sel' : ''}" role="listitem" tabindex="0" data-id="${esc(a.id)}">
        <span class="dot" style="background:${a.look.shirt}"></span>
        <span class="nm">${esc(a.name)}${a.temp ? '<span class="pill">Temp</span>' : ''}</span>
        <span class="st ${a.mood}">${MOOD_LABEL[a.mood] || ''}</span>
        <span class="sub">${a.info.machine ? `${esc(a.info.machine)} · ` : ''}${esc(a.info.project || 'unknown project')} · ${esc(a.line)}</span>
      </div>`).join('');
  }
  const counts = { work: 0, think: 0, wait: 0, break: 0, slack: 0 };
  for (const [a] of rows) counts[a.mood] = (counts[a.mood] || 0) + 1;
  $('#stats').innerHTML = `<span><b>${rows.length}</b> on shift</span><span><b>${counts.work + counts.think}</b> working</span>` +
    `<span style="color:${counts.wait ? 'var(--wait)' : 'inherit'}"><b>${counts.wait}</b> need you</span>` +
    `<span class="long"><b>${counts.break}</b> on break</span><span class="long"><b>${counts.slack}</b> slacking</span>`;
}
$('#roster-list').addEventListener('click', (e) => { const row = e.target.closest('.row'); if (row) select(row.dataset.id, true); });
$('#roster-list').addEventListener('keydown', (e) => { const row = e.target.closest('.row'); if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); select(row.dataset.id, true); } });
$('#roster-min').addEventListener('click', () => { const r = $('#roster'); r.classList.toggle('min'); $('#roster-min').textContent = r.classList.contains('min') ? '□' : '_'; });

function fmtAgo(sec) { sec = Math.max(0, Math.round(sec)); if (sec < 60) return `${sec}s`; if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`; return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`; }
function fmtTime(ts) { return new Date(ts * 1000 - (source === 'live' ? live.offset * 1000 : 0)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
function renderFile() {
  const actor = selectedId && actors.get(selectedId);
  const win = $('#file');
  if (!actor || !actor.info) { win.classList.add('hidden'); return; }
  win.classList.remove('hidden');
  const a = actor.info, parent = a.parent && actors.get(a.parent);
  $('#file-title').textContent = `Personnel File: ${actor.name}`;
  $('#file-body').innerHTML = `
    <dl>
      <dt>Position</dt><dd>${a.temp ? `Temp, reporting to ${esc(parent?.name ?? 'a specialist')}` : 'Specialist'}</dd>
      ${a.machine ? `<dt>Computer</dt><dd>${esc(a.machine)}</dd>` : ''}
      <dt>Project</dt><dd>${esc(a.project || '—')}</dd>
      ${a.title ? `<dt>Session</dt><dd>${esc(a.title)}</dd>` : ''}
      <dt>Assignment</dt><dd>${esc(a.assignment || '—')}</dd>
      <dt>Right now</dt><dd>${esc(actor.line)}</dd>
      <dt>For</dt><dd>${fmtAgo(nowSec() - a.since)}</dd>
      <dt>Tasks done</dt><dd>${a.tools}</dd>
      <dt>Badge no.</dt><dd><code>${esc(a.id.slice(0, 13))}</code></dd>
    </dl>
    <div class="log sunken">${(a.log || []).slice().reverse().map((l) => `<div>${fmtTime(l.t)}  ${esc(l.text)}</div>`).join('') || '<div>No entries yet.</div>'}</div>`;
}
$('#file-close').addEventListener('click', () => select(null));

function select(id, focus = false) {
  selectedId = id;
  follow = !!id && focus;
  for (const a of actors.values()) a.tagEl.classList.toggle('sel', a.id === id);
  renderRoster();
  renderFile();
}

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
  const show = source === 'live' && live.connected && !emptyDismissed && !orderedActors().length;
  $('#empty').classList.toggle('hidden', !show);
}
document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => { b.closest('.win').classList.add('hidden'); if (b.closest('#empty')) emptyDismissed = true; }));
$('#empty-demo').addEventListener('click', () => { emptyDismissed = true; setSource('demo'); });

function setConn() {
  const el = $('#conn');
  el.className = 'sunken ' + (source === 'demo' ? 'demo' : live.connected ? 'live' : 'off');
  el.querySelector('span').textContent = source === 'demo' ? 'Demo shift' : live.connected ? (live.hooks ? 'Live · hooks on' : 'Live') : 'Server offline';
  $('#demo').classList.toggle('on', source === 'demo');
  $('#demo').setAttribute('aria-pressed', String(source === 'demo'));
  document.querySelector('[data-cmd="demo"]').textContent = source === 'demo' ? 'Back to live office' : 'Demo shift';
}
$('#demo').addEventListener('click', () => setSource(source === 'demo' ? 'live' : 'demo'));

const startMenu = $('#startmenu');
$('#start').addEventListener('click', (e) => { e.stopPropagation(); startMenu.classList.toggle('hidden'); $('#start').classList.toggle('on', !startMenu.classList.contains('hidden')); });
document.addEventListener('click', (e) => { if (!startMenu.contains(e.target)) { startMenu.classList.add('hidden'); $('#start').classList.remove('on'); } });
startMenu.addEventListener('click', (e) => {
  const cmd = e.target.closest('button')?.dataset.cmd;
  if (!cmd) return;
  startMenu.classList.add('hidden'); $('#start').classList.remove('on');
  if (cmd === 'camera') { select(null); camera.position.copy(HOME_CAM.pos); controls.target.copy(HOME_CAM.target); }
  if (cmd === 'labels') { const off = labelRenderer.domElement.classList.toggle('off'); e.target.textContent = off ? 'Show name tags' : 'Hide name tags'; }
  if (cmd === 'demo') setSource(source === 'demo' ? 'live' : 'demo');
  if (cmd === 'lite') {
    try { localStorage.setItem('tt-lite', LITE ? '0' : '1'); } catch { /* ignore */ }
    params.set('lite', LITE ? '0' : '1');
    location.search = params.toString();
  }
  if (cmd === 'about') $('#about').classList.remove('hidden');
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { select(null); startMenu.classList.add('hidden'); } });

// ---------------------------------------------------------------- live feed & demo shift
function nowSec() { return Date.now() / 1000 + (source === 'live' ? live.offset : 0); }

function connect() {
  let es;
  try { es = new EventSource('/events'); } catch { return; }
  es.onopen = () => { live.connected = true; live.everConnected = true; setConn(); };
  es.onmessage = (e) => {
    const d = JSON.parse(e.data);
    live.offset = d.now - Date.now() / 1000;
    live.agents = d.agents || [];
    live.hooks = !!d.hooks;
    live.connected = true;
    setConn();
    if (source === 'live') sync(live.agents);
  };
  es.onerror = () => { live.connected = false; setConn(); updateEmpty(); };
}

const DEMO_PROJECTS = ['quarterly-report', 'invoice-api', 'website-redesign'];
const DEMO_JOBS = ['Fix the invoice rounding bug', 'Draft the Q3 summary', 'Make the homepage less beige'];
const DEMO_DETAIL = {
  typing: ['invoice.py', 'summary.md', 'styles.css', 'server.py'], files: ['README.md', 'q3_numbers.csv', 'TODO.txt', 'config.yaml'],
  copier: ['npm test', 'pytest -q', 'git status', 'make build'], phone: ['fax etiquette', 'toner prices 1994', 'docs.python.org'],
  whiteboard: ['Plan'], delegate: ['Look into the flaky tests', 'Survey the CSS'],
};
const DEMO_TOOL = { typing: 'Edit', files: 'Read', copier: 'Bash', phone: 'WebSearch', whiteboard: 'TodoWrite', delegate: 'Agent' };
class DemoShift {
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
let demo = null, demoTimer = null;
function setSource(next) {
  source = next;
  clearInterval(demoTimer);
  if (next === 'demo') {
    demo = new DemoShift();
    sync(demo.step());
    demoTimer = setInterval(() => sync(demo.step()), 1000);
    memo('Demo shift started. These are pretend employees.');
  } else {
    demo = null;
    sync(live.agents);
  }
  setConn();
  updateEmpty();
}

// ---------------------------------------------------------------- picking, camera, loop
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
  select(actor && actor !== boss ? actor.id : null, !!actor);
});
controls.addEventListener('start', () => { follow = false; });

function resize() {
  const w = host.clientWidth, h = host.clientHeight;
  renderer.setSize(w, h);
  labelRenderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);
resize();

boss = new Actor('manager', { name: 'Mr. Pemberton', boss: true, seed: 1994 });
boss.root.position.set(SPOTS.boss.x, 0, SPOTS.boss.z);
boss.at = boss.target = SPOTS.boss;
boss.root.rotation.y = boss.heading = 0;
boss.mode = 'home';
boss.anim = 'type';
actors.set(boss.id, boss);

const clockEl = $('#clock');
const clock = new THREE.Clock();
let lastHud = 0;
const tmp = new THREE.Vector3();
let simT = 0;
function step(dt, draw = true) {
  simT += dt;
  const t = simT, ns = nowSec();
  for (const actor of actors.values()) { if (actor !== boss) think(actor, t, ns); actor.update(dt, t); }
  bossBrain(t);

  for (const d of deskSlots) {
    const o = d.owner && actors.get(d.owner);
    const here = o && !o.walking && o.at === d.spot;
    d.screen.material = !here ? screenMats.off : o.anim === 'slack' ? screenMats.mines : o.anim === 'wait' ? screenMats.wait : screenMats.work;
  }
  bossScreen.material = boss.at === SPOTS.boss ? screenMats.work : screenMats.off;
  for (const w of walls) w.g.visible = camera.position.x * w.nx + camera.position.z * w.nz < w.edge;

  if (follow && selectedId && actors.get(selectedId)) {
    actors.get(selectedId).root.getWorldPosition(tmp);
    tmp.y = 1;
    controls.target.lerp(tmp, 1 - Math.exp(-dt * 4));
  }
  if (!draw) return;
  controls.update();
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);

  if (t - lastHud > 1) {
    lastHud = t;
    const d = new Date();
    clockEl.textContent = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    const hrs = d.getHours() % 12 + d.getMinutes() / 60, mins = d.getMinutes() + d.getSeconds() / 60;
    clockHands.h.rotation.z = -(hrs / 12) * Math.PI * 2;
    clockHands.m.rotation.z = -(mins / 60) * Math.PI * 2;
    renderRoster();
    renderFile();
  }
}
let pending = 0;
function frame() {
  pending += clock.getDelta();
  if (!LITE || pending >= 1 / 31) { step(Math.min(pending, 0.25)); pending = 0; }
  requestAnimationFrame(frame);
}
// Handy from the dev console; advance() fast-forwards the floor when the tab is in the background.
window.tokenTally = { actors, deskSlots, camera, controls, advance(sec) { for (let i = 0; i < sec * 30; i++) step(1 / 30, false); step(0); } };

setConn();
renderRoster();
document.querySelector('[data-cmd="lite"]').textContent = LITE ? 'High quality mode' : 'Low quality mode';
if (params.has('demo')) setSource('demo');
connect();
setTimeout(() => { if (!live.everConnected && source === 'live') { memo('Can’t reach the office server, so here’s a demo shift.'); setSource('demo'); } }, 3000);
requestAnimationFrame(frame);
