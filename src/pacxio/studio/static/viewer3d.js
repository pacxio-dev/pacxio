import { tr } from "./i18n.js";
// 可转动的 3D 效果图（ 节）：自己写的小 WebGL 查看器，不用第三方库。
// 读 scene v2 的几何、底色与独立材质 / 高度遮罩；片元中计算随视角、光向变化的反光。
// scene v3 按刀版折叠（）：每块面一个有纸厚的网格，沿压线转，压线处一条过渡带；
// 折叠算法和 mockup/folding.py 同一套，改这里要一起改。
// scene v1 保留原有贴图明暗路径。只在交互、加载、播放或尺寸变化时重画，不跑空闲动画循环。

const FOV = (30 * Math.PI) / 180;
const AMBIENT = 0.66; // 与 mockup/render.py 的光照一致
const KEY_WEIGHT = 0.3;
const FILL_WEIGHT = 0.16;
const KEY_LIGHT = norm([-0.35, -0.8, 0.5]);
const FILL_LIGHT = norm([0.55, 0.7, 0.3]);
const DEFAULT_VIEW = { az: 35, el: 24, zoom: 1 };
const DEFAULT_LIGHTING = { direction: -30, brightness: 1 }; // 只有自然光一种灯光：可调光从哪边来、整体亮度
const MAX_CANVAS_PX = 2048;
const BACKGROUNDS = [
  [0.961, 0.961, 0.953], // 和工作台的浅纸色一致（#f5f5f3）
  [0.043, 0.043, 0.051], // 墨色（#0b0b0d）
  [1, 1, 1],
];

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

function shade(n) {
  return Math.min(1, AMBIENT + KEY_WEIGHT * Math.max(0, dot(n, KEY_LIGHT)) + FILL_WEIGHT * Math.max(0, dot(n, FILL_LIGHT)));
}

const VS = `attribute vec3 p; attribute vec2 uv; attribute vec3 nrm; uniform mat4 m, model; varying vec2 v; varying vec3 pos, vn;
void main() { v = uv; vec4 w = model * vec4(p, 1.0); pos = w.xyz; vn = (model * vec4(nrm, 0.0)).xyz; gl_Position = m * w; }`;
const FS = `precision highp float;
varying vec2 v; varying vec3 pos, vn;
uniform sampler2D t, material, heightMap, region;
uniform float s, a, modern, exposure, relief, selected, cavity, cavityMM, ground, clipGround;
uniform vec3 eye, light, tangent, bitangent, box;
uniform vec2 texel, faceMM;
uniform vec4 foot;
const float PI = 3.14159265;
vec3 linearColor(vec3 c) { return mix(c/12.92, pow((c+0.055)/1.055,vec3(2.4)), step(vec3(0.04045),c)); }
vec3 srgb(vec3 c) { c=max(c,vec3(0.0)); return mix(c*12.92,1.055*pow(c,vec3(1.0/2.4))-0.055,step(vec3(0.0031308),c)); }
float softbox(vec3 r, vec3 l, float rough) {
  vec3 x=normalize(cross(l,vec3(0.01,0.02,1.0))); vec3 y=cross(l,x);
  float blur=0.07+rough*0.65;
  float front=smoothstep(0.0,0.35,dot(r,l));
  return front*exp(-pow(abs(dot(r,x))/(0.18+blur),4.0)-pow(abs(dot(r,y))/(0.55+blur),4.0));
}
// 竖条窗光：在方向 s、和相机差不多高；w 为水平半宽、hgt 为竖直半高（方向向量空间）。
// 左右边缘很硬：亮面是镜面反射，映出来的是一条边缘清楚的亮条；边缘一软就像漫反射，整面发白
float strip(vec3 r, vec3 s, float w, float hgt) {
  vec3 xh=normalize(cross(s,vec3(0.0,0.0,1.0)));
  return smoothstep(0.0,0.3,dot(r,s))*exp(-pow(abs(dot(r,xh))/w,8.0)-pow(abs(r.z-s.z)/hgt,4.0));
}
// 自然光环境：一条窗光、上方明亮的天空、下方暗的地面；亮面材料（亮膜、UV、烫金）映的就是它。
// 直立的面朝下映地面，颜色深；顶面朝上映天空，亮面盒子顶上有一层天光。
// 窗光比主光再往外偏 4°：默认视角、默认光向下映在正面偏右，主图和大字大多不被盖住；随“方向”转动
float naturalEnv(vec3 r, vec3 l) {
  vec3 key=normalize(vec3(l.x*0.998+l.y*0.070,l.y*0.998-l.x*0.070,0.0));
  return 5.0*strip(r,key,0.025,0.55)+1.3*smoothstep(-0.05,0.6,r.z)+0.12;
}
void main() {
  // 插脚穿出底面的那一小截：实物会压弯，不画（从底下看就是插脚塞在孔里）
  if(clipGround>0.5 && a<0.5 && pos.z<-0.001) discard;
  vec4 c=texture2D(t,v);
  if(a>0.5 && modern>0.5) {
    // 地面阴影：贴着盒底的一圈接触阴影，加一片往背光一侧偏的柔和投影（按盒底矩形算）
    float big=max(foot.z,foot.w);
    vec2 half_=foot.zw*0.5, q=pos.xy-foot.xy-half_;
    float d=length(max(abs(q)-half_,0.0));
    vec2 off=-normalize(light.xy+vec2(0.0001))*0.05*big;
    float d2=length(max(abs(q-off)-half_,0.0));
    gl_FragColor=vec4(0.0,0.0,0.0,ground*min(0.42*exp(-d/(0.02*big))+0.22*exp(-pow(d2/(0.08*big),2.0)),0.6)); return;
  }
  if(a>0.5) { gl_FragColor=vec4(0.0,0.0,0.0,c.a*0.22); return; }
  if(c.a<0.5) discard;
  if(modern<0.5) { gl_FragColor=vec4(c.rgb*s,1.0); return; }
  vec4 mat=texture2D(material,v);
  float rough=clamp(mat.r,0.08,0.98), metal=mat.g, coat=mat.b;
  float dx=(texture2D(heightMap,v+vec2(texel.x,0.0)).r-texture2D(heightMap,v-vec2(texel.x,0.0)).r)*relief/(2.0*texel.x*faceMM.x);
  float dy=(texture2D(heightMap,v+vec2(0.0,texel.y)).r-texture2D(heightMap,v-vec2(0.0,texel.y)).r)*relief/(2.0*texel.y*faceMM.y);
  vec3 n=normalize(normalize(vn)-tangent*dx-bitangent*dy), vv=normalize(eye-pos), l=normalize(light), h=normalize(vv+l);
  float nv=max(dot(n,vv),0.001), nl=max(dot(n,l),0.0), nh=max(dot(n,h),0.0), vh=max(dot(vv,h),0.0);
  vec3 base=linearColor(c.rgb);
  vec3 f0=mix(vec3(0.04),base,metal);
  vec3 fres=f0+(1.0-f0)*pow(1.0-vh,5.0);
  float alpha=rough*rough, a2=alpha*alpha, den=nh*nh*(a2-1.0)+1.0;
  float d=a2/(PI*den*den+0.0001);
  float k=(rough+1.0)*(rough+1.0)/8.0;
  float g=(nv/(nv*(1.0-k)+k))*(nl/(nl*(1.0-k)+k));
  vec3 spec=fres*d*g/(4.0*nv*max(nl,0.001)+0.0001);
  vec3 r=reflect(-vv,n);
  float env=softbox(r,l,rough)+0.45*softbox(r,normalize(vec3(-l.x,0.6,0.5)),rough);
  // 自然光：天空（上、偏冷）和地面反光（下、偏暖）的环境光 + 窗光（包裹漫射，明暗过渡柔和）；
  // 越靠近地面越暗：整面从上到下缓缓暗一点（地面挡住下半边的天光），贴地 20%（最多 30 mm）再压暗
  float wrap=max(dot(n,l)+0.3,0.0)/1.3;
  float ao=mix(0.78,1.0,smoothstep(0.0,min(0.2*box.z,30.0),pos.z))*mix(0.9,1.0,clamp(pos.z/max(box.z,1.0),0.0,1.0));
  vec3 daylight=(mix(vec3(0.36,0.33,0.29),vec3(0.64,0.66,0.70),0.5+0.5*n.z)+vec3(1.0,0.97,0.92)*0.62*wrap)*ao;
  vec3 result=base*(1.0-metal)*daylight+f0*mix(0.02+env*0.5*pow(1.0-rough,2.0),0.12+env*1.6,metal)+spec*nl*mix(0.08,0.22,metal);
  // 涂层（覆膜 / 光油）反光：哑面按粗糙度化开成一大片柔光，主光和补光都映出来。
  // 光滑的面（粗糙度 < 约 0.3：亮膜、亮光油、UV、烫金）这层柔光只留两成：亮面不散射，颜色更深更实，
  // 反光交给下面的窗光和天光；哑膜、纸 gloss=0，不受影响
  float gloss=1.0-smoothstep(0.22,0.45,rough), sky=naturalEnv(r,l);
  float coatBlur=mix(0.12,rough,step(0.3,rough));
  float clear=softbox(r,l,coatBlur)+0.45*softbox(r,normalize(vec3(-l.x,0.6,0.5)),coatBlur);
  result+=vec3(coat*(1.0-0.8*gloss)*(0.18*clear+0.06*pow(1.0-nv,5.0)));
  result+=vec3(gloss*coat*(0.04+0.96*pow(1.0-nv,5.0))*sky)+f0*metal*gloss*sky*0.35;
  // 击凸 / 压凹的凹处压暗（击凸脚下一圈、压凹的坑壁和靠边的坑底）：和光向无关，正面光下轮廓也看得出。
  // 拿周围两圈的平均高度和这一点比，周围比这里高就是凹处；旧场景没有 relief_cavity，不压暗
  if(cavity>0.0) {
    float h0=texture2D(heightMap,v).r, around=0.0;
    for(int i=0;i<8;i++) {
      vec2 d=vec2(cos(float(i)*0.785398),sin(float(i)*0.785398))*cavityMM/faceMM;
      around+=texture2D(heightMap,v+d).r+texture2D(heightMap,v+2.0*d).r;
    }
    result*=1.0-cavity*clamp((around/16.0-h0)*2.5,0.0,1.0);
  }
  if(texture2D(heightMap,v).g>0.5) result*=0.72+0.35*cos(vec3(0.0,2.1,4.2)+dot(r,l)*13.0);
  result*=exposure;
  // 亮部柔和收进去（高光肩部），不截成死白；按最亮的通道等比例缩，色相不变，中间调不动
  float peak=max(max(result.r,result.g),result.b);
  float rolled=peak<0.75?peak:0.75+0.25*(1.0-exp(-(peak-0.75)/0.25));
  result*=rolled/max(peak,0.0001);
  result=srgb(result);
  float mask=texture2D(region,v).r*selected;
  result=mix(result,vec3(0.05,0.75,0.95),mask*0.55);
  gl_FragColor=vec4(clamp(result,0.0,1.0),1.0);
}`;

function compile(gl, type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
  return sh;
}

function shadowTexture(gl) {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(64, 64, 8, 64, 64, 64);
  grad.addColorStop(0, "rgba(0,0,0,1)");
  grad.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  return texture(gl, c);
}

function texture(gl, source) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  return t;
}

// 整张刀版贴图：缩到 2 的幂再做 mipmap，盒子转远、面变小时字不闪
function atlasTexture(gl, img) {
  const limit = Math.min(gl.getParameter(gl.MAX_TEXTURE_SIZE), 4096);
  const pot = (n) => Math.min(limit, 2 ** Math.ceil(Math.log2(n)));
  const c = document.createElement("canvas");
  c.width = pot(img.width);
  c.height = pot(img.height);
  const g = c.getContext("2d");
  g.imageSmoothingQuality = "high";
  g.drawImage(img, 0, 0, c.width, c.height);
  const t = texture(gl, c);
  gl.generateMipmap(gl.TEXTURE_2D);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  const aniso = gl.getExtension("EXT_texture_filter_anisotropic");
  if (aniso) gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(8, gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT)));
  return t;
}

function pixel(rgba) { const c=document.createElement("canvas"); c.width=c.height=1; c.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(rgba),1,1),0,0); return c; }

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(tr("Could not load texture: {1}", url)));
    img.src = url;
  });
}

function shadowQuad(x0, y0, w, h, m) {
  const tl = [x0 - m, y0 + h + m, -0.3], tr = [x0 + w + m, y0 + h + m, -0.3], br = [x0 + w + m, y0 - m, -0.3], bl = [x0 - m, y0 - m, -0.3];
  return [...tl, ...tr, ...br, ...tl, ...br, ...bl];
}

// ---- scene v3：按刀版折叠 ----
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
function panelBuffers(gl, part, pn) {
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

async function loadFold(scene, { gl, loc, fileUrl, loadImage, texture, atlasTexture, pixel }) {
  const parts = await Promise.all(scene.parts.map(async (p) => {
    const [base, material, height] = await Promise.all(["base", "material", "height"].map((k) => loadImage(fileUrl(p.textures[k]))));
    const part = { ...p, base: atlasTexture(gl, base), material: texture(gl, material), height: texture(gl, height), texel: [1 / height.width, 1 / height.height], paperTex: texture(gl, pixel([...p.paper, 255])), region: null, textures: p.textures };
    for (const pn of part.panels) Object.assign(pn, panelBuffers(gl, part, pn));
    allocStrips(part);
    part.outerBuf = gl.createBuffer();
    part.innerBuf = gl.createBuffer();
    return part;
  }));
  return { parts, steps: scene.steps, groundStep: scene.ground_step, poseEase: Math.max(...scene.parts.map((p) => p.poses[1].step)), s: scene.steps, size: [1, 1, 1], ground: 1, shadow: gl.createBuffer(), gl };
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

function drawFold(fold, { gl, loc, bind, emptyRegion, neutralMaterial, neutralHeight, sceneInfo }) {
  const attribs = () => {
    const stride = STRIDE * 4;
    gl.enableVertexAttribArray(loc.p); gl.enableVertexAttribArray(loc.uv); gl.enableVertexAttribArray(loc.nrm);
    gl.vertexAttribPointer(loc.p, 3, gl.FLOAT, false, stride, 0);
    gl.vertexAttribPointer(loc.uv, 2, gl.FLOAT, false, stride, 12);
    gl.vertexAttribPointer(loc.nrm, 3, gl.FLOAT, false, stride, 20);
  };
  for (const part of fold.parts) {
    const printed = () => { bind(0, "t", part.base); bind(1, "material", part.material); bind(2, "heightMap", part.height); bind(3, "region", part.region || emptyRegion); };
    const paper = () => { bind(0, "t", part.paperTex); bind(1, "material", neutralMaterial); bind(2, "heightMap", neutralHeight); bind(3, "region", emptyRegion); };
    gl.uniform2fv(loc.texel, part.texel);
    gl.uniform2fv(loc.faceMM, part.tex_mm);
    for (const pn of part.panels) {
      gl.uniformMatrix4fv(loc.model, false, new Float32Array(pn.model));
      gl.uniform3fv(loc.tangent, norm(xfDir(pn.model, 1, 0, 0)));
      gl.uniform3fv(loc.bitangent, norm(xfDir(pn.model, 0, -1, 0)));
      gl.bindBuffer(gl.ARRAY_BUFFER, pn.buf);
      attribs();
      printed();
      gl.drawArrays(gl.TRIANGLES, 0, pn.frontCount);
      paper();
      gl.drawArrays(gl.TRIANGLES, pn.frontCount, pn.restCount);
    }
    gl.uniformMatrix4fv(loc.model, false, new Float32Array(IDENTITY));
    gl.uniform3fv(loc.tangent, [1, 0, 0]);
    gl.uniform3fv(loc.bitangent, [0, -1, 0]);
    gl.bindBuffer(gl.ARRAY_BUFFER, part.outerBuf);
    attribs();
    printed();
    gl.drawArrays(gl.TRIANGLES, 0, part.outerCount);
    gl.bindBuffer(gl.ARRAY_BUFFER, part.innerBuf);
    attribs();
    paper();
    gl.drawArrays(gl.TRIANGLES, 0, part.innerCount);
  }
  gl.disableVertexAttribArray(loc.nrm);
}

// host：放查看器的元素；sceneUrl：scene.json 的网址；fileUrl(相对 mockup/ 的路径) → 贴图网址
// scene：直接给场景对象（改结构页的预览，贴图是 data URL），不给就从 sceneUrl 读；
// initialFold：载入后停在哪（0 平铺 – 1 合上）；initialView：载入后的观察角度（换一版预览时保持不变）
export function mountViewer(host, { sceneUrl, scene: givenScene, fileUrl = (u) => u, onError, preferenceKey = "default",
  initialFold = 1, initialView = null }) {
  const canvas = document.createElement("canvas");
  canvas.className = "viewer-canvas loading"; // 第一帧画好前不显示（WebGL 空画布是黑的），露出底色
  host.append(canvas);
  const gl = canvas.getContext("webgl", { antialias: true, alpha: false, premultipliedAlpha: false });
  if (!gl) {
    host.append(Object.assign(document.createElement("div"), { className: "viewer-msg", textContent: tr("This browser requires WebGL to show interactive 3D.") }));
    return null;
  }
  const fail = (err) => {
    canvas.hidden = true;
    host.append(Object.assign(document.createElement("div"), { className: "viewer-msg", textContent: tr("3D unavailable: {1}. Open the static overview below or refresh to try again.", err.message) }));
    if (onError) onError(err);
  };
  let prog;
  try {
  prog = gl.createProgram();
  gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VS));
  gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FS));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
  } catch (err) { fail(err); return null; }
  gl.useProgram(prog);
  const loc = {
    p: gl.getAttribLocation(prog, "p"),
    uv: gl.getAttribLocation(prog, "uv"),
    nrm: gl.getAttribLocation(prog, "nrm"),
    m: gl.getUniformLocation(prog, "m"),
    model: gl.getUniformLocation(prog, "model"),
    ground: gl.getUniformLocation(prog, "ground"),
    s: gl.getUniformLocation(prog, "s"),
    a: gl.getUniformLocation(prog, "a"),
  };
  for (const key of ["material", "heightMap", "region", "modern", "exposure", "relief", "selected", "cavity", "cavityMM", "clipGround", "box", "foot", "eye", "light", "tangent", "bitangent", "texel", "faceMM", "t"]) loc[key] = gl.getUniformLocation(prog,key);
  const neutralMaterial = texture(gl, pixel([173,0,0,255]));
  const neutralHeight = texture(gl, pixel([128,0,128,255]));
  const emptyRegion = texture(gl, pixel([0,0,0,255]));
  // 默认改成自然光时（2026-10-22）换了键：以前存下的灯光选择作废一次，打开都从自然光开始
  const prefsKey = `pacxio:lighting:v2:${preferenceKey}`;
  let lighting = { ...DEFAULT_LIGHTING };
  try { const p=JSON.parse(localStorage.getItem(prefsKey) ?? localStorage.getItem(`packengine:lighting:v2:${preferenceKey}`)); if(p && Number.isFinite(p.direction) && Number.isFinite(p.brightness)) lighting={direction:Math.max(-180,Math.min(180,p.direction)),brightness:Math.max(0.5,Math.min(1.5,p.brightness))}; } catch (_) { /* 显示偏好可丢弃 */ }
  let sceneInfo = null, selectedRegion = "", selectionToken = 0;
  function saveLighting() { try { localStorage.setItem(prefsKey,JSON.stringify(lighting)); } catch (_) { /* 无存储权限 */ } }
  function bind(unit, name, tex) { gl.activeTexture(gl.TEXTURE0+unit); gl.bindTexture(gl.TEXTURE_2D,tex); gl.uniform1i(loc[name],unit); }
  const uvBuf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, uvBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1]), gl.STATIC_DRAW);
  const shadowTex = shadowTexture(gl);

  const view = { ...DEFAULT_VIEW, ...(initialView || {}) };
  const states = {}; // 状态名 → { size, faces: [{buf, tex, normal, centre}], shadow }；v3 是 { t }
  let current = null;
  let fold = null; // v3：{ parts, steps, s, size, shift, shadow, ground, … }
  let foldAnim = null;
  let onFoldChange = null;
  let bg = 0;
  let scale = 1;
  let pending = false;
  let destroyed = false;

  function quad(corners) {
    const [a, b, c, d] = corners; // 左上、右上、右下、左下
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([...a, ...b, ...c, ...a, ...c, ...d]), gl.STATIC_DRAW);
    return buf;
  }

  async function load() {
    let scene = givenScene;
    if (!scene) {
      const res = await fetch(sceneUrl, { cache: "no-store" });
      if (!res.ok) throw new Error(tr("Could not load scene.json (HTTP {1})", res.status));
      scene = await res.json();
    }
    if (![1,2,3].includes(scene.version)) throw new Error(tr("Unsupported scene version {1}. Run mockup again.", scene.version));
    sceneInfo = scene;
    if (scene.version === 3) {
      Object.assign(states, scene.states);
      fold = await loadFold(scene, { gl, loc, fileUrl, loadImage, texture, atlasTexture, pixel });
      current = "closed";
      if (destroyed) return;
      updateFold(initialFold * scene.steps);
      if (onFoldChange) onFoldChange(initialFold);
      draw();
      return;
    }
    for (const [name, st] of Object.entries(scene.states)) {
      const faces = await Promise.all(
        st.faces.map(async (f) => {
          const img = await loadImage(fileUrl(f.texture));
          const centre = [0, 1, 2].map((i) => f.corners.reduce((s, c) => s + c[i], 0) / 4);
          const extra = {};
          for (const name of ["material", "height"]) if(f.maps?.[name]) extra[name]=texture(gl,await loadImage(fileUrl(f.maps[name])));
          const u=sub(f.corners[1],f.corners[0]), v=sub(f.corners[3],f.corners[0]);
          return { buf: quad(f.corners), tex: texture(gl, img), normal: norm(f.normal), centre, key: f.key, maps:f.maps||{}, ...extra,
            tangent:norm(u), bitangent:norm(v), faceMM:[Math.hypot(...u),Math.hypot(...v)], texel:[1/img.width,1/img.height], region:emptyRegion };
        }),
      );
      const [x, y] = st.size;
      const m = 0.18 * Math.max(x, y);
      const shadow = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, shadow);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(shadowQuad(0, 0, x, y, m)), gl.STATIC_DRAW);
      states[name] = { size: st.size, faces, shadow };
    }
    current = states.closed ? "closed" : Object.keys(states)[0];
    if (destroyed) return;
    draw();
  }

  function updateFold(s) {
    fold.s = Math.max(0, Math.min(fold.steps, s));
    posePart(fold);
    if (onFoldChange) onFoldChange(fold.s / fold.steps);
  }

  function drawShadow(foot, buf) {
    gl.uniform4fv(loc.foot, foot);
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.vertexAttribPointer(loc.p, 3, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }

  function stopFold() {
    if (foldAnim) cancelAnimationFrame(foldAnim);
    foldAnim = null;
  }

  // 从现在的进度走到 t（0–1）；seconds 是走完全程要多久
  function animateFold(t, seconds, done) {
    stopFold();
    const from = fold.s, to = t * fold.steps;
    const dur = Math.max(0.2, (Math.abs(to - from) / fold.steps) * seconds) * 1000;
    let t0 = null;
    const tick = (now) => {
      if (destroyed) return;
      if (t0 === null) t0 = now;
      const k = Math.min(1, (now - t0) / dur);
      updateFold(from + (to - from) * k);
      draw();
      if (k < 1) foldAnim = requestAnimationFrame(tick);
      else { foldAnim = null; if (done) done(); }
    };
    foldAnim = requestAnimationFrame(tick);
  }

  function camera() {
    const [x, y, z] = fold ? fold.size : states[current].size;
    const centre = [x / 2, y / 2, z / 2];
    const radius = Math.hypot(x, y, z) / 2;
    const dist = ((radius / Math.sin(FOV / 2)) * 1.08) / view.zoom;
    const az = (view.az * Math.PI) / 180;
    // 平铺时相机抬高：斜着看一张平的刀版看不清
    const flatness = fold ? 1 - ease(fold.s / fold.poseEase) : 0;
    const el = ((view.el + (FLAT_ELEVATION - view.el) * flatness) * Math.PI) / 180;
    const eye = [centre[0] + dist * Math.sin(az) * Math.cos(el), centre[1] - dist * Math.cos(az) * Math.cos(el), centre[2] + dist * Math.sin(el)];
    return { eye, centre, dist };
  }

  function draw() {
    pending = false;
    if (destroyed || !current) return;
    const w = canvas.width;
    const h = canvas.height;
    gl.viewport(0, 0, w, h);
    const c = BACKGROUNDS[bg];
    gl.clearColor(c[0], c[1], c[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const { eye, centre, dist } = camera();
    const mvp = multiply(perspective(FOV, w / h, dist * 0.05, dist * 4), lookAt(eye, centre, [0, 0, 1]));
    gl.uniformMatrix4fv(loc.m, false, new Float32Array(mvp));
    gl.bindBuffer(gl.ARRAY_BUFFER, uvBuf);
    gl.enableVertexAttribArray(loc.uv);
    gl.vertexAttribPointer(loc.uv, 2, gl.FLOAT, false, 0, 0);
    gl.enableVertexAttribArray(loc.p);
    const st = fold ? { size: fold.size } : states[current];
    // 每帧一样的光照参数（地面阴影也要用：投影按光向偏）
    const angle=lighting.direction*Math.PI/180;
    gl.uniformMatrix4fv(loc.model,false,new Float32Array(IDENTITY));
    gl.uniform1f(loc.ground,fold?fold.ground:1);
    gl.uniform1f(loc.clipGround,fold&&fold.ground>0.5?1:0);
    gl.uniform1f(loc.modern,sceneInfo.version>=2?1:0);
    gl.uniform1f(loc.exposure,lighting.brightness);
    gl.uniform1f(loc.relief,2*(sceneInfo.relief_height_mm||0.08));
    gl.uniform1f(loc.cavity,sceneInfo.relief_cavity||0);
    gl.uniform1f(loc.cavityMM,sceneInfo.relief_edge_mm||0.2);
    gl.uniform1f(loc.selected,selectedRegion?1:0);
    gl.uniform3fv(loc.box,[st.size[0],st.size[1],Math.max(st.size[2],1)]);
    gl.uniform3fv(loc.eye,eye);
    gl.uniform3fv(loc.light,norm([Math.sin(angle),-Math.cos(angle),0.9]));
    gl.disableVertexAttribArray(loc.nrm);
    if (eye[2] > 0 && (!fold || fold.ground > 0.001)) {
      // 地面阴影：从上往下看时才画
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.disable(gl.DEPTH_TEST);
      gl.uniform1f(loc.a, 1);
      bind(0,"t",shadowTex);
      if (fold) for (const foot of fold.feet) {
        const m = 0.18 * Math.max(foot[2], foot[3]);
        gl.bindBuffer(gl.ARRAY_BUFFER, fold.shadow);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(shadowQuad(foot[0], foot[1], foot[2], foot[3], m)), gl.DYNAMIC_DRAW);
        drawShadow(foot, fold.shadow);
      }
      else drawShadow([0, 0, st.size[0], st.size[1]], st.shadow);
      gl.disable(gl.BLEND);
    }
    gl.enable(gl.DEPTH_TEST);
    gl.uniform1f(loc.a, 0);
    if (fold) {
      drawFold(fold, { gl, loc, bind, emptyRegion, neutralMaterial, neutralHeight, sceneInfo });
      return;
    }
    for (const f of st.faces) {
      if (dot(f.normal, sub(eye, f.centre)) <= 0) continue; // 背对相机的面不画（和静态渲染的剔除一样）
      gl.uniform1f(loc.s, shade(f.normal));
      bind(0,"t",f.tex); bind(1,"material",f.material||neutralMaterial); bind(2,"heightMap",f.height||neutralHeight); bind(3,"region",f.region||emptyRegion);
      gl.vertexAttrib3fv(loc.nrm,f.normal); gl.uniform3fv(loc.tangent,f.tangent); gl.uniform3fv(loc.bitangent,f.bitangent);
      gl.uniform2fv(loc.texel,f.texel); gl.uniform2fv(loc.faceMM,f.faceMM);
      gl.bindBuffer(gl.ARRAY_BUFFER, f.buf);
      gl.vertexAttribPointer(loc.p, 3, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    }
  }

  function redraw() {
    if (!pending) {
      pending = true;
      requestAnimationFrame(draw);
    }
  }

  function resize(s) {
    if (s) scale = s;
    const dpr = window.devicePixelRatio || 1;
    const px = Math.max(64, Math.min(MAX_CANVAS_PX, Math.round(host.clientWidth * scale * dpr)));
    if (canvas.width !== px) {
      canvas.width = px;
      canvas.height = px;
      draw(); // 改尺寸会清空画面：立刻重画（窗口在后台时 requestAnimationFrame 不触发）
    }
  }

  // 拖动旋转、滚轮缩放、双击回到默认视角；事件不交给外面的画布（不然会平移画布）
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => {
    e.stopPropagation();
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, az: view.az, el: view.el };
    try {
      canvas.setPointerCapture(e.pointerId);
    } catch (_) {
      /* 脚本发的事件没有真的指针 */
    }
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    view.az = drag.az - (e.clientX - drag.x) * 0.5;
    view.el = Math.max(-80, Math.min(85, drag.el + (e.clientY - drag.y) * 0.4));
    redraw();
  });
  const end = (e) => {
    if (drag && e.pointerId === drag.id) drag = null;
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    e.stopPropagation();
    view.zoom = Math.max(0.5, Math.min(4, view.zoom * Math.exp(-e.deltaY * 0.0015)));
    redraw();
  }, { passive: false });
  canvas.addEventListener("dblclick", (e) => {
    e.stopPropagation();
    Object.assign(view, DEFAULT_VIEW);
    redraw();
  });

  resize(1);
  // 载入慢时（超过 0.3 秒）才出“正在加载”，快的时候不闪字
  const loadingMsg = Object.assign(document.createElement("div"), { className: "viewer-msg", textContent: tr("Loading 3D…") });
  const loadingTimer = setTimeout(() => { if (!destroyed) host.append(loadingMsg); }, 300);
  const loaded = () => { clearTimeout(loadingTimer); loadingMsg.remove(); };
  load().then(() => { loaded(); canvas.classList.remove("loading"); }, (err) => { loaded(); fail(err); });
  canvas.addEventListener("webglcontextlost", (ev) => { ev.preventDefault(); if(!destroyed) fail(new Error(tr("WebGL context lost. Refresh to try again."))); });

  return {
    resize,
    lighting: () => ({...lighting}),
    setLighting(change) { Object.assign(lighting,change); saveLighting(); redraw(); },
    resetLighting() { lighting={ ...DEFAULT_LIGHTING }; saveLighting(); redraw(); },
    async selectRegion(id) {
      selectedRegion=id;
      const token=++selectionToken;
      for(const st of Object.values(states)) for(const f of st.faces) {
        if(f.region && f.region!==emptyRegion) gl.deleteTexture(f.region);
        f.region=emptyRegion;
      }
      if(fold) for(const part of fold.parts) { if(part.region!==emptyRegion) gl.deleteTexture(part.region); part.region=emptyRegion; }
      redraw();
      if(!id) return;
      if(fold) {
        try { await Promise.all(fold.parts.map(async part=>{
          const url=part.textures[`region:${id}`]; if(!url) return;
          const img=await loadImage(fileUrl(url));
          if(token===selectionToken && !destroyed) part.region=texture(gl,img);
        })); redraw(); } catch(err) { if(onError) onError(err); }
        return;
      }
      try { await Promise.all(Object.values(states).flatMap(st=>st.faces.map(async f=>{
        const url=f.maps[`region:${id}`]; if(!url) return;
        const img=await loadImage(fileUrl(url));
        if(token===selectionToken && !destroyed) f.region=texture(gl,img);
      }))); redraw(); } catch(err) { if(onError) onError(err); }
    },
    states: () => Object.keys(states),
    view: () => ({ ...view }),
    state: () => current,
    setState(name) {
      if (states[name]) {
        current = name;
        if (fold) animateFold(states[name].t, FOLD_SECONDS);
        else redraw();
      }
    },
    // v3 的折叠：t 是 0（平铺）到 1（合上）
    foldProgress: () => (fold ? fold.s / fold.steps : null),
    setFold(t) { if (fold) { stopFold(); updateFold(t * fold.steps); redraw(); } },
    playFold(done) {
      if (!fold) return;
      if (fold.s >= fold.steps - 1e-3) updateFold(0);
      animateFold(1, FOLD_SECONDS, done);
    },
    stopFold,
    onFold(fn) { onFoldChange = fn; },
    nextBackground() {
      bg = (bg + 1) % BACKGROUNDS.length;
      redraw();
    },
    reset() {
      Object.assign(view, DEFAULT_VIEW);
      redraw();
    },
    destroy() {
      destroyed = true;
      loaded();
      stopFold();
      const ext = gl.getExtension("WEBGL_lose_context");
      if (ext) ext.loseContext();
      canvas.remove();
    },
  };
}
