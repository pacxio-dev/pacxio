# Factory profiles

Every value that "differs by factory" lives in a profile; none is hard-coded. The default profile `default` is `verified: true`: it passed a physical proof by the user and its values were confirmed by the factory. Exceptions that are still initial values: the CF2 line type numbers (CF2 was not part of that confirmation) and `registration_tolerance_mm`. For another factory, create a new profile and keep `verified: false` until it is confirmed.

## Where they are

The engine looks for `<name>.yaml` in this order: the `PACXIO_PROFILE_DIR` environment variable → `profiles/` in the current folder → the copy installed with the package. To make a profile for a factory, copy the default profile to `profiles/<factory>.yaml` in the current folder, change the values, then `job init ... --profile <factory>`.

## Main fields

| Field | Meaning | Default |
|---|---|---|
| `verified` | Confirmed by the factory. Set to true only after confirmation | false |
| `bleed_mm` | Bleed | 3 |
| `safe_margin_mm` | Distance of text / logos from cut and fold lines | 3 |
| `glue_flap.ink` | Glue flap: `none` no ink at all / `bleed_only` ink allowed inside the crease / `full` no limit | bleed_only |
| `glue_flap.bleed_into_mm` | Width where ink is allowed with `bleed_only` | 2 |
| `tac_limit_pct` | Total ink limit | 300 |
| `flexo.tac_limit_pct` / `flexo.min_text_pt` | Total ink limit and minimum text size for flexo (water-based on corrugated): used instead of the offset values when the job's print method is flexo | 240 / 8 (initial values) |
| `window.*` | Windows: minimum window size, minimum corner radius, distance from creases / glue lines, gap between windows (window film is the factory's business) | see default.yaml |
| `icc.path` / `icc.name` | Output color profile. **Required to declare PDF/X-4** | empty |
| `finish_rules.*` | Minimum line width and gap for foil / spot UV / embossing (debossing shares them) / white ink, distance of finishes from creases | see default.yaml |
| `spot_names` | PDF spot color names | `Dieline_Cut` … `Foil_Gold` … |
| `dxf.layers` | DXF layer names and colors | CUT / CREASE … |
| `cf2.linetypes` | CF2 line type numbers | cut 1, crease 2, perf 3, partial_cut 4 |
| `compensation` | Thickness compensation factors (compensation = factor × board thickness) | initial values, calibrate with a proof |
| `deliverables` / `archive` | What goes into the delivery pack (default: production file, spec sheet, die DXF) / what only goes into the archive | see default.yaml |

## ICC

The user does not need to understand ICC, and you should not ask them to pick one:
- **Default (no `icc.path`)**: a standard ICC already on the machine is chosen from the job's paper — white board, white-top grey board, no paper given, and litho-laminated corrugated count as coated (Japan Color 2001 Coated, then Coated FOGRA39); kraft board and flexo corrugated count as uncoated (Japan Color 2001 Uncoated, then Uncoated FOGRA29). They come with Adobe software; `env check` shows which was found. It is only used to convert photos to print colors: it is not embedded, PDF/X-4 is not declared, and total ink in photo shadows is pulled under the limit automatically. X02 is an info line in the report.
- **No standard ICC on the machine**: photos are converted with a simple formula and X02 warns "check colors on a proof"; pass it on as it is.
- **The factory gives its own ICC**: first make sure its **license allows redistribution with the delivery pack**, then set `icc.path` (ICC files never go into a code repository). It is then embedded in the production file and PDF/X-4 is declared; a declared file still needs a manual Acrobat preflight.
- Colors are final only on the proof: after proofing the factory adjusts on press; files are not changed back and no sign-off record is kept.

## When the user says "the factory requires …"

Map the requirement to profile fields, change the profile, and tell the user which fields changed. **Never** relax a rule in the profile just to pass a check; if a rule fails, change the design. The user decides when a factory-confirmed profile becomes `verified: true`.
