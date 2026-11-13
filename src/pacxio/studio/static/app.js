import { apiUrl, localizePage, tr } from "./i18n.js";
// pacxio 工作台 · S1 画布、实时刷新、侧栏；S2 批注；S3 确认按钮；S4 可转动 3D（）

import { mountViewer } from "./viewer3d.js";
// 不引用任何外部资源；读 /api/state、/api/events、/files/，写批注走 /api/notes（带页面口令）。

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

const stageNo = (n) => String(n).padStart(2, "0"); // 阶段编号：等宽两位（01、02…），和网站的格子编号一样
const THUMBS = [480, 960, 1920]; // 与服务端 THUMB_WIDTHS 一致
const CARD_W = { content: 640, plan: 300, concept: 520, flat_concept: 300, design: 900, mockup: 320, delivery: 380 }; // ③ 概念图放大看
const COLS = { content: 1, plan: 3, concept: 3, flat_concept: 3, design: 1, flat: 3, mockup: 3, delivery: 2 };
const ANNOTATION_PAD_MM = 1.5;
const PATH_LABELS = [
  ["job.json", "job.json"], ["preview", tr("Dieline proof")], ["mockup/", tr("3D mockup")], ["concepts/", tr("Concepts")],
  ["plan/", tr("Layout draft")], ["flat/", tr("Flat comparison (agent)")], ["out/", tr("Preflight and delivery")], ["assets/", tr("Assets")],
  ["content_confirmed", tr("Content list")], ["plan_confirmed", tr("Layout draft")],
  ["review/", tr("Notes")],
];
const MIN_SCALE = 0.04;
const MAX_SCALE = 4;

const canvas = $("#canvas");
const world = $("#world");
const view = { x: 40, y: 40, s: 0.5 };
const toggles = { findings: true, regions: true, lines: true }; // 面名、尺寸已经画在刀版确认图里
const cardEls = new Map(); // 卡片 id → { el, key }：内容没变就复用，图片不重新加载
let liveIds = new Set();
let state = null;
let placed = false; // 首次渲染后定好视角
let agentLink = { status: "loading", stage: null, updated_at: null, expires_at: null };
let savedConfirmation = null;
let checkingAgent = null;

// ---------------- 工具 ----------------

function fileUrl(file, v, w) {
  const path = file.split("/").map(encodeURIComponent).join("/");
  return `/files/${path}?v=${v ?? 0}` + (w ? `&w=${w}` : "");
}

function hhmm(sec) {
  if (!sec) return "";
  const d = new Date(sec * 1000);
  const p = (n) => String(n).padStart(2, "0");
  const t = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return d.toDateString() === new Date().toDateString() ? t : `${p(d.getMonth() + 1)}-${p(d.getDate())} ${t}`;
}

function size(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function chip(text, tone) {
  return el("span", `chip ${tone || "mute"}`, text);
}

function storageKey() {
  return state ? `pe-studio-view:${state.job_dir}` : null;
}

function saveView() {
  const k = storageKey();
  if (!k) return;
  try { localStorage.setItem(k, JSON.stringify(view)); } catch (_) { /* 无痕窗口等：不记也能用 */ }
}

function loadView() {
  const k = storageKey();
  if (!k) return null;
  try {
    const v = JSON.parse(localStorage.getItem(k) || "null");
    return v && Number.isFinite(v.x) && Number.isFinite(v.y) && Number.isFinite(v.s) ? v : null;
  } catch (_) {
    return null;
  }
}

// ---------------- 平移与缩放 ----------------

function apply(animate = false) {
  world.style.transition = animate ? "transform 0.28s ease" : "";
  world.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.s})`;
  world.style.setProperty("--inv", String(1 / view.s)); // 批注标记不随缩放变大变小
  $("#zoom-pct").textContent = `${Math.round(view.s * 100)}%`;
  if (bubbleId) setTimeout(repositionBubble, animate ? 300 : 0);
  updateMinimapView();
  scheduleThumbs();
  saveView();
}

function zoomAt(cx, cy, factor) {
  const s = clamp(view.s * factor, MIN_SCALE, MAX_SCALE);
  const k = s / view.s;
  view.x = cx - (cx - view.x) * k;
  view.y = cy - (cy - view.y) * k;
  view.s = s;
  apply();
}

function worldRect(node) {
  const a = node.getBoundingClientRect();
  const b = world.getBoundingClientRect();
  return { x: (a.left - b.left) / view.s, y: (a.top - b.top) / view.s, w: a.width / view.s, h: a.height / view.s };
}

// 顶栏浮在画布上：取景时让开它
function topInset() {
  const t = $("#top").getBoundingClientRect();
  const c = canvas.getBoundingClientRect();
  return Math.max(0, t.bottom - c.top) + 8;
}

// 画布上看得见的区域的中心（让开顶栏）
function viewCentre() {
  const top = topInset();
  return [canvas.clientWidth / 2, top + (canvas.clientHeight - top) / 2];
}

function zoomToRect(r, pad = 48, maxScale = 2.5) {
  const W = canvas.clientWidth;
  const top = topInset();
  const H = canvas.clientHeight - top;
  const s = clamp(Math.min((W - 2 * pad) / r.w, (H - 2 * pad) / r.h), MIN_SCALE, maxScale);
  view.s = s;
  view.x = W / 2 - (r.x + r.w / 2) * s;
  view.y = top + H / 2 - (r.y + r.h / 2) * s;
  apply(true);
}

function fitAll() {
  zoomToRect({ x: 0, y: 0, w: world.offsetWidth, h: world.offsetHeight }, 24, 1);
}

function zoomTo(node) {
  zoomToRect(worldRect(node));
}

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = canvas.getBoundingClientRect();
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? canvas.clientHeight : 1;
  if (e.ctrlKey || e.metaKey) {
    zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * unit * 0.01));
  } else {
    view.x -= e.deltaX * unit;
    view.y -= e.deltaY * unit;
    apply();
  }
}, { passive: false });

let drag = null;
let spaceHeld = false;
function capture(id) {
  try {
    canvas.setPointerCapture(id);
  } catch (_) {
    /* 指针已经抬起（或是脚本发的事件）：不用捕获 */
  }
}
canvas.addEventListener("pointerdown", (e) => {
  if (e.button > 1 || e.target.closest("a, button, textarea, #minimap, #zoomctl, .npin, .nbox:not(.draft), .viewer3d")) return;
  const media = e.target.closest(".card.img .slice, .card.img .media:not(.sliced)");
  if (e.button === 0 && !spaceHeld && tool !== "move" && media) {
    startMark(e, media);
    return;
  }
  e.preventDefault(); // 拖动是平移画布，不是选文字
  const sel = window.getSelection();
  if (sel) sel.removeAllRanges();
  drag = { id: e.pointerId, x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
  capture(e.pointerId);
  canvas.classList.add("panning");
});
canvas.addEventListener("pointermove", (e) => {
  if (mark && e.pointerId === mark.id) return moveMark(e);
  if (!drag || e.pointerId !== drag.id) return;
  view.x = drag.vx + e.clientX - drag.x;
  view.y = drag.vy + e.clientY - drag.y;
  apply();
});
const endDrag = (e) => {
  if (mark && e.pointerId === mark.id) return e.type === "pointercancel" ? cancelMark() : endMark(e);
  if (!drag || e.pointerId !== drag.id) return;
  drag = null;
  canvas.classList.remove("panning");
};
canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);

window.addEventListener("keyup", (e) => {
  if (e.key === " ") spaceHeld = false;
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    cancelComposer();
    closeBubble();
    if (!$("#confirm").hidden) closeConfirm();
    else if (struct.open) closeEditor();
    return;
  }
  if (e.target.closest("input, textarea, select")) return;
  if (e.key === " ") spaceHeld = true;
  const key = e.key.toLowerCase();
  if (key === "v") setTool("move");
  else if (key === "p") setTool("pin");
  else if (key === "b") setTool("box");
  const [W, H] = viewCentre();
  if (e.key === "0") fitAll();
  else if (e.key === "1") zoomAt(W, H, 1 / view.s);
  else if (e.key === "+" || e.key === "=") zoomAt(W, H, 1.25);
  else if (e.key === "-") zoomAt(W, H, 0.8);
});
$("#zoom-in").addEventListener("click", () => zoomAt(...viewCentre(), 1.25));
$("#zoom-sharp").addEventListener("click", () => zoomVisible().catch((e) => toast(String(e.message || e), 5000)));
$("#zoom-out").addEventListener("click", () => zoomAt(...viewCentre(), 0.8));
$("#fit-all").addEventListener("click", fitAll);
new ResizeObserver(() => {
  if (!placed && state) place();
  else {
    updateMinimap();
    scheduleThumbs();
  }
}).observe(canvas);

// ---------------- 缩略图：放大看时换高分辨率 ----------------

let thumbTimer = 0;
function scheduleThumbs() {
  clearTimeout(thumbTimer);
  thumbTimer = setTimeout(updateThumbs, 180);
}

function updateThumbs() {
  for (const { el: card } of cardEls.values()) if (card._viewer) card._viewer.resize(view.s);
  const c = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  for (const img of world.querySelectorAll("img[data-file]")) {
    const r = img.getBoundingClientRect();
    if (r.right < c.left - 200 || r.left > c.right + 200 || r.bottom < c.top - 200 || r.top > c.bottom + 200) continue;
    const natural = Number(img.dataset.natural) || 0;
    const need = img.clientWidth * view.s * dpr;
    let w = THUMBS.find((t) => t >= need) || 0; // 0 = 原图
    if (natural && w >= natural) w = 0;
    const cur = img.dataset.w === "0" ? Infinity : Number(img.dataset.w);
    const next = w === 0 ? Infinity : w;
    if (next > cur) {
      img.dataset.w = String(w);
      img.src = fileUrl(img.dataset.file, img.dataset.v, w);
    }
  }
}

// ---------------- 卡片 ----------------

const SLICE_GAP = 14; // 长图分栏时栏与栏的间隔（与 CSS 一致）
const ITEM_COL_W = 380; // 信息清单每栏宽

// 信息清单分几栏：条目少就一栏，多了分两三栏，卡片变宽、变矮
function tableCols(card) {
  const n = card.data.rows.length;
  return n <= 12 ? 1 : n <= 30 ? 2 : 3;
}

function cardWidth(stage, card) {
  if (card.kind === "table") {
    const k = tableCols(card);
    return k === 1 ? 640 : k * ITEM_COL_W + (k - 1) * 22 + 20;
  }
  if (card.kind === "text") return card.id === "plan:brief" ? 440 : 380;
  if (card.kind === "report") return 300;
  if (card.kind === "viewer3d") return 720; // ⑥ 只有它，放大
  if (card.kind === "files") return 440;
  if (card.data && card.data.width) return card.data.width; // 按真实毫米排的图（平面图、拉正的面）
  const images = stage.cards.filter((c) => c.kind === "image").length;
  const base = stage.id === "design" ? (images > 1 ? 560 : 900) : CARD_W[stage.id] || 320;
  const cols = card.data && card.data.columns;
  return cols ? cols.length * base + (cols.length - 1) * SLICE_GAP : base;
}

function head(card) {
  const cap = el("div", "cap");
  cap.append(el("b", null, card.title));
  if (card.caption) cap.append(el("span", "desc", card.caption));
  if (card.mtime) cap.append(el("span", "when", hhmm(card.mtime)));
  cap.title = [card.title, card.caption, card.file].filter(Boolean).join("\n");
  return cap;
}

// 刀版确认图用不带标注的底图；"刀线"关掉时换成不画刀线、压线的一份（看折线上的图有没有断开）
function designFile(card) {
  const m = card.data?.map;
  if (!m) return card.file;
  return (!toggles.lines && m.art_file) || m.base_file || card.file;
}

function swapDesignImages() {
  const st = state && state.stages.find((s) => s.id === "design");
  for (const { el: card } of cardEls.values()) {
    if (!card.dataset.map) continue;
    const data = st && st.cards.find((c) => c.id === card.dataset.id);
    if (!data) continue;
    const file = designFile(data);
    const patches = card.querySelector(".zoom-patches");
    if (patches && card.querySelector("img[data-file]")?.dataset.file !== file) patches.replaceChildren(); // 换了图层，局部作废
    for (const img of card.querySelectorAll("img[data-file]")) {
      if (img.dataset.file === file) continue;
      img.dataset.file = file;
      img.src = fileUrl(file, img.dataset.v, Number(img.dataset.w) || 0);
    }
  }
}

function cardImg(card) {
  const img = el("img");
  img.alt = card.title;
  img.decoding = "async";
  img.draggable = false;
  const imageFile = designFile(card);
  img.dataset.file = imageFile;
  img.dataset.v = String(card.mtime ?? 0);
  img.dataset.natural = String(card.size ? card.size[0] : 0);
  const first = card.size && card.size[0] <= THUMBS[0] ? 0 : THUMBS[0];
  img.dataset.w = String(first);
  img.src = fileUrl(imageFile, card.mtime, first);
  return img;
}

// 长图（确认页）分栏：同一张图切成几段并排，切口在段落之间的空白处（服务端算好）；文件本身不动
function slicedMedia(card) {
  const media = el("div", "media sliced");
  const [W, H] = card.size;
  for (const [y0, y1] of card.data.columns) {
    const sl = el("div", "slice");
    sl.dataset.y0 = String(y0);
    sl.dataset.y1 = String(y1);
    sl.dataset.h = String(H);
    sl.style.aspectRatio = `${W} / ${y1 - y0}`;
    const img = cardImg(card);
    img.style.top = `${(-y0 / (y1 - y0)) * 100}%`;
    img.style.height = `${(H / (y1 - y0)) * 100}%`;
    sl.append(img, el("div", "marks"));
    media.append(sl);
  }
  return media;
}

function imageCard(card) {
  const e = el("div", "card img");
  const sliced = Boolean(card.data && card.data.columns && card.size);
  const media = sliced ? slicedMedia(card) : el("div", "media");
  if (!sliced) {
    media.style.aspectRatio = card.size ? `${card.size[0]} / ${card.size[1]}` : "4 / 3";
    const img = cardImg(card);
    const crop = card.size && card.data && card.data.crop;
    if (crop) {
      // 只显示画面那一块（平面图四周的留白裁掉，几张才对得齐）；批注照样按整张图记
      const [x0, y0, x1, y1] = crop;
      const [W, H] = card.size;
      media.style.aspectRatio = `${x1 - x0} / ${y1 - y0}`;
      media.classList.add("cropped");
      Object.assign(media.dataset, { x0, y0, cw: x1 - x0, ch: y1 - y0, w: W, h: H });
      Object.assign(img.style, {
        inset: "auto",
        left: `${(-x0 / (x1 - x0)) * 100}%`,
        top: `${(-y0 / (y1 - y0)) * 100}%`,
        width: `${(W / (x1 - x0)) * 100}%`,
        height: `${(H / (y1 - y0)) * 100}%`,
      });
    }
    media.append(img);
  }
  const concept = card.id.startsWith("concept:") ? card.badges[0] : null;
  if (card.data?.chosen) {
    media.append(el("span", "badge-star", "★"));
    e.classList.add("chosen");
  }
  if (card.data?.in_use) media.append(el("span", "badge-use", tr("In use")));
  if (card.data && card.data.map) {
    const ov = el("div", "overlay");
    media.append(ov);
    e.dataset.map = "1";
    e.dataset.part = card.data.map.part ?? "";
  }
  if (!sliced) media.append(el("div", "marks"));
  e.dataset.file = card.file;
  e.append(media);
  const cap = head(card);
  if (concept) cap.prepend(el("span", "tag-concept", concept)); // 标在底栏，不遮图
  const open = el("a", "open-link", tr("Original ↗"));
  open.href = fileUrl(card.file, card.mtime);
  open.target = "_blank";
  open.rel = "noopener";
  open.title = tr("Open the original image in a new tab");
  cap.append(open);
  if(card.data?.map?.regions?.length) {
    const list=el("div","region-list");
    const clear=el("button","vbtn",tr("Clear highlight"));
    clear.addEventListener("click",ev=>{ev.stopPropagation();delete e.dataset.region;renderOverlays();for(const b of list.querySelectorAll(".on")) b.classList.remove("on");});
    list.append(clear);
    for(const r of card.data.map.regions) {
      const b=el("button","region-entry",`${r.number} · ${r.label}`);
      b.title=`${r.id} · ${r.detail}`;b.append(el("small",null,r.detail));
      b.addEventListener("click",ev=>{ev.stopPropagation();e.dataset.region=e.dataset.region===r.id?"":r.id;toggles.regions=true;for(const t of world.querySelectorAll('[data-overlay="regions"]')) t.classList.add("on");renderOverlays();for(const btn of list.querySelectorAll(".region-entry")) btn.classList.toggle("on",btn===b && e.dataset.region===r.id);});
      list.append(b);
    }
    e.append(list);
  }
  e.append(cap);
  return e;
}

// 信息清单：按类别分块，块在卡片里分栏排（条目全在，不收起、不截断）
function tableCard(card) {
  const e = el("div", "card table");
  e.append(head(card));
  const body = el("div", "body items");
  body.style.columnCount = String(tableCols(card));
  const groups = new Map();
  for (const row of card.data.rows) {
    if (!groups.has(row.category)) groups.set(row.category, []);
    groups.get(row.category).push(row);
  }
  for (const [cat, rows] of groups) {
    body.append(el("div", "cat-head", `${cat} · ${rows.length}`));
    for (const row of rows) {
      const r = el("div", "irow");
      r.dataset.item = row.id;
      const txt = el("div", "t", row.text);
      if (!row.required) txt.append(el("span", "opt-tag", tr("Optional")));
      if (row.pending) txt.append(el("span", "pend", tr("Pending: {1}", row.pending)));
      const act = el("div", "row-act");
      const add = el("button", "row-add", tr("Notes"));
      add.title = tr("Add a note about this item, its wording, or its placement");
      add.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const b = add.getBoundingClientRect();
        openComposer(b.right, b.top, { card: card.id, file: null, shape: "item", rel: null, item: row.id }, null);
      });
      act.append(add, el("span", "row-notes"));
      r.append(el("div", "rid", row.id), txt, act);
      body.append(r);
    }
  }
  e.append(body);
  return e;
}

function textCard(card) {
  const e = el("div", "card text");
  e.append(head(card));
  const body = el("div", "body");
  if (card.data.text != null) {
    body.append(el("pre", null, card.data.text + (card.data.truncated ? tr("\n… (Open the original file to read the full text)") : "")));
  }
  if (card.data.lines) {
    const ul = el("ul");
    card.data.lines.forEach((line, i) => {
      const li = el("li", null, line);
      const color = card.data.swatches && card.data.swatches[i];
      if (color) { // 关键颜色：前面一个色块（屏幕示意）
        const chip = el("span", "chip");
        chip.style.background = color;
        li.prepend(chip);
        li.classList.add("has-chip");
      }
      ul.append(li);
    });
    body.append(ul);
  }
  if (card.file) {
    const a = el("a", null, tr("Open original file"));
    a.href = fileUrl(card.file, card.mtime);
    a.target = "_blank";
    a.rel = "noopener";
    const note = el("div", "note");
    note.append(a);
    body.append(note);
  }
  e.append(body);
  return e;
}

function reportCard(card) {
  const e = el("div", "card report");
  e.append(head(card));
  const body = el("div", "body");
  const d = card.data;
  body.append(el("div", "big", `${d.errors} error · ${d.warnings} warning`));
  body.append(el("div", "note", d.passed ? tr("Preflight passed (0 errors)") : tr("Errors found. Factory delivery pack blocked (D12).")));
  if (!d.profile_verified) body.append(el("div", "note", tr("Profile pending factory verification")));
  body.append(el("div", "note", tr("See individual findings under Issues in the sidebar")));
  e.append(body);
  return e;
}

function filesCard(card) {
  const e = el("div", "card files");
  e.append(head(card));
  const body = el("div", "body files-list");
  for (const f of card.data.files) body.append(fileLink(f));
  e.append(body);
  return e;
}

function fileLink(f) {
  const a = el("a");
  a.href = fileUrl(f.path, f.mtime);
  a.target = "_blank";
  a.rel = "noopener";
  a.append(el("span", null, f.name || f.path.split("/").slice(-2).join("/")));
  if (f.size != null) a.append(el("span", "size", size(f.size)));
  return a;
}

// 可转动 3D：拖动旋转、滚轮缩放、双击回到默认角度；多部件盒型可以切合上 / 打开
function viewerCard(card) {
  const e = el("div", "card viewer");
  const host = el("div", "viewer3d");
  e.append(host);
  const v = mountViewer(host, {
    sceneUrl: fileUrl(card.file, card.mtime),
    fileUrl: (rel) => fileUrl(`${card.file.replace(/[^/]*$/, "")}${rel}`, card.mtime),
    preferenceKey: state.job_id,
  });
  e._viewer = v;
  const bar = el("div", "viewer-bar");
  if (v) {
    const names = { closed: tr("Closed"), open: tr("Open"), flat: tr("Unfolded"), filled: tr("Filled"), laid: tr("Laid flat") };
    const btns = [];
    const first = card.data.states.includes("closed") ? "closed" : card.data.states[0];
    for (const s of card.data.states) {
      const b = el("button", `vbtn${s === first ? " on" : ""}`, names[s] || s);
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        v.setState(s);
        for (const x of btns) x.classList.toggle("on", x === b);
      });
      btns.push(b);
      if (card.data.states.length > 1) bar.append(b);
    }
    const reset = el("button", "vbtn", tr("Reset view"));
    reset.title = tr("Return to the default viewing angle");
    reset.addEventListener("click", (ev) => {
      ev.stopPropagation();
      v.reset();
    });
    const bg = el("button", "vbtn", tr("Change background"));
    bg.title = tr("Switch between light and dark backgrounds to see the box edges");
    bg.addEventListener("click", (ev) => {
      ev.stopPropagation();
      v.nextBackground();
    });
    bar.append(reset, bg);
    v.buttons = btns;
  }
  if (v && card.data.version >= 2) {
    // 灯光与工艺区域只改这台电脑上的显示，不改设计、不影响确认
    const controls = el("div", "material-controls");
    if (card.data.version === 3) {
      // 折叠进度：0 是平铺的刀版，1 是合上；只改显示
      const foldRow = el("div", "mc-row");
      const foldTitle = el("span", "mc-title", tr("Folding"));
      foldTitle.title = tr("Fold along the dieline and pause anywhere to inspect the structure");
      const play = el("button", "vbtn", tr("Play"));
      play.title = tr("Play the fold from the start");
      const input = el("input");
      input.type = "range"; input.min = 0; input.max = 1000; input.step = 1; input.value = 1000;
      input.setAttribute("aria-label", tr("Folding progress"));
      const adjust = el("div", "mc-adjust");
      const label = el("label", null);
      label.append(input);
      adjust.append(label, play);
      foldRow.append(foldTitle, adjust);
      controls.append(foldRow);
      const clearOn = () => { for (const x of v.buttons) x.classList.remove("on"); };
      input.addEventListener("input", () => { clearOn(); v.setFold(Number(input.value) / 1000); });
      play.addEventListener("click", () => { clearOn(); play.disabled = true; v.playFold(() => { play.disabled = false; }); });
      v.onFold((t) => { input.value = Math.round(t * 1000); });
    }
    const lightRow = el("div", "mc-row");
    const lightTitle = el("span", "mc-title", tr("Lighting"));
    lightTitle.title = tr("Simulate window light with a bright top, darker sides, and a ground shadow");
    lightRow.append(lightTitle);
    const side = (deg) => (deg === 0 ? tr("Front") : Math.abs(deg) === 180 ? tr("Back") : `${deg < 0 ? tr("Left") : tr("Right")} ${Math.abs(deg)}°`);
    const sliders = {};
    const adjust = el("div", "mc-adjust"); // 方向、亮度、重置放一组：换行时一起换
    for (const [key, title, min, max, step, hint, fmt] of [
      ["direction", tr("Direction"), -180, 180, 1, tr("Drag to move the light and watch the reflection shift"), side],
      ["brightness", tr("Brightness"), 0.5, 1.5, 0.05, tr("Overall brightness"), (x) => `${Math.round(x * 100)}%`],
    ]) {
      const label = el("label", null, title);
      label.title = hint;
      const input = el("input");
      input.type = "range"; input.min = min; input.max = max; input.step = step;
      input.setAttribute("aria-label", title);
      const value = el("output");
      input.addEventListener("input", () => {
        v.setLighting({ [key]: Number(input.value) });
        value.textContent = fmt(Number(input.value));
      });
      label.append(input, value);
      adjust.append(label);
      sliders[key] = { input, value, fmt };
    }
    const resetLight = el("button", "vbtn", tr("Reset"));
    resetLight.title = tr("Reset lighting to 30° left and 100% brightness");
    resetLight.addEventListener("click", () => { v.resetLighting(); sync(); });
    adjust.append(resetLight);
    lightRow.append(adjust);
    function sync() {
      const now = v.lighting();
      for (const [key, els] of Object.entries(sliders)) {
        els.input.value = now[key];
        els.value.textContent = els.fmt(Number(now[key]));
      }
    }
    sync();
    controls.append(lightRow);
    if (card.data.regions?.length) {
      // 点一下在盒子上用蓝色标出这项工艺的范围，再点取消
      const regionRow = el("div", "mc-row");
      regionRow.append(el("span", "mc-title", tr("Finishes")));
      const counts = {};
      for (const r of card.data.regions) counts[r.label] = (counts[r.label] || 0) + 1;
      const chips = [];
      for (const r of card.data.regions) {
        const b = el("button", "vbtn", `${r.label}${counts[r.label] > 1 ? `（${r.id}）` : ""}`);
        b.title = tr("{1}Click to highlight this area. Click again to clear it.", r.detail ? r.detail + tr("; ") : "");
        b.addEventListener("click", () => {
          const on = !b.classList.contains("on");
          for (const x of chips) x.classList.toggle("on", on && x === b);
          v.selectRegion(on ? r.id : "");
        });
        chips.push(b);
      }
      regionRow.append(...chips);
      controls.append(regionRow);
    }
    controls.addEventListener("pointerdown", (ev) => ev.stopPropagation());
    controls.addEventListener("wheel", (ev) => ev.stopPropagation());
    e.append(controls, el("div", "material-note", tr("Use a physical proof to verify the finish. Lighting controls apply to this preview.")));
  }
  host.append(bar);
  const caption=head(card);
  const still=el("a","open-link",tr("Static overview ↗"));
  still.title=tr("Open the static overview of all panels");
  still.href=fileUrl(card.file.replace(/[^/]*$/,"00_mockup_sheet.png"),card.mtime);still.target="_blank";still.rel="noopener";caption.append(still);
  e.append(caption);
  return e;
}

const BUILDERS = { image: imageCard, table: tableCard, text: textCard, report: reportCard, files: filesCard, viewer3d: viewerCard };

function dropCard(id) {
  const prev = cardEls.get(id);
  if (prev && prev.el._viewer) prev.el._viewer.destroy(); // 释放 WebGL
  cardEls.delete(id);
}

function cardEl(card, stage) {
  const w = cardWidth(stage, card);
  const key = JSON.stringify([card, w]);
  liveIds.add(card.id);
  const prev = cardEls.get(card.id);
  if (prev && prev.key === key) return prev.el;
  if (prev) dropCard(card.id);
  const e = BUILDERS[card.kind](card);
  if (card.action) e.querySelector(".cap").append(actionButton(card.action, card));
  e.dataset.id = card.id;
  e.style.width = `${w}px`;
  if (card.stale) {
    e.classList.add("stale");
    e.append(el("span", "stale-ribbon", tr("Out of date")));
  }
  e.addEventListener("dblclick", (ev) => {
    ev.stopPropagation();
    zoomTo(e);
  });
  cardEls.set(card.id, { el: e, key });
  return e;
}

function frameEl(stage) {
  const f = el("div", "frame");
  f.dataset.stage = stage.id;
  const h = el("div", "frame-head");
  h.append(el("span", "fh-no", stageNo(stage.no)), el("span", "fh-title", stage.title), stageChip(stage));
  if (stage.notes_open) h.append(chip(tr("{1} note(s)", stage.notes_open), "note"));
  for (const a of stage.actions || []) h.append(actionButton(a, null));
  if (stage.id === "design") h.append(overlayTools(stage));
  f.append(h);
  const groups = new Map();
  const folds = new Map(); // 过程稿：默认收起
  for (const c of stage.cards) {
    const into = c.fold ? folds : groups;
    const g = c.fold || c.group || "";
    if (!into.has(g)) into.set(g, []);
    into.get(g).push(c);
  }
  for (const [g, cards] of groups) f.append(groupEl(stage, g, cards));
  for (const [fold, cards] of folds) {
    const key = foldKey(stage, fold);
    const open = expandedFolds.has(key);
    const ids = new Set(cards.map((c) => c.id));
    const noted = ((state && state.notes) || []).filter((n) => n.status !== "resolved" && ids.has(n.target.card)).length;
    const btn = el("button", `fold-toggle${open ? " open" : ""}`,
      tr("{1} {2} · {3} images{4}{5}", open ? "▾" : "▸", fold, cards.length, noted ? tr(" · {1} note(s)", noted) : "", open ? tr(" (click to collapse)") : tr(" (click to expand)")));
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (expandedFolds.has(key)) expandedFolds.delete(key);
      else expandedFolds.add(key);
      saveFolds();
      render(state);
    });
    f.append(btn);
    if (open) {
      const sec = groupEl(stage, "", cards);
      sec.classList.add("fold-section");
      f.append(sec);
    }
  }
  return f;
}

// 还没有内容的阶段：相邻的几个合成一张横排的小卡，每行写这里以后放什么
const STAGE_HINTS = {
  content: tr("Packaging copy will appear here when the content list is ready"),
  plan: tr("Use a layout draft when the packaging has substantial copy"),
  concept: tr("Review main-view concepts and choose a direction here"),
  flat_concept: tr("Unfolded views of the chosen concept"),
  design: tr("Render the dieline proof to review copy, artwork, dimensions, and finishes"),
  mockup: tr("Rotate the 3D mockup to inspect the assembled box"),
  delivery: tr("Preflight checks and factory delivery pack"),
};

function framesFor(stages) {
  const out = [];
  let run = [];
  const flush = () => {
    if (run.length) out.push(upcomingEl(run));
    run = [];
  };
  for (const s of stages) {
    if (s.cards.length) {
      flush();
      out.push(frameEl(s));
    } else {
      run.push(s);
    }
  }
  flush();
  return out;
}

function upcomingEl(stages) {
  const f = el("div", "frame empty upcoming");
  f.dataset.stage = stages[0].id;
  if (stages.length > 1) f.append(el("div", "up-eyebrow", tr("Waiting for content · {1} stages", stages.length)));
  for (const s of stages) {
    const row = el("div", "up-row");
    row.dataset.stage = s.id;
    const line = el("div", "up-line");
    line.append(el("span", "up-title", s.title), stageChip(s, true));
    const text = el("div", "up-text");
    text.append(line, el("div", "up-hint", STAGE_HINTS[s.id] || ""));
    row.append(el("span", "up-num", stageNo(s.no)), text);
    f.append(row);
  }
  return f;
}

function groupEl(stage, label, cards) {
  const ge = el("div", "group");
  if (label) ge.append(el("div", "group-label", label));
  const gc = el("div", "group-cards");
  const cols = stage.id === "design" && cards.length > 1 ? 2 : COLS[stage.id] || 3;
  const w = Math.max(...cards.map((c) => cardWidth(stage, c)));
  gc.style.maxWidth = `${cols * w + (cols - 1) * 16}px`;
  for (const c of cards) gc.append(cardEl(c, stage));
  ge.append(gc);
  return ge;
}

// ---------------- 过程稿的折叠组（展开哪些记在本机浏览器里） ----------------

const expandedFolds = new Set();
let foldsLoaded = false;

function foldKey(stage, fold) {
  return `${stage.id}|${fold}`;
}

function loadFolds() {
  if (foldsLoaded || !state) return;
  foldsLoaded = true;
  try {
    for (const k of JSON.parse(localStorage.getItem(`pe-studio-folds:${state.job_dir}`) || "[]")) expandedFolds.add(k);
  } catch (_) {
    /* 无痕窗口等：默认全收起 */
  }
}

function saveFolds() {
  try {
    localStorage.setItem(`pe-studio-folds:${state.job_dir}`, JSON.stringify([...expandedFolds]));
  } catch (_) {
    /* 不记也能用 */
  }
}

function expandFoldOf(cardId) {
  const st = state.stages.find((s) => s.cards.some((c) => c.id === cardId));
  const card = st && st.cards.find((c) => c.id === cardId);
  if (!card || !card.fold || expandedFolds.has(foldKey(st, card.fold))) return false;
  expandedFolds.add(foldKey(st, card.fold));
  saveFolds();
  render(state);
  return true;
}

const NO_ART_FILE = tr("Ask the agent to render this proof again to get the artwork-only view. Existing approvals still apply.");

function overlayTools(stage) {
  const t = el("span", "tools");
  // 早先 render 的确认图没有 art_file：开关点了也换不了图，标成不可用并说明原因
  const hasArt = (stage.cards || []).some((c) => c.data?.map?.art_file);
  for (const [key, label] of [["findings", tr("Issue markers")], ["regions", tr("Finish areas")], ["lines", tr("Cut lines")]]) {
    const b = el("button", `toggle${toggles[key] ? " on" : ""}`, label);
    b.dataset.overlay=key;
    if (key === "lines") {
      b.title = hasArt ? tr("Hide cut and crease lines to check for breaks or misalignment across seams") : NO_ART_FILE;
      if (!hasArt) b.classList.add("unavailable");
    }
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (key === "lines" && !hasArt) {
        toast(NO_ART_FILE, 5000);
        return;
      }
      toggles[key] = !toggles[key];
      b.classList.toggle("on", toggles[key]);
      if (key === "lines") swapDesignImages();
      else renderOverlays();
    });
    t.append(b);
  }
  return t;
}

const ZOOM_MAX_PX = 1600; // 云端一次最多给这么大的局部（每边，原图像素）

// 画布上露出最多的那张刀版确认图：把露出的那块向本机工作台要清楚版（工作台再向云端要），盖在图上
async function zoomVisible() {
  const vp = world.parentElement.getBoundingClientRect();
  let best = null;
  let bestArea = 0;
  for (const { el: card } of cardEls.values()) {
    if (!card.dataset.map) continue;
    const img = card.querySelector(".media > img[data-file]");
    if (!img) continue;
    const r = img.getBoundingClientRect();
    const x0 = Math.max(r.left, vp.left), y0 = Math.max(r.top, vp.top);
    const x1 = Math.min(r.right, vp.right), y1 = Math.min(r.bottom, vp.bottom);
    const area = Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
    if (area > bestArea) {
      bestArea = area;
      best = { card, img, box: [(x0 - r.left) / r.width, (y0 - r.top) / r.height, (x1 - r.left) / r.width, (y1 - r.top) / r.height] };
    }
  }
  if (!best) return toast(tr("Pan the canvas to the dieline proof first"));
  const st = state.stages.find((s) => s.id === "design");
  const data = st && st.cards.find((c) => c.id === best.card.dataset.id);
  const m = data && data.data.map;
  if (!m) return toast(tr("Detail zoom is unavailable for this image"));
  const [W, H] = m.size_px;
  const { box } = best;
  if ((box[2] - box[0]) * W > ZOOM_MAX_PX || (box[3] - box[1]) * H > ZOOM_MAX_PX) {
    const mm = Math.round(ZOOM_MAX_PX / (m.dpi / 25.4));
    return toast(tr("Zoom in further. The maximum area is about {1} × {2} mm.", mm, mm), 4000);
  }
  const res = await fetch(apiUrl("/api/zoom"), {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Studio-Token": TOKEN },
    body: JSON.stringify({ file: best.img.dataset.file, box }),
  });
  if (res.status === 403) throw new Error(STALE_PAGE);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const d = await res.json();
      if (d.errors && d.errors.length) msg = d.errors.map((e) => (e.fix ? tr("{1} ({2})", e.message, e.fix) : e.message)).join(tr("; "));
    } catch (_) { /* 不是 JSON */ }
    throw new Error(msg);
  }
  const media = best.img.parentElement;
  let layer = media.querySelector(".zoom-patches");
  if (!layer) {
    layer = el("div", "zoom-patches");
    media.insertBefore(layer, media.querySelector(".overlay"));
  }
  const patch = el("img", "zoom-patch");
  patch.alt = "";
  patch.src = URL.createObjectURL(await res.blob());
  Object.assign(patch.style, {
    left: `${box[0] * 100}%`, top: `${box[1] * 100}%`,
    width: `${(box[2] - box[0]) * 100}%`, height: `${(box[3] - box[1]) * 100}%`,
  });
  layer.append(patch);
  toast(tr("Showing this area at 200 DPI with a watermark"));
}

// 正式稿上的面名与印前问题框：按 preview.map.json 的毫米 ↔ 像素换算，用百分比定位
function renderOverlays() {
  const items = (state && state.findings && state.findings.items) || [];
  for (const { el: card } of cardEls.values()) {
    if (!card.dataset.map) continue;
    const id = card.dataset.id;
    const st = state.stages.find((s) => s.id === "design");
    const data = st && st.cards.find((c) => c.id === id);
    const ov = card.querySelector(".overlay");
    if (!data || !ov) continue;
    ov.replaceChildren();
    const m = data.data.map;
    const k = m.dpi / 25.4;
    const [W, H] = m.size_px;
    const px = (x) => ((x - m.x0_mm) * k / W) * 100;
    const py = (y) => ((m.y1_mm - y) * k / H) * 100;
    if (toggles.regions && m.regions?.length) {
      const ns="http://www.w3.org/2000/svg";
      const svg=document.createElementNS(ns,"svg");
      svg.setAttribute("viewBox",`0 0 ${W} ${H}`); svg.classList.add("region-svg");
      const regions = m.regions;
      for(const r of regions) {
        const path=document.createElementNS(ns,"path");
        path.setAttribute("d",r.rings.map(poly=>poly.map(ring=>ring.map(([x,y],i)=>`${i?'L':'M'}${(x-m.x0_mm)*k},${(m.y1_mm-y)*k}`).join(" ")+" Z").join(" ")).join(" "));
        path.setAttribute("fill-rule","evenodd");path.setAttribute("vector-effect","non-scaling-stroke");
        path.setAttribute("class",`region-path${card.dataset.region===r.id?' selected':''}${r.type.includes('lamination')||r.type.includes('varnish')?' surface':''}`);
        path.dataset.region=r.id;svg.append(path);
      }
      ov.append(svg);
    }
    if (toggles.findings) {
      items.forEach((f, i) => {
        const w = f.where;
        if (!w || (w.part ?? "") !== (m.part ?? "")) return;
        const b = el("div", `fbox ${f.level}`);
        b.dataset.fi = String(i);
        b.style.left = `${px(w.x0 - ANNOTATION_PAD_MM)}%`;
        b.style.top = `${py(w.y1 + ANNOTATION_PAD_MM)}%`;
        b.style.width = `${((w.x1 - w.x0 + 2 * ANNOTATION_PAD_MM) * k / W) * 100}%`;
        b.style.height = `${((w.y1 - w.y0 + 2 * ANNOTATION_PAD_MM) * k / H) * 100}%`;
        b.append(el("i", null, f.id));
        b.title = f.message;
        ov.append(b);
      });
    }
  }
}

function focusFinding(i) {
  const f = state.findings.items[i];
  if (!f.where) return;
  if (!toggles.findings) {
    toggles.findings = true;
    for (const b of world.querySelectorAll(".toggle")) if (b.dataset.overlay === "findings") b.classList.add("on");
    renderOverlays();
  }
  const box = world.querySelector(`.fbox[data-fi="${i}"]`);
  if (!box) return;
  const r = worldRect(box);
  const grow = Math.max(0, 260 - r.w) / 2;
  const growY = Math.max(0, 200 - r.h) / 2;
  zoomToRect({ x: r.x - grow, y: r.y - growY, w: r.w + 2 * grow, h: r.h + 2 * growY }, 60, 3);
  box.classList.remove("flash");
  void box.offsetWidth;
  box.classList.add("flash");
}

// ---------------- 顶栏与侧栏 ----------------

function renderTop(st) {
  const s = st.summary || {};
  $("#job-name").textContent = st.job_id || tr("Workbench");
  document.title = tr("{1} · pacxio Workbench", st.job_id || "job");
  if (!s.style) return;
  const parts = (s.parts || []).map((p) => `${p.label} ${p.id}`).join(" + ");
  $("#job-meta").textContent = [
    `${s.style_name} ${s.style.toUpperCase()}`, s.dims, s.material, s.print_method,
    parts ? tr("Parts: {1}", parts) : null,
    tr("Profile {1}{2}", s.profile_name || s.profile, s.profile_verified ? "" : tr(" (pending factory verification)")),
  ].filter(Boolean).join(" · ");
}

function renderBanner(st) {
  const b = $("#banner");
  if (st.errors && st.errors.length) {
    b.hidden = false;
    b.textContent = tr("{1}: {2}", st.now, st.errors.map((e) => e.message).join(tr("; ")));
  } else {
    b.hidden = true;
  }
}

function setTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("on", b.dataset.tab === name);
  for (const p of document.querySelectorAll(".pane")) p.classList.toggle("on", p.id === `pane-${name}`);
}
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => setTab(b.dataset.tab));

function agentStatus() {
  if (["waiting", "notified"].includes(agentLink.status)
      && (!Number.isFinite(agentLink.expires_at) || agentLink.expires_at <= Date.now() / 1000)) return "disconnected";
  return agentLink.status;
}

function actionContinues(a) {
  const stages = { content: "content", plan: "plan", concept: "concept", flats: "flat_concept", approve: "design" };
  return a.enabled && agentStatus() === "waiting" && stages[a.id] === agentLink.stage;
}

function actionLabel(a) {
  if (!actionContinues(a)) return a.label;
  return a.id === "concept" ? tr("Choose this direction and continue") : tr("{1} and continue", a.label);
}

function recordConfirmation(result, action, startedAt) {
  if (result.data?.already_confirmed === true) return false;
  savedConfirmation = { started_at: startedAt, saved_at: Date.now() / 1000, action, revision: result.data?.revision };
  return true;
}

function matchesConfirmation() {
  if (!savedConfirmation) return true;
  if (savedConfirmation.revision && agentLink.revision) return savedConfirmation.revision === agentLink.revision;
  return agentLink.updated_at >= savedConfirmation.started_at;
}

function reconcileConfirmation(st, requestedAt = null) {
  if (!savedConfirmation) return;
  if (agentStatus() === "received" && matchesConfirmation()) {
    savedConfirmation.received = true;
  }
  if (requestedAt !== null && requestedAt >= savedConfirmation.saved_at) {
    savedConfirmation.review_pending = st.now_who === "user";
  }
  if (savedConfirmation.review_pending && st.now_who === "user"
      && agentStatus() === "waiting" && agentLink.stage === st.next_stage
      && agentLink.updated_at > savedConfirmation.started_at) savedConfirmation = null;
}

function handoffMessage() {
  if (state && !state.now_who) return null;
  const status = agentStatus();
  const currentConfirmation = savedConfirmation && matchesConfirmation();
  const received = status === "received" && (matchesConfirmation() || savedConfirmation?.received);
  if (received) return {
    tone: "ok", title: currentConfirmation ? tr("Agent received your confirmation") : tr("Agent received the Workbench update"),
    detail: tr("Your agent can now continue. New results will appear here."),
  };
  if (status === "notified" && (matchesConfirmation() || savedConfirmation?.received)) return {
    tone: "wait", title: currentConfirmation ? tr("Choice sent · waiting for your agent") : tr("Workbench update sent · waiting for your agent"),
    detail: currentConfirmation ? tr("Your choice is saved. Waiting for your agent to read the next step.") : tr("Waiting for your agent to read the latest Workbench state."),
  };
  if (status === "waiting") return savedConfirmation ? {
    tone: "wait", title: tr("Confirmation saved"),
    detail: tr("Waiting for your agent to receive it. Keep the agent conversation open."),
  } : {
    tone: "wait", title: tr("Agent is waiting for your confirmation"),
    detail: tr("Confirm here to let your agent continue automatically. Keep the agent conversation open."),
  };
  if (status === "loading") return {
    tone: "mute", title: tr("Checking automatic continuation…"), detail: "",
  };
  if (savedConfirmation && !savedConfirmation.received && ["received", "notified", "idle"].includes(status)) return {
    tone: "mute", title: tr("Confirmation saved"),
    detail: tr("Return to the original agent conversation to continue from this updated choice."),
  };
  if (!state?.now_who && !savedConfirmation) return null;
  if (status === "idle" && state?.now_who === "agent") return {
    tone: "mute", title: tr("The next step is with your agent"),
    detail: tr("Check the original agent conversation for progress. New results will appear here."),
  };
  return {
    tone: "warn", title: savedConfirmation ? tr("Confirmation saved · agent disconnected") : tr("Automatic continuation is not connected"),
    detail: tr("Return to the original agent conversation and ask it to reconnect. Your saved choices are kept."),
  };
}

function refreshAgentUI() {
  if (state) reconcileConfirmation(state);
  const box = $("#agent-handoff");
  const message = handoffMessage();
  if (box) {
    const key = JSON.stringify(message);
    if (box.dataset.message !== key) {
      box.dataset.message = key;
      box.hidden = !message;
      box.replaceChildren();
      if (message) {
        box.className = `agent-handoff ${message.tone}`;
        box.append(el("div", "agent-handoff-title", message.title));
        if (message.detail) box.append(el("p", "note", message.detail));
      }
    }
  }
  for (const button of document.querySelectorAll("button[data-confirm-action]")) {
    if (button.dataset.submitting === "true") continue;
    const a = { id: button.dataset.confirmAction, label: button.dataset.confirmLabel, enabled: button.dataset.confirmEnabled === "true" };
    button.textContent = actionLabel(a) + (button.dataset.confirmDialog === "true" ? "" : a.enabled ? "…" : "");
  }
}

function bindActionLabel(button, a, dialog = false) {
  button.dataset.confirmAction = a.id;
  button.dataset.confirmLabel = a.label;
  button.dataset.confirmEnabled = String(a.enabled);
  button.dataset.confirmDialog = String(dialog);
}

async function checkAgent() {
  if (checkingAgent) return checkingAgent;
  checkingAgent = (async () => {
    try {
      const response = await fetch(apiUrl("/api/agent"), { cache: "no-store", signal: AbortSignal.timeout(5000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!["waiting", "notified", "received", "disconnected", "idle"].includes(data.status)) throw new Error("Invalid agent state");
      agentLink = data;
    } catch (_) {
      agentLink = { status: "disconnected", stage: null, updated_at: null, expires_at: null };
    }
    refreshAgentUI();
  })();
  try { await checkingAgent; } finally { checkingAgent = null; }
}

function renderFlow(st) {
  const pane = $("#pane-flow");
  pane.replaceChildren();
  const now = el("div", `now${st.now_who === "user" ? "" : " agent"}`);
  now.append(el("div", "eyebrow", tr(st.now_who === "user" ? "Your next step" : "Next step")), el("div", "now-text", st.next_action || st.now));
  const handoff = el("div", "agent-handoff");
  handoff.id = "agent-handoff";
  handoff.setAttribute("role", "status");
  handoff.setAttribute("aria-live", "polite");
  now.append(handoff);
  const nowSec = el("div", "sec");
  nowSec.append(now);
  pane.append(nowSec);
  const acts = st.stages.flatMap((s) => (s.actions || []).map((a) => [a, s]));
  if (acts.length) {
    const sec2 = el("div", "sec");
    sec2.append(el("h4", null, tr("Your confirmations")));
    for (const [a, s] of acts) {
      const row = el("div", "act-row");
      row.append(el("span", "n", stageNo(s.no)), el("span", null, s.title), actionButton(a, null));
      if (!a.enabled && a.reason) row.append(el("div", "note", a.reason));
      sec2.append(row);
    }
    sec2.append(el("div", "note", tr("Confirm these items yourself. To pick a concept, click Choose this direction below a main view in stage ③.")));
    pane.append(sec2);
  }
  const sec = el("div", "sec");
  sec.append(el("h4", null, tr("Stages")));
  for (const stage of st.stages) {
    const row = el("div", "stage-row");
    row.append(el("span", "n", stageNo(stage.no)), el("span", null, stage.title), stageChip(stage, true));
    row.title = stage.action ? tr(stage.todo === "user" ? "Waiting for you: {1}" : "Waiting for the agent: {1}", stage.action) : "";
    row.addEventListener("click", () => {
      const f = world.querySelector(`[data-stage="${stage.id}"]`); // 空阶段在合并的小卡里：跳到整张卡
      if (f) zoomTo(f.closest(".frame") || f);
    });
    sec.append(row);
  }
  pane.append(sec);
  const s = st.summary || {};
  if (s.style) {
    const info = el("div", "sec");
    info.append(el("h4", null, "job"));
    const dl = el("dl", "kv");
    const rows = [
      [tr("Box style"), tr("{1} ({2})", s.style_name, s.style)], [tr("Dimensions"), s.dims], [tr("Material"), `${s.material} · ${s.caliper_mm} mm`],
      [tr("Print method"), s.print_method], [tr("Profile"), `${s.profile_name || s.profile}${s.profile_verified ? "" : tr(" (pending factory verification)")}`],
    ];
    if (s.parts && s.parts.length) rows.push([tr("Parts"), s.parts.map((p) => `${p.label} ${p.id}`).join(tr(", "))]);
    for (const [k, v] of rows) dl.append(el("dt", null, k), el("dd", null, v));
    info.append(dl, el("div", "note", tr("Use Pin or Box in the toolbar to add notes. Confirm above or tell the agent in chat.")));
    pane.append(info);
  }
  refreshAgentUI();
}

function renderIssues(st) {
  const pane = $("#pane-issues");
  pane.replaceChildren();
  const count = $("#issue-count");
  const f = st.findings;
  if (!f) {
    count.hidden = true;
    pane.append(el("div", "empty-note", tr("Run pacxio preflight to check this job.")));
    return;
  }
  const errs = f.items.filter((x) => x.level === "error").length;
  const warns = f.items.length - errs;
  count.hidden = !f.items.length;
  count.textContent = String(errs || warns);
  count.className = `count${errs ? "" : " w"}`;
  const top = el("div", "sec");
  const line = el("div");
  line.append(el("span", null, tr("Checked at {1} ", hhmm(f.mtime))), chip(f.passed ? tr("Passed") : tr("Errors found"), f.passed ? "ok" : "bad"));
  if (f.stale) line.append(" ", chip(tr("Out of date"), "warn"));
  top.append(line);
  if (!f.profile_verified) top.append(el("div", "note", tr("The factory has yet to verify this profile. Its values are provisional.")));
  for (const [rule, why] of Object.entries(f.skipped || {})) top.append(el("div", "note", tr("Skipped {1}: {2}", rule, why)));
  if (f.pdf) {
    const a = el("a", null, tr("Open preflight report PDF"));
    a.href = fileUrl(f.pdf, f.mtime);
    a.target = "_blank";
    a.rel = "noopener";
    const note = el("div", "note");
    note.append(a);
    top.append(note);
  }
  pane.append(top);
  const list = el("div", "sec");
  const order = f.items.map((x, i) => [x, i]).sort((a, b) => (a[0].level === "error" ? 0 : 1) - (b[0].level === "error" ? 0 : 1));
  for (const [x, i] of order) {
    const box = el("div", `finding${x.where ? " click" : ""}`);
    const h = el("div", "head");
    h.append(chip(x.level, x.level === "error" ? "bad" : "warn"), el("b", null, x.id));
    if (x.where) h.append(el("span", "where", [x.where.part, x.where.panel].filter(Boolean).join(".") || tr("Location")));
    box.append(h, el("div", null, x.message));
    if (x.fix) box.append(el("div", "fix", tr("Fix: {1}", x.fix)));
    if (x.where) box.addEventListener("click", () => focusFinding(i));
    list.append(box);
  }
  if (!f.items.length) list.append(el("div", "empty-note", tr("No errors or warnings.")));
  if (f.info) list.append(el("div", "note", tr("See the preflight report for {1} informational finding(s).", f.info)));
  pane.append(list);
}

function renderFiles(st) {
  const pane = $("#pane-files");
  pane.replaceChildren();
  const delivery = st.stages.find((s) => s.id === "delivery");
  const files = delivery && delivery.cards.find((c) => c.kind === "files");
  const sec = el("div", "sec");
  sec.append(el("h4", null, files ? files.title : tr("Delivery pack")));
  if (files) {
    const list = el("div", "files-list");
    for (const f of files.data.files) list.append(fileLink(f));
    sec.append(list);
  } else {
    sec.append(el("div", "empty-note", tr("The delivery pack has yet to be exported.")));
  }
  pane.append(sec);
  const other = el("div", "sec");
  other.append(el("h4", null, "job"));
  const list = el("div", "files-list");
  list.append(fileLink({ path: "job.json", mtime: Date.now() / 1000 }));
  if (st.findings) list.append(fileLink({ path: st.findings.file, mtime: st.findings.mtime }));
  other.append(list);
  pane.append(other);
}

// ---------------- 全图导航 ----------------

function updateMinimap() {
  const mm = $("#minimap");
  const ww = world.offsetWidth;
  const wh = world.offsetHeight;
  if (!ww || !wh) return;
  const k = Math.min((mm.clientWidth - 8) / ww, (mm.clientHeight - 8) / wh);
  mm.dataset.k = String(k);
  mm.dataset.ox = String((mm.clientWidth - ww * k) / 2);
  mm.dataset.oy = String((mm.clientHeight - wh * k) / 2);
  mm.replaceChildren();
  for (const f of world.children) {
    const b = el("div", `mm-frame${f.classList.contains("empty") ? "" : " has"}`);
    b.style.left = `${Number(mm.dataset.ox) + f.offsetLeft * k}px`;
    b.style.top = `${Number(mm.dataset.oy) + f.offsetTop * k}px`;
    b.style.width = `${Math.max(2, f.offsetWidth * k)}px`;
    b.style.height = `${Math.max(2, f.offsetHeight * k)}px`;
    mm.append(b);
  }
  mm.append(el("div", "mm-view"));
  updateMinimapView();
}

function updateMinimapView() {
  const mm = $("#minimap");
  const v = mm.querySelector(".mm-view");
  if (!v) return;
  const k = Number(mm.dataset.k);
  v.style.left = `${Number(mm.dataset.ox) + (-view.x / view.s) * k}px`;
  v.style.top = `${Number(mm.dataset.oy) + (-view.y / view.s) * k}px`;
  v.style.width = `${(canvas.clientWidth / view.s) * k}px`;
  v.style.height = `${(canvas.clientHeight / view.s) * k}px`;
}

$("#minimap").addEventListener("click", (e) => {
  const mm = $("#minimap");
  const r = mm.getBoundingClientRect();
  const k = Number(mm.dataset.k);
  const wx = (e.clientX - r.left - Number(mm.dataset.ox)) / k;
  const wy = (e.clientY - r.top - Number(mm.dataset.oy)) / k;
  view.x = canvas.clientWidth / 2 - wx * view.s;
  view.y = canvas.clientHeight / 2 - wy * view.s;
  apply(true);
});

// ---------------- 批注（S2） ----------------

const TOKEN = (document.querySelector('meta[name="studio-token"]') || {}).content || "";
const NOTE_STATUS = { open: [tr("Waiting for the agent"), "warn"], answered: [tr("Awaiting your review"), "wait"], resolved: [tr("Resolved"), "mute"] };
const MIN_BOX_PX = 6; // 拖出的框比这小就当图钉
let tool = "move"; // move | pin | box
let showResolved = false;
let mark = null; // 正在画的图钉 / 框
let composing = null;
let bubbleId = null;
let bubbleAnchor = null;

function setTool(t) {
  tool = t;
  canvas.dataset.tool = t;
  for (const b of document.querySelectorAll("#tools button")) b.classList.toggle("on", b.dataset.tool === t);
}
for (const b of document.querySelectorAll("#tools button")) b.addEventListener("click", () => setTool(b.dataset.tool));

async function post(path, body, { refresh = true } = {}) {
  const res = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Studio-Token": TOKEN },
    body: JSON.stringify(body),
  });
  if (res.status === 403) throw new Error(STALE_PAGE); // 口令过期：服务重启过，页面要刷新
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    data = null;
  }
  if (!data || !data.ok) {
    const msg = data && data.errors && data.errors.length ? data.errors.map((e) => e.message).join(tr("; ")) : (data && data.error) || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  if (refresh) await load();
  return data;
}

function toast(text, ms = 2600) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), ms);
}

function findCard(id) {
  for (const st of (state && state.stages) || []) {
    const c = st.cards.find((x) => x.id === id);
    if (c) return c;
  }
  return null;
}

function relPoint(media, e) {
  const r = media.getBoundingClientRect();
  return [clamp((e.clientX - r.left) / r.width, 0, 1), clamp((e.clientY - r.top) / r.height, 0, 1)];
}

// 整张图上的框 [x0, y0, x1, y1] → 裁过的那一块里的位置（toFull 的反过来）
function toCropped(media, rel) {
  const d = media.dataset;
  const x = (v) => (v * Number(d.w) - Number(d.x0)) / Number(d.cw);
  const y = (v) => (v * Number(d.h) - Number(d.y0)) / Number(d.ch);
  return [x(rel[0]), y(rel[1]), x(rel[2]), y(rel[3])];
}

// 分栏图的一段里的位置 → 整张图上的位置
function toFull(media, x, y) {
  if (media.classList.contains("cropped")) {
    const d = media.dataset;
    return [(Number(d.x0) + x * Number(d.cw)) / Number(d.w), (Number(d.y0) + y * Number(d.ch)) / Number(d.h)];
  }
  if (!media.classList.contains("slice")) return [x, y];
  const y0 = Number(media.dataset.y0);
  const y1 = Number(media.dataset.y1);
  return [x, (y0 + y * (y1 - y0)) / Number(media.dataset.h)];
}

function placeBox(node, r) {
  const x0 = Math.min(r[0], r[2]);
  const y0 = Math.min(r[1], r[3]);
  node.style.left = `${x0 * 100}%`;
  node.style.top = `${y0 * 100}%`;
  node.style.width = `${Math.abs(r[2] - r[0]) * 100}%`;
  node.style.height = `${Math.abs(r[3] - r[1]) * 100}%`;
}

function placePin(node, x, y) {
  node.style.left = `${x * 100}%`;
  node.style.top = `${y * 100}%`;
}

function startMark(e, media) {
  cancelComposer();
  closeBubble();
  const p = relPoint(media, e);
  mark = { id: e.pointerId, card: media.closest(".card"), media, p0: p, p1: p, draft: null };
  capture(e.pointerId);
  if (tool === "box") {
    mark.draft = el("div", "nbox draft");
    media.querySelector(".marks").append(mark.draft);
    placeBox(mark.draft, [...p, ...p]);
  }
}

function moveMark(e) {
  mark.p1 = relPoint(mark.media, e);
  if (mark.draft) placeBox(mark.draft, [...mark.p0, ...mark.p1]);
}

function cancelMark() {
  if (mark && mark.draft) mark.draft.remove();
  mark = null;
}

function endMark(e) {
  const m = mark;
  mark = null;
  const r = m.media.getBoundingClientRect();
  const big = Math.abs(m.p1[0] - m.p0[0]) * r.width >= MIN_BOX_PX && Math.abs(m.p1[1] - m.p0[1]) * r.height >= MIN_BOX_PX;
  let rel;
  let shape;
  const a = toFull(m.media, ...m.p0);
  const b = toFull(m.media, ...m.p1);
  if (tool === "box" && big) {
    shape = "box";
    rel = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  } else {
    shape = "pin";
    rel = [b[0], b[1], b[0], b[1]];
    if (m.draft) m.draft.remove();
    m.draft = el("div", "npin draft");
    m.draft.append(el("b", null, "+"));
    placePin(m.draft, m.p1[0], m.p1[1]);
    m.media.querySelector(".marks").append(m.draft);
  }
  openComposer(e.clientX, e.clientY, { card: m.card.dataset.id, file: m.card.dataset.file, shape, rel }, m.draft);
}

function positionPopup(node, x, y) {
  node.style.left = "0px";
  node.style.top = "0px";
  const w = node.offsetWidth;
  const h = node.offsetHeight;
  node.style.left = `${clamp(x + 14, 8, innerWidth - w - 8)}px`;
  node.style.top = `${clamp(y - 10, 56, innerHeight - h - 8)}px`;
}

function openComposer(x, y, target, draft) {
  cancelComposer();
  closeBubble();
  composing = { target, draft };
  const c = findCard(target.card);
  const what = target.shape === "item" ? tr("Content list · Item {1}", target.item) : `${target.shape === "pin" ? tr("Pin") : tr("Box")} · ${c ? c.title : target.card}`;
  $("#composer-where").textContent = what;
  $("#composer-err").textContent = "";
  $("#composer-text").value = "";
  $("#composer").hidden = false;
  positionPopup($("#composer"), x, y);
  $("#composer-text").focus();
}

function cancelComposer() {
  if (!composing) return;
  if (composing.draft) composing.draft.remove();
  composing = null;
  $("#composer").hidden = true;
}

async function saveComposer() {
  const text = $("#composer-text").value.trim();
  if (!text) {
    $("#composer-err").textContent = tr("Describe what needs to change");
    return;
  }
  const btn = $("#composer-save");
  btn.disabled = true;
  try {
    const d = await post("/api/notes", { ...composing.target, text });
    cancelComposer();
    toast(tr("Saved {1}: {2}", d.data.note.id, d.data.note.where));
  } catch (err) {
    $("#composer-err").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}
window.addEventListener("resize", () => repositionBubble());
$("#composer-save").addEventListener("click", saveComposer);
$("#composer-cancel").addEventListener("click", cancelComposer);
$("#composer-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    saveComposer();
  }
});

// 设计图（有毫米对照）上的批注按毫米位置画：图重出、尺寸变了也落在原来的地方
function noteRel(n) {
  const t = n.target;
  if (!t.rel) return null;
  const card = findCard(t.card);
  const m = card && card.data && card.data.map;
  if (m && t.world_mm && m.space === "blank") {
    const k = m.dpi / 25.4;
    const [W, H] = m.size_px;
    const w = t.world_mm;
    return [(w[0] - m.x0_mm) * k / W, (m.y1_mm - w[3]) * k / H, (w[2] - m.x0_mm) * k / W, (m.y1_mm - w[1]) * k / H];
  }
  return t.rel;
}

function noteMarker(n, rel) {
  const pin = n.target.shape === "pin";
  const node = el("div", `${pin ? "npin" : "nbox"} ${n.status}`);
  node.dataset.note = n.id;
  node.title = `${n.id}：${n.text}`;
  if (pin) {
    placePin(node, rel[0], rel[1]);
    node.append(el("b", null, n.id));
  } else {
    placeBox(node, rel);
    node.append(el("i", null, n.id));
  }
  node.addEventListener("click", (e) => {
    e.stopPropagation();
    openBubble(n.id, node);
  });
  return node;
}

function renderMarks() {
  for (const { el: c } of cardEls.values()) {
    const layer = c.querySelector(".marks");
    if (layer) for (const m of [...layer.children]) if (!m.classList.contains("draft")) m.remove();
    for (const holder of c.querySelectorAll(".row-notes")) holder.replaceChildren();
  }
  for (const n of state.notes || []) {
    if (n.status === "resolved" && !showResolved) continue;
    const t = n.target;
    if (t.shape === "item") {
      const row = world.querySelector(`[data-item="${CSS.escape(t.item || "")}"] .row-notes`);
      if (!row) continue;
      const b = el("button", `nbadge ${n.status}`, n.id);
      b.dataset.note = n.id;
      b.title = n.text;
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        openBubble(n.id, b);
      });
      row.append(b);
      continue;
    }
    const entry = t.card && cardEls.get(t.card);
    const rel = noteRel(n);
    if (entry && rel) placeMarks(entry.el, n, rel);
  }
}

function placeMarks(cardEl, n, rel) {
  const slices = cardEl.querySelectorAll(".slice");
  if (!slices.length) {
    const layer = cardEl.querySelector(".marks");
    const media = cardEl.querySelector(".media.cropped");
    if (media) rel = toCropped(media, rel); // 整张图上的位置 → 裁过的那一块里的位置
    if (layer) layer.append(noteMarker(n, rel));
    return;
  }
  let first = true;
  for (const sl of slices) {
    const y0 = Number(sl.dataset.y0);
    const y1 = Number(sl.dataset.y1);
    const H = Number(sl.dataset.h);
    const a = (rel[1] * H - y0) / (y1 - y0);
    const b = (rel[3] * H - y0) / (y1 - y0);
    const pin = n.target.shape === "pin";
    if (pin ? a < 0 || a >= 1 : b < 0 || a > 1) continue;
    const node = noteMarker(n, [rel[0], clamp(a, 0, 1), rel[2], clamp(b, 0, 1)]);
    if (!first) node.classList.add("cont"); // 跨栏的框：后面几段不重复标编号
    first = false;
    sl.querySelector(".marks").append(node);
  }
}

function noteById(id) {
  return ((state && state.notes) || []).find((n) => n.id === id);
}

function who(by) {
  return by === "用户" ? tr("You") : by;
}

function noteTime(n) {
  return hhmm(Date.parse(n.updated || n.created) / 1000);
}

function openBubble(id, anchor) {
  const n = noteById(id);
  if (!n) return closeBubble();
  cancelComposer();
  const old = bubbleId === id ? $("#bubble textarea") : null;
  const keep = old ? old.value : "";
  bubbleId = id;
  bubbleAnchor = anchor;
  const b = $("#bubble");
  b.replaceChildren();
  const head = el("div", "b-head");
  const [label, tone] = NOTE_STATUS[n.status];
  head.append(el("b", null, n.id), chip(label, tone), el("span", "who", `${who(n.by)} · ${noteTime(n)}`));
  const x = el("button", "x", "×");
  x.title = tr("Collapse (Esc)");
  x.addEventListener("click", closeBubble);
  head.append(x);
  b.append(head, el("div", "b-where", n.where), el("div", "b-text", n.text));
  if (n.stage === null && n.target.card) b.append(el("div", "b-where", tr("The original image is no longer on the canvas.")));
  for (const r of n.replies) {
    const d = el("div", `b-reply${r.by === "用户" ? " me" : ""}`);
    d.append(el("b", null, `${who(r.by)}：`), r.text);
    b.append(d);
  }
  const err = el("div", "b-err");
  const acts = el("div", "b-actions");
  const act = (text, fn, cls) => {
    const btn = el("button", `btn${cls ? ` ${cls}` : ""}`, text);
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      err.textContent = "";
      try {
        await fn();
      } catch (e) {
        err.textContent = e.message;
      } finally {
        btn.disabled = false;
      }
    });
    acts.append(btn);
  };
  if (n.status !== "resolved") {
    const ta = el("textarea");
    ta.rows = 2;
    ta.placeholder = tr("Reply… (⌘↵ to send)");
    ta.value = keep;
    const reply = async () => {
      const text = ta.value.trim();
      if (!text) throw new Error(tr("Enter a reply first"));
      await post(`/api/notes/${n.id}/reply`, { text });
      const fresh = $("#bubble textarea");
      if (fresh) fresh.value = "";
    };
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        reply().catch((x2) => (err.textContent = x2.message));
      }
    });
    b.append(ta);
    act(tr("Reply"), reply, "primary");
    act(tr("Close"), () => post(`/api/notes/${n.id}/resolve`, {}));
  } else {
    act(tr("Reopen"), () => post(`/api/notes/${n.id}/reopen`, {}));
  }
  if (n.by === "用户" && !n.replies.length) {
    act(tr("Delete"), async () => {
      await post(`/api/notes/${n.id}/delete`, {});
      closeBubble();
    }, "danger");
  }
  b.append(acts, err);
  b.hidden = false;
  repositionBubble();
}

function repositionBubble() {
  const b = $("#bubble");
  if (b.hidden || !bubbleId) return;
  const live = world.querySelector(`[data-note="${bubbleId}"]`) || bubbleAnchor;
  bubbleAnchor = live;
  if (live && live.isConnected) {
    const r = live.getBoundingClientRect();
    positionPopup(b, r.right, r.top);
  } else {
    const c = canvas.getBoundingClientRect();
    positionPopup(b, c.left + c.width / 2 - 150, c.top + 60);
  }
}

function closeBubble() {
  bubbleId = null;
  bubbleAnchor = null;
  $("#bubble").hidden = true;
}

function focusNote(id) {
  const n = noteById(id);
  if (!n) return;
  if (n.target.card) expandFoldOf(n.target.card);
  if (n.status === "resolved" && !showResolved) {
    showResolved = true;
    renderMarks();
    renderNotesPane(state);
  }
  const node = world.querySelector(`[data-note="${id}"]`);
  if (!node) {
    openBubble(id, null);
    return;
  }
  const r = worldRect(node);
  const minW = Math.max(r.w, 280);
  const minH = Math.max(r.h, 200);
  zoomToRect({ x: r.x + r.w / 2 - minW / 2, y: r.y + r.h / 2 - minH / 2, w: minW, h: minH }, 80, 2.5);
  setTimeout(() => openBubble(id, world.querySelector(`[data-note="${id}"]`)), 320);
}

async function tellAgent() {
  const waiting = (state.notes || []).filter((n) => n.status === "open").length;
  const text = tr("There are {1} open notes in the Workbench. Run pacxio notes list \"{2}\" --open, use the locations and screenshots to make changes, then reply with pacxio notes reply.", waiting, state.job_dir);
  try {
    await navigator.clipboard.writeText(text);
    toast(tr("Copied. Paste into your chat with the agent."));
  } catch (_) {
    window.prompt(tr("Copy this message and send it to the agent:"), text);
  }
}

function renderNotesPane(st) {
  const pane = $("#pane-notes");
  pane.replaceChildren();
  const notes = st.notes || [];
  const waiting = notes.filter((n) => n.status === "open");
  const answered = notes.filter((n) => n.status === "answered");
  const count = $("#note-count");
  count.hidden = !(waiting.length + answered.length);
  count.textContent = String(answered.length || waiting.length);
  count.className = `count${answered.length ? " a" : " w"}`;
  const top = el("div", "sec");
  top.append(el("div", "note", tr("Choose Pin (P) or Box (B) in the toolbar. Click or drag on an image and write a note. The agent receives the part, panel, position in millimetres, elements, and a screenshot.")));
  const bar = el("div", "notes-bar");
  const tell = el("button", "btn primary", tr("Tell the agent{1}", waiting.length ? tr(" ({1})", waiting.length) : ""));
  tell.disabled = !waiting.length;
  tell.addEventListener("click", tellAgent);
  const toggle = el("button", `toggle${showResolved ? " on" : ""}`, tr("Show resolved notes on the canvas"));
  toggle.addEventListener("click", () => {
    showResolved = !showResolved;
    renderMarks();
    renderNotesPane(state);
  });
  bar.append(tell, toggle);
  top.append(bar);
  pane.append(top);
  const groups = [
    ["answered", tr("Awaiting your review (agent replied)")],
    ["open", tr("Awaiting agent")],
    ["resolved", tr("Resolved")],
  ];
  for (const [status, title] of groups) {
    const list = notes.filter((n) => n.status === status);
    if (!list.length) continue;
    const sec = el(status === "resolved" ? "details" : "div", "sec");
    const h = el(status === "resolved" ? "summary" : "h4", null, `${title} · ${list.length}`);
    sec.append(h);
    for (const n of [...list].reverse()) sec.append(noteItem(n));
    pane.append(sec);
  }
  if (!notes.length) pane.append(el("div", "empty-note", tr("No notes yet.")));
}

function noteItem(n) {
  const box = el("div", "nitem");
  const head = el("div", "head");
  const [label, tone] = NOTE_STATUS[n.status];
  head.append(el("b", null, n.id), chip(label, tone), el("span", "who", `${who(n.by)} · ${noteTime(n)}`));
  box.append(head, el("div", "where", n.where), el("div", "text", n.text));
  const last = n.replies[n.replies.length - 1];
  if (last) box.append(el("div", `last${last.by === "用户" ? " me" : ""}`, tr("{1}: {2}", who(last.by), last.text)));
  if (n.snapshot) {
    const img = el("img", "snap");
    img.src = fileUrl(n.snapshot, Date.parse(n.created) / 1000, 480);
    img.alt = tr("Screenshot for {1}", n.id);
    img.loading = "lazy";
    box.append(img);
  }
  box.addEventListener("click", () => focusNote(n.id));
  return box;
}

// ---------------- 确认（S3）：和命令行同一个函数，只能由用户本人点 ----------------

const CONFIRM_TEXT = {
  content: {
    title: tr("Confirm content wording"),
    body: tr("Check the exact wording of every item. Panel placement is reviewed separately. Confirm again if the list changes."),
  },
  plan: {
    title: tr("Confirm the layout draft"),
    body: tr("Review which items go on each panel, their hierarchy, and whether they fit. The final placement is set after the flats are confirmed. Confirming regenerates the draft from the current design. Export approval happens separately."),
  },
  concept: {
    title: tr("Choose a design direction"),
    body: tr("The back view, flats, and final design will follow this image. The final artwork is rebuilt for print. Main colours are sampled from the whole image. To exclude background colours, ask the agent to choose again with --region."),
  },
  use: {
    title: tr("Use this version"),
    body: tr("This image becomes the reference for this back view or set of panels. The previous image moves to older versions and can be restored. A later import automatically becomes the active version."),
  },
  flats: {
    title: tr("Confirm these flats"),
    body: tr("Check that these flats match the chosen main and back views. The final artwork will follow these flats, with graphics traced as vectors and text typeset by the engine. Confirm again after replacing any image."),
  },
  approve: {
    title: tr("Approve the dieline proof for export"),
    body: tr("Check the text, artwork placement, dimensions, and finish annotations against what should go to print. If the design changes, approve the proof again before export. Confirm a physical proof before mass production."),
  },
};

// 不能点的确认也给反馈：点了说清为什么还不能确认（disabled 的按钮点了没任何反应，像坏了）
function runAction(a, card) {
  if (a.enabled) openConfirm(a, card);
  else toast(tr("Unable to {1}: {2}", a.label, a.reason || tr("requirements are pending")), 6000);
}

function actionButton(a, card) {
  const b = el("button", `btn act${a.enabled ? " primary" : " blocked"}`, a.enabled ? `${actionLabel(a)}…` : a.label);
  bindActionLabel(b, a);
  if (a.reason) b.title = a.reason;
  b.addEventListener("click", (ev) => {
    ev.stopPropagation();
    runAction(a, card);
  });
  return b;
}

// 阶段的状态标签：有确认项时可以点（能确认就打开确认框，不能就说原因）
function stageChip(stage, short = false) {
  // short：侧栏和小卡里去掉括号里的说明（英文更长，放不下）；全文在悬停提示和画布的区标题里
  const label = short ? stage.label.replace(/\s*[（(][^）)]*[）)]/g, "") : stage.label;
  const c = chip(label, stage.tone);
  if (label !== stage.label) c.title = stage.label;
  const a = (stage.actions || [])[0];
  if (!a) return c;
  c.classList.add("clickable");
  c.title = a.enabled ? tr("Click to {1}", a.label) : a.reason || "";
  c.addEventListener("click", (ev) => {
    ev.stopPropagation();
    runAction(a, null);
  });
  return c;
}

function stageById(id) {
  return state.stages.find((s) => s.id === id);
}

function previewImg(c) {
  const i = el("img");
  i.src = fileUrl(c.file, c.mtime, 1920);
  i.alt = c.title;
  return i;
}

function confirmPreview(a, card) {
  const box = el("div", "c-preview");
  if (a.id === "content") {
    const rows = stageById("content").cards[0].data.rows;
    const required = rows.filter((r) => r.required).length;
    box.append(el("div", null, tr("{1} items: {2} required and {3} optional. See the original wording in stage ①, Content list.", rows.length, required, rows.length - required)));
    const pend = rows.filter((r) => r.pending);
    if (pend.length) {
      box.append(el("div", "warn-line", tr("{1} pending item(s). Confirm again once they are completed:", pend.length)));
      const ul = el("ul");
      for (const r of pend) ul.append(el("li", null, `${r.id}：${r.pending}`));
      box.append(ul);
    }
  } else if (a.id === "plan") {
    for (const c of stageById("plan").cards.filter((x) => x.kind === "image" && !x.fold)) box.append(previewImg(c));
  } else if (a.id === "concept") {
    box.append(previewImg(card));
    const st = stageById("concept");
    if (st.status === "chosen") box.append(el("div", "warn-line", tr("This replaces the current choice ({1}).", st.label)));
  } else if (a.id === "use") {
    box.append(previewImg(card));
    box.append(el("div", "warn-line", tr("Ask the agent to regenerate the flat proof and mockup proof to see this version there.")));
  } else if (a.id === "approve") {
    const st = stageById("design");
    for (const c of st.cards.filter((x) => x.kind === "image")) box.append(previewImg(c));
    const diffs = st.cards.find((x) => x.id === "design:diffs");
    if (diffs) {
      box.append(el("div", null, tr("Differences from the flats (agent notes):")));
      const ul = el("ul");
      for (const line of diffs.data.lines) ul.append(el("li", null, line));
      box.append(ul);
    }
  }
  return box;
}

function openConfirm(a, card) {
  cancelComposer();
  closeBubble();
  const t = CONFIRM_TEXT[a.id];
  const dlg = $("#confirm .dialog");
  dlg.replaceChildren();
  dlg.append(el("h3", null, t.title), el("p", null, t.body), confirmPreview(a, card));
  const note = el("textarea");
  note.rows = 2;
  note.placeholder = tr("Note (optional)");
  const check = el("label", "c-check");
  const cb = el("input");
  cb.type = "checkbox";
  check.append(cb, el("span", null, tr("I have reviewed this and confirm it")));
  const err = el("div", "b-err");
  const ok = el("button", "btn primary", actionLabel(a));
  bindActionLabel(ok, a, true);
  ok.disabled = true;
  cb.addEventListener("change", () => (ok.disabled = !cb.checked));
  const cancel = el("button", "btn", tr("Cancel"));
  cancel.addEventListener("click", closeConfirm);
  ok.addEventListener("click", async () => {
    if (ok.dataset.submitting === "true") return;
    ok.dataset.submitting = "true";
    ok.disabled = true;
    ok.textContent = tr("Processing…");
    err.textContent = "";
    const startedAt = Date.now() / 1000;
    try {
      const result = await post("/api/confirm", { action: a.id, target: a.target || null, note: note.value }, { refresh: false });
      const alreadyConfirmed = !recordConfirmation(result, a.id, startedAt);
      closeConfirm();
      setTab("flow");
      $("#pane-flow").scrollTop = 0;
      refreshAgentUI();
      toast(alreadyConfirmed ? tr("This direction is already selected") : tr("Confirmation saved"));
      await Promise.allSettled([load(), checkAgent()]);
    } catch (e) {
      err.textContent = e.message;
      ok.dataset.submitting = "false";
      ok.textContent = actionLabel(a);
      ok.disabled = !cb.checked;
    }
  });
  const row = el("div", "b-actions");
  row.append(ok, cancel);
  dlg.append(note, check, row, err, el("div", "note", tr("The record identifies you as “User (Workbench)”. Confirm this step yourself.")));
  $("#confirm").hidden = false;
}

function closeConfirm() {
  $("#confirm").hidden = true;
}
$("#confirm").addEventListener("click", (e) => {
  if (e.target.id === "confirm") closeConfirm();
});

// ---------------- 改结构（全屏编辑页；可编辑项见 ） ----------------
// 基础：长宽高、尺寸类型、材料；高级：数值型的结构尺寸（鼠标移上去，刀版上高亮它会动到的面）。
// 改一下先“试”（服务端生成刀版和折好的样子，不写文件），点保存才写 job.json。页面只用设计师的说法。
const PART_ORDER = [tr("Top"), tr("Bottom"), tr("Body"), tr("Side flaps"), tr("Locks"), tr("Glue flap"), tr("Windows and hang holes"), tr("Corners"), tr("Other")];
const struct = { info: null, key: null, patch: {}, material: null, corners: {}, seq: 0, result: null, error: null,
  trying: false, timer: 0, saving: false, open: false, tab: "basic" };

async function loadStructure() {
  try {
    const res = await fetch(apiUrl("/api/structure"), { cache: "no-store" });
    struct.info = await res.json();
  } catch (e) {
    struct.info = { ok: false, errors: [{ message: tr("Could not load editable parameters: {1}", e.message) }] };
  }
  $("#edit-structure").hidden = !(struct.info && struct.info.ok);
  if (struct.open) renderEditor();
}

function refreshStructure() {
  // job.json 变了（agent 改了，或刚保存）：重读；编辑页开着、没有没保存的改动时把“现在的样子”也重画
  const key = state && state.version;
  if (key === struct.key || struct.saving) return;
  struct.key = key;
  loadStructure().then(() => { if (struct.open && !changed()) tryPatch(); });
}

function setDeep(obj, path, value) {
  const keys = path.split(".");
  let cur = obj;
  for (const k of keys.slice(0, -1)) {
    if (typeof cur[k] !== "object" || cur[k] === null) cur[k] = {};
    cur = cur[k];
  }
  cur[keys[keys.length - 1]] = value;
}

function getDeep(obj, path) {
  let cur = obj;
  for (const k of path.split(".")) {
    if (cur === null || typeof cur !== "object" || !(k in cur)) return undefined;
    cur = cur[k];
  }
  return cur;
}

function unsetDeep(obj, path) {
  const keys = path.split(".");
  const parents = [];
  let cur = obj;
  for (const k of keys.slice(0, -1)) {
    if (typeof cur[k] !== "object" || cur[k] === null) return;
    parents.push([cur, k]);
    cur = cur[k];
  }
  delete cur[keys[keys.length - 1]];
  for (const [parent, k] of parents.reverse()) if (!Object.keys(parent[k]).length) delete parent[k];
}

const changed = () => Object.keys(struct.patch).length > 0 || !!struct.material || Object.keys(struct.corners).length > 0;
const pending = (path) => getDeep(struct.patch, path);
const isEdited = (path) => pending(path) !== undefined;

function current(path, original) {
  const v = pending(path);
  return v === undefined ? original : v;
}

function setValue(path, original, value) {
  // 改回原值就从改动里去掉；null = 删掉这项、回到按规则算
  if (value === original || (value === null && (original === null || original === undefined))) unsetDeep(struct.patch, path);
  else setDeep(struct.patch, path, value);
  scheduleTry();
}

function scheduleTry() {
  clearTimeout(struct.timer);
  struct.timer = setTimeout(tryPatch, 350);
  renderEditor();
}

// 工作台服务重启过（口令换了），这个页面还是重启前打开的：写操作会被拒（403）
const STALE_PAGE = tr("The Workbench service restarted. Refresh (⌘R / Ctrl+R) to continue.");

async function tryPatch() {
  const seq = ++struct.seq;
  struct.errorKind = null;
  struct.trying = true;
  renderEditorStatus();
  try {
    const res = await fetch(apiUrl("/api/structure/try"), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Studio-Token": TOKEN },
      body: JSON.stringify({ patch: struct.patch, material: struct.material, corners: struct.corners }),
    });
    if (seq !== struct.seq) return; // 后面又改过：这次的结果不要了
    if (res.status === 403) {
      struct.error = STALE_PAGE;
    } else {
      const data = await res.json();
      if (data.ok) {
        struct.result = data;
        struct.error = null;
      } else {
        struct.error = (data.errors || []).map((e) => e.message).join(tr("; ")) || data.error || `HTTP ${res.status}`;
        struct.errorKind = "geometry";
      }
    }
  } catch (e) {
    if (seq === struct.seq) struct.error = tr("Could not reach the Workbench service ({1}). Try again shortly.", e.message);
  }
  if (seq === struct.seq) {
    struct.trying = false;
    renderEditor();
  }
}

function resetEdits() {
  struct.patch = {};
  struct.material = null;
  struct.corners = {};
  struct.result = null;
  struct.error = null;
}

function openEditor() {
  struct.open = true;
  $("#struct-editor").hidden = false;
  renderEditor();
  if (!struct.result) tryPatch(); // 先画现在的样子
}

async function closeEditor() {
  if (changed() && !(await ask(tr("Discard unsaved changes?"), tr("Closing will discard the changes in this editor."), tr("Discard changes")))) return;
  struct.open = false;
  resetEdits();
  struct.shown = null;
  if (view3d.v) { view3d.v.destroy(); view3d.v = null; }
  vb.full = [0, 0];
  $("#struct-editor").hidden = true;
}

function ask(title, body, okLabel, list) {
  return new Promise((resolve) => {
    const dlg = $("#confirm .dialog");
    dlg.replaceChildren(el("h3", null, title), el("p", null, body));
    if (list) dlg.append(list);
    const ok = el("button", "btn primary", okLabel);
    const cancel = el("button", "btn", tr("Cancel"));
    const done = (v) => { closeConfirm(); resolve(v); };
    ok.addEventListener("click", () => done(true));
    cancel.addEventListener("click", () => done(false));
    const row = el("div", "b-actions");
    row.append(ok, cancel);
    dlg.append(row);
    $("#confirm").hidden = false;
  });
}

function numberField(labelText, path, original, opts = {}) {
  const box = el("div", `se-field${isEdited(path) ? " edited" : ""}`);
  box.append(el("label", "se-label", labelText));
  const wrap = el("div", "se-input");
  const input = el("input");
  input.type = "number";
  input.step = opts.step || "0.5";
  if (opts.min != null) input.min = String(opts.min);
  if (opts.max != null) input.max = String(opts.max);
  const v = current(path, original);
  input.value = v === null || v === undefined ? (opts.auto ?? "") : String(v);
  input.placeholder = opts.placeholder || "";
  input.addEventListener("change", () => {
    if (input.value === "") setValue(path, original, opts.clearable ? null : original);
    else setValue(path, original, opts.int ? Math.round(Number(input.value)) : Number(input.value));
  });
  wrap.append(input, el("span", "se-unit", opts.unit ?? "mm"));
  box.append(wrap);
  if (opts.hint) box.append(el("div", "se-hint", opts.hint));
  return box;
}

function renderBasic(d) {
  const pane = $("#se-basic");
  pane.replaceChildren();
  const b = d.basics;
  const sec = el("section", "se-sec");
  sec.append(el("h4", null, tr("Dimensions")));
  const grid = el("div", "se-grid");
  for (const [k, name] of [["L", tr("Length")], ["W", tr("Width")], ["H", tr("Height")]]) {
    grid.append(numberField(name, b.paths[k], b.values[k], { min: 1, step: "1" }));
  }
  sec.append(grid);
  const basis = current(b.basis_path, b.basis);
  const seg = el("div", "se-seg");
  for (const [val, text] of [["inner", tr("Inside dimensions")], ["outer", tr("Outside dimensions")]]) {
    const btn = el("button", basis === val ? "on" : "", text);
    btn.addEventListener("click", () => setValue(b.basis_path, b.basis, val));
    seg.append(btn);
  }
  seg.title = basis === "inner" ? tr("Length, width, and height of the usable space inside the box") : tr("Length, width, and height measured on the outside of the box");
  sec.append(el("label", "se-label", tr("Dimension basis")), seg);
  if (b.master) sec.title = tr("These dimensions apply to the part that holds the product. Outer parts follow its length and width. Use Advanced to adjust their heights.");
  pane.append(sec);
  const m = el("section", `se-sec${struct.material ? " edited" : ""}`);
  m.append(el("h4", null, tr("Material")));
  const sel = el("select", "se-sel");
  const now = struct.material || d.material;
  // 纸太厚、盒子太小折不起来的变灰（最短的边至少 min_dim；按现在的长宽高，含没保存的改动）
  const dims = ["L", "W", "H"].map((k) => current(b.paths[k], b.values[k])).filter((v) => typeof v === "number");
  const shortest = dims.length ? Math.min(...dims) : Infinity;
  for (const opt of b.materials) {
    const tooThick = opt.min_dim && shortest < opt.min_dim;
    const o = el("option", null, tr("{1} · {2} mm thick{3}", opt.name, opt.caliper_mm, tooThick ? tr(" (requires a shortest edge of at least {1} mm)", opt.min_dim) : ""));
    o.value = opt.id;
    o.disabled = !!tooThick && opt.id !== now;
    if (opt.id === now) o.selected = true;
    sel.append(o);
  }
  sel.addEventListener("change", () => {
    struct.material = sel.value === d.material ? null : sel.value;
    scheduleTry();
  });
  sel.title = tr("Disabled boards are too thick to fold at these dimensions");
  m.append(sel);
  pane.append(m);
}

function advancedRow(p, simple = false) {
  // simple：常用调整里的写法——一句话说改的是哪儿，不写“自动算 / 默认值”这些
  const v = current(p.path, p.value);
  const auto = v === null || v === undefined;
  const [lo, hi] = p.range || [null, null];
  // 用户：不要这么多小字——字段下面不写说明，悬停时看（一句话说改哪儿、能调多少、两头是否一起变）
  const tip = [];
  if (p.explain) tip.push(p.explain);
  if (auto && p.auto && p.auto_value != null) tip.push(tr("Calculated from dimensions: {1}{2}. Clear the field to use this value.", p.auto_value, p.unit || "mm"));
  if (lo != null && hi != null) tip.push(tr("Range: {1}–{2}{3}", lo, hi, p.unit || "mm"));
  if (p.both_ends) tip.push(tr("Applies to both top and bottom"));
  const field = numberField(p.name, p.path, p.value, {
    auto: p.auto_value ?? "", placeholder: tr("Auto"), min: lo, max: hi, unit: p.unit,
    int: p.type === "int", step: p.type === "int" ? "1" : "0.5", clearable: !!p.auto,
  });
  field.title = tip.join("\n") || p.label || p.name;
  const panels = (p.affects && p.affects.panels) || [];
  field.addEventListener("mouseenter", () => highlight(panels));
  field.addEventListener("mouseleave", () => highlight([]));
  field.addEventListener("focusin", () => highlight(panels));
  field.addEventListener("focusout", () => highlight([]));
  return field;
}

// 按部件（多件套）再按部位排的一组数值
function paramGroups(container, items, simple) {
  const comps = new Map();
  for (const p of items) {
    const c = p.component_name || "";
    if (!comps.has(c)) comps.set(c, []);
    comps.get(c).push(p);
  }
  const multi = comps.size > 1 || !comps.has("");
  for (const [comp, rows] of comps) {
    if (multi) container.append(el("h3", "se-comp", comp || tr("Full assembly")));
    if (simple) {
      const grid = el("div", "se-grid se-grid-1");
      for (const p of rows) grid.append(advancedRow(p, true));
      container.append(grid);
      continue;
    }
    const parts = new Map();
    for (const p of rows) {
      if (!parts.has(p.part)) parts.set(p.part, []);
      parts.get(p.part).push(p);
    }
    for (const [part, list] of [...parts.entries()].sort((a, b) => PART_ORDER.indexOf(a[0]) - PART_ORDER.indexOf(b[0]))) {
      container.append(el("h5", "se-sub", part));
      const grid = el("div", "se-grid");
      for (const p of list) grid.append(advancedRow(p));
      container.append(grid);
    }
  }
}

function renderAdvanced(d) {
  const pane = $("#se-advanced");
  const wasOpen = !!(pane.querySelector("details.se-more") || {}).open;
  pane.replaceChildren();
  // 只放调现有结构尺寸的数值；加部件的（墙顶折边、底板孔……）和做法选项是换结构，不在这里。
  // 用户（2026-11-07）：调节太多、很多看不懂——上面只留几项常用的（每项一句话说改哪儿），其余收进“更多”。
  const list = d.parameters.filter((p) => p.group === "size" && !p.adds_parts);
  const common = list.filter((p) => p.common);
  const rest = list.filter((p) => !p.common);
  const sec = el("section", "se-sec");
  sec.append(el("h4", null, tr("Common adjustments")));
  if (common.length) paramGroups(sec, common, true);
  else sec.append(el("div", "se-hint", tr("Adjust the length, width, and height in Basic.")));
  pane.append(sec, cornerSection(d));
  {
    const more = el("details", "se-sec se-more");
    more.open = wasOpen || rest.some((p) => isEdited(p.path)) || cornerMode(d) === "custom";
    more.append(el("summary", null, tr("More adjustments")));
    if (rest.length) paramGroups(more, rest, false);
    more.append(cornerSizes(d));
    pane.append(more);
  }
}

// 刀模圆角：整个项目一道工艺（卡纸默认外角 2、内角 1，瓦楞不倒）
const CORNER_KINDS = [["outer", tr("Outer corners")], ["inner", tr("Inner corners")]];
const CORNER_MAX = { outer: 20, inner: 5 };

function cornerNow(d) {
  // 现在生效的设置（没保存的改动优先）：{outer, inner}，null = 按材料
  const saved = (d.corners && d.corners.set) || {};
  const pick = (k) => (k in struct.corners ? struct.corners[k] : saved[k] ?? null);
  return { outer: pick("outer"), inner: pick("inner") };
}

function cornerMode(d) {
  const now = cornerNow(d);
  const round = (d.corners && d.corners.round) || [2, 1];
  if (now.outer === null && now.inner === null) return "auto";
  if (now.outer === 0 && now.inner === 0) return "sharp";
  if (now.outer === round[0] && now.inner === round[1]) return "round";
  return "custom";
}

function setCorners(d, outer, inner) {
  const saved = (d.corners && d.corners.set) || {};
  for (const [k, val] of [["outer", outer], ["inner", inner]]) {
    const was = saved[k] === undefined ? null : saved[k];
    if (val === was) delete struct.corners[k];
    else struct.corners[k] = val;
  }
  scheduleTry();
}

function cornerSection(d) {
  const r = struct.result && struct.result.data.corners;
  const def = (r && r.default) || (d.corners && d.corners.default) || [0, 0]; // 跟着没保存的换材料走
  const round = (d.corners && d.corners.round) || [2, 1];
  const sec = el("section", `se-sec${Object.keys(struct.corners).length ? " edited" : ""}`);
  sec.append(el("h4", null, tr("Dieline corner rounding")));
  const mode = cornerMode(d);
  const seg = el("div", "se-seg");
  const autoText = def[0] > 0 || def[1] > 0 ? tr("By material (rounded)") : tr("By material (square)");
  for (const [val, text, o, i] of [["auto", autoText, null, null], ["round", tr("Rounded"), round[0], round[1]], ["sharp", tr("Square"), 0, 0]]) {
    const btn = el("button", mode === val ? "on" : "", text);
    btn.addEventListener("click", () => setCorners(d, o, i));
    seg.append(btn);
  }
  sec.title = tr("Small radii reduce tearing and aid release from the die. Folding carton is rounded by default and corrugated is square.");
  sec.append(seg);
  if (mode === "custom" && r) sec.append(el("div", "se-hint", tr("Custom radii: outer {1} mm, inner {2} mm", r.outer, r.inner)));
  return sec;
}

function cornerSizes(d) {
  // “更多”里的圆角大小：空着 = 按材料，0 = 不倒
  const saved = (d.corners && d.corners.set) || {};
  const r = struct.result && struct.result.data.corners;
  const def = (r && r.default) || (d.corners && d.corners.default) || [0, 0];
  const wrap = el("div");
  wrap.append(el("h5", "se-sub", tr("Corner radii")));
  const grid = el("div", "se-grid");
  CORNER_KINDS.forEach(([k, name], i) => {
    const box = el("div", `se-field${k in struct.corners ? " edited" : ""}`);
    box.append(el("label", "se-label", name));
    const iw = el("div", "se-input");
    const input = el("input");
    input.type = "number";
    input.step = "0.5";
    input.min = "0";
    input.max = String(CORNER_MAX[k]);
    const v = k in struct.corners ? struct.corners[k] : saved[k];
    input.value = v === null || v === undefined ? "" : String(v);
    input.placeholder = def[i] > 0 ? tr("By material: {1}", def[i]) : tr("By material: square");
    input.addEventListener("change", () => {
      let val = input.value === "" ? null : Math.min(CORNER_MAX[k], Math.max(0, Number(input.value)));
      if (val !== null && Number.isNaN(val)) val = null;
      const was = saved[k] === undefined ? null : saved[k];
      if (val === was) delete struct.corners[k];
      else struct.corners[k] = val;
      scheduleTry();
    });
    iw.append(input, el("span", "se-unit", "mm"));
    box.append(iw);
    const cnt = r ? r[`${k}_corners`] : null;
    box.title = tr("{1}. Leave blank to use the material setting or enter 0 for square corners{2}.", k === "outer" ? tr("Sharp corners along the outside contour") : tr("Inside corners on cut lines, including crease endpoints"), cnt != null ? tr(". {1} rounded", cnt) : "");
    grid.append(box);
  });
  wrap.append(grid);
  return wrap;
}

function highlight(ids) {
  const svg = $("#se-hl");
  svg.replaceChildren();
  const r = struct.result;
  if (!r || !ids.length) return;
  applyView();
  for (const id of ids) {
    const pts = r.data.panels_px[id];
    if (!pts) continue;
    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    poly.setAttribute("points", pts.map((q) => q.join(",")).join(" "));
    svg.append(poly);
  }
}

function fmtSize(a) {
  return a ? `${a.map((v) => String(v)).join(" × ")} mm` : "—";
}

function renderInfo() {
  const box = $("#se-info");
  box.replaceChildren();
  const legend = el("div", "se-legend");
  for (const [cls, text] of [["cut", tr("Cut lines")], ["crease", tr("Crease lines")], ["perf", tr("Perforations")]]) {
    const item = el("span", `lg ${cls}`);
    item.append(el("i"), el("span", null, text));
    legend.append(item);
  }
  box.append(legend);
  const r = struct.result;
  if (!r) return;
  const s = r.data.sizes || {};
  const dl = el("dl", "se-sizes");
  const who = s.master ? tr(" ({1})", s.master) : ""; // 多件套：内尺寸、压线尺寸按说了算的那件
  if (s.inner) dl.append(el("dt", null, tr("Inside dimensions{1}", who)), el("dd", null, fmtSize(s.inner)));
  if (s.crease) dl.append(el("dt", null, tr("Crease-to-crease dimensions{1}", who)), el("dd", null, fmtSize(s.crease)));
  if (s.parts) for (const p of s.parts) {
    dl.append(el("dt", null, tr("Blank · {1}{2}", p.name, p.count > 1 ? ` ×${p.count}` : "")), el("dd", null, fmtSize(p.sheet)));
  }
  else dl.append(el("dt", null, tr("Blank size")), el("dd", null, fmtSize(s.sheet)));
  box.append(dl);
}

function renderEditorStatus() {
  const s = $("#se-status");
  s.className = "se-status";
  if (struct.trying) s.textContent = tr("Generating…");
  else if (struct.error) {
    s.classList.add("bad-line");
    s.textContent = struct.errorKind === "geometry"
      ? tr("Could not build these settings: {1}. Showing the last valid preview.", struct.error)
      : struct.error;
  } else if (!struct.result) s.textContent = tr("Drawing the current structure…");
  else {
    const w = (struct.result.warnings || []).map((x) => x.message);
    s.textContent = changed() ? tr("Valid settings. Click Save to apply them to the job.") : tr("Showing the current structure.");
    if (w.length) {
      s.classList.add("warn-line");
      s.textContent += tr(" Note: {1}", w.join(tr("; ")));
    }
  }
  const save = $("#se-save");
  save.disabled = struct.saving || struct.trying || !struct.result || !!struct.error || !changed();
  save.textContent = struct.saving ? tr("Saving…") : tr("Save");
  $("#se-undo").disabled = !changed();
}

function renderEditor() {
  if (!struct.open) return;
  const info = struct.info;
  for (const b of document.querySelectorAll("[data-se-tab]")) b.classList.toggle("on", b.dataset.seTab === struct.tab);
  $("#se-basic").hidden = struct.tab !== "basic";
  $("#se-advanced").hidden = struct.tab !== "advanced";
  if (!info || !info.ok) {
    $("#se-basic").replaceChildren(el("div", "empty-note", info ? (info.errors || []).map((e) => e.message).join(tr("; ")) : tr("Loading box dimensions…")));
    renderEditorStatus();
    return;
  }
  const d = info.data;
  $("#se-maturity").textContent = d.route.startsWith("calibrated")
    ? tr("Checked against a reference dieline")
    : tr("These values are provisional. Confirm a physical sample before ordering.");
  const keep = [$("#se-basic").scrollTop, $("#se-advanced").scrollTop];
  renderBasic(d);
  renderAdvanced(d);
  [$("#se-basic").scrollTop, $("#se-advanced").scrollTop] = keep;
  const r = struct.result;
  if (r && r !== struct.shown) {
    struct.shown = r; // 同一份结果只画一次（3D 每换一版要重建）
    $("#se-dieline").innerHTML = r.data.dieline_svg; // 服务端生成的矢量刀版（只有数字和固定颜色）
    const [pw, ph] = r.data.size_px || [r.data.px, r.data.px];
    setFull(pw, ph);
    renderDims();
    mount3d(r);
  }
  renderInfo();
  renderEditorStatus();
}

// ---- 刀版上的长宽高尺寸线（压线尺寸，服务端按面找好位置） ----
function renderDims() {
  const svg = $("#se-dims");
  svg.replaceChildren();
  const r = struct.result;
  if (!r || !$("#se-show-dims").checked) return;
  const [pw, ph] = r.data.size_px || [r.data.px, r.data.px];
  applyView();
  const k = Math.max(pw, ph) / 520 / zoomLevel(); // 刀版坐标越大、放得越小，箭头和字按比例反算（屏幕上一样大）
  const NS = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
  };
  for (const d of r.data.dims_px || []) {
    const [x1, y1] = d.from, [x2, y2] = d.to;
    const g = mk("g", {});
    const len = Math.hypot(x2 - x1, y2 - y1) || 1;
    const ux = (x2 - x1) / len, uy = (y2 - y1) / len, a = 5 * k;
    g.append(mk("line", { x1, y1, x2, y2, "stroke-width": 1.2 })); // 屏幕上 1.2 像素（non-scaling-stroke）
    for (const [x, y, s] of [[x1, y1, 1], [x2, y2, -1]]) { // 两头的箭头
      g.append(mk("path", { d: `M${x},${y} l${s * a * ux - a * 0.5 * uy},${s * a * uy + a * 0.5 * ux} l${a * uy},${-a * ux} Z` }));
    }
    const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
    const vertical = Math.abs(uy) > Math.abs(ux);
    const text = mk("text", { x: cx, y: cy, "dominant-baseline": "middle", "text-anchor": "middle",
      "font-size": 11 * k, transform: vertical ? `rotate(-90 ${cx} ${cy})` : "" });
    text.textContent = d.label;
    const w = (d.label.length * 6.2 + 8) * k;
    g.append(mk("rect", { x: cx - w / 2, y: cy - 8 * k, width: w, height: 16 * k, rx: 3 * k,
      transform: vertical ? `rotate(-90 ${cx} ${cy})` : "" }), text);
    svg.append(g);
  }
}

// ---- 刀版缩放、拖动（只改显示）：改三张 SVG 的 viewBox（不用 CSS 放大，线宽和字才保持原样、一直是矢量） ----
const vb = { x: 0, y: 0, w: 0, h: 0, full: [0, 0] };
function svgs() {
  return [$("#se-dieline svg"), $("#se-hl"), $("#se-dims")].filter(Boolean);
}
function applyView() {
  for (const sv of svgs()) sv.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
}
function setFull(pw, ph) {
  // 换一版预览：尺寸没变就保持现在的缩放和位置
  if (vb.full[0] === pw && vb.full[1] === ph && vb.w) return applyView();
  Object.assign(vb, { x: 0, y: 0, w: pw, h: ph, full: [pw, ph] });
  applyView();
}
function stageBox() {
  const rc = $("#se-stage").getBoundingClientRect();
  const b = Math.min(rc.width / vb.w, rc.height / vb.h); // viewBox 单位 → 屏幕像素（等比，居中）
  return { rc, b, ox: (rc.width - vb.w * b) / 2, oy: (rc.height - vb.h * b) / 2 };
}
const zoomLevel = () => (vb.full[0] ? vb.full[0] / vb.w : 1);
function zoomAround(cx, cy, k) {
  if (!vb.w) return;
  const { b, ox, oy } = stageBox();
  const ux = vb.x + (cx - ox) / b, uy = vb.y + (cy - oy) / b; // 鼠标下的刀版点
  const z = Math.min(16, Math.max(0.5, zoomLevel() * k));
  vb.w = vb.full[0] / z;
  vb.h = vb.full[1] / z;
  const { b: b2, ox: ox2, oy: oy2 } = stageBox();
  vb.x = ux - (cx - ox2) / b2; // 缩放后那个点还在鼠标下
  vb.y = uy - (cy - oy2) / b2;
  applyView();
  renderDims(); // 尺寸字、箭头按缩放反算，看起来一样大
}
function zoomFit() {
  if (!vb.full[0]) return;
  Object.assign(vb, { x: 0, y: 0, w: vb.full[0], h: vb.full[1] });
  applyView();
  renderDims();
}
{
  const c = $("#se-canvas");
  let drag = null;
  const local = (e) => { const rc = $("#se-stage").getBoundingClientRect(); return [e.clientX - rc.left, e.clientY - rc.top]; };
  c.addEventListener("wheel", (e) => {
    e.preventDefault();
    zoomAround(...local(e), Math.exp(-e.deltaY * 0.0015));
  }, { passive: false });
  c.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".se-zoom")) return;
    drag = { x: e.clientX, y: e.clientY, vx: vb.x, vy: vb.y, b: stageBox().b };
    c.setPointerCapture(e.pointerId);
    c.classList.add("dragging");
  });
  c.addEventListener("pointermove", (e) => {
    if (!drag) return;
    vb.x = drag.vx - (e.clientX - drag.x) / drag.b;
    vb.y = drag.vy - (e.clientY - drag.y) / drag.b;
    applyView();
  });
  const end = () => { drag = null; c.classList.remove("dragging"); };
  c.addEventListener("pointerup", end);
  c.addEventListener("pointercancel", end);
  c.addEventListener("dblclick", (e) => { if (!e.target.closest(".se-zoom")) zoomFit(); });
  const centre = () => { const rc = $("#se-stage").getBoundingClientRect(); return [rc.width / 2, rc.height / 2]; };
  $("#se-zoom-in").addEventListener("click", () => zoomAround(...centre(), 1.3));
  $("#se-zoom-out").addEventListener("click", () => zoomAround(...centre(), 1 / 1.3));
  $("#se-zoom-fit").addEventListener("click", zoomFit);
  $("#se-show-dims").addEventListener("change", renderDims);
}

// ---- 能转、能展开的 3D（和工作台 ⑥ 同一个查看器；每换一版预览重建，保持角度和展开程度） ----
const view3d = { v: null, fold: 1 };
function mount3d(r) {
  const host = $("#se-3d-host");
  const img = $("#se-folded");
  const keepView = view3d.v ? view3d.v.view() : null;
  if (view3d.v) view3d.v.destroy();
  view3d.v = null;
  host.replaceChildren();
  if (!r.data.scene) { // 没有折法的盒型：退回静态图
    img.hidden = !r.data.folded_png;
    if (r.data.folded_png) img.src = `data:image/png;base64,${r.data.folded_png}`;
    host.hidden = true;
    return;
  }
  img.hidden = true;
  host.hidden = false;
  view3d.v = mountViewer(host, { scene: r.data.scene, preferenceKey: "struct-editor", initialFold: view3d.fold,
    initialView: keepView });
  if (view3d.v) {
    view3d.v.resize(1); // 编辑页不缩放：按宿主宽度出清楚的画布
    view3d.v.onFold((t) => { view3d.fold = t; $("#se-fold").value = String(Math.round(t * 1000)); });
  }
}
window.addEventListener("resize", () => { if (view3d.v) view3d.v.resize(1); });
$("#se-fold").addEventListener("input", (e) => {
  view3d.fold = Number(e.target.value) / 1000;
  if (view3d.v) view3d.v.setFold(view3d.fold);
});
$("#se-play").addEventListener("click", () => { if (view3d.v) view3d.v.playFold(); });
$("#se-view-reset").addEventListener("click", () => { if (view3d.v) view3d.v.reset(); });

function changeList() {
  const list = el("ul", "struct-changes");
  const d = struct.info.data;
  const b = d.basics;
  for (const [k, name] of [["L", tr("Length")], ["W", tr("Width")], ["H", tr("Height")]]) {
    if (isEdited(b.paths[k])) list.append(el("li", null, `${name}：${pending(b.paths[k])} mm`));
  }
  if (isEdited(b.basis_path)) list.append(el("li", null, tr("Dimension basis: {1}", pending(b.basis_path) === "inner" ? tr("Inside dimensions") : tr("Outside dimensions"))));
  if (struct.material) {
    const m = b.materials.find((x) => x.id === struct.material);
    list.append(el("li", null, tr("Material: {1}", m ? m.name : struct.material)));
  }
  for (const p of d.parameters) {
    if (!isEdited(p.path)) continue;
    const v = pending(p.path);
    list.append(el("li", null, tr("{1}: {2}", `${p.part} · ${p.name}`, v === null ? tr("Auto") : `${v}${p.unit || ""}`)));
  }
  for (const [k, name] of CORNER_KINDS) {
    if (!(k in struct.corners)) continue;
    const v = struct.corners[k];
    list.append(el("li", null, tr("Corner rounding · {1}: {2}", name, v === null ? tr("By material") : v === 0 ? tr("Square corner") : `${v} mm`)));
  }
  return list;
}

async function saveStructure() {
  const ok = await ask(tr("Save structure changes"), tr("Saving updates the job and regenerates the dieline. Approve the updated proof and regenerate the 3D mockup afterward."), tr("Save"), changeList());
  if (!ok) return;
  struct.saving = true;
  renderEditorStatus();
  try {
    await post("/api/structure/save", { patch: struct.patch, material: struct.material, corners: struct.corners });
    resetEdits();
    toast(tr("Structure saved and dieline regenerated"));
  } catch (e) {
    struct.error = e.message;
    struct.errorKind = e.message === STALE_PAGE ? null : "geometry";
  }
  struct.saving = false;
  await loadStructure();
  if (struct.open) tryPatch();
}

$("#edit-structure").addEventListener("click", openEditor);
$("#se-close").addEventListener("click", closeEditor);
$("#se-save").addEventListener("click", saveStructure);
$("#se-undo").addEventListener("click", () => {
  resetEdits();
  tryPatch();
  renderEditor();
});
for (const b of document.querySelectorAll("[data-se-tab]")) {
  b.addEventListener("click", () => {
    struct.tab = b.dataset.seTab;
    renderEditor();
  });
}

// ---------------- 渲染与实时刷新 ----------------

function render(st) {
  const keepOld = st.errors && st.errors.length && !st.stages.length && state && state.stages.length;
  renderBanner(st);
  if (keepOld) return; // job.json 暂时读不出来（agent 正在写）：画布保持上一版
  state = st;
  loadFolds();
  renderTop(st);
  $("#zoom-sharp").hidden = !st.cloud; // 开源版（确认图是 100 DPI 预览）才有"看清小字"
  liveIds = new Set();
  world.replaceChildren(...framesFor(st.stages)); // 复用的卡片节点只是挪位置，图片不重新加载
  for (const id of [...cardEls.keys()]) if (!liveIds.has(id)) dropCard(id);
  renderOverlays();
  renderMarks();
  renderFlow(st);
  renderIssues(st);
  renderFiles(st);
  renderNotesPane(st);
  refreshStructure();
  if (bubbleId) openBubble(bubbleId, world.querySelector(`[data-note="${bubbleId}"]`));
  place(); // 读 offsetWidth 会同步排版；不用 requestAnimationFrame（窗口在后台时不触发）
}

const MIN_CANVAS_PX = 120; // 面板还没展开（宽高接近 0）时不定视角，等它有了尺寸再定

function place() {
  updateMinimap();
  const sized = canvas.clientWidth > MIN_CANVAS_PX && canvas.clientHeight > MIN_CANVAS_PX;
  if (!placed && sized && state && state.stages.length) {
    placed = true;
    const saved = loadView();
    if (saved) {
      Object.assign(view, saved);
      apply();
    } else {
      // 第一次打开：对准"现在等谁做什么"的那个阶段；没有待办就看全部
      const todo = state.stages.find((s) => s.todo && s.cards.length);
      const f = todo && world.querySelector(`.frame[data-stage="${todo.id}"]`);
      if (f) zoomTo(f);
      else fitAll();
    }
  }
  scheduleThumbs();
}

async function load() {
  const requestedAt = Date.now() / 1000;
  const res = await fetch(apiUrl("/api/state"), { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const st = await res.json();
  reconcileConfirmation(st, requestedAt);
  render(st);
}

function setLive(ok, text) {
  const live = $("#live");
  live.classList.toggle("off", !ok);
  live.textContent = text || (ok ? tr("Live") : tr("Disconnected. Reconnecting…"));
}

function changedLabel(paths) {
  const labels = [];
  for (const p of paths) {
    const hit = PATH_LABELS.find(([prefix]) => p.startsWith(prefix));
    const label = hit ? hit[1] : tr("Other files");
    if (!labels.includes(label)) labels.push(label);
  }
  return labels.length > 3 ? tr("{1} and more", labels.slice(0, 3).join(tr(", "))) : labels.join(tr(", "));
}

function connect() {
  const es = new EventSource("/api/events");
  es.addEventListener("hello", (ev) => {
    setLive(true);
    const v = JSON.parse(ev.data).version;
    if (!state || v !== state.version) load().catch(() => setLive(false));
  });
  es.addEventListener("change", (ev) => {
    const d = JSON.parse(ev.data);
    load().then(() => {
      const now = hhmm(Date.now() / 1000);
      setLive(true, tr("{1} Updated: {2}", now, changedLabel(d.changed)));
      const live = $("#live");
      live.classList.remove("flash");
      void live.offsetWidth;
      live.classList.add("flash");
    }).catch(() => setLive(false));
  });
  es.onerror = () => setLive(false);
}

localizePage();
apply();
load().catch(() => setLive(false));
connect();
checkAgent();
setInterval(checkAgent, 2000);
