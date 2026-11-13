# Command reference


| Command | What it does |
|---|---|
| `env check` | Check dependencies / Ghostscript / fonts / profiles |
| `next` | Where the job stands, who it waits on, which command runs next and which reference to read (same source as the workbench's "Now"; run it before every round of work) |
| `styles list` | Box styles and their parameters |
| `job init` | Create a job and generate the dieline; `--set key=value` sets other style parameters (e.g. lock tongue width `--set tongue_w=30`); `--print-method offset\|flexo` (slotted boxes default to flexo) |
| `job init --fefco <code> / --ecma <code> / --recipe <file>` | Geometry-engine structures (style `structure` / multi-part `structure_set`); `--L --W --H` are inside dimensions; FEFCO defaults to material flute_b (`structures.md`) |
| `structures list / show / preview / suggest / check` | Structure catalog (FEFCO + ECMA): search by criteria, show one (with a recipe for given sizes), dieline image + 3D without a job, suggest 2–3 options for a need, practicality check (`structures.md`) |
| `structures files <code> --L --W --H --out <dir> [--format dxf\|cf2\|pdf\|svg] [--material]` | Dieline files without a job (the same functions as the delivery pack's dieline, with die corner radii); for when only the dieline is needed, no design |
| `structures list / show / editable … --lang en\|zh` | Language of the structure descriptions, closing steps, labels and editable parameters |
| `job liner <job> kraft\|white` | Change the corrugated liner (`job init --liner` works too): with kraft liner the dieline confirmation image and 3D show kraft color, and the spec sheet says so |
| `panels` | Panel map: size, orientation, safe area, suggested pixels |
| `window add / list / remove` | Windows on carton body panels (window film is the factory's job; `--film` only records whether there is film); a window that breaks the rules (too close to a crease / the glue area …) is not written to the job, and a fix is given |
| `concept gen / import / list / choose` | Concept images: generate / import (`--view main\|back\|flat --faces`; several flat candidates can be imported at once, only the one with the most accurate proportions is kept) / list / record the chosen direction and extract its main colors (main view only) |
| `concept confirm` | Record that the user confirmed the flats in use (`--by` names the user; only after the user clearly confirms; confirm again after swapping an image) |
| `concept prompt` | Write the image prompt without generating: `--step main` main view (`--design` `--text` `--scene` `--name`, same skeleton as `concept gen`), `--step back` back view (flat boxes seen from below), `--step flat` flats (single-part default is the body wrap; `--pairs` two panels per image, `--separate` each panel its own image; multi-part grouped by part; the file header gives canvas ratio and resolution tier); an SVG logo is saved as a PNG input image |
| `concept use` | Switch back to an older back view / flat after several versions (the newest is in use by default; only when the user says so) |
| `concept tag / rectify` | Tag older concept images with view and panels; straighten flats into one image per panel (for measuring) |
| `visual import / gen / prepare` | Print assets: import / generate (no text) / prepare |
| `visual trace` | Trace to vector SVG; without `--method` it picks by the image — whole illustrations with vtracer full-color tracing (colors converted with ICC), line art, icons and flat-color graphics traced by color area (`--palette` snaps to brand colors, `--lines` traces thin lines as even strokes; dense one-color line art is traced with vtracer and merged into a few `--tones`, lightened overall with `--strength`; `--erase` paints out an area before tracing) |
| `restore graphics` | Measure positions and sizes of icons, logos and dividers from the flats (aligned columns move together, finishes made from them follow); `--apply` writes them to the job. Run it before `restore text` |
| `restore photo` | Redraw a photo from the flat: `restore photo <job> <placement>` cuts the reference and writes the prompt (no generation); after generating, `--image <image>` brings it back, prepares it, swaps the asset and records the source |
| `restore frames` | For rectangular photos, measure the full extent from a visible neutral frame first; the photo, vectors in the same frame and linked finishes move together. No frame, missing edges or custom clipping are not guessed. Only `--apply` writes back, and it rolls back if new preflight issues appear. Run it before cutting the photo reference |
| `restore rows` | Repeated selling-point rows: only when the count and order of long colored dividers are reliable, align the group of lines first, then measure icons and text within each row; `--fonts` allows installed fonts, `--apply` writes back with preflight rollback. Shared styles, wording, stacking and linked finishes are not changed |
| `restore colors` | Fit reliable text and one-color small icons to the current ICC, liner preview and total ink; keeps the given key colors and linked finishes, refuses without ICC. `--apply` writes local colors and rolls back on new preflight issues. `COLOR_LIMIT` means the difference is still above target after limited sampling; it is not a strict gamut proof or a press result |
| `restore text` | Measure text size, line spacing and position from the flat (each level set together, line breaks unchanged, kept inside the safe area); `--apply` writes to the job (backed up to `history/` first), blocks new overlaps with later vector / solid layers using real glyphs, never changes stacking; `--fonts` swaps fonts too (by default only suggests; only with the user's consent), `--tracking` measures tracking of single-line text, multi-line body text is left alone. Fonts must already be in the font folder or added to the job with `font add`; system fonts are never downloaded or imported automatically |
| `restore face` | A single panel (top, bottom, mailer panels): straighten that panel of the flat to its real size and remove text, for `visual trace`; the output includes the box to place it back on the panel |
| `restore strip` | Join the body panels of the flat into a wrap at real size (text removed, fold lines removed at joins, small offsets aligned), for `visual trace`; `--erase x,y,w,h` removes leftover text, `--keep <panel>` uses that panel as the reference when joins are badly offset |
| `font add / list` | Add the user's brand fonts, view licence information |
| `content import / add / edit / remove / list / confirm` | Content list: records word for word what goes on the pack; the user confirms the wording |
| `conventions list / show` | Category conventions: what usually goes on each panel (hints, not rules) |
| `art add` | Background fill / pattern / image / vector graphic / text (`--tag` labels the information type, `--before` inserts under a placement; `--vertical` vertical text; `--skew-y` / `--skew-x` skew, see fonts.md; multi-part styles use full panel names like `lid.top`, or `--part`) |
| `art move / remove` | Change stacking order / remove a placement |
| `art same-face` | Same front and back: one panel shares another's layout |
| `art set` | Change a placement (box, panel, style, color, clipping shape …; `--clear` removes a field); id and stacking order stay |
| `art fill` | Replace a placeholder with a real image / graphic (position and stacking unchanged; keeps the placeholder's shape and linked content item) |
| `style set / list / remove` | Text styles (levels): heading, body, small print … changing a style changes the whole level |
| `plan` | Copy layout draft: text per panel, layout sketches, layout brief, placeholder target sizes, checks; `--confirm` records the user's confirmation |
| `finish add`, `finish list / set / remove` | Local finishes: named spot colors, overprinted; real outlines from SVG / PNG, or `--placement <text id>` from set text |
| `finish fit` | Make finish graphics pass F02 (remove spikes, fill narrow gaps); writes a candidate and a comparison image first, `--apply` after the user agrees |
| `attach add`, `attach list / set / remove` | Attachments (metal badges): made separately and stuck on with adhesive, not printed, no finish plate; shape + relief SVGs, shown on the confirmation image, a placement layer, the spec sheet, 1:1 artwork and 3D |
| `color add / list / remove` | Key colors: name, Pantone, print CMYK, where used (adding the same name again changes it); go into the spec sheet and the callout layer, K01 checks whether the layout uses them |
| `surface add / list / set / remove` | Lamination / varnish: coverage (default the whole sheet, no knock-out at bonding areas), explicit knock-outs when the factory asks; no spot color is generated |
| `render` | Dieline confirmation image (flat + cut lines + dimensions + finish callouts, `preview.png`) and dieline SVG, no checks; this is what the user confirms |
| `studio start / status / stop / serve` | Local workbench: a web canvas on this machine showing every output by stage, refreshing live |
| `notes list / add / reply / resolve` | Notes the user wrote on workbench images (with position and snapshot); the agent checks them every round and replies after changing things; `resolve` only when the user says it can be closed |
| `zoom` | Read small text on the confirmation image: a 200 DPI crop of a panel (`--panel`) or area (`--box`), saved to `studio/zoom/`; the open-source edition asks the cloud for it (watermarked, with a usage limit) |
| `flat` | Compare panel by panel with the flats (for the agent); `--diff` writes the differences, shown next to the dieline confirmation image on the workbench |
| `mockup` | 3D mockup (view only; `--require` declares information that must appear) |
| `approve` | Record the user's confirmation of the dieline confirmation image (the only gate before export); `--status` only shows it |
| `preflight` | Preflight check, writes the report |
| `export` | Delivery pack (production files + spec sheet + die DXF) and archive (confirmation image `confirm.png`, proof, preflight report …); refused with errors, and without confirmation when `--require-approval` is given |

Every command supports `--json`, printing `{ok, command, job, artifacts, data, errors, warnings}`; exit codes are 0 success, 1 errors, 2 bad arguments, 3 missing environment.

## Cloud account (open-source edition)

| Command | What it does |
|---|---|
| `login` | Sign in to the pacxio cloud service: gives a web address and an 8-character code; the user signs in in their own browser with an email code (the first time signs them up), checks it and clicks Approve; the token is saved in `~/.pacxio/cloud.json`. Agents use `--no-wait`: it returns right after giving the address and code, and once the user has approved, run the cloud command again. Without a sign-in, the first cloud command starts it automatically (`CLOUD_LOGIN`) |
| `logout` | Sign out: revokes this computer's token on the server and deletes the token saved locally |
| `cloud status` | Service address, signed-in email, whether it is reachable, whether closed commands run locally or in the cloud |

Usage, connected computers and account deletion are on the web: `https://api.pacxio.dev/account`.

## MCP (agents call tools instead of typing commands)

- Install: `pacxio setup --agent claude-code` (or `codex`; without `--agent` it installs into every agent found on this machine) installs the pacxio plugin (this Skill + MCP) into the agent, downloads Source Han Sans if it is missing (about 33 MB, needs the network), and checks itself at the end; without `--yes` it only lists what it would do. A Skill / MCP installed separately before is reported in the warnings with the command to remove it, which runs only with the user's consent. `pacxio mcp doctor` checks the setup. To register only the MCP (e.g. a project `.mcp.json`), `pacxio mcp install --client …` still works.
- Tools: 8 structure tools; `job_next`, `studio_open`; `pacxio_run` (any command, same as the command line above, `--json` added automatically); `pacxio_help`.
- **Only the user confirms**: `pacxio_run` cannot run `content confirm`, `plan --confirm`, `concept choose / use / confirm` or `approve`. After the user clearly says they confirm in the conversation, use `user_confirm` with the user's own words in `user_words`; vague words ("looks good") do not count.
