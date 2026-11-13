# Finish areas, surface treatments and 3D

Local finishes use `finish`; lamination and varnish use `surface`. Both are written to the job; after a change, `render` again and ask the user to confirm the dieline image again. Read existing records and manufacturing-notes.md first and follow the user's requirements; never add foil, UV or embossing on your own for show.

## Local finishes

Supported: foil_gold, foil_silver, foil_copper, foil_holo, spot_uv, emboss, deboss, white. The area comes from a final SVG (preferred) or a two-tone PNG; white is knocked out; concept images cannot be used. `--box` is in panel-local mm: origin at the bottom left, x,y,w,h; multi-part panels use full names like `lid.top`. The graphic is fitted to the box proportionally by the source file's **artboard**, the same as print artwork: save a logo and its finish plate as two files on the same artboard and use the same box, and they line up. For a finish on a logo already in the layout use `--content logo` and omit `--box` (it uses that logo's box). The real outline may be smaller than the box; never treat the box as the finish coverage. F04 warns that a finish and its printed graphic do not line up; F05 warns that white parts of the finish image get no finish (the white letters of a reversed logo, for example — use the normal logo file or draw in black instead).

```bash
pacxio finish list jobs/x --json
pacxio finish add jobs/x --id logo-foil --type foil_gold --tag brand_mark --source assets/logo.svg --panel front --box 25,15,30,24 --json
pacxio finish add jobs/x --id logo-emboss --type emboss --content logo --panel front --json   # uses the box of the logo in the layout
pacxio finish set jobs/x logo-foil --box 24,15,30,24 --json
pacxio finish set jobs/x logo-foil --note "Copper foil, match to the factory's sample" --json   # a note for the factory, goes into the spec sheet; --note "" removes it
pacxio finish remove jobs/x logo-foil --json
```

**Spot UV, screen or digital**: by default spot UV is checked as screen UV, line width ≥ 0.3 and gap ≥ 0.5 mm (profile `spot_uv`). When fine lines or small text need UV, first ask the user whether the factory can do **digital UV** (inkjet); only after the user confirms add `--process digital`, which checks against `spot_uv_digital` (default line width ≥ 0.1, gap ≥ 0.2 mm); `--process screen` switches back. Never assume digital UV is available. Large UV areas (mountain shapes, color blocks) are fine with screen UV.

**Finishes on text** (foiled product names, letters in a badge): set the text first with `art add --kind text`, then `finish add <job> --type foil_gold --placement <text placement id>` — the finish is that text's glyphs (the same curves as the outlined text in the print file), and its panel and box follow the text, so `--panel` / `--box` are not needed; when the text changes or moves, the finish follows. Never convert text to paths outside the engine to make an SVG.

**F02 (line width / gap) fails**: finishes with text almost always hit it — stroke tips and slanted ends are thinner than the minimum line width, and ink traps at sharp corners are narrower than the minimum gap; simply "making it bolder" fills the ink traps into wedge gaps. Run `pacxio finish fit <job> <finish id>` first: an automatic closing (fills narrow gaps) + opening (removes spikes), self-checked with the same algorithm as F02, writing a candidate SVG and a before/after comparison (`finish-fit/<id>.png`: red = removed, blue = added). **Show the comparison to the user, and only after they agree** run `finish fit <job> <id> --apply` to swap in the fitted graphic (a finish made from text becomes a fixed graphic, so later text changes need `--placement` again and another fit; once the graphic changes, the dieline confirmation image has to be confirmed again). Give `--apply` the same parameters as the comparison run (`--close` / `--open`): if the finish graphic and parameters have not changed, the last candidate is used without recomputing (`data.reused: true`); if they do not match, it fits again and warns that the comparison changed and must be shown to the user again. For small foiled text use a sans or bold face: Latin letters about 5 mm high in Source Han Serif Bold have thin horizontals under 0.24 mm that break after opening.

A failed check does not change the job. `foil_gold` / `foil_silver` / `foil_copper` only say gold / silver / copper foil; give the exact foil number or finer colors such as rose gold with `--note`, and the spec sheet's finishes section adds "Requirement: …" under that finish; `--note` is only a note: it does not change the finish graphic, does not expire the dieline confirmation and needs no new render. When other fields of a finish change (box, note …), a replaced finish graphic (e.g. a fitted foil outline) is kept; only `--content` swaps in the item's asset — after the logo in the content list gets a new file (`content edit … --asset`), `finish set <job> <id> --content logo` with the same item again swaps in the new file. The layout's same_faces does not copy finishes. Overlapping finishes on the same material channel are an error; white ink overlapping metallic also needs the process stated separately, and the first version does not stack them automatically. UV shows as a clear coat in the material preview and embossing as a shallow relief; process order is never guessed from list order.

## Attachments: metal badges

When a stamp or badge in the concept looks like a metal part (thick edges, bevels, raised letters, metallic reflections), print + foil + embossing cannot achieve that look: ask the user early whether to make a **metal badge** (made separately by a badge maker, stuck on with adhesive when the box is assembled). It is not a finish, is not printed and has no finish plate.

```bash
pacxio attach add jobs/x --id badge --panel lid.top --box 91,64.5,71,71 \
    --source assets/badge-shape.svg --relief assets/badge-relief.svg \
    --material zinc_alloy --plating champagne_rose_gold --thickness 3 \
    --note "Raised letters and pattern in dark antique bronze, fine sandblasted base; 3M adhesive on the back" --json
pacxio attach list / set / remove jobs/x …
```

- Two SVGs on the same artboard: `--source` the shape (a round badge is a solid circle), `--relief` the raised pattern (letters, wide rim, ornaments). Letters are font outlines converted to paths (`text_geom` and the like), never AI-drawn letters.
- `--material` zinc_alloy (die-cast zinc, many relief levels) / aluminum (stamped aluminium); `--plating` champagne_rose_gold / rose_gold / champagne_gold / gold / silver (colors follow the badge maker's swatches); the first version only does flat adhesive mounting, no recessed seat.
- What comes out: drawn in the plating color and numbered on the dieline confirmation image; the production PDF gets a non-printing "attachment position" layer (Attach_Position: outline + center cross), the DXF an ATTACH layer; the delivery pack includes `<job>_attach_<id>.svg` (1:1 artwork for the badge maker); the spec sheet's "Attachments" section gives size, position, material, plating, thickness and mounting; the 3D mockup draws it as a metal part with thickness.
- Checks: A01 the badge leaves the panel, or is less than 3 mm from a fold / cut line [initial value] (error); A02 the badge covers foil / UV (warning) or a window (error). The original printed + foiled + embossed stamp has to be removed with `art remove` / `finish remove`.
- Changing an attachment needs a new confirmation of the dieline image (except `--note`).

## Lamination / varnish

```bash
pacxio surface list jobs/x --json
pacxio surface add jobs/x --id outer-film --type matte_lamination --label "Outside matte film" --json
pacxio surface set jobs/x outer-film --type gloss_lamination --json
pacxio surface remove jobs/x outer-film --json
```

Types: matte_lamination, gloss_lamination, matte_varnish, gloss_varnish. Default `--panels outside_all`; specific panels with `--panels front,back`; multi-part styles use `--part lid` to cover every printable outside panel of that part, or `--panels lid.top,lid.front`. When changing the coverage give the whole coverage; to lift a part restriction use `--panels outside_all`.

outside_all follows the real panel outlines, not the blank's bounding box; windows have no paper and are subtracted automatically. It may include printable panels that are hidden after folding, such as the glue flap or tucks; check the coverage list.

**Whole-sheet coverage by default** (`bonding_policy=full_sheet`): lamination covers the whole sheet, including the glue flap and the glued bottom areas; bonding is the factory's job (abrading or special glue). No "bonding areas pending factory confirmation" warning is given. Only when the factory explicitly asks for areas without lamination / varnish, enter rectangle or SVG knock-outs:

```bash
pacxio surface set jobs/x outer-film --exclusions '[{"panel":"front","box":{"x":0,"y":0,"w":10,"h":20}}]' --bonding-policy explicit_exclusions --note 'Knock-outs given by the factory; proof still needed' --json
```

SVG knock-outs add `source` in that object, a path relative to the job or absolute; the real outline includes holes. `--exclusions '[]' --bonding-policy full_sheet` restores whole-sheet coverage. explicit_exclusions only says the entered areas are knocked out; it is not a factory acceptance record. factory_pending in old records is treated as whole-sheet coverage. Surface treatments cannot overlap; give them separate panels or knock-outs.

Surface treatments appear on the confirmation image, in the spec sheet and as 3D materials; they add no print spot color. Local finishes keep their own spot plates. Other manufacturing requirements still go into manufacturing-notes.md, without claiming the engine checked them.

## Checking areas and looks

1. `render`: in ⑤ the "Finish areas" switch shows the real outlines; selecting an item in the list highlights its coverage, holes stay open; clear the highlight to see the artwork again. The legend and coverage notes sit outside the image and never go into the print plates.
2. `mockup`: ⑥ builds materials from the same mm areas; click an item in the "Finishes" row to mark its area (click again to clear) and check the position. Reuse the existing manifest.required labels, see mockup.md.
3. There is one lighting setup, natural light: like light by a window, with sky and ground ambient light, darker towards the ground with a contact shadow, soft highlights that do not blow out, and sky reflected at the top of bright surfaces. Drag "Direction" (degrees left / right) to see how reflections and relief change; "Brightness" changes the overall level; "Reset" returns to 30° left, 100%. These only change local display preferences: no production data changes and no confirmations expire. Check text and color on the ⑤ dieline confirmation image. Matte film on a dark box shows as a soft broad light; gloss film, gloss varnish, UV and foil are specular: an upright panel reflects a window highlight with clear edges (by default right of center on the front), with no soft haze elsewhere and deeper color than matte; the highlight moves when the box turns or "Direction" is dragged.
4. Coverage notes always use short names: surface treatments say "whole surface" (or the listed panels, noting how many knock-outs when there are explicit ones); local finishes use the linked content item, e.g. "logo area", "product name area", and "graphic area" when no item is linked. Exact position and size are only in the spec sheet.

Metallic highlights change with view and light, UV is a clear gloss, matte film adds no grey layer. Embossing only changes surface normals, not the box outline; holographic foil is an approximate color shift. Material presets are not factory measurements and colors are not color-accurate; a proof is needed. `view_*.png` and the summary sheet remain static illustrations with fixed highlights, not screenshots of the interactive 3D: the studio light is fixed (one tall panel light front left, `static_reflection` in `render-materials.json`); gloss film, gloss varnish and spot UV show a soft vertical highlight on panels facing the light, matte film and paper hardly reflect. UV on gloss film reflects about as much as its surroundings, so the lack of contrast is real, not a missing finish.

Old scene v1 files still open; when told the renderer / materials were updated, run mockup again — production confirmations do not expire because of it. When images, compilation or WebGL are unavailable, click "Static summary"; reopen the workbench after fixing.
