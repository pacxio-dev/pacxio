---
name: packaging-prepress
description: "Packaging design to factory-ready print files. First concept images so the decision maker picks a direction, then the final design, confirmed on the dieline confirmation image (3D mockups show the finished look), then a production pack the factory accepts: dieline DXF/CF2/PDF, CMYK print PDF, foil / spot UV plates, spec sheet, preflight report. Use when the user wants packaging design, concept images, mockups, dielines or print files for folding cartons (reverse tuck end, straight tuck end, snap-lock or auto-lock bottom), mailer boxes, corrugated shipping boxes, tray-and-lid or drawer boxes; wants foil, spot UV or embossing; wants to check files for a printer; or wants to view stages on the local workbench and fold the 3D view along the dieline. Also bottle and jar labels and flexible pouches (stand-up, three-side seal, pillow, flat-bottom). Not for: imposition, rigid set-up boxes, shrink sleeves, or structures outside the FEFCO / ECMA catalog that the engine cannot build (see references/structures.md)."
---

# Packaging prepress (packaging-prepress)

The whole workflow runs through the `pacxio` command line:

> Brief → content list → (lots of copy: layout draft) → **concept images**: main view (pick a direction) → back view → flats → final design rebuilt from the flats → **dieline confirmation image (confirm)** → 3D mockups (view only) → preflight → delivery pack

The user confirms: the content list wording, the layout draft, the concept direction, the flats, and the dieline confirmation image. The first four are records; **`approve` on the dieline confirmation image is the only gate before export**. Concept images confirm the **direction and feel** (AI-drawn: proportions and text are not guaranteed, the images never go into print files; graphics on the flats are traced to vectors for the final artwork). The dieline confirmation image confirms **exactly what will be printed** (it is the same data as the print files). 3D mockups are for viewing, not confirming.

Dielines, 3D views and the structure library run on this machine. Composing artwork, separations, preflight and the delivery pack run on the pacxio server. AI images (concepts and assets) cost the user image credits.

## Hard rules (read first)

1. **Errors mean no factory files.** When `preflight` / `export` report errors, tell the user as they are and fix each one by its `fix` until it passes; never say "the files can go to the factory" while there are errors.
2. **All text in print files is set by the engine and outlined.** Only concept images may contain AI-drawn text; the engine forces "no text" on print asset prompts, and you must not ask for text in them either. Every piece of text in the final files is placed with `art add --kind text`.
3. **Concept images are never assets.** Main views, back views and flats must not be cropped, cut out or enlarged into the layout (the engine refuses, and recognises identical pixels). The final design is **rebuilt from the confirmed flats** (from the main view when there are no flats).
4. **Only the user confirms; never confirm for them.** Run `content confirm`, `plan --confirm`, `concept choose`, `concept confirm` (this set of flats) and `approve` only after the user **clearly says** they confirm / which one they choose. If the design changes after confirmation, `render` again and ask the user to confirm again. The user can also click confirm on the workbench themselves; the agent never clicks it for them and never calls the workbench's confirm endpoint.
5. **Say plainly what is not verified.** Profile `verified=false`, lid / tuck direction not confirmed (PROFILE / ORIENT / MK03), CF2 not checked by a die maker, no standard ICC found — pass these on as they are, without softening (the `default` profile and the directions of the original eight box styles are confirmed; newer structures are not physically checked yet). ICC profiles are picked by paper automatically; do not ask the user.
6. **Explain the gap between the concept and the product.** Tell the user when they pick a direction what the engine cannot reproduce (gradients / transparency / shadows inside vectors, special fonts without a font file, AI letterforms and proportions; see `references/design.md`).
7. **A physical proof comes before a production run.** Remind the user at every delivery.
8. **Never install anything at run time** (no `pip install` / `uv add`). If something is missing, stop and give the user the install instructions.
9. **Do not guess key parameters**: size basis (inside / outside), material, text content, finishes. Ask for everything missing at once. Which information **must appear** depends on the category and market regulations; the engine cannot judge it, so ask the user, who is responsible for it.
10. **The user confirms font licenses.** When `font add` refuses, do not add `--accept-license-risk` yourself; add it only after the user clearly says they have checked (`references/fonts.md`).
11. **Report structural problems first.** If the dieline does not match expectations, record the style, dieline version, size and where it goes wrong and report it to the developers; do not hand-edit cut lines, switch to a similar style, tweak parameters to make it look right, or change the size and structure the user decided (normal structure options are in `references/box-styles.md`).
12. **Use the user's copy exactly as given.** Only check spelling, grammar, punctuation and capitalisation, and list them as "original → suggestion" for the user to decide; do not rewrite or polish, do not judge whether claims are supported or compliant, and do not delete, change or mark anything pending because of that (pending is only for information the user has not given; `references/content.md`).

## How to start, how to continue

1. **Check the environment**: `python3 <skill-dir>/scripts/check_env.py --json` (`<skill-dir>` is this Skill's real folder). If the engine or fonts are missing, **stop** and give the user the instructions; if only Ghostscript is missing you can go on, but separation checks (B02, F01) are skipped — tell the user.
2. **New project**: ask for the whole brief at once, create the job, open the workbench right away, build the content list — `references/requirements.md`.
3. **Continuing** (the user gives an existing job, or says "continue / change this"): `references/resume.md`. Confirmations that still hold carry over; only ask about what is missing or what this change made expire.
4. **Run `pacxio next <job>` before every round of work**: it shares its source with the workbench's "Now" and says where the job stands, who it waits on, which command runs next and which reference to read; user notes come first (`notes list --open` first, `notes reply` when done). In stages that wait on the user it only tells you what to show and what to run after they confirm.
5. **The user wants to see their own dieline folded** (they give a DXF / vector PDF): not supported at the moment; tell the user so.
6. **Images**: use the agent's built-in image tool if it has one (Codex); otherwise a configured API, or ask the user for images. Prompts come from `pacxio concept prompt`; tell the user how many images you will make before you make them.
7. **Cloud sign-in**: composing, rendering, preflight and export run on the pacxio server, and the first time they return `CLOUD_LOGIN` (an address and an 8-character code). Give the user the address and code exactly; **the user** signs in in the browser with an email code, checks the code and clicks Approve (the agent never signs in or approves for them, and never uses a browser tool to click it). When they say it is done, rerun the same command (`references/troubleshooting.md`).

## Stage map

| Stage | Key commands | What the user does | Read |
|---|---|---|---|
| Brief, structure, job, workbench | `structures suggest / show`, `job init` (`--fefco` / `--ecma` / `--recipe`), `panels`, `studio start`, `window add` | Answer the brief, pick a structure | `requirements.md`, `structures.md`, `box-styles.md`, `studio.md` |
| Content list (lots of copy: layout draft first, route A) | `content import / list / confirm`; `style set`, `art add`, `plan` | Confirm the wording (and the draft) | `content.md`, `layout-draft.md` |
| Main-view concepts: one image for each of 2 directions | `concept prompt --step main` / `concept gen`, `concept import` | **Pick a direction** (`concept choose --region …`) | `concept.md` |
| Back view (always first) → flats | `plan`, `concept prompt --step back / flat`, `concept import --view … --faces …` (several flat candidates can be imported at once; only the most accurate is kept), `concept rectify` | **Confirm the set of flats** (ask only when every proportion is within 10%; then `concept confirm --by`) | `concept.md` |
| Final design from the flats | `art add / set / move`, `restore strip / face`, `visual trace / gen / import / prepare`, `finish add`, `surface add`, `color add`, `font add` | — | `design.md`, `vector.md`, `fonts.md`, `finishes.md`, `visual-prompts.md` |
| Dieline confirmation image | `render`, `flat --diff`, `approve` | **Confirm (the only gate before export)** | `mockup.md` |
| 3D mockups | `mockup` | View only | `mockup.md`, `finishes.md` |
| Preflight | `preflight --json` | — | `prepress-rules.md`, `troubleshooting.md` |
| Export | `export --proof --require-approval` | — | `delivery.md` |

Multi-part styles `tray_lid`, `drawer`: same workflow (back view and flats too); panels use full names `part.panel`, flats cover the panels visible when closed (`lid.top` …), and each part gets its own dieline confirmation image (`box-styles.md`).

Every command takes `--json` and prints `{ok, command, job, artifacts, data, errors, warnings}`; exit codes 0 success, 1 errors, 2 bad arguments, 3 environment missing. All commands: `references/commands.md`.

## References

| File | When to read |
|---|---|
| `references/requirements.md` | New project: the brief, creating the job, opening the workbench, the content list, choosing route A / B |
| `references/resume.md` | Continuing an existing project |
| `references/content.md` | Content list: organising, importing, the user confirming wording, using items in the layout |
| `references/layout-draft.md` | Layout draft when there is a lot of copy (route A); the layout after the flats are confirmed |
| `references/concept.md` | Main view, back view, flats: prompts, import, choosing, proportion checks, straightening, comparison |
| `references/design.md` | Final design from the flats: color, text, graphics, photos, finishes, coordinates, stacking, what the engine cannot reproduce |
| `references/vector.md` | Tracing vectors from the flats, hand-written SVG, clip shapes, continuity across folds |
| `references/fonts.md` | Built-in fonts, tracking and line spacing, hanging indents, brand fonts and licenses |
| `references/visual-prompts.md` | Image prompts for print assets, sizes, product photos on transparent backgrounds |
| `references/finishes.md` | Foil / spot UV / embossing, lamination and varnish, checking finishes in 3D |
| `references/mockup.md` | Dieline confirmation image, 3D mockups, confirmation and required content |
| `references/studio.md` | Local workbench, notes, confirmations the user clicks on the page |
| `references/structures.md` | Choosing a structure: by code, suggestions from the catalog (with 3D previews), co-designing a new one, how to state maturity |
| `references/box-styles.md` | Box styles, parameters, windows, material presets, panel-local coordinates |
| `references/factory-profile.md` | Factory profiles, ICC |
| `references/prepress-rules.md` | Preflight rules in plain words |
| `references/troubleshooting.md` | Common errors and fixes, exit codes |
| `references/delivery.md` | Preflight, export, what is in the delivery pack, what to tell the user |
| `references/commands.md` | Command reference |
