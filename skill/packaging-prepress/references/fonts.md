# Fonts

All text is set by the engine and **outlined**: print files only contain outlines, no fonts. Fonts are only used while generating files and are never distributed with the delivery pack.

## Bundled open-source fonts

All SIL OFL (free for commercial use), downloaded by `scripts/fetch_fonts.py` to `~/.pacxio/fonts` (needs the network once); they cover Latin and common Simplified Chinese characters:

| Font | Font names | Download |
|---|---|---|
| Source Han Sans (sans-serif) | `SourceHanSansSC-Regular`, `SourceHanSansSC-Bold` | `scripts/fetch_fonts.py` (about 33 MB, required) |
| Source Han Serif (serif) | `SourceHanSerifSC-Regular`, `SourceHanSerifSC-Bold` | `scripts/fetch_fonts.py --with-serif` (another 50 MB or so, optional) |

"Elegant serif headlines" in a concept use Source Han Serif; `check_env.py` says whether it is installed. When a font is missing `art add` reports an error with the download command — downloading needs the network, so ask the user first.

## Tracking and line spacing

```bash
--tracking 200        # tracking: 1/1000 em (the same unit as InDesign / Illustrator tracking); positive opens up, negative tightens
--line-height 1.6     # line spacing: a multiple of the font size, default 1.2
```

- "Wide-tracked small caps / slogans" in a concept are usually `--tracking 100`–`300`; fine-tuning large titles `-20`–`50`.
- Tracking is added after each character and counts in line breaking and alignment (center and right alignment use the visible characters; tracking after the last character does not count).
- With tracking, ligatures are off (fi, fl split), as is usual in typesetting.

## Skew (big numbers, punchy titles)

```bash
--skew-y 6            # rises 6° to the right, verticals stay vertical (big numbers "shooting up" in a concept)
--skew-x 10           # the top leans 10° to the right, like italics (fake italics when there is no italic font)
```

- Horizontal text only (-30° to 30°), with `art add` / `art set`; `--clear skew_x` removes it; skewed around the ink center, the whole block's outlines shear together and stay lines and curves after outlining.
- Measure from the flat: how much the top edge of neighbouring characters rises per character width to the right (`tan`, converted to degrees) is `--skew-y`; how far the top of a vertical stroke is right of its bottom is `--skew-x`. Both can be given together.
- Measure size and position unskewed first (`restore text` measures unskewed text), then add the skew and fine-tune the position on the overlay.

## Numbered lists and bullets: hanging indent

When a numbered list (tips, steps, cautions) wraps, continuation lines align with **the text after the marker** of the first line, not back under the marker:

```bash
--hanging-indent auto   # measures where the text starts after markers like "1. " "2) " "• " "- " "（一）" "1、" "①" and aligns continuation lines there
--hanging-indent 6      # gives that start in mm from the box's left edge (the text column measured from the flat)
--hanging-indent 0      # no indent (when the style sets one but this paragraph should not have it)
```

- Can be set on placements (`art add / art set`) and text styles (`style set`); usually on the list style, not the placement. `art set --clear hanging_indent` removes the placement's value (back to the style's).
- Each paragraph's marker stays at the line start; with `auto` the widest marker in the whole text sets the text column ("9." and "10." line up too). With a number, narrower markers leave space after them and wider ones are followed directly by the text.
- A paragraph without a marker right after a list item (no blank line between) belongs to that item and starts at the text column — e.g. two lines of methods after "7. Use one of the two methods below …". A blank line ends the list.
- ASCII markers need a space after them ("1.5kg", "-5°C" are not markers).
- Left-aligned text only; centered / right-aligned placements refuse the field, and style values have no effect on them.
- The indent counts in line breaking: the same box may need more lines, and `plan`'s T02 (does not fit) and fill ratio use the indented line count; sketches, the dieline confirmation image, mockups and print files all use the same typesetting.

## The user's own brand fonts

1. Ask the user for the font files (OTF / TTF; TTC, WOFF, WOFF2 are accepted too). In Claude Code / Codex the user gives a path or drags the file into the conversation, and it lands at a local path.
2. Add it to the current job:

   ```bash
   pacxio font add jobs/<name> /path/to/BrandFont.otf --name BrandSans
   ```

   The font is stored in `jobs/<name>/fonts/` (never in the code repository), with a `.meta.json` recording licence information next to it. The job gives the same result wherever it is regenerated.
3. Set text with `--font BrandSans`. Brand fonts often lack CJK glyphs, and the command says so; for such copy use a **font chain**, comma separated, where each character goes to the first font in the chain that has it:

   ```bash
   --font BrandSans,SourceHanSansSC-Bold
   ```

   Latin and digits use the brand font, CJK characters fall back to Source Han Sans. Every name in the chain must exist, or `art add` reports an error.
4. `pacxio font list jobs/<name>` shows added fonts, CJK coverage and licence information.

## Embedding bits and what you do

Font files have an "embedding permission" field (fsType), a declaration by the font's author. The engine reads it, but it is **only a technical hint, not legal advice**.

| Embedding permission | Meaning | What the engine does |
|---|---|---|
| `installable` | Installable embedding | Added directly |
| `editable` / `preview_print` | Editable / preview & print only | Added with an L01 warning |
| `restricted` / `bitmap_only` | Restricted / bitmap only | **Refused** (exit code 2) |

**When a font is refused:**

1. Tell the user the reason as it is (the font's author does not allow embedding or distributing its outlines).
2. Ask the user to check with the rights holder whether it may be outlined for print. Brands usually hold the licence for their own fonts, so this is often quick.
3. Only after the user **clearly says "confirmed"** may `--accept-license-risk` be added. **Never add it yourself to get around the refusal.** The preflight report then keeps a record "the user confirmed they bear the licence risk" (L01, warning).

Fonts without any licence information also get an L01 warning: ask the user to confirm it can be used for print.

## Vertical text

Vertical titles and slogans (tea gift boxes, Chinese-style packs) use `--vertical`, not one character per line (`\n`):

```bash
pacxio art add jobs/<name> --kind text --content name --panel lid.top --box 200,40,20,110 \
    --font SourceHanSerifSC-Bold --size-pt 28 --vertical
pacxio art add jobs/<name> --kind text --content slogan --panel lid.top --box 170,40,24,110 \
    --font SourceHanSansSC-Regular --size-pt 9 --vertical --align top
```

- Top to bottom; when a column is full (box bottom) it continues in the column to the **left**; `\n` forces a new column. The first column sits at the box's right edge, and each column to the left moves by one column pitch (`--line-height`, a multiple of the font size).
- `--align`: within a column top (top, default) / center (center) / bottom (bottom). `top` / `bottom` are only for vertical text.
- Punctuation uses the font's vertical glyphs (Source Han Sans / Serif have them: commas and periods top right, brackets and quotes rotated); without vertical glyphs built-in offsets are used, with T07 — check the dieline confirmation image.
- Latin text and digits lie sideways clockwise, centered on the column, breaking columns only at spaces; upright two- or three-digit numbers (tate-chu-yoko) are not in the first version.
- When it does not fit, T02 reports by **width** (how many columns overflow the box width): widen the box, reduce the size / column pitch, or shorten the copy.
- Vertical text does not support hanging indent (`--hanging-indent`). Back to horizontal: `art set <job> <id> --clear vertical`.
- The layout brief says the text is vertical, and the AI draws it vertically in concepts / flats; finishes made from vertical text (`finish add --placement`) work as usual.


- **Variable fonts** (several weights in one file, with an `fvar` table) are not supported yet: ask the user for static weight files.
- **WOFF2** needs the brotli library; without it, ask the user to convert to OTF / TTF first.
- Font names may only contain letters, digits, underscores and hyphens; other characters become `_`. A font name cannot repeat an existing font (including Source Han Sans), so they never shadow each other.
- Missing glyphs (`FONT` error): add a fallback font in `--font`, or change fonts.
- **The delivery pack contains no font files** and no font data; `job.json` only records font names. Factory files do not need and should not include fonts.
- Brand font licence records in the preflight report: complete information and `installable` is info, doubts are warnings; pass warnings on to the user.

## Text under foil and spot UV

Finishes have a minimum line width (foil 0.2 mm by default, see the profile's `finish_rules`). Thin horizontals and serifs of serif faces are thinner than that at small sizes: Latin letters about 5 mm high in Source Han Serif Bold break into pieces after opening. Use sans or bold faces for small foiled text; serif is fine for large titles. Finishes made from text use `finish add --placement`; if F02 fails use `finish fit` (see `finishes.md`).
