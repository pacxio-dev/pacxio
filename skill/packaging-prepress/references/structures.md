# Choosing a structure: standard catalog styles, suggestions from needs, co-design

The geometry engine builds dielines and 3D from recipes: the FEFCO catalog (12th edition) and the public ECMA catalog are both in the structure catalog. The original box style entries (`box-styles.md`) still work; catalog styles go through `job init --fefco / --ecma`, with style `structure` (multi-part sets `structure_set`).

## Three situations

**1. The user gives a code** ("FEFCO 0711", "0427 mailer", "ECMA A20.20.03.01"):

```bash
pacxio structures show 0711 --L 400 --W 300 --H 250 --json      # description, maturity and how it closes first
pacxio job init jobs/<name> --fefco 0711 --L 400 --W 300 --H 250 --basis inner   # material defaults to flute_b
pacxio job init jobs/<name> --ecma A20.20.03.01 --L 80 --W 50 --H 120 --material sbs_350
```

ECMA codes need all four parts (bottom closure.top closure.bottom position.top position).

**2. The user can describe it but has no code** ("slotted case with a crash-lock bottom and a window on the front"): first look for a matching catalog style:

```bash
pacxio structures list --search crash-lock --json
pacxio structures list --series 07 --tag window --json
```

Build by code if one is found; otherwise go to co-design in case 3.

**3. The user does not know which structure**: suggest from the catalog first, co-design only if nothing in the catalog fits (never the other way round).

1. **Ask about the needs (all at once)**: product and size, rough weight; how it is sold / shipped (e-commerce parcel / shipping case / retail carton / display / insert); whether it opens and closes repeatedly; whether tape is allowed; corrugated or folding carton; whether it has several parts (lid and base, sleeve, dividers).
2. **Pick 2–3 options from the catalog, each with 3D**:

   ```bash
   pacxio structures suggest --use ecommerce --reclosable --no-tape --material corrugated \
       --L 240 --W 170 --H 70 --out jobs/_structures --json
   ```

   Uses can repeat: shipping ecommerce display insert carton handle window hang_hole hole multi_part three_piece. Each option has `why` (why it was picked), `structure` (description, maturity, how it closes) and previews (`*_3d.png` folded, `*_dieline.png` the dieline). Show the user the 3D images and reasons and let them choose; after they choose, `job init --fefco …`.
3. **Nothing in the catalog fits** (`suggest` returns nothing, or the user rejects all of them): say which need the catalog cannot meet and settle a new structure with the user — change the shape, the closure, add a mechanism. Write it as a recipe JSON and create the job with `job init --recipe <file>`:

   ```json
   {"body": {"base": "rect", "window": "front"}, "top": {"kind": "slope"}, "bottom": {"kind": "autolock", "autolock_form": "web"}}
   ```

   Sizes go in `--L --W --H` (written into body); multi-part sets are `{"set": "kit", "parts": {...}, "counts": {...}}`, with sizes in each part's body (no `--L --W --H`). For recipe fields, start from the `recipe` returned by `pacxio structures show <similar code> --L … --json` and change it (the safest way). **Mark new structures as "non-standard structure"**, then check practicality:

   ```bash
   pacxio structures check --recipe my.json --L 400 --W 300 --H 250 --material flute_b \
       --need reclosable --need no_tape --load-kg 8 --json
   pacxio structures check jobs/<name> --need reclosable --json     # works on an existing job too
   ```

   The report says how it closes, whether it recloses, whether it needs tape, whether panels pass through each other when folded, and its maturity, checked need by need (unmet needs are `FIT` warnings). Machine runnability and load are not calculated by the engine; say plainly that the factory has to confirm them, and suggest a proof whenever unsure. Catalog styles can be checked the same way.

## Common combinations (boxes outside the catalog, built from added parts)

Boxes that are not in the catalog but that users asked for with a sample or description, built by adding parts, are saved as **common combinations** (series C). `structures suggest` / `list` find them together with catalog styles — the more words from the user's description match (e.g. "tear open", "display lid", "header card", "pull tab", "no glue", "gift box"), the higher they rank:

```bash
pacxio structures list --series C --json                      # what there is
pacxio structures suggest --search "tear open display lid header card" --L 150 --W 150 --H 40 --out jobs/_structures --json
pacxio job init jobs/<name> --combo tear_display_tray --L 150 --W 150 --H 40   # carton combinations default to sbs_350
```

Their maturity is "custom" (proportions read from samples / descriptions); say so when suggesting and delivering, and suggest a proof first.

- **`tear_display_tray` tear-open display tray** (food gift boxes, snack boxes): when closed the display lid sits outside the top panel; on first opening the tear-off window comes away and the display lid stands up as a header card, with a semicircular pull tab at the front (inside the display lid, not sticking out of the box), rounded corners on the tear-off window, no glued sides (side flaps with lock tongues into the bottom), and rounded free corners. The options can be combined separately: see the `recipe` from `structures show tear_display_tray --L … --json` — `display_lid: tab` (two rounded corners + semicircular pull tab, total width including the tab = box width) / `rect`, `tear_window` (the tear-off window; the glue strips at its edge come with it), `dust_lock` (the top panel's side flaps go **outside** the side walls and lock into the bottom; with it the sides are not glued — remove it if the client wants glued sides), `lid_front: none`, `corner_radius` (works on any structure). Panel names: the top panel `lid` hangs on `back`, the display lid `display` on `front` — if the user's "front" is the side the top panel hangs on, lay out on `back` instead; when the display lid stands up it shows the printed area of the tear-off window (the middle of `lid`), so the "opened" artwork goes there.

When the user brings a box that is neither in the catalog nor in the common combinations: find the closest catalog style first; if none is close, confirm the structure with the user from the sample / description, add the missing parts (as options), and save it as a new common combination.

## Mechanism check (S07)

After generating the dieline the engine folds it for real and checks: whether a closed box has gaps, whether flaps or tongues stick out, whether lids or bottoms spring up, whether panels pass through each other, whether slits / slots get something inserted, whether holes are blocked.

- When `structures suggest` has sizes, styles that fail are not suggested (`skipped` gives the reason); without sizes it warns "not checked yet" — **give sizes before suggesting whenever possible**.
- `structures check` returns `usable`; `false` means this style cannot be used at these sizes, and S07 says where it fails.
- In preflight a failure is an **error, and no factory delivery pack is produced**. When it happens: change the sizes, pick another style, or change the recipe — never try to get around it.
- Catalog styles pass the check at common sizes; a few fail under extreme conditions (BC double flute for 0219 / 0871 / 0307 / 0457, very small 0352 / 0730, very large 0483); when that happens change sizes or style as S07 says. Insert trays like 0942 need W clearly larger than 2H, 0205 needs W ≥ ½L; anything that does not fit is refused outright (S03).

The check is geometric only: passing means "it folds together and locks", not how tight inserts are, how firm locks are or how much it carries — those still need a proof.

## Tell the user the maturity honestly

Every style has a maturity (`maturity` from `structures show` / `suggest`):

- **Calibrated** (★, e.g. 0200–0203, 0427, 0711 and a few ECMA tuck / snap-lock / crash-lock styles): checked against reference dielines.
- **Standard**: a recognised FEFCO / ECMA code, but the geometry is read from catalog drawings / public sources, values [initial]; some details in the catalog drawing are not drawn (noted in the description).
- **Custom**: generated by the engine from a recipe, not a standard catalog style.

Non-calibrated structures still produce a delivery pack (the spec sheet and preflight report state the structure's source and maturity and say "proof first"). Tell the user so when suggesting and delivering; never say "ready for production". By rule the spec sheet does not print FEFCO / ECMA codes; give the code to the factory verbally / separately when talking to them.

## Limits (tell the user plainly)

**Convert the size direction first**: L × W is the opening face (the tuck end for tuck cartons, the flap face for slotted cases), H is the tube length / height. A user's "100×70×30 soap box" usually has the tuck on the 70×30 end, so write `--L 70 --W 30 --H 100`, otherwise the lid opens on the large face. Slotted cases need L ≥ W; swap them if reversed. Multi-part sets put sizes in each part of the recipe, without `--L/--W/--H`.

**Can do**: catalog styles at common sizes; most very tall, slender, wider-than-long (non-slotted) boxes; FEFCO styles in E flute / BC flute; regular polygon tubes (3–12 sides, tuck / glued lids for cartons, flaps for corrugated); combinations such as crash-lock top and bottom, corrugated tuck lids, gable handle tops, slope top + lock bottom + window.

**Can do, but confirm with the user and factory** (the engine does not block it; you have to raise it):
- Very large cases may exceed machine sheet sizes (e.g. a 1500×1000×1000 0201 blank is 5 m long); the engine has no sheet-size check — the factory may need to join pieces or change the structure.
- Very thick corrugated (BC double flute) on small boxes: panels too narrow to fold crisp edges.
- Load: not calculated by the engine. A small E-flute box carrying tens of kilograms still shows "needs met"; remind the user to change flute / structure and have the factory calculate it.
- Structures are not tied to material (there is one rectangular tube assembler): the same recipe gives the same structure in carton and corrugated, only thickness-related values differ. FEFCO styles can be made in white board and ECMA styles in E flute, and window / tear strip / joint position with side tucks, reduced flaps and overlap gluing can sit on the same box. `flap`: `full` all four flaps W (0203), `full_long` long flaps W + end flaps ½W (ECMA 10), `overlap` all four the same depth. Defaults by thickness (t < 1 mm counts as carton): tuck friction locks on for carton, off for corrugated (`tuning.friction_lock`); hanger `hanger_layers` single for carton, double for corrugated; crash-lock hooks use their own calibrated proportions for carton / corrugated; carton glue flaps get the carton-style bevelled / rounded ends. Closure flap glue areas `glue` (not given: drawn for glued closures and carton flaps, not for corrugated). Tuck length follows the carton rule, at most 25 mm (for longer tucks on big corrugated boxes give `tuck_len`). Slotted-case minimum size max(20, min(50, 15·t)). On corrugated side-tuck lids hung on the front, the far end of the side_b flap is shortened to clear the glue flap; if too shallow it reports "must clear the glue flap". Unknown `tuning` names are errors. The mechanism check runs as usual after a material change.
- Suggestions ignore proportions: a tall square product may still get a mailer suggested; look at the 3D when choosing.
- Panels passing through each other is only a hint; multi-part 3D shows the parts side by side.

**Cannot do** (the engine refuses with a reason; pass the reason on and change the plan):
- Too small: slotted styles by board thickness max(20, min(50, 15·t)) mm (about 22 for E flute, 45 for B, 50 for BC), cartons with L / H < 20 mm (all [initial], possibly conservative; the factory can confirm).
- Curved creases (pillow boxes), round tubes / paper tubes, pyramids, tapered tubes, double-wall cartons.
- Polygon tubes with crash-lock bottoms or windows.
- Some trays at extreme proportions (very high walls, very small boxes), and some corrugated trays in carton board: reports "cannot be generated at these sizes / this material".

When something cannot be done, look for a similar catalog style first, then discuss changing size / material / structure with the user; never force it.

## Changing structure parameters (editable items)

When the user wants to adjust the structure (longer tuck, shallower dust flaps, another lock, single-layer hanger …), see what can be changed first, then change it:

```bash
pacxio structures editable jobs/<name> [--json]      # or --fefco / --ecma / --recipe + --L --W --H
pacxio structures update jobs/<name> --set top.tuck_len=20       # hand-built styles use the parameter name directly: --set lug_w=24
pacxio structures update jobs/<name> --corner-outer 0 --corner-inner auto   # die corner radii: carton defaults outer 2 / inner 1 mm, corrugated none; 0 = none, auto = by material
```

- Only parameters that **really change the dieline** are listed (the engine tries each one); only listed ones are editable — never offer the user anything unlisted (old compatibility fields have no effect).
- Each item has its current value (unset ones show "auto" and the value worked out by the rules), allowed values, and which panels and lines it affects; tell the user first about "both ends change together" and "switches the generation route" (e.g. leaving the calibrated method for engine generation), since the maturity changes with it.
- "Auto values that follow size changes": they follow when length / width / height change, unless the user fixed them.
- Run the checks as usual after a change (the S07 mechanism check is part of preflight); an existing dieline confirmation expires and the user confirms again.
- Multi-part sets work the same: parameters carry `component_name` (base / lid, tray / sleeve …), so tell the user "the lid's height" and the like; for engine multi-part sets (`--fefco 0310` and similar) length / width / height change the inner part (path `parts.<part>.body.L`), the outer part's length and width follow automatically and its height is changed separately with `parts.<part>.body.H`.
- If the user wants to adjust it themselves: have them click "Edit structure" in the workbench's top bar (a full page: basic length / width / height, inside / outside size, material; advanced fine-tuning of structure dimensions, with the dieline and folded box updating on every change, written to the job only on Save; changes like another flap style need you). After they save, job.json changes; continue as usual (remake the dieline confirmation image and 3D). When talking about parameters use each item's `name` / `option_names` from `structures editable --json` (designer wording), never code names.

## How the workflow differs from regular box styles

- Panel names are the engine's panel ids (listed by `pacxio panels`); multi-part sets use `part.panel` (e.g. `body.panel_2`, `end.base`).
- Multi-part mockups show each part folded **side by side**; assembly is not drawn yet; dividers and flat parts lie flat in 3D.
- All other steps (content list, concepts, flats, final design, dieline confirmation, checks, export) are the same as for regular box styles.
