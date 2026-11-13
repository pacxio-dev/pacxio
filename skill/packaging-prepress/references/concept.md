# Concept images: set the direction first

## What they are

A concept image is **what the finished pack should end up looking like**: an AI-drawn image, with text, for the decision maker to confirm the design. There are three kinds in the workflow, all in `concepts/`:

| Step | Image | Used to | Imported with |
|---|---|---|---|
| 1 Main view | 3D finished pack showing front + right side + top (2 directions each time) | Choose a direction | `--view main` |
| 2 Back view | After choosing, a 3D image of back + left side | Confirm the look of the other panels | `--view back` |
| 3 Flats | Straight-on flat artwork: front + right, back + left, top, bottom | Set the exact layout — once the user confirms them **they are the reference** | `--view flat --faces …` (left to right) |

`--view` / `--faces` are stored in the concept's meta: `concept list` shows them; `concept choose` only accepts main views (untagged old images count as main views); `flat` uses them to compare panel by panel, and the mockup summary sheet uses them to find the back view. Tagging old images:

```bash
pacxio concept tag jobs/x c13-flat-front --view flat --faces front,side_b
pacxio concept tag jobs/x c13-back1 --view back --faces back,side_a,top_lid
```

Panel ids come from the dieline: `front`, `back`, `side_a` (left side), `side_b` (right side), `top_lid`; the bottom depends on the style: `btm_lid` for reverse / straight tuck end, `btm_front` for snap-lock, `base` for mailers; `top` / `bottom` / `left` / `right` work as shorthands and are resolved by style. Crash-lock bottoms and slotted-case bottoms (and slotted-case tops) are made of two flaps half each and get no flat of their own. Panels in one flat must join left to right on the dieline (`front,side_b`, `back,side_a`); top and bottom get one each.

The **image itself is not** a print asset: AI-written text may be wrong, the resolution is far below 300 ppi, and 3D images have perspective and inaccurate proportions. The engine refuses images from `concepts/` in `art add`, `finish add`, `visual import`, `visual prepare` and composition (even when job.json is edited by hand). The final design is **rebuilt from it**; final confirmation happens on the dieline image, and the exact mockup is for comparing side by side.

**Multi-part styles** (`tray_lid`, `drawer`): back view, flats, straightening and `flat` comparison work as below, with full panel names, grouped by panels visible when closed (tray and lid: the lid's two pairs of walls, the lid top `lid.top`, the base bottom `base.bottom`; see `box-styles.md`). Main and back view sizes are the closed outside shape. Each part's dieline image is confirmed at the end; 3D is view only.

## Making concept images

```bash
pacxio concept gen jobs/x --design "<style, palette, graphics, layout>" \
    --text "TIDEWELL" --text "COLOUR FOR EVERY ROOM" --text "Water-Based Paint" --n 2 \
    --scene "<optional: photo scene>"
```

- **Style and real proportions** are written into the prompt by the engine ("front width : side depth : height = 1 : 0.62 : 0.88"). The AI may still draw them wrong; when choosing a direction judge only the feel, and proportions follow the exact mockup.
- **`--text`**: asks the AI to draw the text as given; it may still misspell. **Confirm the wording with the user first** and check it again on the image; concept text never replaces the content list. Without `--text` the prompt asks for no text in the image.
- Make **`--design`** specific: main colors (with values, e.g. "deep olive #3D4536, off-white #F5EEE4"), graphic language ("soft organic curved color areas, leaves"), imagery ("a warm interior photo inset on the right"), type style ("elegant serif headline + wide-tracked sans small caps"), layout ("brand name at the top, title center left").
- **`--n`**: 1–4. By default two directions with one main view each; follow the user if they ask otherwise. Each image is one generation request and costs money / quota; a failed generation stops right away and spends nothing more.
- The default scene is clean commercial product photography; `--scene` can change it to "marble countertop, soft natural light" and the like.

Only the prompt, without the engine generating (for Codex built-in generation, or to look at the prompt first):

```bash
pacxio concept prompt jobs/x --step main --design "<style, palette, graphics, layout>" \
    --text "Brand name" --text "Product name" --scene "<optional>" --name c3      # without --name the next free number is used
```

The same skeleton as `concept gen` (style, real proportions, `--text` as given, scene), written to `concept-prompts/<id>.txt`, with the 1536 × 1024 canvas and the import command with `--view main` at the top; an original logo is listed as an input image. One direction at a time: two directions means running it twice (each with its own `--name`). It refuses an id that already has a concept image, and needs `--force` when a prompt of the same name was edited by hand.

**When the input image is an SVG** (original logos often are): the prompt step saves a white-background PNG to `concept-prompts/inputs/<name>.png` and lists that — some image tools cannot read SVG. That PNG is only for the image tool and never enters the print workflow; print still uses the original SVG.

Other sources:

```bash
pacxio concept import jobs/x from_designer.png --view main  # made by a designer / another AI tool
pacxio concept import jobs/x /path/to/this/image.png --view main  # after Codex built-in generation, with the path the tool returned
```

Prefer the explicit path of this generation. Only when the tool returns no usable path consider `--codex-latest`, after checking the image and its generation time; it takes the newest image in the global output folder and does not guarantee it belongs to this project. Prompt files from the engine still show a `--codex-latest` import example; replace that option with this image's path before running it, keeping `--name` / `--view` / `--faces`.

## Route A: paste the layout brief

With lots of copy you made a layout draft first (`references/layout-draft.md`): add `--brief` to the main-view prompt (`concept prompt --step main --design … --brief`), and the engine writes the parts of the layout brief `plan/brief.txt` for the panels visible in the main view (front, right side, top) into the prompt (what goes on each panel, hierarchy, density), so you do not pick panels yourself; style goes in `--design`. The concept is judged on overall feel and **is not expected to hold exact positions**. This step makes only **1 main view per direction** (front + right side + top); the back view comes after a direction is chosen.

## Choosing a direction

After the user has looked and **clearly says** which one:

```bash
pacxio concept choose jobs/x c2 --region 0.20,0.14,0.66,0.82 --by <who> --note "Main title a bit bigger"
```

- `--region x0,y0,x1,y1`: where the box front is in the image (0–1, origin at the image's **top left**). Main colors are sampled only there, so wall and countertop colors stay out. If unsure, make it smaller.
- It returns 3–6 main colors: HEX, share and **approximate** CMYK (with an ICC converted by it, printing close to the image's colors; without one a simple formula, printing greyer and darker; brand colors follow the brand guide / swatches).
- `--note` records the decision maker's change requests, to be followed in the rebuild.
- To change direction just `choose` again; the later one replaces the earlier.
- Only main views can be chosen: images tagged as back views or flats are refused by `choose`.

## After choosing ①: back + left view

Using the chosen main view as reference, make one more 3D image **from behind**, to confirm the look of the back and left side. **This step cannot be skipped**: without a back view `concept prompt --step flat` refuses to write flat prompts (flats draw the back and left from it, which is more stable than the main view alone). Proportions may drift (2:3 has come out close to square) — this image is only for the look; proportions and positions follow the flats. Tell the user how many images before generating.

The engine writes the prompt (run `pacxio plan jobs/x` first to refresh the layout brief):

```bash
pacxio concept prompt jobs/x --step back --design "<optional: design language in English>"
```

It goes to `concept-prompts/<chosen>-back1.txt` (back2 if back1 exists …): style and outside size, proportions, same-front-and-back notes, the layout brief's left side (and back) paragraphs and input images (the chosen main view, the original logo) are filled in; the top of the file says "generate first, then import" with the import command. It refuses to overwrite a file of the same name with different content (edited by hand); add `--force` once you are sure it is not needed. The prompt follows this skeleton (English; `<>` is filled in by the engine):

```text
Use case: product-mockup, REAR view of an already chosen design.
Input image 1 (concepts/<chosen>.png) is the CHOSEN DESIGN seen from the front. Keep exactly the same
design language: colors, typography style, graphic motifs, photo style, paper, scene and lighting.
Input image 2 is the real brand logo artwork.
Show the SAME closed <style>, outer <L x W x H> mm, from behind: a rear three-quarter view showing the
BACK face and, to its right, the LEFT SIDE panel (the side on the left when looking at the front),
plus the top. Proportions: back width : side depth : height = <ratio>; keep them (the box is taller
than wide). Landscape 3:2.
[same front and back] The BACK is identical to the FRONT in image 1 (same layout and content).
The LEFT SIDE contains (layout brief): <LEFT SIDE paragraphs>
Design the left side in the same visual language as the front and right side of image 1.
Spell short texts exactly; long texts may be drawn as blocks of small text.
```

```bash
pacxio concept import jobs/x /path/to/this/back-view.png --name <chosen>-back1 --view back --faces back,side_a,top_lid
```

**Flat boxes** (height ≤ length and ≤ half the smaller of the widths: tray and lid, drawers, mailers and the like, whose main view looks down on the top) get a back view **from below**: the box turned over and seen from the back left, the bottom taking most of the image, with the back and left side (product information is often on the bottom, so the user sees the bottom in 3D too); `--faces` in the prompt and import command is bottom, back, left, already filled in by the engine. The mockup summary sheet puts it next to the exact "bottom" mockup (the camera is not exactly the same).

Always generate first, then import with this image's path. An image identical to an existing concept is refused (DUPLICATE); check you did not pick up an old image.

After looking, list the differences from the main view (palette, graphics, type changed or not); the user confirms or it is redone. **Do not** `concept choose` this image — the chosen direction stays the main view.

### Several versions: which one is in use

When the user comments and a new back view (`<chosen>-back2`) or flat is made, **the new version is in use by default**; old versions are kept:

- One image per group is in use: the back view is one group; flats are grouped by the panels they draw (versions of `front,side_b` are one group);
- Everything that uses them uses only the one in use: the back view the flat prompt refers to, the flats `flat` compares against, the back view on the mockup summary sheet;
- On the workbench: the back view in use is marked "in use", older ones fold into "older versions (not in use)" with a "use this one" button; only the flat set in use is shown, older ones are not (when the user wants an older one, they say so in the conversation and the agent runs `concept use`);
- When the user switches to a main view redone in the original direction (numbered on from the original, e.g. `main-A-front2` for `main-A`): groups not yet redone in this branch keep the original direction's image in use (if only the back was redone, front, top and bottom still show); panels drawn by this branch's redone images no longer use the original's;
- Only run it when the user says "use the first version after all" (like confirmations, it is the user's call):

```bash
pacxio concept use jobs/x <chosen>-back1 --by <who>     # switch to an older version; recorded in concepts/in_use.json
pacxio concept list jobs/x                             # groups with several versions are marked "in use" or "older, not in use"
```

After switching, existing flat comparisons and the mockup summary sheet still use the previous image; run `flat` / `mockup` again. A new version imported later is in use automatically.

## After choosing ②: flats

Straight-on **flat artwork** without perspective or scene. The engine sets grouping and drawing (most sets now pass on the first try):

| Style | Flats (one image per group) |
|---|---|
| Single part (reverse tuck end, snap-lock …) | **The body wrap** (back \| left \| front \| right) in one image + top and bottom one each; when the wrap is thinner than 3:1 (short flat boxes), two panels per image (front + right, back + left) |
| Tray and lid | Lid top; the lid's front / back walls **upright, stacked** in one; right / left walls stacked in one; base bottom |
| Drawer | Sleeve left \| top \| right in one strip as on the dieline (sides lying down, gradients and bands wrapping from the top onto both sides); sleeve bottom; tray face / back wall upright, stacked in one |

Why this grouping:

- **The artwork fills the canvas, with no white margins whose width the AI can choose**: when asked for 40 px white margins all round, the AI kept widening top and bottom and squashing the artwork (slender strips off by 20–88%). Now the canvas is the artwork (or the artwork + one white strip of fixed proportion on one side), so proportions cannot drift.
- **The canvas uses the image API's preset ratios** (1:1, 5:4, 4:3, 3:2, 16:9, 2:1, 21:9, 3:1 and portrait): preset ratios are always honoured, pixel sizes are sometimes swapped for another ratio. When the artwork differs from the nearest preset by more than 2%, the rest becomes a plain white strip at the bottom or right (the prompt says how much), and the engine only measures the artwork.
- **Slender panels sit between other panels and the canvas edge**: slender panels placed alone with white gaps around them get squashed 15–30%; stacked with only a thin line between them they come out right. **Every panel is drawn upright**: on a cross-shaped dieline the AI draws the back wall upright (it should be upside down) and side-wall landscapes standing (they should lie down).
- **The body wrap**: continuous artwork (hills, waves, bands around the box) only joins up in one image, including both ends of the glue-flap join; for non-continuous artwork add `--separate` (says each panel is its own picture and graphics do not cross folds) instead of making them separately — separate images have the same proportion problems and cost an extra image.
- **Resolution tier**: tracing illustrations from the flats needs at least 8 px/mm per panel; the engine picks 1k / 2k / 4k by size and writes it in the file header (large boxes get 4k).

```bash
pacxio concept prompt jobs/x --step flat              # default grouping; refers to the back view in use (--back-view <id> to choose)
pacxio concept prompt jobs/x --step flat --separate   # non-continuous artwork: each panel of the wrap its own picture
pacxio concept prompt jobs/x --step flat --pairs      # single-part styles back to two panels per image
pacxio concept prompt jobs/x --step flat --ref lid.top=A:348,37,1432,203,1305,900,97,652
                                                          # a panel clearly visible in the main view: straightened from the concept as an input image (corner pixels, clockwise from top left)
```

Run `pacxio plan jobs/x` before generating to refresh the layout references (`plan/pairs/` has stacked, wrap and dieline-strip references). Each group gets one prompt file; the file header gives the input images, **canvas ratio**, **resolution tier** and the import command with `--view flat --faces`. The prompt itself reads like:

```text
OUTPUT FORMAT REQUIREMENT: generate a 2:1 landscape image. ...
The printed artwork fills the full width of the canvas, from the top edge down to exactly 95.1% of the canvas height;
the bottom 4.9% is a plain pure-white strip ...
These panels are SEPARATE walls ..., each drawn UPRIGHT ..., stacked from top to bottom with NO gap: FRONT; BACK.
Separate the panels with ONE thin straight horizontal hairline at exactly 47.5% of the canvas height ...
...
FINAL GEOMETRY CHECK BEFORE OUTPUT: a 2:1 canvas. ...
```

- **Generate with the ratio and resolution from the file header**: a local image plugin can read those two lines; when the image tool cannot set them, follow the prompt's first line. **When rewriting the prompt keep the first and last lines as they are** (stated only in the middle, Codex still produces 1024 × 1536).
- Folds / separations are drawn as one thin line: the engine splits panels on it (the AI often divides panel widths inaccurately); without it, it splits by the real proportions.
- Panels facing the camera in the main view (lid top, front) are often drawn too busy in flats: use `--ref <panel>=<concept>:corner pixels` to straighten that panel from the concept as an input image, and the prompt asks to redraw following its composition, white space and tones.
- The flats are the source for restoring the final artwork: illustrations, color areas and contour lines are traced from them as vectors (`restore strip / face` → `visual trace`), AI-drawn text is removed and set by the engine; **photos are generated separately** (not from the flat's pixels; see the image tiers in `visual-prompts.md`).

```bash
pacxio concept import jobs/x /path/to/this/flat.png --name <chosen>-flat-front-back --view flat --faces lid.front,lid.back
pacxio concept import jobs/x /candidate1.png /candidate2.png --name <chosen>-flat-body --view flat --faces back,side_a,front,side_b
```

**After importing, measure proportions and check content**: `concept import` measures right away (`data.overall_error` for the whole image, `data.aspect_errors` per panel):

- **Whole-image proportion** off by more than 10%, any panel off by more than 20%, or unmeasurable: marked "redo" (FLAT_RATIO), kept off the workbench, and `flat` does not compare against it;
- Whole image right but a panel off by 10–20%: a reminder only (FLAT_FACE, the AI divided panel widths inaccurately); straightening splits at the folds it finds; keep it in mind when comparing;
- With several candidates imported together only the one with the most accurate overall proportion is kept; **right proportions do not mean right drawing**: one with the best proportions drew the top's design onto the drawer face — after importing the agent **looks at each image's content and direction** (text or illustration upside down, drawn onto another panel), and treats a wrong one as "redo";
- **At most two rounds**: if a group still fails after two rounds, stop, straighten the best one and show the user, and ask whether to accept the straightened one, change the arrangement (e.g. `--pairs`) or keep generating, saying how much more it will cost; never keep redoing on your own;
- Never pad with straightened images: straightening also distorts photos and text in the image, and measured positions shift with them.

Once the whole set passes, list the differences (drift) against the two 3D images and show the user; once the user confirms the set, **the final design follows the flats**. When the flats are complete, the workbench's "Now" shows it is waiting for the user to confirm the set; after the user clearly confirms in the conversation, record it with `pacxio concept confirm <job> --by <who>` (the user can also click on workbench ④ themselves), then start building the layout. Never start the final artwork without this record; swapping images or switching versions expires it, and the user has to confirm again.

**Straightening**: `pacxio concept rectify jobs/x <chosen>-flat-front --grid` finds the artwork edges and folds (otherwise splits by real width ratios), saves each panel at its real mm size (`concepts/rectified/<id>.<panel>.png`, `--px-per-mm` default 4, `--grid` overlays a 10 mm grid with the origin at the bottom left) and reports each panel's proportion error (over 10% is also marked "redo"). Straightened images are still concept images and cannot enter the print workflow.

## Rebuilding from the flats: panel by panel

With confirmed flats, they are the reference: straighten with `concept rectify --grid` and measure each element's position and size on the grid (mm, origin bottom left; straight-on, so accurate). When a group has several versions use the one in use; when several images draw the same panel, use the one with the smallest proportion error (with the same front and back, the front can be measured from the back's image). Without flats you can only estimate from the main view. For **each panel** list:

| Element | How to rebuild |
|---|---|
| Text (brand name, product name, slogan, selling points, body) | `art add --kind text`, one block at a time, size and hierarchy from the concept, with `--tag` |
| Serif titles | `--font SourceHanSerifSC-Bold` (Source Han Serif; if not installed `scripts/fetch_fonts.py --with-serif`), or the user's brand font |
| Wide-tracked small text | `--tracking 100`–`300` (1/1000 em); line spacing of multi-line text with `--line-height` |
| Numbered lists, bullets (continuation lines aligned with the text after the marker) | `--hanging-indent auto` on the list style; when the flat has more space after the marker give the measured mm (see `fonts.md`) |
| Color | Main colors from `concept choose` → `--color` / `--colors` (approximate CMYK; brand colors follow the guide) |
| Curved color areas, bands, geometric shapes, lines | Write SVG → `art add --kind vector` (see `vector.md`) |
| Curves wrapping from front to side / bands around the box | `--kind vector --space body` |
| Icons (leaf, house …) | SVG → `--kind vector --color`; or ask the user for SVGs |
| Photos inside arches / circles | `visual gen` a **text-free, perspective-free** flat photo → `--kind image --clip shape.svg` |
| Illustrations, textures, gradients | `visual gen` a flat asset (describing the concept's style and palette in the prompt); or ask the user |
| Logo | The user's SVG (`--kind vector` for ink, `finish add` for foil and other finishes) or PNG |

**Not reproducible yet (tell the user when they choose a direction):**

- **Gradients, transparency, shadows / glows** inside vector graphics: only as bitmap assets (≥ 300 ppi).
- **Special fonts** in the concept (handwriting, decorative type): without a matching font file they can only be approximated with Source Han Sans / Serif or the user's brand font.
- AI-drawn text in the concept will not match a real font exactly, and the concept's proportions will differ from the real box.

## Comparing the final artwork with the flats and concept

After building the layout, `render` the dieline confirmation image, then compare with `pacxio flat`: with images tagged `--view flat`, each panel gets "straightened AI flat | final artwork | 50% overlay" (only the version in use; when several images draw a panel, the one with the most accurate proportions; large images in `flat/vs_flat_<panel>.png`). These comparisons are for you; write the differences one by one with `--diff`, which shows next to the ⑤ dieline confirmation image on the workbench for the user to see when confirming.

The top of `pacxio mockup`'s summary sheet puts **the concept and the exact mockup side by side**; with a back view (`--view back`) another row shows back-view concept | exact mockup from the same angle (back + left side). Use it to point out differences (fonts, graphics, proportions, colors) to the user. Without a chosen concept `mockup` gives an MK07 reminder.

Concept images **never** go into the factory delivery pack or the archive (the archive only holds the confirmed dieline image `confirm.png`).
