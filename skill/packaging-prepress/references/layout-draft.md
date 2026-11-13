# Copy layout draft (route A; route B after a direction is chosen)

With lots of copy (long instructions, warnings, regulatory information, or more than a dozen items), **the engine sets the text first and the AI draws following that layout**: the AI cannot draw lots of small text well, and its positions and sizes may not fit the real copy. The draft has two layers, rough then fine; **you draft, the user revises**.

Multi-part `tray_lid` / `drawer` lay out with `part.panel`; back views, flats, straightening and `flat` comparison work as for cartons (full panel names, see `box-styles.md`); after changes, check each part's dieline confirmation image with `render`.

## 1. Agree on the text per panel first (for discussion)

Draft from the category conventions (`pacxio conventions show paint`): which items go on each panel, their order and level. Show the user a table and settle "front or side" first. Common layouts:

- **Same front and back**: front = back (key visual, brand, product name, main selling points); directions, warnings, manufacturer etc. on the two sides;
- **Front and back split**: key visual on the front, description and directions on the back, manufacturer, barcode etc. on the sides.

**Bottom panels are plain background color by default**: bottom panels (bottom lid, tray bottom, sleeve bottom) only get text in the draft (coding area, barcode and the like), no illustration or pattern placeholders; the layout brief says "plain background color, no illustration" and the flats are drawn that way. Add a placeholder in the draft only when the client wants a pattern on the bottom.

Move on to the layout sketch once the user has no more comments.

## 2. Set it as a layout sketch with commands

```bash
# Text style = level: changing a style changes all text of that level
pacxio style set jobs/x name    --label "Product name" --font SourceHanSansSC-Bold    --size-pt 40 --line-height 1.05
pacxio style set jobs/x heading --label Subheading     --font SourceHanSansSC-Bold    --size-pt 12
pacxio style set jobs/x body    --label Body           --font SourceHanSansSC-Regular --size-pt 8 --line-height 1.35
pacxio style set jobs/x small   --label "Small print"  --font SourceHanSansSC-Regular --size-pt 7
# Numbered lists / bullets: continuation lines align with the text after the marker (hanging indent, see references/fonts.md)
pacxio style set jobs/x tips    --label "Numbered list" --font SourceHanSansSC-Regular --size-pt 7 --line-height 1.35 --hanging-indent auto

# Text: reference the content list; several items together with {{item_id}}
pacxio art add jobs/x --kind text --content product_name --style name --panel front --box 15,185,170,58
pacxio art add jobs/x --kind text --style body --panel side_b --box 15,115,170,42 \
    --text "{{warning_1}}\n{{warning_2}}\n{{warning_3}}\n{{warning_4}}"

# Image / graphic positions start as placeholders that say what goes there (--label for people, --label-en for the English layout brief)
pacxio art add jobs/x --kind placeholder --label "bathroom scene photo (rounded top right)" \
    --panel front --box 15,45,170,118

# Same front and back: the back shares the front's layout (one set of data; change the front and the back follows)
pacxio art same-face jobs/x back --as front

pacxio plan jobs/x
```

`plan` writes:

| File | Use |
|---|---|
| `plan/plan.md` | **Text per panel**: each panel's text and placeholders top to bottom, level, size, fill ratio, referenced content items, and issues |
| `plan/wireframe.png` | **Layout sketch** (the whole flat, with panel names and safe areas): for the user |
| `plan/faces/<panel>.png` | Flat view of each panel, in the real panel proportions |
| `plan/pairs/<panel>+<panel>.png` | Neighbouring panel pairs (front + right, back + left): layout references for image generation, with graphics across folds in one image |

In the sketches all text is dark grey with no background, and images / graphics are labelled grey boxes — layout only, not color.

## 3. Read the issues, change, run again

| Code | Meaning | Fix |
|---|---|---|
| MK01 | Required information is not on the layout yet | `art add ... --content <item>` |
| MK08 | Information still pending | Set a draft or placeholder first; `content edit` once settled |
| T02 | Text does not fit its box | Adjust the box, columns, panels or size first (still readable); if copy really has to be cut or changed, list suggestions and change the list only after the user agrees |
| T05 | Text smaller than the profile's minimum size | Make it bigger; regulatory text height (net content etc.) follows market rules |
| T01 | Text outside the safe area | Move it inside |
| PL01 | A block of text uses under 40% of its box | The text can be bigger, or shrink the box and give the space to images |
| T04 | Text covered by a later placement | `art move <id> --top` |

Changing a style (`style set`) is the fastest global adjustment: e.g. body text from 7.5pt to 9pt changes all body text; run `plan` again to see if it fits.

Show `wireframe.png` and `plan.md` to the user; after the user **clearly confirms**:

```bash
pacxio plan jobs/x --confirm --by <who> --note <note>
```

The confirmation is only a record (not a gate); if the design changes later, `plan` shows "changed after confirmation".

## 4. Concept images: paste the layout brief, judge the overall feel

`plan/brief.txt` is the **layout brief** (English) for the image model: what goes on each panel, what is largest, where the dense small text is, where on the panel photos / graphics sit; short text word for word, long paragraphs only as a few lines of small text, **no mm coordinates**. Paste it whole into the concept prompt, then add the style (palette, photo mood, graphic language). Placeholders, images and graphics use the English `--label-en` in the brief first, otherwise `--label` (and then `--tag`); `--label` stays in the user's language for the user. A non-English label without `--label-en` gets a reminder from `art add / set` and `plan` (LABEL_LANG) and is blocked by `concept prompt`; add `--label-en` and run `plan` again.

- The concept is judged on **overall feel**: the AI cannot hold an exact layout (not with coordinates, not after a round of corrections), so do not ask for "same positions";
- this way panels you cannot see are not invented at random, and the density of the sides comes out close to real;
- after choosing, make the **back + left view** and the **flats** (`references/concept.md`): flats are straight-on and exact positions are measured from them; once the user confirms the flats they are the reference.
- A 3D concept and the finished pack always differ in layout details (title size, photo position, how the sides are set); tell the user when they choose, and compare side by side on the confirmation page at the end.

## 5. After the flats are confirmed: layout follows the flats, parts swap in

1. Adjust the draft to the confirmed flats (to the main view without flats): title size and position, photo boxes, decorative areas. Change styles with `style set` and placements with `art set` (id and stacking unchanged; nothing changes if validation fails):

   ```bash
   pacxio style set jobs/x slogan --size-pt 28 --color 45,25,42,35
   pacxio art set jobs/x text2 --box 14,166,172,14
   pacxio art set jobs/x text1 --text "{{product_name:1}}" --style name_lead   # split a title by line
   pacxio art set jobs/x placeholder1 --box 0,38,192,124 --clip photo-shape.svg  # placeholder with a shape
   pacxio art set jobs/x text20 --clear color                                     # remove a field
   ```

   After changes look at `flat` (real colors and graphics); `plan` sketches draw vector graphics grey and photos as grey boxes (keeping their shape). Text color is guarded by T06: light text on a light background (e.g. a sage green slogan on off-white) needs a darker shade of the same hue; 8–10pt small text on the sides uses single black (K100), since small text in four colors registers poorly;
2. Photos (illustrations, color areas, contour lines and other graphics are not generated but traced from the flats as vectors, see `references/vector.md`): use that area of the flat as the composition reference and generate a text-free flat photo at the target size at the end of `plan/plan.md` ("Image / graphic placeholders": mm, aspect ratio, 300 ppi pixels), `visual prepare --target-mm width,height`, then:

   ```bash
   pacxio art fill jobs/x placeholder1 --source assets/bath.prepared.png --clip corner.svg
   ```

   **Product photos and object shots** (a bowl of cereal, a few berries) are cut out on a transparent background to blend into the background: ask for a transparent PNG with the subject complete and space around it; without transparency use `prepare --remove-bg`; on the side that runs off the box, extend the placement box past the edge and let the bleed cut it (see "Product photos and object shots" in `visual-prompts.md`).

   Position, stacking and coordinate space stay; a placeholder's shape carries over automatically (the target size table in `plan.md` says "how much shows", so keep the subject out of the cropped part); SVGs are treated as vector graphics automatically (`--color` / `--colors` allowed).
3. Decorative graphics and icons (curved color areas, bands, icons): **write the SVG directly first** (exact and reproducible); only for really complex shapes (illustrative, organic) `visual gen --role graphic` a flat-color shape image → `visual trace --trim` to SVG. Then `art add --kind vector` (across folds `--space body`, see `vector.md`);
4. With the same front and back, `--space body` graphics are not copied to the back: place a separate copy on the back (stopping at the fold, or wrapping onto the neighbouring side — check that side for text first);
5. Write the differences: `flat <job> --diff "…"`; against the per-panel "flat | final artwork | overlay" on the confirmation page, list each difference from the flat / concept (fonts, why colors changed, how hidden panels are set, items pending or waiting for the factory).

## 6. Placeholders must be replaced

`art fill` keeps the placeholder's position, coordinate space, stacking, clipping shape and referenced content item (`--content`); nothing needs adding.

Placeholders only belong in drafts and previews: preflight **PH01** and dieline confirmation **MK11** both block them — replace them in place with real images / graphics using `art fill`; only use `art remove` for placeholders no longer needed.
