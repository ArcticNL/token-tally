import * as THREE from 'three';
import { OrbitControls } from '../vendor/OrbitControls.js';
import { CSS2DRenderer } from '../vendor/CSS2DRenderer.js';
import { hash, mulberry32 } from './utils.js';

// The room owns its material cache, geometry helpers and rendering resources.
export function createOfficeScene({ host, lite: LITE }) {
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

  function setDeskLabel(desk, text, vacant) {
    if (desk.plaque) redraw(desk.plaque, drawPlate(text, vacant));
  }
  function clearDeskScreen(desk) { desk.screen.material = screenMats.off; }
  function setEmployeeOfMonth(winner) { redraw(eotmTex, drawEotm(winner)); }

  function updateScreens(actors, boss) {
    for (const d of deskSlots) {
      const o = d.owner && actors.get(d.owner);
      const here = o && !o.walking && o.at === d.spot;
      d.screen.material = !here ? screenMats.off : o.anim === 'slack' ? screenMats.mines : o.anim === 'wait' ? screenMats.wait : screenMats.work;
    }
    bossScreen.material = boss.at === SPOTS.boss ? screenMats.work : screenMats.off;
  }

  function updateWalls() {
    for (const w of walls) w.g.visible = camera.position.x * w.nx + camera.position.z * w.nz < w.edge;
  }

  function updateClock(d) {
      const hrs = d.getHours() % 12 + d.getMinutes() / 60, mins = d.getMinutes() + d.getSeconds() / 60;
      clockHands.h.rotation.z = -(hrs / 12) * Math.PI * 2;
      clockHands.m.rotation.z = -(mins / 60) * Math.PI * 2;
  }

  function resize() {
    const w = host.clientWidth, h = host.clientHeight;
    renderer.setSize(w, h);
    labelRenderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  function render() {
    controls.update();
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  }

  return {
    scene, renderer, labelRenderer, camera, controls, HOME_CAM, box, cyl,
    layout: { deskSlots, ST, SPOTS, westVia, breakVia },
    setDeskLabel, clearDeskScreen, setEmployeeOfMonth,
    updateScreens, updateWalls, updateClock, resize, render,
  };
}
