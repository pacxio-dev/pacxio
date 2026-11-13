# Preflight rules in plain words

Check code → meaning. `error` must be fixed until none are left; pass every `warning` on to the user as it is; `info` is explanation.

## Structure (the dieline itself)

- **S01** The dieline outline must be closed, not self-intersecting, with no duplicate lines; every crease has a panel on both sides.
- **S02** Dimensions measured back from the geometry must match the parameters (tolerance 0.05 mm).
- **S03** Style parameters must meet their constraints (ranges, tuck length < height, etc.).
- **S05** Multi-part styles: the parts must fit together (lid inside size − base outside size = clearance; sleeve − tray = clearance + one layer of the joint flap), measured from the geometry. For multi-part styles every issue starts with the part it belongs to.
- **S04** (warning) Style advice on material / size, a reminder that does not block: e.g. a mailer box in C / BC flute, a slotted box in E flute, a blank larger than common machines.
- **W01, W02** Window not allowed (blocked by `window add`, never written into the job): W01 window too small / corner radius too small / wrong shape parameters; W02 window too close to a crease, the outline, the glue line or another window, or on a lid, tuck or other panel where windows are not allowed (corrugated boxes cannot have windows in the first version either). Window film is applied later by the factory and is not checked.
- **W04** (warning) Windows take up too much of a panel (initial value 60%); the box gets weak.
- **W05** (warning) Most of a text / image falls inside a window and will be cut away.

## Bleed and no-ink areas

- **B01** Artwork must fill the bleed: where there is ink inside a cut line (the outline and window edges), the bleed band outside must have ink too — the window area is waste, so print through it, do not knock it out. When an asset is too small the engine extends its edges to fill the bleed and notes it in the report (info).
- **B02** On the glue flap, ink is allowed only in a narrow band inside the crease (default 2 mm); the rest of the flap must have no ink at all (that is where glue goes).
- Bleed defaults to 3 mm, safe margin to 3 mm, glue flap to `bleed_only` — all from the profile.

## Text and images

- **T01** Text, logos and finish graphics must be inside the safe area (at least 3 mm from cut and fold lines). The report gives fixes like "move X down 2.4mm into the safe area".
- **T02** (warning) Text overflows its box.
- **T03** The print PDF contains fonts — text was not outlined. Does not happen in the normal workflow.
- **T04** (warning) Text is covered by a layer placed after it (image, vector graphic, fill). Placements stack bottom to top in order, text included; if it is not intended, `art move <job> <text_id> --top`.
- **T05** (warning) Text smaller than the profile's minimum size (initial value 6pt; flexo 8pt).
- **T06** (warning) Too little contrast between text and its background (e.g. black text on a dark green band, below 3:1): change the text color, add a background, or move it.
- **T07** (warning) The font for vertical text has no vertical punctuation glyphs, so punctuation is placed with built-in offsets: switch to Source Han Sans / Serif, or confirm on the dieline confirmation image that the positions are acceptable.
- **B03** (warning) An image / graphic placed on one panel crosses a crease onto a visible neighbouring panel (e.g. a lid illustration's bleed drawn onto the side wall): pull the box back into the panel; for continuous artwork across panels use `--space body / blank`.
- **PH01** The layout still has placeholders: replace them with real images / graphics using `art fill`.
- **I01** Effective image resolution: < 300 ppi is a warning, < 200 ppi an error. Effective resolution = asset pixels ÷ placement size (inches); with cover cropping the tighter direction counts.

## Color

- **C01** No RGB / Lab may remain in print content.
- **C02** (warning) Total ink (sum of C+M+Y+K) above the limit (default 300%; flexo jobs use the profile's `flexo` section, initial value 240%).
- **X02** (warning) The profile has no ICC: bitmaps are converted to CMYK with a simple formula, **colors are not reliable**, and the PDF does not declare PDF/X-4.

## Vector graphics

- **V01** (warning) Some SVG colors have no CMYK given and are converted by the profile (a simple formula without ICC, not reliable). The report lists each color and its converted value; ask the user to confirm, or give exact ink values for brand colors with `--colors`.
- **V02** (warning) The SVG has a full-size white shape — white prints as paper white and covers the fill underneath. It is usually a white background from export; delete it from the SVG.
- Vector graphics and text are solid CMYK (knocking out the colors below); cut lines and finish spot colors sit on top and overprint.

## Spot colors and overprint

- **P01** Spot color names must match the profile (cut `Dieline_Cut`, crease `Dieline_Crease`, foil `Foil_Gold` …) and all must overprint.
- **P02** Registration color is not allowed. A cut line in Registration would print on every plate.

## Finishes (foil / spot UV / embossing)

- **F01** Finish layers must be 100% solid, no gradients, no halftones; no ink outside the given area.
- **F02** Minimum line width and gap (foil 0.2 / 0.3 mm, spot UV 0.3 / 0.5 mm, embossing / debossing 0.5 / 1.0 mm, white ink 0.3 / 0.5 mm; initial values).
- **P03** (flexo only) Lines thinner than 0.3 mm and reversed-out gaps narrower than 0.5 mm (thin inkless gaps, small white dots) in the print artwork are reported per panel as warnings: flexo screens are coarse and plates are soft, so thin lines break and narrow gaps fill in. The values are initial; ask the factory how fine it can go. Text is not checked (T05 handles minimum size). Woodcuts and fine-line illustrations usually trigger it on flexo; show the user where, and whether to simplify or have the factory confirm.
- **F03** Finish areas at least 2 mm from creases.
- Embossing / debossing needs a matching die; the spec sheet says so, and the die has to be confirmed with the die maker.

## PDF structure

- **X01** The print PDF must have TrimBox / BleedBox / MediaBox, OutputIntent, XMP and Trapped.
- A file that declares PDF/X-4 (when the profile has an ICC) still needs one manual Acrobat preflight; there is no automatic validator.

## Checks that did not run

Without Ghostscript, **B02 and F01 do not run** and the report says "not run". That is not a pass — tell the user.
