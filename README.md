# pacxio

Packaging prepress for coding agents. Describe a box, and pacxio turns box dimensions and brand artwork into the files a packaging factory accepts:

- the dieline: DXF, CF2 and PDF;
- a CMYK print PDF;
- named spot-color plates for foil, spot UV and embossing;
- a spec sheet;
- a preflight report.

pacxio runs inside Claude Code or Codex as a plugin (a workflow Skill plus MCP tools), and as a plain command-line tool.

## What runs where

| On your machine (this repository, MIT) | On the pacxio server (sign-in required) |
|---|---|
| Structure library: FEFCO and public ECMA codes, plus hand-built carton, mailer, label and pouch entries | Composing artwork onto the dieline (`render`, `art`, `restore`, `visual trace`) |
| Dieline geometry, editable parameters, DXF / CF2 / dieline PDF | Print PDF, spot-color separations, preflight |
| Folding 3D preview and the local workbench (127.0.0.1 only) | The factory delivery pack (`export`) |
| Jobs, content lists, concept-image records, notes | |

The open client sends server-side commands to the pacxio server automatically. Every command prints the same JSON either way. The first such command starts a browser sign-in (`pacxio login`). Your vector artwork stays on the server; previews, reports and the delivery pack come back to your job folder.

## Install

You need [uv](https://docs.astral.sh/uv/).

```bash
uv tool install pacxio
```

```bash
pacxio setup --agent claude-code --yes
```

Use `--agent codex` for Codex. Leave out `--agent` to install into every agent found on the machine. Setup also downloads the open-source Source Han Sans fonts if they are missing. Restart the agent afterwards, open a project folder, and describe the box you want.

## Command line

```bash
pacxio structures suggest --use ecommerce --no-tape --material corrugated --json
pacxio structures preview 0427 --L 233 --W 161 --H 66.5 --out preview/
pacxio job init jobs/demo --fefco 0427 --L 233 --W 161 --H 66.5 --material flute_e
pacxio studio start jobs/demo --open
pacxio next jobs/demo
```

- `structures suggest` picks candidate structures from the library for what the box has to do.
- `structures preview` draws a dieline and a 3D view without making a job.
- `studio start` opens the local workbench in your browser.
- `next` tells you which step comes next and which command runs it.

Every command takes `--json` and prints `{ok, command, job, artifacts, data, errors, warnings}`. Each error carries a `fix`. Exit codes: `0` success, `1` an error was found, `2` bad arguments, `3` something is missing from the environment.

Messages are in English. Set `PACXIO_LANG=zh` for Chinese.

## Rules the engine enforces

- **Text in print files is typeset by the engine and outlined.** AI-generated images in print files must not contain text.
- **Concept images are for direction only.** The engine refuses to place a concept image in a print file. The final artwork is rebuilt from the confirmed flat.
- **The user confirms; the agent does not.** `approve` on the dieline confirmation image is the only gate before export.
- **A preflight error means no factory files.** `export` refuses until every error is fixed.
- **Dielines use lines and arcs only.** No Béziers, so DXF R12 and CF2 stay exact.
- **Cut and crease lines are named spot colors set to overprint.** They are never drawn in Registration.
- **Values marked as initial are not verified.** Structures that are not calibrated against a factory dieline say so. Make a physical proof before a production run.

## Development

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run mypy
```

```bash
uv run pacxio env check
```

Separation tests are skipped when Ghostscript is not installed. Fonts and ICC profiles are not part of this repository.

## License

MIT. See [LICENSE](LICENSE).
