# Continuing an existing project

For when the user gives an existing job, switches agent or conversation, or asks to continue, revise or handle notes. Restore the state first, then decide what this round does.

## 1. Find the current job

Use the folder the user names; without a path, look at the candidates under `jobs/` in the current project. `history/` holds snapshots and `out/` holds delivery output; neither is the current job. If several candidates cannot be told apart, ask the user which one only.

Read the folder's `job.json`: style, inside / outside size basis, material, print method, content list, text styles, placements, finishes, windows. Read `manufacturing-notes.md` too when it exists; lamination / varnish requirements may only be written there.

Do not `job init` an existing job again, and do not re-import or replace its content list. Check the environment for that folder first; the repository source and the installed CLI are two copies, so engine changes need a reinstall through the development process — never install anything while the Skill runs.

## 2. Restore the state with read-only commands

Replace `jobs/<name>` with the job's real path; these commands confirm nothing and change no content.

**Run `pacxio next jobs/<name>` first**: it shares its source with the workbench's "Now" and says where the job stands, who it waits on and what runs next. Use the commands below for detail.

```bash
pacxio panels jobs/<name> --json
pacxio content list jobs/<name> --json
pacxio content confirm jobs/<name> --status --json
pacxio concept list jobs/<name> --json
pacxio approve jobs/<name> --status --json
pacxio notes list jobs/<name> --open --json
pacxio studio status jobs/<name> --json
```

- **Wording**: `confirmed` from `content confirm --status` carries over; `stale` means the list changed, not that the content is wrong. For an old job without a list, read the existing text first, then decide from this round's task whether to build one.
- **Tracing back to the source**: a confirmed list may hold corrections or chosen spellings the user accepted. When comparing with the original document, read the confirmation and change notes first; never revert just because the two differ. When the user gives new copy, update it as instructed and then handle the expired wording confirmation.
- **Direction and references**: `chosen` in `concept list` is the chosen main view and `in_use` is each group's current reference; keep both, never swap images by file name or modification time, and do not `choose` again.
- **Dieline image**: from `approve --status`, `approved` means the current design and dieline are confirmed; `pending` means the current image waits for confirmation; `stale` means the design or dieline changed after the image was made; `none` means there is no confirmation image yet. Act on the status; never say it is confirmed just because `preview.png` or `approval.json` exists.
- **Draft and 3D**: read `plan_confirmed.json` and `mockup/manifest.json` if they exist and check the state on the workbench. An expired draft is only a record; an expired 3D view is made again from the current job and does not affect a dieline confirmation that still holds. When making `mockup` again, pass back the manifest's `required` labels.
- **Notes**: look at open notes and their snapshots first and find what to change by placement id; after the change `notes reply`, and the user closes the note. Details in `studio.md`.

Reuse the workbench address if it is running; start it if it is not and the user has not declined it. Desktop panel first, default browser as fallback; see `studio.md`.

The workbench may have received a choice or confirmation after the previous agent stopped waiting. Those saved decisions still count; restoring the connection does not require the user to confirm again. If `job_next` says `who: agent`, continue the authorized next action. If it says `who: user`, show the review that remains and call `studio_wait` with the returned `revision` to receive the next workbench decision in this turn. Follow the timeout, cancellation and reconnect rules in `studio.md`; a closed conversation is not an active wait connection.

## 3. Continue from the current stage

First tell the user briefly: which stage this is, which confirmations still hold, what this round will change. Keep decisions that still hold; the user's new instructions can change the matching parts, without starting the brief again.

| Situation | Next step |
|---|---|
| Only copy waits for checking | List the original text with spelling / grammar suggestions; change the list only after the user accepts |
| A direction is chosen and the final artwork is in progress | Keep the references in use, apply this round's comments with `art set` / `style set` etc., then update the affected outputs |
| Dieline image `none` / `stale` | `render`; with supported flats also `flat --diff`; show the current image and wait for the user to confirm |
| Dieline image `pending` | Show the current image and its issues; record `approve` only if the user already clearly confirmed this version, otherwise wait for their decision |
| Dieline image `approved`, only 3D or export left | Update what is needed, run preflight, then `export --require-approval` |
| `tray_lid` / `drawer` | Work by `part.panel` and check each part's confirmation image; back views, flats, `restore face lid.top` and the `flat` comparison work as usual (full panel names) |

After changing text, images, size, structure or finishes, regenerate the affected images as the commands report and handle expired confirmations. Stages that did not change and are confirmed need no new confirmation. History snapshots and old images are kept; never copy them over the current design to clear an expired state.
