# Image prompts and asset specs (print assets)

> This is about **assets for print** (key visuals, patterns, icons), which never contain text.
> **Concept images** (the finished look with a scene and text) are something else; see `concept.md`.
> When rebuilding from a concept, describe its style, graphic language and colors in the asset prompt (with the HEX values from `concept choose`).

## Constraints the engine always appends (cannot be turned off)

`visual gen` always appends to your prompt:

- no text, letters, numbers, logos or watermarks;
- flat front-on composition, no perspective, no shadows, not a product photo, no box in the image;
- pattern / texture: seamless tileable;
- `--transparent`: pure green background #00FF00 (keyed out later with `visual prepare --chroma-key`).

So you only describe **content, style and color**. Describe brand colors in words with their values, e.g. "main color deep ink green #1F4D3A, secondary off-white". These constraints are only requests and **the model does not always follow them** — so every image goes through `prepare --check-text`, and is generated again if text turns up.

## Prompt templates

- **Key visual (hero)**: `<subject>, <style>, <colors>, empty space at <position>, for the front of a tall pack`
- **Pattern**: `repeating pattern of <elements>, <style>, <colors>, evenly spread`
- **Texture**: `<material> texture, <colors>, fine and even`
- **Logo / icon**: ask the user for vector files (SVG) first; with the engine API, `--transparent` currently uses a pure green background; with a built-in tool, use its transparent output if it has one (below).

**Never** ask for text, brand names or slogans in the prompt — text is set with `art add --kind text`.

## Asset roles and sizes

| Role | Use | Size |
|---|---|---|
| `hero` | Key visual, placed on a panel | Same aspect ratio as the target area; pixels from `px` in `pacxio panels` (at 300 ppi) |
| `pattern` | Background pattern tiled over the whole outside | A 1024–2048 px tile; `--tile-mm` sets the tile size |
| `texture` | Material, like pattern | Same |
| `logo` / `icon` | Logos, icons | SVG preferred; bitmaps ≥ 300 ppi with the background removed |
| `graphic` | Decorative graphics to be traced as vectors (curved color areas, bands, leaves, icons) | A few flat colors, no gradients; after generating, `visual trace` it to SVG (see `vector.md`) |

For reference: an 80×180 mm front panel is about 945×2126 px at 300 ppi. Do not generate a whole blank in one image (a 281×316 mm blank needs about 3319×3732 px); tile or upscale instead.

**Pick the image tier by size (large boxes get 4k)**: work out how many pixels the print asset needs at 300 ppi (about 11.8 px/mm) and pick the smallest tier that is enough, rather than upscaling:

| Tier | Long edge (gpt-image-2) | Long edge it covers (300 ppi) |
|---|---|---|
| 1k | about 1500–2000 px | about 130–170 mm |
| 2k | about 2050–2700 px | about 175–230 mm |
| 4k | about 2900–3840 px | about 245–325 mm |

- The **image tier** column of the "Image / graphic placeholders" table at the end of `plan/plan.md` is already worked out from the placeholder sizes; without a placeholder (e.g. the full background strip of a sleeve) work it out from the mm it covers.
- A local image plugin: `--size <preset ratio> --resolution 2k|4k` (2k and 4k only take preset ratios such as `21:9`, `3:2`; pixel sizes only at 1k). If the result's ratio is a little off the covered area, `prepare --target-mm` trims it (V01 reminds you).
- Larger than 4k (the full wrap of a big box, slotted boxes): generate per panel or area with the joins on folds; or upscale with `prepare` after 4k and say so plainly (Real-ESRGAN, if installed, is better).
- Tell the user the number of images before generating, and estimate the cost by tier (1k is about 0.0085 per image, 2k and 4k cost more; the `cost` the image plugin returns is what counts).

The engine's own API adapter (`visual gen`) only picks from 1024×1024, 1536×1024 and 1024×1536. When the image is under 300 ppi, `prepare --target-mm` upscales it (Lanczos, adds no detail; uses Real-ESRGAN if installed) with a V04 reminder; an aspect ratio more than 5% off gives V01.

## Product photos and object shots (transparent background)

Product photos and objects on the pack (a bowl of cereal, a few berries, a bar of soap) are usually **cut out on a transparent background**, so the box's background color and pattern show through instead of a square photo — the way supermarket food packs do it. If the photo blends into the background on the flat, do the same in the final artwork.

1. **Best: ask for a transparent background when generating.** Codex's built-in `image_gen` can output transparent PNGs directly: request a transparent background and keep the transparency (its imagegen instructions say so). The command-line fallback gpt-image-2 does not support transparency; do not switch models just for that — use step 3.
   Say clearly in the prompt: **subject complete with space around it** (not cut by the image edge); no drop shadow, or only a very light contact shadow; no text; view and composition following that area of the flat.
   Check the result first: real transparency (not a painted grey-and-white checkerboard) and a complete subject; generate again if it painted a fake checkerboard.
2. `visual import … --role hero` → `visual prepare … --role hero --target-mm width,height --check-text`. A hero with transparency is checked for **the subject touching the image edge** (V06, listing which edges): the edge that runs off the box / into the bleed may touch; if another edge touches, generate again.
3. **Images without transparency** (the user's own photos, or a tool without transparency): `visual prepare … --remove-bg` removes a light background (gradients and vignettes are fine; only background connected to the image edge is removed, light areas inside the object stay; `--background '#hex'` sets the background color).
   **Limits**: it separates background and subject by color, so parts of the subject close to the background color get removed too — the rim of a white bowl or oat flakes on an off-white background show gaps on a dark pack; soft shadows around grains stay as a light haze. For such images generate again with a transparent background, or shoot / generate on a solid background with strong contrast to the subject. Pure green (`--chroma-key`) only when the subject has no green.
4. Placement: on the side where the subject should run off the box (e.g. cereal reaching the top edge), the placement box keeps the photo's original aspect ratio and **extends past the box**, so the bleed cuts that straight edge; the other edges show the complete subject with the background around it.
   After `render`, zoom in on the edges on the workbench (⑤): on the real background color, check for white fringes or gaps.

## Codex built-in image generation

- Use the built-in image tool the current session actually provides; import by the local path it returns: `visual import jobs/x /path/to/this/asset.png --role hero --name hero`. Generating and importing are two steps; the import command does not generate.
- Only when the tool returns no usable path, consider `--codex-latest`: it scans `$CODEX_HOME/generated_images/` (default `~/.codex/generated_images/`) and takes the newest file in the whole folder, which cannot tell projects or parallel generations apart. Before importing, check the image, its time and what it is for; do not import if there is no new image.
- When transparency is needed and the tool supports native transparent output, request it and keep the transparency; when the tool has `transparent_background`, set it to `true`. Only if the tool cannot, generate on a light solid background and remove it with `prepare --remove-bg`, or use pure green and `prepare --chroma-key`; never key out green if the subject contains green (product photos: see above).
- Built-in image generation uses the user's quota; say how many images first, and do not assume a fixed cost ratio for text. If it fails, read the error before deciding whether to retry.
- `visual gen` (API) needs the network and a key; whether built-in generation is available depends on the current session's tools and what they return — do not ask the user to change a fixed sandbox configuration first.
