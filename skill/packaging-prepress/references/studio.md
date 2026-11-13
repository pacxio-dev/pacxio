# Local workbench (pacxio studio)

A web page on this machine: all stages of one job laid out left to right on one zoomable canvas, refreshing every time the agent changes something. It shows the **user** progress and results, so the agent does not have to send images one by one and the user does not have to open files one by one.

## When to open it

- **Right after a successful `job init` on a new project, without asking**; when continuing a project, `studio status` first, reuse it if it is running, otherwise start it. Keep it open for the whole workflow; only skip it if the user says no;
- open it again if it was closed, or when the user says "open the workbench / canvas / show me where it is" (a running one is reused, never two);
- while the workbench is open you no longer send images at every step: tell the user "it is updated on the workbench, see ⑤ dieline confirmation".

## Commands

```bash
pacxio studio start jobs/<name> --json      # start the server in the background (local only); data.url is the address; reused if already running
pacxio studio status jobs/<name> --json     # whether it runs, and the address
pacxio studio stop jobs/<name>              # stop it when done
pacxio studio serve jobs/<name>             # run in the foreground (Ctrl+C to quit), when the background start fails
```

- **Desktop environments with a browser panel** (Codex, Claude and others): after `studio start` returns `data.url`, open it next to the conversation if you can, so the user watches while talking. In Codex, if `open_in_codex` exists, use `target: {type: "browser", url: data.url}`; what tools exist depends on the current session, and opening the panel does not mean you checked the page.
- **Terminals or environments without a browser panel**: `studio start --open` opens the default browser; also tell the user the address. If there is already a URL and only the panel fails, give the URL to the user or open it in the default browser; no need to restart the server.
- **If it will not start** (a STUDIO error, or the sandbox blocks the command: no listening on ports, no opening a browser): do not retry again and again, and do not change sandbox settings for it. Give the user this command as is, ask them to run it in their own terminal and tell you when it is open:

  ```bash
  pacxio studio start <job folder> --open
  ```

  The server the user starts is the same one you would use (identified by the job folder); keep working through the command line, the page refreshes by itself; `studio status <job>` finds it.

## What is on the canvas

| Area | Content |
|---|---|
| ① Content list | The item table and the wording confirmation status |
| ② Layout draft | Layout sketches of neighbouring panel pairs, each panel, the layout brief |
| ③ Concepts | Main and back views, the chosen one marked ★; each image's footer says "concept" (the label is never drawn on the image) |
| ④ Flats | Only the set of flats in use (front + right, back + left, top, bottom, laid out at one real-mm scale: panels of the same height show the same height), for the user to confirm "the flats match the concept". Older versions, other directions and straightened panels (for the agent's measuring) are not shown; flats more than 10% off in proportion are not shown either, only a note "proportions failed, not shown", and the area title says "N to redo" (redo first, then ask the user to confirm the set) |
| ⑤ Dieline confirmation | The dieline confirmation image from `render` (flat + cut lines + panel names and width × height + overall size + finish callouts) with preflight boxes on top; the "differences from the flat" written with `flat --diff`. The user confirms here (the only gate before export). Turning off the "Cut lines" button above switches to a copy without cut and crease lines (`preview.art.png`): when artwork on a fold is hidden by the crease line, use it to check joins for breaks or offsets, and look at it once before asking the user to confirm |
| ⑥ 3D mockup | Only the turnable 3D (folded along the real dieline: drag to rotate, scroll to zoom, slider or "Unfold / Open / Close / Play" to see folding and the inside), view only, no confirmation |
| ⑦ Preflight and delivery | Preflight results; when checks pass but nothing is exported yet, "waiting for agent: export the delivery pack"; after export two cards: the production pack (3 files for the factory) and the archive (to keep) |

Right sidebar: each stage's status and "who does what now", preflight issues (click one to jump to its place on the dieline image), delivery files. Images not remade after a design change are marked "not current".

Process drafts and duplicate images are folded into a group at the bottom of each area by default: concepts not chosen once a direction is picked, older back views (with several versions only the one in use is shown, marked "in use"), flat views of each panel from the draft. ⑥ only holds the turnable 3D (the summary sheet and static views stay in the archive, not on the canvas). ④ goes further: only the set in use is shown; older versions, other directions and straightened panels are not on the canvas (to switch back to an older flat, tell the agent: `concept use`). So when importing concepts, back views and flats, **name them by convention** (`<chosen>-back1`, `<chosen>-flat-front`) so the workbench knows which belong to the chosen direction.

## Notes: the user's comments on images

On the workbench the user clicks with the "Pin" or drags an area with the "Box" and writes a comment. Every note carries:

- **Position**: part, panel, panel-local mm (the same coordinates as `art --box`), and the elements in the box (placement id, type, text excerpt, content item);
- **Snapshot** `review/<id>.png`: the area the user pointed at, still visible after the image is remade.

**Check before every round of work**:

```bash
pacxio notes list jobs/<name> --open --json     # waiting for you; each has where (one line of position), target, snapshot
```

Handling a note:

1. Open its snapshot first to see what the user means;
2. change things by the ids in `target.elements` (`art set` / `content edit` / …);
3. remake the matching image (dieline confirmation `render`, 3D mockup `mockup` …);
4. reply:

```bash
pacxio notes reply jobs/<name> n3 --text "Now two columns, body text 8pt → 9pt; please look at the new artwork"
```

After the reply the note becomes "waiting for the user"; the user closes it on the workbench or replies again (back to "waiting for you"). If it is not finished and you are only replying first, add `--keep-open`.

- **Never close the user's notes**: `notes resolve` only when the user clearly says in the conversation "this one can be closed".
- Wording comments (notes on content list items): after the change, ask the user to confirm the wording again (`content confirm` still runs only when the user says so).
- Notes are comments, not confirmations. "OK" or "fine" in a note does not confirm the dieline image; confirmation still needs the user to say so clearly.
- You can leave messages on images for the user too, e.g. "I tried both here, pick one":

```bash
pacxio notes add jobs/<name> --text "Both layouts are on; pick one" --panel side_b --box 5,100,40,60
```

When notes are waiting for you, the output of `render` / `mockup` / `flat` / `preflight` / `export` adds a NOTE reminder (it does not block). The user may also say "look at the notes" in the conversation — a button on the workbench copies that sentence.

## Confirmation buttons: clicked by the user

The sidebar's "Flow" tab, next to area titles, and under each main-view concept have confirmations the user can click. Clicking opens a dialog showing what is being confirmed; it can only be clicked after ticking "I have looked at it myself".

| Button | Same as |
|---|---|
| Confirm wording | `content confirm` |
| Confirm draft | `plan --confirm` |
| Choose this direction | `concept choose <id>` (only main-view images have it) |
| Use this one | `concept use <id>` (only on older back views / flats) |
| Confirm dieline | `approve` (the only gate before export; on ⑤ dieline confirmation) |

- The record says `by: user (workbench)`, the same as command-line records; `export --require-approval` judges it as usual.
- Buttons that cannot be clicked show why next to them ("design changed, ask the agent to render the dieline image again", "required information missing" …). When you see the user stuck there, do what the reason says.
- **The agent never clicks these buttons or calls the workbench's confirmation API** (`/api/confirm`). When the user says "confirmed" in the conversation, the agent runs the command as usual; when the user clicked on the workbench, the agent checks the state with `approve <job> --status`, `content confirm <job> --status` etc. and continues.
- When a main-view concept is chosen on the workbench, main colors are sampled from the whole image (no `--region`). If the user cares about the colors, choose again in the conversation with `concept choose … --region`.

## Rules

1. **The workbench is for people.** Confirmations are either clicked by the user on the workbench, or said clearly by the user in the conversation and then run by the agent. **The agent never clicks confirmation buttons**, and never writes or closes notes on the page for the user; the agent's messages go through `notes add`.
2. **The agent still changes the design through the command line**, then `render` / `mockup` / `flat` / `preflight` as usual: the workbench reads those commands' outputs. The final artwork only updates through `render`.
3. The workbench creates `studio/` in the job folder (server info, logs, thumbnail cache), which is not design data and can be deleted; notes are in `review/` (comments and snapshots, not design data, not in job.json). Neither is an asset, and neither is committed.
4. The workbench only listens on 127.0.0.1, sends no outside requests and its page references no outside resources; nothing needs to be installed for it, and nothing should be.

## When something goes wrong

| Symptom | What to do |
|---|---|
| `studio start` reports STUDIO: the server did not start | Look at the log in the error; if the port is taken use `--port`; or run `studio serve` in the foreground to see the error |
| "Server disconnected" at the top right | The server stopped: `studio start` again (the address may change; open it again) |
| An image stays old | The matching command did not run again: dieline image `render`, 3D mockup `mockup`, preflight `preflight` |
| Red bar at the top "cannot read job.json" | job.json is being written or is broken; `pacxio panels <job>` shows the exact error |
