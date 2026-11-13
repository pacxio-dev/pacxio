# Content list: every piece of information on the pack

Before concept images, put **all information that goes on the pack** from the user's brief into a list and have the user confirm the **wording**. The list drives everything after it: concept prompts, text on the layout, the mockup completeness check.

**Only the wording is confirmed here, not which panel it goes on.** Placement is handled at layout time: route A splits panels with `plan` first, route B looks at the concept first, then lays out the final artwork.

## 1. Write one file and import it

Read the user's brief, write a YAML file (outside or inside the job folder), then import it:

```yaml
conventions: paint            # category conventions (see pacxio conventions list)
items:
  - {id: brand, category: brand, text: TIDEWELL}
  - {id: logo, category: logo, asset: logo.svg}                 # graphic asset: path relative to this file
  - {id: name, category: product_name, text: "WATER-BASED\nWOOD FINISH"}
  - {id: usp1, category: selling_point, text: NATURAL LOOK}
  - {id: usp2, category: selling_point, text: WATER CLEAN-UP, required: false}
  - {id: dry, category: technical, pending: drying time to be supplied by the brand}  # not settled yet: say what is missing
  - {id: maker, category: manufacturer, text: "TIDEWELL Coatings Co., Ltd.", note: address to follow}
```

```bash
pacxio content import jobs/<name> brief.yaml        # merges by default; --replace replaces everything
pacxio content list jobs/<name>                     # listed by category, with usual-placement hints
```

- **Copy the brief word for word**: do not rewrite, translate or "polish". Do not invent information the brief does not give; write it as `pending`.
- **Use the user's copy exactly as given**:
  - Only check reading and writing problems: spelling, grammar, punctuation, inconsistent capitalisation, obvious typos. Enter the list **as written** first, and list each finding as "original → suggestion" for the user; change an item (`content edit`) only when the user agrees to it.
  - **Do not judge whether claims are supported or compliant** ("3× concentrated", "non-irritating", "gentle and safe", GHS / hazard statements, ingredient names …); that is up to the user and the brand. Never delete or change the original for that reason, never replace it with a placeholder, and never mark it `pending`.
  - `pending` is only for information **the user has not given** (e.g. "drying time to be supplied by the brand"). If the copy contradicts itself (e.g. "non-hazardous" and "harmful if swallowed" in the same text), mention it once when checking, enter it as written and do not mark it pending.
- `required: false` is optional information the user allows to be left out; everything is required by default. Check the user's requirements before making an item optional; never turn a required item into optional or pending because it does not fit — adjust the layout first, and cutting or changing copy is the user's call.
- Categories: `brand`, `logo`, `product_name`, `variant` (range / series: color, sheen), `spec`, `net_content`, `slogan`, `selling_point`, `icon` (selling-point icon), `description`, `usage`, `technical`, `ingredients`, `caution`, `storage`, `standard`, `manufacturer`, `contact`, `origin`, `barcode`, `certification`, `batch` (batch / date), `other`.
- One idea per item: three selling points are three items, so they can be placed separately later.
- Single changes: `content add / edit / remove` (`edit --clear pending` clears the pending note).

## 2. The user confirms the wording

Show the user the result of `content list` **item by item** (category + original text + required / optional + pending items), with spelling / grammar suggestions listed separately after it ("original → suggestion"), and ask them to check the wording. After the user **clearly says they confirm**:

```bash
pacxio content confirm jobs/<name> --by <who> --note <note>
```

- **Never confirm for the user** (same as `approve` and `concept choose`).
- The list can be confirmed with pending items; the layout uses a draft or a `[TBD]` placeholder for now. Once they are filled in the list has changed, the confirmation expires, and the user confirms again.
- If the list changes after confirmation, `content list` says "confirmation expired".
- When continuing an old project, the list with a valid confirmation is the current wording; the original brief is for tracing back. Read existing correction records first and never revert choices or corrections the user accepted to the original document automatically; see `resume.md`.

## 3. Referencing the list in the layout

```bash
pacxio art add jobs/<name> --kind text --content name --panel front --font ... --size-pt 54 --box ...
pacxio art add jobs/<name> --kind vector --content logo --panel front --box ... --color 82,61,79,31
pacxio finish add jobs/<name> --type foil_gold --content logo --panel front --box ...
```

- With `--content` **no `--text` / `--source` is needed**: text and assets come from the list, and the layout follows when the list changes; the label defaults to the category name.
- **Several items in one text** (a row of selling points, an information table on a side): reference them with `{{item_id}}` in `--text`; labels and formatting are written in the text, the information still comes from the list:

  ```bash
  --text "{{usp1}}   |   {{usp2}}   |   {{usp3}}"
  --text "Net content: {{net}}\nVOC content: {{voc}} g/L\nManufacturer: {{maker}}"
  ```

  Every referenced item counts as "on the layout"; referencing an item that does not exist is refused.
- **Only one line of an item**: `{{item_id:N}}` (N starts at 1). When a two-line title needs two sizes, split it into two text placements:

  ```bash
  --text "{{product_name:1}}" --style name_lead     # Tub & Sink & Tile
  --text "{{product_name:2}}" --style name          # Refinishing Kit (larger)
  ```

  Too many lines are refused with the available range; both lines follow when the list changes.
- `--text` that differs from the list is refused — to change wording change the list, and ask the user to confirm again.

## 4. Mockup checks

With a list, `mockup` checks (MK01 and MK08 also block confirming the dieline image):

- **MK01** (error): required information from the list is not on the layout yet — cannot be confirmed;
- **MK08** (error): required information is still pending (a draft or placeholder on the layout) — cannot be confirmed;
- **MK09** (warning): wording not confirmed, or the list changed after confirmation;
- **MK10** (info): text not linked to the list (use `--content`).
- **MK12** (info): the brand name only appears as a logo, with no separate brand-name text — a reminder only, it does not block confirmation; whether to add brand-name text is the user's call after seeing the mockup, do not press. With no logo either, it is still MK01.

## 5. Category conventions

```bash
pacxio conventions list
pacxio conventions show paint
```

Where each type of information usually goes for a category, common layouts (same front and back / front and back split), regulatory reminders. **Hints, not rules**: put them in the concept prompt for the AI to follow, and use them to check the images. Only "home improvement · paint" exists for now (initial values, not confirmed); the user has to check regulatory reminders against current standards.
