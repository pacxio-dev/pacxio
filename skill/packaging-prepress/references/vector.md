# Vector graphics, clipping shapes, graphics across folds

**Curved color areas, bands, icons, lines and geometric ornaments** in a concept should all become vectors (SVG), placed with `art add --kind vector`: each color prints in the CMYK ink you give it, with sharp edges at any size. Only continuous-tone things like photos and painted illustrations use bitmaps (`--kind image`).

## What to use when

| In the concept | How |
|---|---|
| An illustration around the body (hills, plants, patterns) | Trace from the flats: `restore strip` → `visual trace` (method picked by the image, full-color for illustrations) → `--kind vector --space body` |
| An illustration on one panel (top, bottom, mailer panels) | Trace from the flats: `restore face <panel>` → `visual trace` → `--kind vector --panel <panel>` |
| Curved color areas, wavy bands, geometric shapes | SVG → `--kind vector` |
| Icons (leaf, house, tick, eco mark) | SVG → `--kind vector` (one-color icons add `--color` to print in one color) |
| A photo inside an arch / circle / custom frame | Photo `--kind image` + `--clip shape.svg` |
| A curve wrapping from the front onto the side, a band around the box | `--kind vector --space body` |
| A graphic running from the front onto the bottom / top lid | `--kind vector --space blank` |
| Gradients, light and shade, textures, photos | Bitmap assets (`visual gen` / supplied by the user) |
| Foil / spot UV on a logo | `finish add` (finish spot colors, not ink) |

## Trace from the flats, hand-write simple icons

With flats the user has confirmed, **illustrative graphics** (hills, contour lines, plant patterns, areas of curved color) are **traced from the flats as vectors** (next section): shapes, layering and density all come from the flat. Procedurally generated hills (a few stacked sine waves, evenly shifted lines) scored far lower in tests than graphics traced from the flat (SSIM 0.67 and thin-line recall 0.39, against 0.92 and 0.81–0.96 per body panel). The concept image itself cannot go on the layout, but vectors traced from it can (`restore strip` removes AI-drawn text and records which flat it came from).

**Simple icons and geometric ornaments** (leaves, drops, dividers, rounded frames) are written as SVG directly first ("SVG requirements" and "Artboard and placement box" below), with exact control of line width, round caps and size. When both work, use whichever looks closer, compared with `restore score`. Only without a flat to trace (or when that part of the flat is badly drawn) go "AI flat-color shape image → trace to vector".

## Choosing a method (`visual trace --method`)

| Image | Method | Colors |
|---|---|---|
| Whole illustrations: many colors, fine detail, shading (hills, shrubs, berries, ferns) | `vtracer`: full-color tracing that keeps detail and shading | **No color limit**; in print they are converted to CMYK with the ICC and limited in total ink like photos; only background / key colors get CMYK |
| Line frames, line icons, graphics in a few flat colors (logos, color areas, bands) | `shapes`: traces color areas by color; thin lines (`--lines`) are traced along their centerline as even strokes | Each color gets CMYK with `--colors` (brand colors) |

Without `--method`, `visual trace` picks by the image (dense one- or few-color pen line art — thin lines over 80%, ≤ 3 dark inks, broken into more than 150 pieces, like AI-drawn line landscapes → vtracer full color with colors merged into 6 tones (`--tones` changes it); other mostly-thin-line images with ≤ 3 colors → shapes with `--lines`; ≥ 20 solid color areas → vtracer; otherwise shapes); what it picked and why goes into the output's `route` and the asset record; giving `--colors` / `--lines` means shapes. If it does not look right, set `--method`. Do not trace large brand-color areas: hand-write the SVG or use `art add --kind fill` with exact CMYK.

With vtracer:
- Background: the background fill recorded by `restore strip / face` (otherwise the lowest full-coverage layer) is not traced and shows through from the layout's fill; shapes within the illustration that sit on other colors and match the background (light lines on hills, gaps) are kept and snapped to the background color — the output gives `--colors '<background>=<fill CMYK>'`; add it when placing so both print with the same ink;
- Key colors (optional): when the illustration touches brand color areas and the join must match, give those colors with `--palette '#hex,…'`; colors within ΔE 3 snap to them, and only they get CMYK when placing;
- Many shapes (a body wrap often has over ten thousand): rendering and checks get slower (one render of such a wrap takes about 20 seconds); if too many, `--speckle 8` merges smaller specks;
- There are no strokes, so `restore score`'s KINK only checks thin lines traced by shapes; BREAK, SPIKE and FOLD are checked as usual.

## Tracing from the flats (illustrations around the body)

```bash
# Set the final text first: restore strip removes AI-drawn text where the text placements are
pacxio restore strip jobs/x                     # → assets/body-strip.png (real size, front at the left)
pacxio visual trace jobs/x assets/body-strip.png --name body      # illustration: vtracer full color automatically
pacxio art add jobs/x --kind vector --source assets/body.svg --space body --box 0,0,<circumference>,<panel height> \
    --fit stretch --colors "<background=fill CMYK from the output>"
pacxio restore score jobs/x                     # per-panel fidelity: SSIM, color difference, thin-line recall; breaks, kinks, spikes, both sides of folds
```

A single panel (top, bottom, mailer panels):

```bash
pacxio restore face jobs/x bottom               # → assets/btm_lid-face.png (reverse tuck end; bottom finds the bottom panel by style, btm_front for snap-lock; real size, upright, at the resolution of the flat)
pacxio visual trace jobs/x assets/btm_lid-face.png
pacxio art add jobs/x --kind vector --source assets/btm_lid-face.traced.svg --panel btm_lid \
    --box <the box in data.place of the output> --fit stretch
```

- When `restore score` reports BREAK / KINK / SPIKE / FOLD, fix them before showing the user (positions are in the warnings, circled red on `flat/score_<panel>.png`):
  BREAK (a line broken next to text) usually means an `--erase` box caught the line; shrink the box, join and trace again; KINK (small hooks, loops or tails at forks on thin lines): trace again first, and if it stays, smooth it by hand in the SVG;
  SPIKE (a color area poking out of the artboard, a thin diagonal on the layout): trace again first, and if it stays, delete that point in the SVG;
  FOLD (artwork does not meet across a body fold or the glue-flap join: a thin gap, a line stopping at the fold, color edges offset) — check on workbench ⑤ with "Cut lines" off; thin gaps usually mean the body illustration was not traced as a joined wrap from `restore strip`; for mismatches look at the same spot on the flat — if the flat is drawn that way it is not reported (`flat_too`), only recorded in score.json;

- When `restore strip` warns that a join is badly offset (most often the glue-flap join: the two ends of the AI image do not meet), ask the user which panel to follow, then `restore strip --keep <panel>` (the other panel bends over within 15 mm to meet it); if unsure, join and trace both and show the user the joins side by side;
- `restore strip` also warns when "the two sides of a join show different things" (too many rows with pattern on one side and background on the other, common at the glue-flap join: the AI did not connect the ends): ask the user which panel to follow; `--keep <panel>` makes the other panel blend into its mirrored image within 15 mm, so the edge meets once folded; on a box corner where it does not show, it can be left;
- `restore strip`, like `restore face`, erases AI text block by block (bold titles and light text on dark backgrounds too; the output's `text_blocks_mm` lists erased blocks); it joins at the flat's own resolution by default (a 4k flat is about 13 px/mm, at most 16; `--px-per-mm` sets it); `--erase` only paints strokes in the box that differ from the paper color, judging the paper color from a ring just outside the box, so boxing a logo or mark that is mostly ink does not paint the whole box in ink;
- When it warns about leftover text, deal with it before tracing (traced text becomes a printed graphic): when AI text is larger than the final text box and a few strokes remain, box them out with `--erase x,y,w,h` and join again (body coordinates in mm: x from the front's left edge, y from the bottom, repeatable); offset joins are occasionally mistaken for text (e.g. "SS"), so take a look;
- Crash-lock bottoms and bottoms of slotted cases made of two flaps (the top of slotted cases too) have no separate flat, and `restore face` refuses them: hand-write SVGs for those graphics from the main view / layout draft and place them on each flap;
- **Illustrations on a gradient background** (a gradient with an illustration on it, such as a cacao pod or a crescent moon): `restore face <panel> --crop x,y,w,h` (panel-local mm) takes only that area, then `visual trace … --background gradient` (removes the slowly changing background and traces only the illustration; full-color tracing only); lay the gradient itself as a text-free asset or vector gradient, and place the traced illustration in the box `restore face` returns;
- Background color (the untraced one): `restore strip / face` records the job's background fill for those panels, and `visual trace` uses it by default; other images guess from the most common color at the image edge, which can go wrong when hills fill the lower edge (tracing the sky as a layer and dropping the main color as background) — it then warns "background color uncertain"; set it with `--background '#hex'`;
- `--colors` for shapes only counts color areas (not the background or thin lines): too many pulls light colors near the background (AI-drawn barcode boxes) into separate colors, lightens thin-line colors and traces faint background texture as lines;
- Every color traced by shapes gets CMYK with `--colors` (brand colors from the user; otherwise approximate values, and tell the user to check on a proof); without it colors are converted automatically by ICC (V01 warning); illustration colors traced by vtracer are converted automatically anyway, without a warning;
- Thin-line colors and widths from shapes are in the `lines` of the `visual trace` output; give them CMYK when placing;
- When `restore strip` joined a whole wrap, `visual trace` automatically continues across the ends while tracing (recorded as `wrap: true`): color areas and thin lines run continuously over that fold, with no background gap, notch at the top of a color area or hooked line stopping short of the fold; use `--no-wrap` when it is not a whole wrap (rarely needed). After tracing, `render`, turn off "Cut lines" on workbench ⑤ and look along each fold (crease lines hide problems on folds);
- Single panels: `restore face` removes AI text near the final text on that panel — AI text larger than the text box, reaching several mm beyond it, or with heavy strokes (titles, seals) is also erased as whole blocks: same-color, text-sized, adjacent blocks are joined, and strokes are painted in the surrounding paper color (strokes only, no streaks; the output's `text_blocks_mm` lists erased blocks — check no illustration was erased). AI text that is not in the final artwork ("blank area for …" notes, barcode digits), far from the text box or in a different color is not removed; the text check reports it, then box it out with `--erase x,y,w,h` (panel-local mm, from the bottom left with the panel upright; strokes in the box that differ from the background are painted in the background color, so just box the text, and box edges touching the pattern do not streak); the 0.5 mm frame line around the edges and fold lines are replaced automatically;
- **Two-color / spot-color flexo illustrations** (dark green + orange woodcut style on kraft cases and the like): do not use `shapes --colors 2` (gaps between fine engraved lines and small dots get merged, and fruit trees trace as solid silhouettes); use `--method vtracer --tones <number of inks + 1>`, where the extra tone is the transition color between ink edges and engraved lines, given `0,0,0,0` (no ink) with `--colors` when placing, and each ink color its CMYK; the background (kraft) is `0,0,0,0` too. Zoom in on details after tracing and look at the thinnest lines: thin flexo lines and small reversed dots fill in easily (ask the factory how fine it can go);
- Before tracing, every image is padded on all four sides (a whole wrap continues across the ends, others repeat the outermost row) and cropped back afterwards: color areas and lines stop exactly at the image edge, and corners touching the edge are not rounded into notches.

## AI flat-color shape image, then trace (when there is no flat to trace)

When there is no flat to trace and the shape is hard to draw directly, have the image model draw **a flat image in a few solid colors**, then trace it to vectors; simple icons and geometric ornaments are still written as SVG first.

```bash
pacxio visual gen jobs/x --role graphic --name waves --size 1536x1024 \
    --prompt "two overlapping organic wave bands in deep forest green #3D4536 and sage #8A9A6E on cream"
#   (Codex: after built-in image generation, visual import jobs/x /path/to/this/asset.png --role graphic --name waves)
pacxio visual trace jobs/x assets/waves.png --palette "#3d4536,#8a9a6e" --name waves
pacxio art add jobs/x --kind vector --source assets/waves.svg --space body --box 130.96,0,261.92,70 \
    --fit stretch --colors "#3d4536=82,61,79,31;#8a9a6e=45,25,65,5"
```

- `--role graphic` forces "flat solid colors, no gradients, no text"; give `--palette` (brand colors) when tracing — AI images often drift slightly, and snapping cleans the colors; without brand colors give `--colors 3` (background excluded);
- The background (transparent areas, or the most common color around the image edge) is removed by default; add `--keep-background` to keep it;
- **Icons and logos: add `--trim`**: crops the artboard to the graphic (AI icons often have 30% margin around them; untrimmed, they fill only 70% of the box and lines look thin);
- **Geometrically simple icons** (leaf, drop, shield, gear, star …) are often better as hand-written SVG: even line widths, consistent round caps, drawn to the box size (in tests hand-written icons matched the flat better than AI images traced to vectors);
- Traced shapes are stacked from largest to smallest area, so no gaps show between neighbouring colors;
- **Images with thin lines** (contours, outlines, line illustrations): add `--lines`: thin lines are traced separately along their centerlines as even round-capped strokes on top of the color areas, instead of being merged away; `--colors` excludes line colors, and line colors and widths are in the output's `lines`; give them CMYK when placing;
- **No text in the image**: once traced it becomes a printed graphic; `visual trace` checks as far as it can and warns;
- Graphics across folds: generate close to the placement box's aspect ratio (e.g. for a 3.7:1 body wrap, two images of half each, with the join on a box corner); place with `--fit stretch`.

## SVG requirements

The engine **refuses** the following (and explains how to fix it in design software), because the print file cannot reproduce them faithfully:

- Gradient / pattern fills (`url(#...)`) → use solid colors, or make that part a bitmap;
- Transparency (`opacity`, `fill-opacity`, semi-transparent colors) → use opaque solid colors;
- Text (`<text>`) → outline it in design software, or remove it and use `art add --kind text`;
- Embedded bitmaps (`<image>`) → photos go in separately with `--kind image`;
- Clip paths / masks / filters / blend modes → release clipping masks, expand appearance;
- Dashed strokes → expand strokes to shapes.

Supported: solid fills (nonzero / even-odd rules as given), solid strokes (width, caps and joins as given, expanded to outlines automatically), `transform`, `<use>`.

**White is paper white**: a white shape prints as "no ink" and covers the background below it. SVGs exported from design software often carry a full-size white background rectangle — the engine gives a V02 warning; delete it from the SVG.

## Artboard and placement box

The SVG's **artboard** (`viewBox`, or absolute `width`/`height`) maps to `--box`:

- `--fit contain` (default for vectors): scaled proportionally, centered in the box;
- `--fit stretch`: stretched to fill the box (common for bands and background blocks);
- `--fit cover`: scaled proportionally to cover, anything outside the box is cropped.

When writing SVG yourself, **set the viewBox to the same mm as the placement box** (e.g. `viewBox="0 0 261.92 60"` with `--box 0,0,261.92,60`), so coordinates in the SVG are mm (SVG y points down: 0 is the box's top edge).

## Color: SVG colors → CMYK ink

```bash
--colors "#3d4536=75,55,80,40;#c8a464=20,35,70,0"     # per color (brand colors from the brand guide / swatches)
--color 0,0,0,100                                      # the whole graphic in one color (one-color icons)
```

Colors without values are converted by the profile (a simple formula without ICC, **not reliable**), with a V01 warning listing each color and its converted value — pass it on to the user and ask them to confirm or give exact CMYK. White (#ffffff) needs no value.

## Clipping images with shapes

```bash
pacxio art add jobs/x --kind image --source assets/room.prepared.png --panel front --box 10,60,60,80 --clip arch.svg
```

The area covered by all shapes in `arch.svg` is what shows of the image (color does not matter); the shape's artboard is scaled proportionally and centered in `--box`. The image itself fills the box by `--fit` (default cover). Arch example (60×80 mm):

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 80"><path d="M0 80 L0 30 A30 30 0 0 1 60 30 L60 80 Z"/></svg>
```

## Continuous across folds: `--space body` and `--space blank`

`pacxio panels jobs/x` lists the body-wrap coordinates, e.g. for an 80×50 box:

```
Body wrap (--space body, origin at the front's bottom left): circumference 261.92 mm; front 0–80.48 → side_b 80.48–130.96 → back 130.96–211.44 → side_a 211.44–261.92; the end join (glue-flap edge) at x=130.96
```

- **body**: origin at the front's bottom left, running right front → right side → back → left side, back to the front after one circumference (x can be negative: the left side is −50.48–0). Graphics are only drawn on the four body panels (including bleed), **never on lids, bottoms or dust flaps**; they stay continuous across the glue-flap join.
- **Graphics all the way round**: box width = the circumference, **left edge at the join** (x=130.96 above), so the join hides on a box corner, out of sight from the front.
- **blank**: whole-blank coordinates (origin at the bottom left of the dieline's bounding box), crossing onto any panel (e.g. from the front onto the bottom lid); limited only by where ink is allowed (not in the glue flap's no-ink area). The upright direction of lids and tucks is not confirmed by a proof yet, so warn the user about graphics running onto them.

## Stacking order

Placements stack bottom to top in the order they are added, **text included**. Vector graphics, clipped images and text are written to the print PDF in this order; mockups, previews and checks use the same order.

- Add under a placement: `art add ... --before <id>`;
- reorder: `pacxio art move jobs/x <id> --top` (or `--before <id>`);
- remove: `pacxio art remove jobs/x <id>`;
- text covered by a later layer gets a **T04** warning — move the text to the top unless intended.

## Erasing an area before tracing, number of tones

- **`visual trace --erase x,y,w,h`** (pixels, origin top left, repeatable): when the AI drew something where space should stay empty (a circle in the middle of the lid where a badge goes, clouds where the title goes), paint it in the background color before tracing; the original image is untouched. No need to write a script, edit the image and import it again.
- **`visual trace --tones N`** (vtracer): merges colors other than the background into N tones. Full-color tracing of one-color line art turns anti-aliasing into thousands of slightly different colors; dense line art picked automatically gets 6 tones.
- **`visual trace --strength 0.35`** (vtracer): lightens the lines overall, pulling colors toward the background and keeping only that much of the difference, without changing the composition; use it when the user finds side or back illustrations too loud. Show previews at a few strengths to the user first, then trace.
- The text check (TEXT) gives the positions of suspected text (pixel boxes): look at them first; real text gets removed and redone, or `--erase`d; strokes of dense line drawings are sometimes mistaken for text and can be ignored.
