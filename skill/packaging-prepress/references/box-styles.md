# Box styles and parameters

`pacxio styles list --json` shows every style entry with its parameters, version, accuracy level and direction-verification flag. The 8 original styles are L2; newer entries (seal-end carton, four hanger variants, three slotted cases, cards / header cards, and later additions) are L0 and still need calibration against reference dielines and a folded sample. Say plainly when a structure is not supported yet; never approximate it with a similar style.

| ID | Notes |
|---|---|
| `seal_end` | Carton, both ends with four flaps glued together; end_overlap / end_relief / glue_band control overlap, relief and glue band, all initial values |
| `rte_hanger` / `ste_hanger` / `snaplock_hanger` / `autolock_hanger` | The original bottom, a tuck lid on the front, and an upright hanger panel at the back; hanger_h / hanger_radius; hang_hole=none/round/euro |
| `fefco_0200` | Corrugated half slotted case, no top flaps |
| `fefco_0202` / `fefco_0203` | Corrugated partial / full overlap flaps, flexo by default; 0202 uses flap_overlap, flap_gap fixed at 0 |
| `card` / `header_card` | Carton board; L×W is the whole flat width and height; CLI H=1 is a compatibility placeholder, thickness comes from the material; header_card folds in half by default, folded=false for a single sheet; corner_radius for rounded corners; outline_svg only as one closed shape of lines / arcs, no Béziers / stretched arcs |
| `carton` | Combination carton: one four-panel tube with `--set top=tuck|hanger|seal|gable --set bottom=tuck|tuck_same|snaplock|autolock|seal` in any combination; each end takes the original style's parameters (hanger, snap-lock / crash-lock, end_overlap, roof_deg / handle_h; the gable top's handle hole is handle_hole_w / handle_hole_h). Use it when the user's top / bottom combination has no style of its own. Die-cut options: `--set thumb_notch=true` (tuck top only, `thumb_w`), `--set tear_strip=true` (`tear_y` / `tear_w` / `perf_cut` / `perf_bridge`); windows with `window add` as usual; these are options, not other styles. The whole box is L0 |
| `gable` | Carton gable handle box: four-panel tube + snap-lock bottom + roof panels (roof_deg 30), back-to-back handle panels with a racetrack hole (handle_h, hole_w, hole_h), slotted end flaps (lean_deg 30) locking the handle; H is the body height, the roof and handle add to the total height; handle load not verified, all initial values |
| `label_panel` | Bottle / jar labels (front / back labels, cap labels): `--L` width `--W` height (`--H 1` placeholder), label materials such as `--material label_coated`, digital print by default; shape `--set shape=round_rect|rect|circle|ellipse|custom` (custom takes `outline_svg`, curves converted to arcs automatically); for a cap label write `"container": {"on": "lid"}` in job.json. Front and back labels are one job each. All initial values |
| `label_wrap` | Wrap-around label: `--L` is the **bottle diameter**, `--W` the label height, width = π·L + overlap (`overlap` default 6); the overlap strip at the left end is covered, so no text / barcode there (LB02). All initial values |
| `label_cone` | Conical sector label (cups, tapered jars): `--L` diameter at the label's lower edge, `--set top_d=` diameter at the upper edge, `--W` vertical height; full circle or only part with `arc_deg`. All initial values |
| `pouch_standup` | Stand-up pouch (Doypack): `--L` width, `--W` height, `--H` bottom gusset depth (0 = from width), laminates such as `--material pouch_kraft_pe` / `pouch_pet_vmpet_pe`, digital print by default (`--print-method gravure` for large runs); options `zipper` (not given = none with a spout, otherwise yes), `tear_notch` / `tear_cut=mark\|v\|slit`, `hang_hole=round\|euro`, `corner_radius`, `spout=top\|corner` (spout, `spout_d` diameter), `handle` (handle hole, the zipper moves down automatically), `window=rect\|oval` (clear window, `window_w/h/y`). Front and back laid out side by side; no text or barcodes in seal / zipper / tear notch / spout / handle seal areas (PO01). All initial values |
| `sachet_3side` | Three-side seal sachet: `--L` / `--W`, `--H 0`; tear notch (`tear_cut`), hang hole, rounded corners, clear window; `shape=custom` + `outline_svg` for shaped sachets (sealed along the outline, waste outside, the back mirrored automatically); `supply=roll` roll stock (front and back folded together, the engine places eye marks in the cross seal, the spec sheet gives web width / repeat length / unwind direction). All initial values |
| `pouch_backseal` | Back-seal pillow pouch (crisp bags / stick packs): `--L` front width, `--W` height, `--H` side gusset depth (0 = none); the back is split in two halves either side of the center seam (panels back_a / back_b; artwork across the whole back uses `--space blank` across both halves). Options `seam=corner` quad seal (needs gussets; seam at the front's left corner, the back is one panel `back`), `handle` (needs seam=corner), `window`, `tear_cut`, `supply=roll` roll stock (stick packs, small pillow bags; eye marks in the cross seal next to the center seam). All initial values |
| `pouch_flat_bottom` | Flat-bottom box pouch (coffee beans, tea, nuts, pet food): `--L` front width, `--W` height, `--H` bottom depth (= side gusset width, 0 = 0.4 × front width); panels back_a / side_a / front / side_b / back_b + a separate bottom on the right (10 mm from the body, its four edges sealed to the lower edges of front, right gusset, back and left gusset; the front edge is y = 0). Options `zipper` (on by default), `valve` one-way degassing valve (centered high on the front, position marked), `corner_seals` four vertical corner seals (on by default), `tear_notch` / `tear_cut`, `seam=corner` true quad seal (seam at the front's left corner, the back one panel), `handle` (needs seam=corner), `window`; no hang hole, no rounded corners. No text on the tops of the gussets that tuck into the mouth. All initial values |
| `counter_display` | Counter display box (carton or corrugated): tray with rolled double side walls (ears into the gap, inner wall lugs into the base, no glue) + a dip cut in the middle of the front top edge (`cut_w` / `cut_d`, `cut_d=0` for none) + the back extended upward as a double header (`header_h`, default = W), the header's inner layer running down to the bottom lugs; L / W / H are the tray's inside dimensions, H the wall height; all initial values |
| `crashlock_rsc` | Corrugated crash-lock slotted case: 0201 body / joint / top flaps + a pre-glued crash-lock bottom (forms itself when opened; needs a factory folder-gluer for the side seam and two glue points at the bottom); needs L ≥ W, L/W ≤ 3; major_depth defaults to 0.8×inside width, major_taper_deg 5, web_corner / web_clear from the reference dieline for 0711, all initial values; flexo by default |

Hang holes at least 3 mm from cut / crease lines [initial value]; default round hole 6 mm, euro hole 32×6 mm with a 3 mm center bump; hole load not verified. Folded header cards get mirrored holes in both layers that line up when closed. Hole type, size and material still come from the user's needs. `styles calibrate <ID> --ref <local vector.pdf> --size LxWxH --material ... --out ... --json` only compares locally; it does not download references or change cut lines or verification levels.

When the dieline structure does not match expectations, record style, dieline version, size, parameters and the location of the problem and report it to the developers; never edit cut lines by hand or tweak tuck / dust flap parameters to make it look right. Structural options the user clearly asks for are set from the parameter table; structural needs beyond it are reported first.

## rte — reverse tuck end

The top lid hangs on the top edge of back, the bottom lid on the bottom edge of front; dust flaps come off the top and bottom edges of side_a / side_b. Tuck head (calibrated against a standard reference dieline): the dust flap is straight on the lid side, slanted on the side the tuck goes in, with a friction notch at the root; the tuck is as wide as the lid, with a short crease at each end of its root (working with the notch to hold the tuck; to be confirmed with a folded sample). All four carton styles (rte, ste, snaplock, autolock) share this top. Panels: `glue`, `back`, `side_a`, `front`, `side_b`, `top_lid`, `btm_lid`, `top_tuck`, `btm_tuck`, `dust_ta/tb/ba/bb`.

## ste — straight tuck end

The same as rte, except that both lids hang on back.

## snaplock — snap-lock bottom (1-2-3 bottom)

Body and top as rte; the bottom is four interlocking flaps, **no glue** (version 2, reworked against FEFCO 0215 and the standard snap-lock drawing):
① first fold the main flap `btm_back` with the **notch** on back (the side with the glue flap); ② fold the two side flaps `btm_sa` `btm_sb` in, under it (their slanted edges face the notched flap, with barbs at the ends); ③ finally fold the **V-shaped lock flap** `btm_front` on front and push its tongue under the notched flap. Stronger than a tuck bottom, for medium weights.

| Parameter | Meaning | Default / range |
|---|---|---|
| `major_depth` | Depth of the two main flaps | 0.75×inside width; 0.5–0.95×inside width |
| `notch_w` | Notch width | 0.45×inside length; 0.3–0.7×inside length, ≥ 5 left each side |
| `notch_d` | Notch depth | 0.3×main flap depth |
| `tongue_w` | Tongue width | 0.42×inside length; 0.25–0.6×inside length, and ≥ 1 narrower than the notch |
| `tongue_len` | Tongue length | 0.32×main flap depth |
| `tongue_r` | Tongue corner radius | min(3, tongue length / 3) |
| `minor_depth` | Side flap depth | min(0.5×inside width, 0.5×inside length − 1); 10–0.5×inside length − 1 |
| `hook` | Side flap barb | min(3, 0.06×side panel width) |

`major_taper_deg` and `lock_slits` (version 1 parameters) in old job.json files are still read but have no effect.

## autolock — crash-lock bottom

Body and top as rte; the bottom is **pre-glued by the factory during folding-gluing**, drops into place when the box is opened into a tube, and the two main flaps lock together; no folding, good load (version 2, against FEFCO 0713 and calibrated to a standard reference dieline). **Tell the user: it needs the factory's folder-gluer to glue "the side seam + two places at the bottom"**; say so when asking for quotes.

The two main flaps `btm_back` `btm_front` each have a 45° diagonal crease across the corner at the right end, with a glue triangle `glue_back` `glue_front` outside it; when gluing, the triangle's **inside** is glued to the **outside (printed side)** of the next side flap `btm_sa` `btm_sb`. The glue areas are drawn on the `Glue_Area` information layer of the dieline PDF and the `GLUE` layer of the DXF (on the side flaps); files marked this way have been accepted by a factory as usable. **Whether the glue areas on the side flaps must be ink-free (glue holds poorly on lamination / UV / varnish) is not confirmed**; pass this on when delivering; currently they are not knocked out.

| Parameter | Meaning | Default / range |
|---|---|---|
| `major_depth` | Full depth of the two main flaps | 0.75×inside width; half the side width + 3 to side width − 5 |
| `lock_step` | The vertical part of the step in the middle of the main flap | min(4, 0.32×(main flap depth − half the side width)); 0 to half the slanted shoulder |
| `major_taper_deg` | Outer slant of the main flap | Not given = the outer slanted edge ends 0.2×inside width − 2 from the division at full depth (standard dieline); given: 0–25 degrees, with the left tooth width ≥ 5 |
| `diag_crease` | Diagonal crease type | `crease`; or `reverse_crease` |

Side flap depth and step bottom depth are both half the side width (set by the geometry, not adjustable). Constraints: **L ≥ W** (an error for L < W: either swap L and W — the front becomes the narrow panel — or use a snap-lock bottom), W ≥ 30, L / W ≤ 3. `overlap`, `lock_w`, `diag_deg`, `glue_leg` (version 1 parameters) in old job.json files are still read but have no effect.

**Both lock bottoms**: L, W, H each ≥ 25; the bottom flaps overlap when closed, so **never put key information on the bottom** (the mockup's bottom is made of half of each main flap, side flaps inside; the bottom has no flat of its own, so bottom graphics are hand-written SVGs placed on each flap); the artwork direction on the bottom flaps has been confirmed by a printed sample. Whether locks hold and crash-lock bottoms pop open only shows with a folded sample — the first time a size is used, suggest the user folds one from the 1:1 proof of `export --proof`. Bottom parameters are set like `job init ... --set tongue_w=30`.

## fefco_0427 — mailer box (corrugated)

One corrugated sheet in a cross centered on the base `base`: front `front`, back `back`, two side panels `side_a` `side_b` (double: the outer side plus the inner side `side_a_in/b_in` turned into the box over the roll `rim_a/b`), lid `top_lid` + dust flaps + tuck `top_tuck` (with locking ears `tuck_ear_a/b` at both ends), four ears. No glue, no staples; **locking-ear construction** (style version 0.2.0): the tuck folds down from the lid's front edge and **wraps the outside of the front**, its two ears go round the front corners into the side walls' gap to lock the lid; the inner sides' lugs go into slots on the base's end lines.
- Material: corrugated, usually E / B flute (C / BC get S04: too thick, rolled edges crack). Sizes L, W 60–700, H 20–250.
- **The lid sits between the two rolled edges**: seen from above when closed, the top = lid + a rolled edge top on each side (about 5 mm in E flute). The lid's printable width is 2 × roll narrower than the base; the roll tops show on both sides of the mockup's top, filled with the top's background color.
- **When closed the front is the tuck `top_tuck`** (depth = box height): brand and artwork for the front go on the tuck; the front panel `front` is hidden behind it and, like the inner sides, **only seen with the lid open** (unboxing messages can go there; the mockup gives MK02). The front is one board thickness lower than the back.
- Panel directions: tuck, front and lid read upright from the front (upside down on the flat); side panels have the roll at the top (sideways on the flat); the base faces back.
- Common parameters: `roll_gap` (roll width, default 3t + 0.5), `thumb_notch` (finger notch on the front), `tuck_ear_len` (locking ear length), `ear_depth`, `lug_w`. Set with `job init ... --set thumb_notch=true`.
- Outside size: length minus 2 × roll + 3t, width and height minus 2t.

## fefco_0201 — regular slotted case (corrugated, RSC)

A body wrap + 4 equal flaps top and bottom: the outer flaps `top_front` `top_back` (`btm_front` `btm_back`) meet in the middle when closed, covering the inner flaps; the joint flap `glue` hangs on back's left and is glued inside side_b.
- Material: corrugated, usually B / C / BC (E gets a warning). **L must be ≥ W** (L is the long side of the opening). The blank is large (400 × 300 × 250 is already 1.4 m long); over 2500 × 1800 gets a warning.
- Joint `joint`: glue (default) / stitch / tape (no joint flap); joint flap width defaults to 35 (BC 40).
- Top and bottom are made of half of each outer flap, with a 2 mm gap in the middle: artwork across the gap will be offset.
- **Printing**: slotted cases are **flexo printed**: new jobs default to `print.method = flexo`, with total ink and minimum text size from the profile's flexo rules (initial 240%, 8pt), stricter than offset; for preprinted cases use `job init ... --print-method offset`.

Mailer boxes are **offset litho-laminated** (offset-printed liner laminated onto corrugated board) and follow offset rules.

**Liner**: the corrugated outer liner is kraft or white, which affects color — ask the user which. `job init ... --liner kraft` / `white`; for an existing job `pacxio job liner <job> kraft`; without it, white is shown (the old behavior). With kraft liner, uninked areas on the dieline confirmation image and 3D are kraft-colored and inks sit "on" the kraft (color multiplied by the paper color, only an illustration); concept and flat prompts say "printed directly on kraft, no white ink"; the spec sheet's material line names the liner. White and very light colors cannot be printed on kraft; avoid them in the design (preflight does not check this yet).

**Tell the user for both corrugated styles**: the flute direction is written on the spec sheet and the proof (not on the dieline); all sizes are [initial]; the first time a size is used, suggest cutting a sample from the `export --proof` proof; corrugated boxes cannot have windows in the first version.

## tray_lid — tray and lid (multi-part: base + lid)

Two dielines. Both base and lid are trays (bottom + single walls with ears + double end walls, inner walls turned in, lugs into the bottom, no glue). L × W × H are the **base's inside dimensions**; the lid's net size = base outside size + clearance (default 1 mm). **Full-depth lid** by default (lid height = total closed height), with a semicircular finger notch in the lid's front and back walls; a shallow lid with `--set lid_h=30`. Four small lug slots show on the outside of the lid top; turn them off with `--set lid.lugs=false`. Windows only on the lid top.

## drawer — drawer box (multi-part: tray + sleeve, carton only)

Two dielines. L × W × H are the **tray's inside dimensions** (drawer face width × pull depth × height); the tray's front wall is the drawer face. The sleeve is a tube open at both ends, and **its joint flap glues the side seam** (straight-line folder-gluer or by hand; ask the factory); a semicircular pull notch in the middle of the top's front edge (`--set notch_on=top_bottom` for top and bottom, `pull=none` for none). Windows only on the sleeve's four panels.

**Writing multi-part styles**: wherever a panel is named, use the **full name `part.panel`** — `art add --panel lid.top` (or `--part lid --panel top`), `finish add --panel lid.top`, `window add --panel sleeve.top`; `pacxio panels` lists every full name. A background fill without `--part` fills every part, with it only that part; `--space blank` needs `--part`; `--space body` is not supported in the first version. Part parameters are written `--set lid.lugs=false`, `--set tray.roll_gap=0`.
- The mockup gets an extra "open" view (lid off / drawer half out); content on the base's walls, inner walls and drawer sides is hidden when closed (MK02); lids and trays can go in reversed (MK14) — tell the user when artwork has to line up.
- The delivery pack has no subfolders: one production file and one die DXF per part (file names include the part), one combined spec sheet; in the archive proofs and preflight reports are combined too. If any part has an error, the whole set gets no delivery pack.
- The concept workflow is the same as for cartons: main view sets the direction → back view (always first; flat boxes seen from below) → flats (confirmed by the user) → final artwork from the flats. Panels count **as seen when closed**, with full names. Flat groups (see `concept.md`): for tray and lid, the lid top `lid.top`, the lid's front / back walls stacked upright in one image, right / left walls stacked in one, the base bottom `base.bottom`; for drawers, the sleeve left | top | right in one strip as on the dieline, the sleeve bottom, the tray's face / back wall stacked in one. `concept import --faces lid.front,lid.back`, `concept rectify`, `restore face lid.top` (`top` works too) and `flat` comparison all use these panels. A full-depth lid hides the base's walls when closed, so they get no flat; with a shallow lid lay out the visible strip from the main view. Have the user check every part's dieline confirmation image.
- For parts with several connected panels, like sleeves and lids, backgrounds, gradients and flowing shapes across folds are one image / one vector laid with `--part <part> --space blank` in the part's dieline coordinates (the drawer sleeve's gradient is one image across left | top | right); do not piece together several flats.

## Windows (all carton styles)

A window is an inner cut line on a body panel. **Window patching (film) is a later factory process the engine does not handle**: `--film pet` (default) / `none` only records whether the factory applies film, which affects the mockup and the spec sheet. **Decide before concept images** — after changing a window, the related concepts, flats, dieline confirmation image and 3D need updating, and the dieline image is confirmed again. The cut-out is waste, and the print file prints straight through it without knocking out; the dieline confirmation image (`render`) shows the finished box, with the window drawn as a checkerboard (cut out) and labelled "window" — what the user sees is the result after cutting; frames and background reaching into the window are not shown.

```bash
pacxio window add jobs/<name> --panel front --box 20,50,40,100              # rounded rectangle, corner radius 3 by default
pacxio window add jobs/<name> --panel side_a --box 15,110,20,20 --shape circle --film none
pacxio window list jobs/<name>
pacxio window remove jobs/<name> w2
```

- Positions in panel-local coordinates (same as `art add --box`); single-part cartons only on the four body panels (front / back / side_a / side_b); allowed positions for multi-part styles are above (`lid.top` or the four sleeve panels); corrugated parts do not support windows. Shapes: `rect` rounded rectangle, `circle`, `obround` racetrack.
- Constraints come from the profile's `window` rules (all [initial]): the window ≥ 6 from creases, the outline and **where the glue flap lands** (the strip along the outer edge inside side_b, as wide as the glue flap); corner radius ≥ 1; ≥ 8 between windows. A window that breaks them is not written to the job; W01, W02 and fixes like "move right 3.0 mm" come back.
- Layout: `windows[].keep_out_local_mm` in `pacxio panels` is the area text has to avoid (the window grown by the safe margin); text over a window gives T01, W05. Backgrounds print through windows as usual (the inside is waste), and artwork has to reach into the window at least as far as the bleed (B01 checks window edges).
- Delivery: window cut lines are in the dieline's CUT; files do not mark window film, and the spec sheet says "window patching by the factory per the window positions".
- In the mockup the window shows an illustration of the box interior (not the product), with an MK13 reminder.

## Parameters (shared by rte / ste / snaplock / autolock)

| Parameter | Meaning | Default / range |
|---|---|---|
| `L` `W` `H` | Length (front width), width (side width), height | Required, each ≥ 20 mm (lock bottoms ≥ 25) |
| `dim_basis` | `inner` inside / `outer` outside dimensions | `inner`; with outside dimensions, inside = outside − 2×thickness |
| `glue_w` | Glue flap width | 15, range 10–20, and ≤ inside width − 2; the glue flap is not inset, bevelled 5 mm at the tuck end and arced 20 mm to the crease corner at the lock-bottom end (as on the standard dieline) |
| `tuck_len` | Tuck length (lock bottoms only have one at the top) | min(0.2×inside width + 6, 25, 0.5×inside height); given explicitly 10–25, and < inside height |
| `dust_depth` | Dust flap depth (lock bottoms only at the top) | min(0.4×inside length, inside width); ≤ 0.5×inside length − 1 |
| `friction_lock` | Lock (dust flap notch + hook cuts at the tuck root) | On |
| `tuck_slit` | Tuck root: `cut` hook cuts at both ends (one board thickness from the crease, 8 long, turning onto the crease at the end, leaving the small catch on the lid) / `crease` a full crease + two short creases above (the older method) | `cut` |
| `apply_compensation` | Thickness compensation | On (factors from the profile) |

`friction_lock_depth`, `friction_lock_pos`, `dust_taper_deg` (version 1 tuck head parameters) and `glue_inset`, `glue_bevel_deg` (version 1 glue flap parameters) in old job.json files are still read but have no effect.

When constraints are not met the command reports S03 with a specific fix. The values above have been verified with printed samples and confirmed by a factory.

## Material presets

Carton board (rte, ste, snaplock, autolock):

| preset | Description | Thickness (mm, initial) |
|---|---|---|
| `sbs_300` | SBS 300 g | 0.42 |
| `sbs_350` | SBS 350 g | 0.48 |
| `sbs_400` | SBS 400 g | 0.55 |
| `gd_350` | Grey-back duplex 350 g | 0.48 |
| `kraft_350` | Kraft board 350 g | 0.50 |

Corrugated (fefco_0427, fefco_0201):

| preset | Description | Thickness (mm, initial) |
|---|---|---|
| `flute_e` | E flute | 1.5 |
| `flute_b` | B flute | 3.0 |
| `flute_c` | C flute | 4.0 |
| `flute_bc` | BC double flute | 6.5 |

Carton styles do not accept corrugated and corrugated styles do not accept carton board (both report S03 with a fix). Thickness has to come from measuring the factory's stock; it affects the blank size (compensation = factor × thickness).

## Panel-local coordinates

`--box x,y,w,h` in `art add` / `finish add` is in panel-local coordinates: seen from **outside** the box with the panel upright, origin at the bottom left, in mm. `pacxio panels` gives each panel's size and safe area (`safe_box_local_mm`). The top lid's "upright" follows the opening direction: upright when read from the front (so upside down on the flat; `up_in_blank` in `panels` is (0, -1)); bottom lids, tucks and lock-bottom flaps follow the flat's +y. The directions of these 8 styles have been confirmed by printed samples; new styles are marked `orientation_provisional` in `panels` until confirmed.


## Regular boxes, first batch (L0)

All are engineering drafts waiting for calibration / folded samples; never claim they were checked against a specific standard or a real box. Not for rigid greyboard construction.

| ID | Use and limits |
|---|---|
| `tray_glued` | Carton tray, single walls, glued at four corner flaps / glue areas; L/W ≥ 40, H ≥ 15; artwork direction pending a folded sample |
| `tray_lock` | Carton tray with folded-back lock tongues at the four corners + receiving slots; slot positions and thickness are aligned in the parameter matrix, real insertion and load not verified |
| `hinged_lid` | Carton tray with an attached lid, front tuck, side flaps that close with the lid; not the same as a rigid hinged-lid box |
| `sleeve` / `belly_band` | The same tube mechanism; L = inside width of the sleeve, W = length along the tube axis / band width, H = inside height. Net sizes, no product fit clearance added automatically; optional `pull=notch` |
| `insert_card` | Platform L×W, leg height H; inner only, all crease dimensions, no body compensation. Holes default to none; round needs hole_w, rect also hole_h; rows/cols, pitch_x/y, margin adjustable. Leg height at most half the short side |
| `divider` | Carton / corrugated; inner only, L/W are the assembly spans, H the strip height, no body compensation. rows/cols 2–8, counts x = rows−1, y = cols−1; two dielines, produced by quantity, not one each per set; full panel names `x.front` / `y.front`. Slot width = thickness + slot_clearance (default 0.3) |

These structures support init / render / mockup / real folding / export and preflight. Divider 3D lays out the real number of pieces, stands them up and slots them together; the spec sheet lists quantities per set. Static images simplify hidden details like corner flaps / lock tongues; check with the real folding and a folded sample. Richer back-view / flat prompts and cross-part content planning work as currently supported; never describe these new dieline entries as having completed the design workflow for every category.


## 0427 helpers — reference proportions and the open view

`pacxio styles presets --style fefco_0427 --json` shows three reference inside sizes: `dtc_mailer_slim` (220×160×35 E flute), `dtc_mailer_standard` (260×180×70 E flute), `dtc_mailer_deep` (300×220×110 B flute). Use / proportion presets are still the same 0427; they add no style and no separate size verification level.

`pacxio job init-preset <new folder> dtc_mailer_standard --set H=60 --liner white --json` creates a regular job; `--material` overrides the material and `--set` the style's parameters. Presets are only for new jobs or empty folders; existing jobs are changed through the continuing workflow, never re-initialized; the resolved parameters are saved, so later preset changes do not touch existing jobs.

Mailer `mockup` also writes `view_open.png` into the static summary, using the same real folding as the interactive 3D. It is single-sided printing: the inside of the lid and the board's back show the material color; never paste a display image onto the back of the lid and claim inside printing is supported; double-sided inside printing / export is not implemented. Normal artwork / dieline confirmation is still done by the user; synthetic validation samples are not production confirmations.

## mailer_tuck — mailer with an inside tuck (L0)

A structure of its own, not a 0427 size preset. Double-wall tray with the same ears / lugs / base slots; a short rounded tuck at the lid front goes inside the front wall, without outside locking ears; 17 panels, front artwork on `front`. E / B flute; all mechanism parameters are engineering initial values pending a folded sample, and it does not inherit the old 0427's L2.

`job init <new folder> --style mailer_tuck --L 260 --W 180 --H 70 --material flute_e --set tuck_len=18 --set tuck_radius=1`. Editable: `tuck_len / tuck_side_inset / tuck_radius / front_clearance / lid_side_clearance / dust_depth / dust_taper_deg / dust_radius` and the body's roll / lug / slot parameters; the new schema refuses the old `tuck_ear_len / lid_flap_inset` and non-finite values. Ranges, defaults and constraints come from `structures editable`. Single-sided printing; closed / open static images come from the real hinges; double-sided inside printing is not implemented.

## mailer_front_lock / mailer_tear_seal (L0)

- `mailer_front_lock`: double-wall rolled tray + an outer front apron + two shouldered lock tabs + receiving slots in the front. `apron_depth / lock_neck_w / lock_tab_len / lock_neck_len / lock_hook / lock_spacing / lock_clearance / lock_bevel / front_clearance` are linked. Slot height / width / height come from thickness and tab neck; the receiving slots cannot be moved independently. Rigid folding does not mean elastic insertion or holding force has been verified.
- `mailer_tear_seal`: top connecting strip → tear strip → seal pad, with a pull tab and explicit PERF cut segments / bridges. `header_depth / tear_width / seal_depth / adhesive_width / adhesive_margin / adhesive_end_inset / pull_tab / perf_cut / perf_bridge` are linked. The adhesive is on the back of `seal_pad`, landing in a no-ink area on `front`'s printed side; Glue_Area / GLUE is the projection of a process on the board's back, not a cut line, and not crash-lock gluing. One adhesive seal; no second strip for returns, and the folding animation without tearing is not a tear simulation.

Both for E / B flute; values are engineering initial values pending a real sample / factory confirmation. Create standalone jobs with `job init <new folder> --style <id> --L ... --W ... --H ... --material flute_e --set ...`.

**Label containers**: the mockup's bottle / jar is inferred from the label parameters; to match the real container write `container` in job.json (shape cylinder / jar / taper / square, diameter, height, shoulder, neck_d / neck_h, cap_d / cap_h, bottom_r, finish such as glass_amber, label_z the label's lower edge above the bottom, angle, on = body / lid). It only affects the mockup and the LB01 / LB02 checks, not the confirmation fingerprint. Clear / metallised materials need white ink (`finish add --type white`), otherwise LB06 warns. Roll label parameters (unwind direction, core, gap) are initial values; the spec sheet says "please confirm with the label printer" — never say they are confirmed.

**Pouches**: the mockup shows the pouch filled and puffed (plus one lying flat); set the fill in job.json with `"pouch": {"fill": 0.4}` (0 flat – 1 very full, default 0.7 initial); it only affects the mockup and PO05, not the confirmation fingerprint. PO05 warns about small text or barcodes where the film deforms most when filled (seal corners, gusset folds). Seal, zipper, tear notch and gusset positions are initial values from a reference source; the spec sheet says "please confirm with the printer / pouch maker" — never say they are confirmed. Clear / metallised films need white ink, otherwise PO04 warns. Clear windows (`window`) only mark a position, and text can be printed on them; no white ink or background in the window, and windows on non-clear film get PO08; in the mockup unprinted parts of the window show the contents (`"pouch": {"contents": "#8a5a3c"}` changes the color, display only). For roll stock (`supply=roll`) the engine places eye marks in the print file; keep the eye-mark lane (a full strip along the web direction) light, otherwise PO07 warns. More materials: `pouch_pet_al_rcpp` (retort), `pouch_ny_pe_clear` (frozen / vacuum), `pouch_mono_pe_white` (mono-material, recyclable), `pouch_kraft_pla` (compostable).
