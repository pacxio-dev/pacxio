# Common errors and fixes

Every error / warning carries a `fix` field; follow it first. Below is a quick reference by code.

## Commands and environment (exit codes 2 / 3)

| Code | Meaning | What to do |
|---|---|---|
| `JOB` | `job.json` not found or unreadable | For an existing project check the path and the read error first, never re-initialize over it; `job init` is only for new projects |
| `ARG` | Bad argument (missing required, out of range, bad format, duplicate id) | Fix the argument as the message says |
| `ENV` | Missing environment (dependency, API key, profile) | Install / set it as `fix` says; **never install at runtime** |
| `ICC` | The profile's ICC file is missing / unusable | Check `icc.path` |
| `NET` | Cannot reach the image service | Check the network; the Codex sandbox has no network by default; or use `visual import` instead |
| `PROVIDER` | The image service returned an error (401 wrong key, 429 quota, 400 content policy) | Follow `fix`; if it keeps failing use `visual import` |
| `ASSET` | Asset file missing / unreadable / duplicate name | Check the path; change `--name` |
| `PANEL` | Panel does not exist | See available panels with `panels` |
| `FONT` | Font missing, missing glyphs, or font refused (restricted / variable font) | Brand fonts via `font add`; for missing glyphs add a fallback in `--font` (`BrandFont,SourceHanSansSC-Bold`); missing Source Han Sans: see `check_env.py`; **never add `--accept-license-risk` yourself**, see `references/fonts.md` |
| `FINISH` | Problem with the finish type / source file | Types are listed in the `finish add` message; SVGs cannot contain `<text>` or embedded bitmaps (outline them first) |
| `CLOUD_LOGIN` | Not signed in to the pacxio cloud service yet (first cloud command in the open-source edition) | Give the user the address and code from the error as they are; the user signs in in their own browser (email code; the first time signs them up), checks the code and clicks Approve; when the user says it is done, run the same command again. The agent never signs in or approves for the user |
| `CLOUD_LOGIN_DENIED` / `CLOUD_LOGIN_EXPIRED` | The user clicked Deny, or the code was not approved within 15 minutes | Run the command again for a new code and ask the user again |
| `CLOUD_AUTH` | The token in the `PACXIO_CLOUD_TOKEN` environment variable is invalid | Remove the variable, then `pacxio login`; an invalid saved token starts a new sign-in automatically (`CLOUD_LOGIN`) |
| `CLOUD_NET` | Cannot reach the cloud service | Check the network; `pacxio cloud status` shows the service address; try again later |
| `CLOUD_BUSY` / `CLOUD_LIMIT` | This job still has a command running in the cloud / the crop limit is used up | Wait for it to finish; `render` again, then `zoom` |

## Preflight (`preflight` / `export` exit code 1)

| Code | Typical cause | Fix |
|---|---|---|
| S01 / S02 | Dieline geometry is wrong | Record style, dieline version, size, parameters and the location, and report to the developers; never edit cut lines by hand or tweak parameters to make it look right |
| S03 | Style parameter out of range | Change it into range as the message says |
| T01 | Text / logo too close to an edge | Move it as the report says ("move X down N mm"), or make it smaller |
| T02 | Text overflows its box | Adjust the box, columns, panels or size first (still readable); if copy has to be cut or changed, list suggestions and change the list only after the user agrees |
| T03 | Fonts in the PDF | Should not happen; report to the developers |
| I01 | Asset resolution too low | Upscale with `visual prepare --target-mm`; use a higher-resolution asset; make the placement smaller |
| B01 | Artwork does not fill the bleed | Use a full-bleed background / pattern, or extend the asset past the bleed edge |
| W02 | Window too close to a crease / the outline / the glue line / another window | Move the window as the error says (e.g. "move right 3.0 mm") or make it smaller |
| B02 | Ink on the glue flap | Pull the artwork back out of the glue area |
| C01 | RGB left over | Report to the developers; does not happen in the normal workflow |
| C02 | Total ink over the limit | Use K black for dark colors, reduce CMY overlap |
| P01 / P02 | Wrong spot color name / no overprint / Registration used | Should not happen; report to the developers |
| F01 | Finish layer is not solid | Finish graphics must be pure black and white without gradients |
| F02 | Finish lines too thin / gaps too narrow | First `finish fit <job> <finish id>` (comparison image, `--apply` after the user agrees); if that is not enough, thicken / widen gaps or simplify the graphic (`finishes.md`) |
| F03 | Finish too close to a crease | Move it as the message says |
| X01 | Incomplete PDF structure | Export again; if it persists, report to the developers |

## Warnings

| Code | Meaning |
|---|---|
| X02 | No ICC; colors use the fallback conversion |
| `PROFILE` | The profile has not been confirmed by the factory |
| `ORIENT` | The upright direction of lids and tucks has not been confirmed by a proof |
| `CF2` | CF2 has not been verified in die-maker software |
| `DXF` | DXF R12 has no unit header variable; units are mm by convention |
| `SKIPPED` | Some checks did not run (e.g. no Ghostscript); **that is not a pass** |
| V01 | Asset aspect ratio differs from the target area by > 5% |
| V02 | Tiled asset has seams (a fix was attempted) |
| V03 | The image seems to contain text, or could not be checked |
| V04 | The asset was upscaled (Lanczos adds no detail) |
| V05 | Chroma keying removed almost nothing; the background may not be pure green |
| `BLEED` | Part of the bleed band is filled by stretching the edge |
| L01 | A brand font is used: the licence is doubtful (restricted but confirmed / preview & print only / no licence information), or it is only a licence record (info) |

## Dieline confirmation image, mockups and confirmation

| Code | Meaning | What to do |
|---|---|---|
| MK01 | Required information is missing; cannot be confirmed | Add it, label it with `--tag` and `render` again (`--require` is declared with `mockup`) |
| MK02 | Content placed on a panel that cannot be seen | Confirm it is meant to be inside the box |
| MK03 | Top / bottom direction not confirmed (only for new styles without a proof) | Tell the user as it is and wait for the proof's direction test |
| MK05 / MK06 | Panel without information / text without a label | Ask the user whether to add some; add labels |
| `APPROVE` | The design has not been confirmed (dieline confirmation image), or it changed after confirmation (stale) | `render` → let the user check and confirm the dieline image → `approve`; **never confirm for the user** |
| MK07 | No chosen concept image, so the confirmation page has no comparison with the design direction | `concept gen` / `concept import` first, then `concept choose` after the user picks |
| `CONCEPT` | Tried to use a concept image itself as a print asset / finish | Not allowed: **rebuild** from the concept — text with `art add --kind text`; graphics on the flats traced with `restore strip` → `visual trace` (with no flat to trace, generate a text-free asset with `visual gen`) |

## Exit codes

0 success · 1 errors (checks failed, style constraints not met) · 2 bad arguments · 3 missing environment (dependency / API key / network).

## Vector graphics / clipping / across panels

| Error | Cause | Fix |
|---|---|---|
| `SVG` … gradient or pattern / transparency / text / embedded bitmap / clip-path | The SVG contains something the print file cannot reproduce faithfully | Handle it in design software as `fix` says (outline text, flatten transparency, release clipping masks, expand strokes); make gradient effects a bitmap asset |
| `SPACE` … wider than the body wrap | The `--space body` box is wider than the wrap's circumference | Keep the box width within the wrap circumference shown by `panels` |
| `SPACE` … space=body not supported | This style's body is not one horizontal strip | Use `--space blank` or place per panel |
| `ARG` … do not give panel | With `--space body / blank` the box belongs to no panel | Remove `--panel` |
| T04 text covered | Text sits under an image / graphic added later | `art move <job> <text_id> --top` |
| V01 colors converted automatically | SVG colors have no CMYK given | `--colors '#hex=c,m,y,k;...'` (values from the brand guide) |
| V02 full-size white shape | The SVG has a white background | Delete the white background from the SVG |
