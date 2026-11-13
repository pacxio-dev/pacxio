# Getting started: brief, job, content list, route

New projects start here (continuing an old one: `resume.md`). Run `pacxio next <job>` before every round of work; it says where the job stands, who it waits on and what to do next.

## Collect the brief (all at once)
Box style or use · length × width × height, and whether these are **inside or outside dimensions** · whether it needs a **window** (clear or open, on which panel, how big) · material and weight (see `references/box-styles.md`) · print method (default offset, outside only; slotted boxes default to flexo) · style direction (references, keywords, colors) · finishes (foil / spot UV / embossing with `finish`; lamination and varnish recorded with `surface`, extent and knock-outs (default: the whole sheet, no knock-out at glue areas; record knock-outs only when the factory asks), which go into the spec sheet and 3D; other post-press requirements go into `manufacturing-notes.md`, see `references/finishes.md`) · brand assets (logo — SVG preferred, brand colors, **brand fonts**) · **the text on the pack**: brand name, product name, slogan, selling points, net quantity, ingredients, manufacturer, shelf life, barcode, certification marks … confirm the wording word for word; which items **must appear** depends on the category and market regulations, which the engine cannot judge — **ask the user, who is responsible for it** · whether the factory has specifications (if so, make a profile with `references/factory-profile.md`).

Do not guess key parameters the user has not given: size basis (inside / outside), material, text content, whether finishes are needed. Ask for everything missing at once.

## Create the job

```bash
pacxio job init jobs/<name> --style rte --L 80 --W 50 --H 180 --basis inner --material sbs_350
pacxio panels jobs/<name>        # size, orientation, safe area and suggested 300 ppi pixels for every panel
```

Box styles and parameters: `references/box-styles.md`. When the user gives a FEFCO / ECMA code, or does not know which structure to use, choose one with `references/structures.md` first (`structures suggest` gives 2–3 options with 3D), then `job init --fefco <code>`. Create the job first, so concept images can use the real proportions.

**Open the workbench right away** (by default, without asking; skip it only if the user says no): a web page on this machine with each stage's images laid out on one canvas in workflow order, refreshing after every change, so you do not have to send images one by one. Details in `references/studio.md`. Right after `job init` succeeds:

- **Desktop environments with a built-in browser** (Codex, Claude and others): `studio start` returns `data.url`; open it next to the conversation if you can. Codex can use its browser-panel tool with the returned address as is;
- **Terminals or environments without a browser panel**: add `--open` to open the default browser, and tell the user the address:

```bash
pacxio studio start jobs/<name> --json            # desktop panel: open the returned data.url
pacxio studio start jobs/<name> --open --json     # terminals etc.: open the default browser directly
pacxio studio stop jobs/<name>                    # stop it when done
```

**If it does not start** (a STUDIO error, usually a sandbox that forbids listening on a port or opening a browser): do not retry again and again; give the user this command as is, ask them to run it in their own terminal and tell you when it is open: `pacxio studio start jobs/<name> --open` (with the real job folder). With the workbench open, keep working through the command line; the page refreshes by itself.

The user clicks or draws on the workbench images to leave comments (notes, with part, panel, position in mm, element and a snapshot). While the workbench is open, **run `pacxio notes list jobs/<name> --open` before every round of work** and `notes reply` after changing things; do not close the user's notes.

**Add windows now** (a window is structure; concept images, flats and mockups all have to show it; see "Windows" in `references/box-styles.md`):

```bash
pacxio window add jobs/<name> --panel front --box 20,50,40,100        # default: rounded rectangle with PET film
```

**Build the content list**: put every piece of information from the brief that goes on the pack into the list word for word, import it, and have the user check the wording item by item (wording only, not which panel it goes on). Make concept images only after the user confirms. Details in `references/content.md`.

**Use the user's copy exactly as given**: only check spelling, grammar, punctuation and capitalisation, and list them as "original → suggestion" for the user to decide; **do not rewrite or polish, and do not judge whether claims are supported or compliant**, and do not delete, change or mark anything pending because of such concerns. Pending is only for information the user has not given.

```bash
pacxio content import jobs/<name> brief.yaml
pacxio content list jobs/<name>
pacxio content confirm jobs/<name> --by <who>     # after the user clearly confirms the wording
```

**Choose a route** (suggest it; the user decides):

- **Route A: lots of copy, content settled** (long instructions / warnings / regulatory text, or more than a dozen items) — make a **copy layout draft** first to decide how information is split across panels, its hierarchy and whether it fits; make concept images after the user confirms, pasting the draft's **layout brief** (`plan/brief.txt`) into the prompt. Details in `references/layout-draft.md`:

  ```bash
  pacxio style set jobs/<name> body --label Body --font SourceHanSansSC-Regular --size-pt 8
  pacxio art add jobs/<name> --kind text --content tip_1 --style body --panel side_a --box 15,22,170,120
  pacxio art add jobs/<name> --kind placeholder --label "bathroom photo" --panel front --box 15,45,170,118
  pacxio art same-face jobs/<name> back --as front     # same front and back
  pacxio plan jobs/<name>                              # text per panel + layout sketches + checks
  pacxio plan jobs/<name> --confirm --by <who>         # after the user confirms the draft
  ```

  `--label` on placeholders, images and graphics is for people (it shows in the sketches, the text draft and the workbench), in the user's language. When it is not English, **also give a short English description with `--label-en`**: the layout brief and image prompts are English and use only the English description, because non-English labels make the AI draw that text. A non-English `--label` without `--label-en` gets a reminder from `art add / set` and `plan` (LABEL_LANG) and is blocked by `concept prompt`; add it and run `plan` again (the English description does not expire confirmations).

- **Route B: little copy, or not settled yet** — make concept images directly, letting the AI follow packaging conventions; after a direction is chosen, draft the layout from the concept (shown to the user with `plan` as well).
