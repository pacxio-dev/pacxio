import { apiUrl, tr } from "./i18n.js";

const node = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const stages = [
  { id: "content", title: "Content list", outcome: "The words and brand assets that must appear on your box.", review: "Check the product name, size, required wording and logo before confirming.", type: "content" },
  { id: "plan", title: "Layout draft", outcome: "A simple layout showing where the logo and copy will go.", review: "Check what belongs on each face. This step can be skipped for simple packaging.", image: "plan" },
  { id: "concept", title: "Concepts", outcome: "A visual direction for the assembled package, before production artwork.", review: "Choose a direction, or tell your agent what to change. A concept image is not a print file.", image: "concept" },
  { id: "flat_concept", title: "Flat layout", outcome: "The chosen direction shown flat so you can inspect each face.", review: "Check the text, artwork placement and continuity between faces before confirming.", image: "flat" },
  { id: "design", title: "Dieline proof", outcome: "The artwork on the actual dieline, ready for a detailed review.", review: "Check every face, text, dimensions and finishes. Confirm your own proof before delivery.", image: "design" },
  { id: "mockup", title: "3D mockup", outcome: "A view of how the artwork fits the assembled box.", review: "Inspect the front, back and folds. Your project's 3D view can be rotated; this example is a still image.", image: "mockup" },
  { id: "delivery", title: "Preflight and delivery", outcome: "Checks and production files for discussing the job with your printer.", review: "Resolve blocking checks, then review the delivery pack with your printer before production.", type: "delivery" },
];
let dialog;
let body;
let title;
let rememberOnClose = false;
let dismissed = false;
let opened = false;
let opener;

function button(label, action, cls = "btn") {
  const b = node("button", cls, label);
  b.type = "button";
  b.addEventListener("click", (ev) => { ev.stopPropagation(); action(); });
  return b;
}

function shell(heading) {
  if (!dialog) return false;
  if (!dialog.open) {
    opener = document.activeElement;
    dialog.showModal();
  }
  opened = true;
  title.textContent = heading;
  body.replaceChildren();
  return true;
}

function closeGuide() {
  dialog.close();
}

async function remember() {
  if (!rememberOnClose || dismissed) return;
  dismissed = true;
  try {
    const res = await fetch(apiUrl("/api/onboarding"), {
      method: "POST", headers: { "Content-Type": "application/json", "X-Studio-Token": document.querySelector('meta[name="studio-token"]').content },
      body: JSON.stringify({ seen: true }),
    });
    if (!res.ok) throw new Error("save failed");
  } catch (_) {
    dismissed = false;
    // A preference failure must never interrupt the user's project.
  }
}

function showWelcome() {
  if (!shell(tr("Your first packaging project"))) return;
  rememberOnClose = true;
  const intro = node("div", "guide-welcome");
  intro.append(node("p", "guide-lead", tr("Describe it in chat. Review it here.")));
  const steps = node("ol", "guide-steps");
  for (const [heading, text] of [
    ["Tell your agent", "Describe your packaging and request changes in the agent conversation."],
    ["Review in the Workbench", "See each result here, choose a concept and confirm when you are ready."],
    ["Keep the conversation open", "When your agent is connected and waiting, your confirmation lets it continue. Check the connection status beside the next step."],
  ]) {
    const li = node("li");
    li.append(node("strong", "", tr(heading)), node("p", "", tr(text)));
    steps.append(li);
  }
  intro.append(steps);
  const actions = node("div", "guide-actions");
  const start = button(tr("Start my project"), closeGuide, "btn primary");
  actions.append(start, button(tr("See stage examples"), () => showExample("concept")));
  intro.append(actions);
  body.append(intro);
  start.focus();
}

function sampleRows(rows) {
  const list = node("dl", "guide-sample-list");
  for (const [label, text] of rows) {
    const row = node("div");
    row.append(node("dt", "", tr(label)), node("dd", "", tr(text)));
    list.append(row);
  }
  return list;
}

function showExample(id) {
  const index = stages.findIndex((s) => s.id === id);
  if (index < 0 || !shell(tr("Stage examples"))) return;
  const stage = stages[index];
  const wrap = node("div", "guide-example");
  const note = node("div", "guide-example-label");
  note.append(node("strong", "", "pacxio Dieline Tee"), node("span", "", tr("Read-only example · separate from your project")));
  const nav = node("nav", "guide-stage-nav");
  nav.setAttribute("aria-label", tr("Example stages"));
  for (const [i, s] of stages.entries()) {
    const b = button(`${String(i + 1).padStart(2, "0")} ${tr(s.title)}`, () => showExample(s.id), "guide-step");
    if (i === index) b.setAttribute("aria-current", "step");
    nav.append(b);
  }
  const content = node("section", "guide-stage-content");
  content.append(node("h3", "", `${String(index + 1).padStart(2, "0")} · ${tr(stage.title)}`), node("p", "guide-outcome", tr(stage.outcome)));
  const sample = node("div", "guide-sample");
  if (stage.image) {
    const img = node("img");
    img.alt = `pacxio Dieline Tee · ${tr(stage.title)} · ${tr("Example")}`;
    img.width = 960; img.height = stage.image === "flat" ? 960 : 640;
    img.decoding = "async";
    img.src = `/static/guide-tee-${stage.image}.webp`;
    img.addEventListener("error", () => { sample.replaceChildren(node("p", "guide-muted", tr("Preview unavailable. You can still read the stage guide below."))); });
    sample.append(img);
  } else if (stage.type === "content") {
    sample.append(sampleRows([["Brand", "pacxio"], ["Product", "Dieline Tee"], ["Size", "Size L"], ["Required wording", "Fictional product made to demonstrate pacxio. Not for sale."]]));
  } else {
    sample.append(node("p", "guide-muted", tr("Illustrative file list · no production files are included in this example")), sampleRows([
      ["Print PDF", "The artwork prepared for print"], ["Dieline · DXF / CF2", "Cutting and creasing geometry"],
      ["Finish plates", "Separate artwork for finishes, when specified"], ["Preflight report", "Checks and issues to review"],
      ["Specification sheet", "Dimensions, materials and production notes"],
    ]));
  }
  const review = node("div", "guide-review");
  review.append(node("strong", "", tr("What you do here")), node("p", "", tr(stage.review)));
  content.append(sample, review);
  const layout = node("div", "guide-example-layout");
  layout.append(nav, content);
  const actions = node("div", "guide-actions");
  actions.append(button(tr("Back to my project"), closeGuide, "btn primary"));
  if (index < stages.length - 1) actions.append(button(tr("Next example"), () => showExample(stages[index + 1].id)));
  wrap.append(note, layout, actions);
  body.append(wrap);
  // Keep keyboard focus inside the rebuilt dialog, rather than on a removed tab.
  nav.querySelector('[aria-current="step"]').focus({ preventScroll: true });
}

export async function initGuide() {
  dialog = node("dialog", "guide-dialog");
  dialog.id = "studio-guide";
  dialog.setAttribute("aria-labelledby", "guide-title");
  const header = node("header", "guide-head");
  title = node("h2", "", tr("Your first packaging project")); title.id = "guide-title";
  header.append(title, button(tr("Close"), closeGuide));
  body = node("div", "guide-body");
  dialog.append(header, body); document.body.append(dialog);
  dialog.addEventListener("close", () => { remember(); if (opener?.isConnected) opener.focus({ preventScroll: true }); });
  try {
    const res = await fetch(apiUrl("/api/onboarding"), { cache: "no-store" });
    if (res.ok && !(await res.json()).seen && !opened) showWelcome();
  } catch (_) { /* A failed preference read must never block the Workbench. */ }
}
