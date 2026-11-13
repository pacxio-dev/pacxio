# Preflight and the delivery pack

## Preflight

```bash
pacxio preflight jobs/<name> --json
```

If there are errors, fix each one by its `fix` (`references/troubleshooting.md`) and run it again until there are none. The rules in plain words: `references/prepress-rules.md`.

## Export and explain it

```bash
pacxio export jobs/<name> --proof --require-approval
```

The delivery pack is in `jobs/<name>/out/<name>_<style>_<L>x<W>x<H>/`. Send the whole folder to the factory. It usually holds three files (multi-part styles get one production file and one DXF per part, with the part name in the file name):
- `<name>_production.pdf`: the production file, in layers (print artwork / cut / crease / each finish / glue areas / callouts). Callouts sit only outside the bleed: panel sizes, blank size, line type and finish legend, each in its own non-printing spot color. Layers can be toggled only in Acrobat; Illustrator opens it as one layer, told apart by spot colors;
- `<name>_spec.pdf`: the spec sheet (every check, and "before this goes to the factory");
- `<name>_dieline.dxf`: the die DXF (units mm).
- With attachments (metal badges) there is also `<name>_attach_<id>.svg`: the 1:1 artwork for the badge maker (`references/finishes.md`, "Attachments").

After export, give the delivery folder and the **full local path** of every file in the chat (absolute paths the user can open or copy directly); one path is enough for the archive folder. The workbench's "Files" panel lists the delivery pack too.

The `…_archive/` folder next to it is the archive and does not go to the factory: the confirmed dieline confirmation image, the 1:1 proof from `--proof`, the full preflight report, previews, separate dieline PDF / CF2 / finish plates, job.json; mention it in one sentence. **Pass on every warning as it is.** End with the reminder: **make a physical proof before the production run**.
