# Final design: rebuilt from the flats

Work from the **flats** the user confirmed (for multi-part styles or without flats, from the main view and `plan`; in route B without a layout draft, draft from the reference and check with `plan` that everything fits). List the elements to rebuild **panel by panel**: text (content, level, position, size, type style), graphics / illustrations, colors. The flats are straight-on, so measure position and size as "panel width in mm ÷ width in pixels". Then:

- **Color**: use the main colors from `concept choose`, or sample the straightened flat (CMYK values are approximate; brand colors follow the brand guide / swatch). If the user gives a brand color's Pantone number or a set CMYK, record it as a key color with `color add --name … --pantone … --cmyk … --where …`: it is only a callout for the factory to match (the spec sheet's Colors section and the swatches on the production file's callout layer), it does not change print content or expire confirmations. The user gives the CMYK for a Pantone number; do not convert it yourself.
- **Text**: `art add --kind text --content <item>`, one item at a time; the text comes from the content list, so `--text` is not needed. Use `--text` with `--tag` only for text outside the list. Serif headings use Source Han Serif (`SourceHanSerifSC-Regular / -Bold`; if not installed, `scripts/fetch_fonts.py --with-serif`); wide-tracked small text uses `--tracking`, line spacing `--line-height`; numbered / bulleted lists use `--hanging-indent auto` (continuation lines align with the text after the marker; give mm when the text column was measured from the flat, see `references/fonts.md`). Vertical titles and slogans use `--vertical` (top to bottom, columns right to left, punctuation set vertically, Latin and digits sideways; see `references/fonts.md`, "Vertical text").
- **Match graphics and photos to the flat**: `pacxio restore graphics jobs/<name> --apply` measures icons, logos and dividers; for repeated selling-point rows try `restore rows --apply` first — dividers whose count and order are unreliable are not guessed. Photos are neither traced nor cut from the flat (the engine refuses images from `restore face / strip`): use the user's own photo if there is one; otherwise **first fit the placement box to the whole photo area** — with a rectangular neutral frame, `restore frames --apply` measures it; without a frame, with missing edges or transparent clipping, check with `art set --box`. A wrong box cuts into other content and gives the redraw the wrong proportions. Then `restore photo jobs/<name> <placement>` writes the prompt; tell the user how many images, generate, bring it back with `--image`, check it enlarged, and see how close it is with `restore score`. After changing the box, cut and redraw the reference again; never keep using a reference from the old wrong box.
- **Match printable colors**: once positions and letterforms are reliable, look at the `restore colors` report first, then `--apply` writes local CMYK for text and one-color small icons. It keeps key colors, linked finishes and shared styles, samples under the current ICC and total ink, and never changes the paper, ICC or spot colors on its own. `COLOR_LIMIT` only means the remaining difference after sampling is still above target; keep the reference color, the printable approximation and the difference — never declare it passed by changing the preview or relaxing the target.
- **Measure once the text is set**: `pacxio restore text jobs/<name> --apply` corrects font size, line spacing and position from the flat (paragraphs it cannot measure are listed for you to set by hand; text under a fixed foil plate made from its letterforms stays put). Run it before `restore strip / face`: the closer the text boxes match the AI-drawn text, the cleaner the text removal.
- **Graphics** (illustrations, curved color areas, bands, contour lines, icons): **trace them from the flats as vectors** — a single panel (top, bottom) with `restore face <panel>`, an illustration around the body with `restore strip` after the text is set (joins the wrap at real size and removes AI-drawn text). After cleaning leftover text, `visual trace` it to SVG (pick the method by the image: full illustrations with vtracer full-color tracing, colors converted with ICC like photos; line art, icons and few-color graphics traced as color areas, each color given CMYK with `--colors`), then place it with `art add --kind vector --space body`. **Simple icons and geometric ornaments** (leaves, drops, dividers, rounded frames) are often cleaner as hand-written SVG (drawn at the placement box's size in mm); when both work, use whichever looks closer, compare with `restore score` and tell the user the scores. Never substitute procedural approximations (stacked sine-wave hills, evenly shifted lines) for restoration: layering and density get lost. Only when there is no flat to trace, generate a flat-color shape image with AI (`visual gen --role graphic`) and trace it. Graphics that **run across folds** use `--space body`. Method and SVG requirements: `references/vector.md`.
- **Photos / illustrations**: `visual gen` makes **flat assets without text or perspective**, using the matching area of the flat as the composition reference and describing the concept's style and colors in the prompt (templates in `references/visual-prompts.md`), or ask the user for them; every image goes through `visual prepare`. With placeholders on the layout, generate at the target size in `plan/plan.md`, then `art fill <job> <placeholder_id> --source image` replaces the placeholder (position and stacking unchanged); to fit it inside an arch, rounded corners or another shape, add `--clip shape.svg`.
- **Finishes**: local finishes with `finish add` (SVG preferred; white means knocked out); lamination / varnish with `surface add`. Position, extent, knock-outs and checks: `references/finishes.md`.

**Tell the user when they pick a direction what the engine cannot reproduce yet** (details in `references/concept.md`):

- **gradients, transparency, shadows / glows** inside vector graphics (they can only go in as bitmap assets);
- **special fonts** drawn by the AI in the concept (handwriting, decorative type) — without a matching font file they can only be approximated with Source Han Sans / Serif or the user's own brand font;
- text the AI wrote in the concept will not match a real font exactly, and proportions will differ.

```bash
pacxio art add jobs/<name> --kind fill --color 0,3,7,4 --id bg   # fills a whole panel (--scope panel_id); draw partial color areas as a rectangle SVG with kind=vector
pacxio art add jobs/<name> --kind vector --source band.svg --space body --box 130.96,0,261.92,60 --fit stretch \
    --colors "#3d4536=75,55,80,40;#c8a464=20,35,70,0"
pacxio art add jobs/<name> --kind image --source assets/hero.prepared.png --panel front --box 5,40,70,110 --clip arch.svg
pacxio art add jobs/<name> --kind text --tag brand --panel front --text "TIDEWELL · Sea Salt Crackers" \
    --font SourceHanSerifSC-Bold --size-pt 18 --box 5,155,70,15 --align center --color 0,0,0,100
pacxio art add jobs/<name> --kind text --tag slogan --panel front --text "COLOUR FOR EVERY ROOM" --tracking 200 \
    --font SourceHanSansSC-Regular --size-pt 7 --box 5,140,70,8 --align center --color 0,0,0,80
pacxio finish add jobs/<name> --type foil_gold --tag brand_mark --source assets/logo.svg --panel front --box 25,15,30,24
pacxio render jobs/<name>        # quick look at the layout while iterating with the user
```

`--box` is in **panel-local coordinates** by default (mm, seen from outside the box, origin at the bottom left with the panel upright); with `--space body` it is in body-wrap coordinates, with `--space blank` in whole-blank coordinates (`pacxio panels` lists each body panel's range). Use `\n` in `--text` for a line break.

**Stacking order**: placements stack bottom to top in the order they are added, **text included** — an image added later covers earlier text. To insert underneath use `art add --before <id>`; to reorder use `pacxio art move jobs/<name> <id> --top`. Covered text triggers T04.

**Where visual assets come from**: import assets the user already gave; to generate, use the built-in image tool if there is one, otherwise a configured API. `visual gen` needs the network and `OPENAI_API_KEY`; follow the environment check, and its instructions when something is missing:

```bash
pacxio visual import jobs/<name> /path/to/this/asset.png --role hero --name hero  # after Codex built-in image generation
pacxio visual gen jobs/<name> --prompt "..." --name hero --role hero --size 951x2132   # with OPENAI_API_KEY
pacxio visual import jobs/<name> image_path --role hero                        # supplied by the user
```

Prepare every image:

```bash
pacxio visual prepare jobs/<name> hero --role hero --target-mm 70,110 --check-text
pacxio visual prepare jobs/<name> tile --role pattern --tile-mm 40
```

Product photos and object shots go in with transparent backgrounds (ask for a transparent PNG when generating; otherwise `prepare --remove-bg`; a subject touching the image edge gives V06); see `references/visual-prompts.md`, "Product photos and object shots".

**Brand fonts** (supplied by the user): `pacxio font add jobs/<name> BrandFont.otf --name BrandSans`, then `--font BrandSans,SourceHanSansSC-Bold` (Source Han covers characters the brand font lacks). Details in `references/fonts.md`.
