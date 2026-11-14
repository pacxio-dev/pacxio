// Extracted from the existing viewer; same transforms, UVs and crease strips.
function norm(v) {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

// 列主序 4×4（WebGL 的约定）
function perspective(fov, aspect, near, far) {
  const f = 1 / Math.tan(fov / 2);
  const nf = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}

function lookAt(eye, center, up) {
  const z = norm(sub(eye, center));
  const x = norm(cross(up, z));
  const y = cross(z, x);
  return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
}

function multiply(a, b) {
  const out = new Array(16).fill(0);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) out[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k];
  return out;
}

const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const FOLD_SECONDS = 6; // 从平铺到合上放多久
const STRIP_SEGMENTS = 6; // 压线过渡带分几段
const FLAT_ELEVATION = 62;
const FOOT_HEIGHT_MM = 3; // 离地面这么近的部分算"贴地"，地面阴影只按它们算
const STRIDE = 8; // 顶点：x y z u v nx ny nz

const ease = (x) => { x = Math.min(1, Math.max(0, x)); return x * x * (3 - 2 * x); };

function angleAt(keys, s) {
  let angle = 0;
  for (const [step, target] of keys) angle += (target - angle) * ease(s - (step - 1));
  return angle;
}

// 绕刀版平面内的直线转（列主序），和 folding._rotation 一样
function hingeMatrix([ox, oy], [x, y], deg) {
  const a = (deg * Math.PI) / 180, c = Math.cos(a), sn = Math.sin(a);
  const r = [c + x * x * (1 - c), x * y * (1 - c), -y * sn, 0, x * y * (1 - c), c + y * y * (1 - c), x * sn, 0, y * sn, -x * sn, c, 0];
  const tx = ox - (r[0] * ox + r[4] * oy), ty = oy - (r[1] * ox + r[5] * oy), tz = -(r[2] * ox + r[6] * oy);
  return [...r, tx, ty, tz, 1];
}

function poseMatrix(q, t) {
  const [x, y, z, w] = q;
  return [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0,
    2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0,
    2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0, t[0], t[1], t[2], 1];
}

// 部件的朝向：在关键位姿之间走（四元数归一化插值）
function poseAt(poses, s) {
  let a = poses[0], b = poses[poses.length - 1];
  for (let i = 0; i + 1 < poses.length; i++) if (s >= poses[i].step && s <= poses[i + 1].step) { a = poses[i]; b = poses[i + 1]; break; }
  if (s >= b.step) return poseMatrix(b.q, b.t);
  const k = ease((s - a.step) / Math.max(b.step - a.step, 1e-6));
  const sign = a.q.reduce((sum, v, i) => sum + v * b.q[i], 0) < 0 ? -1 : 1;
  const q = a.q.map((v, i) => v + (sign * b.q[i] - v) * k);
  const l = Math.hypot(...q);
  return poseMatrix(q.map((v) => v / l), a.t.map((v, i) => v + (b.t[i] - v) * k));
}

const xfPoint = (m, x, y, z) => [m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]];
const xfDir = (m, x, y, z) => [m[0] * x + m[4] * y + m[8] * z, m[1] * x + m[5] * y + m[9] * z, m[2] * x + m[6] * y + m[10] * z];

// 一块面的静态网格：正面（贴刀版图）、背面（纸色）、切边（纸色）
export function panelBuffers(gl, part, pn) {
  const [sx, ox, sy, oy] = part.uv, t = part.caliper;
  const front = [], back = [], walls = [];
  const tris = pn.triangles;
  const at = (i) => [pn.vertices[2 * i], pn.vertices[2 * i + 1]];
  for (let i = 0; i < tris.length; i += 3) {
    for (const idx of [tris[i], tris[i + 1], tris[i + 2]]) {
      const [x, y] = at(idx);
      front.push(x, y, 0, sx * x + ox, sy * y + oy, 0, 0, 1);
      back.push(x, y, -t, 0, 0, 0, 0, -1);
    }
  }
  for (const r of pn.rings) {
    const n = r.length / 2;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      const x0 = r[2 * i], y0 = r[2 * i + 1], x1 = r[2 * j], y1 = r[2 * j + 1];
      const l = Math.hypot(x1 - x0, y1 - y0) || 1, nx = (y1 - y0) / l, ny = -(x1 - x0) / l; // 外环逆时针：右手边是外面
      for (const [x, y, z] of [[x0, y0, 0], [x1, y1, 0], [x1, y1, -t], [x0, y0, 0], [x1, y1, -t], [x0, y0, -t]]) walls.push(x, y, z, 0, 0, nx, ny, 0);
    }
  }
  const data = new Float32Array([...front, ...back, ...walls]);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  return { buf, frontCount: front.length / STRIDE, restCount: (back.length + walls.length) / STRIDE };
}

// 过渡带每次折叠都重算：外面一个缓冲，里面和两头一个缓冲
export function allocStrips(part) {
  const quads = part.strips.length * STRIP_SEGMENTS;
  part.outer = new Float32Array(quads * 6 * STRIDE);
  part.inner = new Float32Array(quads * 18 * STRIDE);
}

export function posePart(fold) {
  const s = fold.s;
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const part of fold.parts) {
    const pose = poseAt(part.poses, s);
    for (const pn of part.panels) {
      const local = pn.parent < 0 ? IDENTITY : (() => {
        const deg = angleAt(pn.keys, s);
        let m = multiply(part.panels[pn.parent].local, hingeMatrix(pn.origin, pn.axis, deg));
        const last = pn.keys.length ? pn.keys[pn.keys.length - 1][1] : 0;
        if (pn.layer && last) m = multiply(m, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, (-pn.layer * part.caliper * deg) / last, 1]);
        return m;
      })();
      pn.local = local;
      pn.world = multiply(pose, local);
      for (const r of pn.rings) for (let i = 0; i < r.length; i += 2) {
        const v = xfPoint(pn.world, r[i], r[i + 1], 0);
        for (let k = 0; k < 3; k++) { if (!(k === 2 && pn.below_ground)) lo[k] = Math.min(lo[k], v[k]); hi[k] = Math.max(hi[k], v[k]); }
      }
    }
  }
  // 放到地面上：包围盒左前下角是原点（相机、地面阴影都按它算）
  const shift = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -lo[0], -lo[1], -lo[2], 1];
  for (const part of fold.parts) {
    for (const pn of part.panels) pn.model = multiply(shift, pn.world);
    updateStrips(part, fold.gl);
  }
  fold.size = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
  fold.ground = ease(s - (fold.groundStep - 1));
  fold.feet = footprints(fold, lo);
}

// 每个部件贴地部分的矩形（x0, y0, 宽, 高）；叠在一起的合成一个，免得阴影叠两层
function footprints(fold, lo) {
  const rects = [];
  for (const part of fold.parts) {
    const a = [Infinity, Infinity], b = [-Infinity, -Infinity];
    for (const pn of part.panels) {
      if (pn.below_ground) continue;
      for (const r of pn.rings) for (let i = 0; i < r.length; i += 2) {
        const v = xfPoint(pn.world, r[i], r[i + 1], 0);
        if (v[2] - lo[2] > FOOT_HEIGHT_MM) continue;
        for (let k = 0; k < 2; k++) { a[k] = Math.min(a[k], v[k] - lo[k]); b[k] = Math.max(b[k], v[k] - lo[k]); }
      }
    }
    if (a[0] > b[0]) continue;
    const hit = rects.find((q) => a[0] <= q[1][0] && b[0] >= q[0][0] && a[1] <= q[1][1] && b[1] >= q[0][1]);
    if (hit) { hit[0] = [Math.min(hit[0][0], a[0]), Math.min(hit[0][1], a[1])]; hit[1] = [Math.max(hit[1][0], b[0]), Math.max(hit[1][1], b[1])]; }
    else rects.push([a, b]);
  }
  return rects.map(([a, b]) => [a[0], a[1], b[0] - a[0], b[1] - a[1]]);
}

// 过渡带：两块面各让出一段，中间按折角弯成圆角（贝塞尔近似圆弧）；有 layer 错位时自然变成一小段 S 形
function updateStrips(part, gl) {
  const [sx, ox, sy, oy] = part.uv, t = part.caliper;
  let no = 0, ni = 0;
  const out = part.outer, inn = part.inner;
  const putO = (p, u, v, n) => { out[no++] = p[0]; out[no++] = p[1]; out[no++] = p[2]; out[no++] = u; out[no++] = v; out[no++] = n[0]; out[no++] = n[1]; out[no++] = n[2]; };
  const putI = (p, n) => { inn[ni++] = p[0]; inn[ni++] = p[1]; inn[ni++] = p[2]; inn[ni++] = 0; inn[ni++] = 0; inn[ni++] = n[0]; inn[ni++] = n[1]; inn[ni++] = n[2]; };
  for (const st of part.strips) {
    const mp = part.panels[st.parent].model, mc = part.panels[st.child].model;
    const [ox0, oy0] = st.origin, [nx, ny] = st.to_parent, [ax, ay] = st.along, h = st.half;
    const pp = xfPoint(mp, ox0 + nx * h, oy0 + ny * h, 0), pc = xfPoint(mc, ox0 - nx * h, oy0 - ny * h, 0);
    const tp = xfDir(mp, -nx, -ny, 0), tc = xfDir(mc, nx, ny, 0); // 都朝向压线
    const np = xfDir(mp, 0, 0, 1), nc = xfDir(mc, 0, 0, 1), d = xfDir(mp, ax, ay, 0);
    const theta = Math.acos(Math.max(-1, Math.min(1, -dot(tp, tc))));
    const handle = theta < 1e-3 ? (2 * h) / 3 : (4 / 3) * Math.tan(theta / 4) * (h / Math.tan(theta / 2));
    const c1 = pp.map((v, k) => v + tp[k] * handle), c2 = pc.map((v, k) => v + tc[k] * handle);
    const ring = [];
    for (let i = 0; i <= STRIP_SEGMENTS; i++) {
      const u = i / STRIP_SEGMENTS, w = 1 - u;
      const p = pp.map((v, k) => w * w * w * v + 3 * w * w * u * c1[k] + 3 * w * u * u * c2[k] + u * u * u * pc[k]);
      const n = norm(np.map((v, k) => v * w + nc[k] * u));
      const range = u < 0.5 ? [st.parent_end, st.crease, u * 2] : [st.crease, st.child_end, u * 2 - 1];
      const l0 = range[0][0] + (range[1][0] - range[0][0]) * range[2], l1 = range[0][1] + (range[1][1] - range[0][1]) * range[2];
      const across = h * (1 - 2 * u);
      const uvAt = (l) => { const x = ox0 + nx * across + ax * l, y = oy0 + ny * across + ay * l; return [sx * x + ox, sy * y + oy]; };
      ring.push({ a: p.map((v, k) => v + d[k] * l0), b: p.map((v, k) => v + d[k] * l1), n, ua: uvAt(l0), ub: uvAt(l1) });
    }
    for (let i = 0; i < STRIP_SEGMENTS; i++) {
      const r0 = ring[i], r1 = ring[i + 1];
      putO(r0.a, ...r0.ua, r0.n); putO(r0.b, ...r0.ub, r0.n); putO(r1.b, ...r1.ub, r1.n);
      putO(r0.a, ...r0.ua, r0.n); putO(r1.b, ...r1.ub, r1.n); putO(r1.a, ...r1.ua, r1.n);
      const inward = (q, n) => q.map((v, k) => v - n[k] * t);
      const ia0 = inward(r0.a, r0.n), ib0 = inward(r0.b, r0.n), ia1 = inward(r1.a, r1.n), ib1 = inward(r1.b, r1.n);
      const m0 = r0.n.map((v) => -v), m1 = r1.n.map((v) => -v);
      putI(ia0, m0); putI(ib0, m0); putI(ib1, m1); putI(ia0, m0); putI(ib1, m1); putI(ia1, m1);
      const da = d.map((v) => -v);
      putI(r0.a, da); putI(r1.a, da); putI(ia1, da); putI(r0.a, da); putI(ia1, da); putI(ia0, da);
      putI(r0.b, d); putI(r1.b, d); putI(ib1, d); putI(r0.b, d); putI(ib1, d); putI(ib0, d);
    }
  }
  gl.bindBuffer(gl.ARRAY_BUFFER, part.outerBuf);
  gl.bufferData(gl.ARRAY_BUFFER, out, gl.DYNAMIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, part.innerBuf);
  gl.bufferData(gl.ARRAY_BUFFER, inn, gl.DYNAMIC_DRAW);
  part.outerCount = no / STRIDE;
  part.innerCount = ni / STRIDE;
}
