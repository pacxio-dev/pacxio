# Dieline confirmation image, 3D mockup and confirmation

## Two things, each with its own job

| | Dieline confirmation image | 3D mockup |
|---|---|---|
| Command | `pacxio render` | `pacxio mockup` |
| File | `preview.png` (multi-part styles `preview_<part>.png`) | `mockup/` |
| Workbench | ⑤ Dieline confirmation | ⑥ 3D mockup (only the rotatable 3D) |
| What the user sees | The flat + cut / crease lines + each panel's name and width × height + overall blank size + finish outlines, numbers and legend; preflight boxes on top | What the box finally looks like, turnable |
| Confirmation | **Confirmed: the only gate before export** (`approve`) | View only, not confirmed |

Both come from the **same job** as the print files: the artwork, text, foil / spot UV on every panel are exactly what will be printed, not AI-drawn illustrations.

Flow: **design → confirm the dieline image → (look at the 3D mockup) → preflight → export**. (Older records confirmed the mockup instead; the record format is the same and old confirmations stay valid.)

The "mockup summary sheet" below is `00_mockup_sheet.png`, titled "static finish illustration"; highlights in static files are fixed, not screenshots of the interactive 3D. It is for summary and archive only and adds no confirmation step.

## Making the dieline confirmation image

```bash
pacxio render jobs/<name>
pacxio flat jobs/<name> --diff "Title smaller than on the flat" --diff "…"   # with flats: compare panel by panel and write the differences
```

`preview.png` from `render` (multi-part styles `preview_<part>.png`) is the **dieline confirmation image**: the flat + cut / crease lines + each panel's name and width × height (the size of that panel on the dieline; the tag sits on the panel's top edge, off the main text) + overall blank size + finish outlines, numbers and legend; the area a window cuts away is drawn as a checkerboard. With "Cut lines" turned off, workbench ⑤ shows the artwork alone (no callouts), for checking artwork joins on folds. Workbench ⑤ shows it with preflight boxes on top and the `flat --diff` differences beside it. `flat` is your tool for comparing with the flats (`flat/vs_flat_<panel>.png` is for you only, no need to send it to the user); `--diff` lists each difference from the flat (fonts, why colors changed, real copy longer than the placeholder text, items pending or waiting for the factory).

**Open-source edition (cloud compose)**: the confirmation image that comes back to this machine is a 100 DPI watermarked preview (3D textures are lower resolution too); everything can still be viewed on the workbench, and the original stays on the server. To read small text, `pacxio zoom jobs/<name> --panel <panel>` (multi-part: `part.panel`; or `--box x0,y0,x1,y1`, relative 0–1) asks the cloud for a full-resolution (200 DPI) crop, saved to `studio/zoom/`, watermarked, at most 24 per confirmation image; clicking "Read small text" after zooming in on workbench ⑤ does the same. With the closed package installed the confirmation image is the original, and `zoom` crops locally.

Go through it with the user: are text and images laid out correctly, are sizes and finish callouts right. Fix the job if not and `render` again. After the user clearly confirms, record the confirmation, see "Confirmation" below. It cannot be confirmed while required information is missing, wording is pending or placeholders remain (MK01); when the design changes the confirmation is void — `render` again and ask the user to confirm again.

Finish fields, area highlighting, materials and lighting: [finishes.md](finishes.md).

Outputs in `mockup/`:

| File | Use |
|---|---|
| `scene.json` + `scene/` | Fold geometry and the whole dieline texture for the turnable 3D (used by the workbench) |
| `00_mockup_sheet.png` | Mockup summary sheet (concept and mockup side by side, 3D views, the content list by panel; flat views of each panel duplicate the flats step and are left out): archive; can be sent to users who do not use the workbench |
| `view_*.png` `face_*.png` | 3D views and flat views of each panel |
| `manifest.json` | Design fingerprint, information labels, checks, labels declared with `--require` |
| `approval.json` | Confirmation record (only after `approve`; what is confirmed is the dieline image) |

## Information labels: making sure the confirmation carries everything

Give every text, image and finish a `--tag`; the summary sheet groups them by label:

```bash
pacxio art add jobs/x --kind text --tag brand_name --panel front --text "TIDEWELL" ...
pacxio art add jobs/x --kind text --tag net_content --panel back --text "Net wt. 320 g" ...
pacxio finish add jobs/x --type foil_gold --tag brand_mark --source logo.svg ...
```

Common labels: brand name, brand mark, product name, description, net content, ingredients, manufacturer (with address), shelf life, production date, barcode, certification marks, directions, contact. Labels are free text in the user's own words.

**Which information must be on the pack depends on the category and market regulations, which the engine cannot judge.** Ask the user, who is responsible for it; pass the answer to the engine with `--require`:

```bash
pacxio mockup jobs/x --require brand_name,product_name,net_content,manufacturer,shelf_life
```

New projects record required items with `required` in the content list first; `--require` above is only for projects that check by free labels, using the requirements the user has set rather than the example.

With `--require`, run it **before** the dieline image is confirmed; when a declared label is missing, `mockup` marks MK01 and `approve` refuses a new confirmation. When making the mockup again, pass back the `required` list from `mockup/manifest.json`, otherwise a `mockup` without options clears the declaration. The engine does not revoke a valid confirmation when `--require` is added later, so never `approve` first and add required items afterwards. Labels match by **exactly the same text**. Required items in the content list (not on the layout, wording pending) and placeholders block confirmation too.

When the brand name is only shown as a logo and the user accepts that, do not make separate brand-name text a required label (MK12 is only a reminder).

## Confirmation

After the user has looked at the dieline confirmation image and **clearly says** they confirm (or the user clicked "Confirm dieline" on workbench ⑤ themselves):

```bash
pacxio approve jobs/x --by <who> --note <note>
```

- **Never confirm for the user.** Without a clear "confirm / that's fine" from the user, do not run `approve`.
- The record is bound to the **design fingerprint** (job.json + referenced asset files + font files used). If anything in the design changes after confirmation, the fingerprint no longer matches and the status becomes stale: `render` again and ask the user to confirm again. `approve --status` shows the state: `none` (no confirmation image yet) / `pending` (made, not confirmed) / `approved` / `stale` (design changed, expired).
- Confirmation does not need the 3D mockup first; making a mockup or not does not affect it.
- Without confirmation `export` gives an APPROVE warning; with `--require-approval` it refuses. The confirmed image goes into the archive next to the delivery pack (`<pack>_archive/confirm.png`, multi-part `confirm_<part>.png`).

## Checks

Codes in the `mockup` output and on the summary sheet:

| Code | Meaning |
|---|---|
| MK01 (must fix) | Information declared with `--require` is missing; cannot be confirmed until it is added |
| MK02 | Content on a panel that cannot be seen (tuck / dust flap / ear / glue flap); on the inside panels or rolled edges of a mailer it is info: hidden when closed, visible when the box is opened |
| MK03 | **The artwork direction on top / bottom panels has not been confirmed by a proof**; the real box may be rotated 90° or 180° (kept with the confirmation record) |
| MK04 | The mockup is an illustration: colors are not accurate, sheen is simulated |
| MK05 | Some panels only have a background or pattern, no information — ask the user whether they should have content |
| MK06 | Text without a label, grouped as "unlabelled" on the confirmation page |
| MK07 | No chosen concept image, so the confirmation page has no comparison with the design direction |
| MK08 (must fix) | Required items in the content list are still pending (a draft or placeholder on the layout); cannot be confirmed until settled |
| MK09 | The content list wording is not confirmed, or changed after confirmation |
| MK10 | Text not linked to the content list |
| MK11 (must fix) | Placeholders remain in the layout; cannot be confirmed until they are replaced by real images / graphics |
| MK12 | The brand name only appears as a logo, with no separate brand-name text — a reminder only, it does not block confirmation; adding it is the user's call, do not press |
| MK13 | Windows: what shows through is an illustration of the box interior (not the product), and filmed windows get a reflection; what can really be seen depends on the sample box |
| MK14 | Multi-part styles: a lid can go on rotated 180° and an insert can go in front-to-back — remind the user when artwork on lid and base (insert and sleeve) has to line up front and back |

With a chosen concept (`concept choose`), the top of the summary sheet puts **the concept and the exact mockup side by side**; with a back-view concept (`concept import --view back`) another row compares it with the mockup from the same angle (back + left side). Use it to point out differences (fonts, graphics, proportions, colors) to the user.

## Limits to tell the user plainly

- **Text direction on top / bottom panels**: the top panel reads the way the box is opened — **upright when read from the front (the side where the tuck opens)**, so the top lid's artwork appears upside down on the flat, which is correct. The bottom panel follows the flat's direction. Snap-lock bottoms fold along the real outlines of the four bottom flaps, side flaps under the main flaps and the lock tongue under the notched flap, so parts of the side flaps may show; auto-lock bottoms still use a simplified illustration with half of the front and back main flaps each. Bottom flaps hide things, so never put key information only on the bottom. The lid directions of the 8 original styles have been confirmed by proofs; new styles get MK03 until confirmed, which must be passed on to the user.
- **Color**: with an ICC the screen soft-proofs with it (both the confirmation image and the 3D mockup, close to the printed result); without one a simple formula is used, too bright and dark. Neither is color-accurate; the proof decides. When hand-set CMYK values (brand color blocks, text colors) look wrong on the confirmation image, that is how those values print, so change the values.
- **Finishes**: the metallic sheen of foil / silver / holographic foil, the gloss of spot UV, the relief of embossing / debossing are simulated; the real result depends on material and process. On the dieline confirmation image finishes are outlines + numbers + a legend, showing position and type.
- Neither the confirmation image nor the mockup replaces a proof. Proof before mass production.
