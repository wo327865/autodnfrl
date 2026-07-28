# Dismantle fixed-position templates

The maintenance workflow automatically uses this manifest after all five
logical template names below have been captured:

| Template name | Capture when | Clickable |
| --- | --- | --- |
| `dismantle_ready` | The gold/enabled 分解 button is ready | yes |
| `dismantle_empty` | 没有可选择的道具 is visible | no |
| `dismantle_confirm` | A dismantle confirmation/warning/result prompt is foreground | yes |
| `dismantle_close` | The empty dismantle panel and its X are visible | yes |
| `inventory_ready` | The normal inventory is visible after closing dismantle | no |

Capture each template while the game is manually positioned on that screen:

```sh
cd python-autodnf
source .venv/bin/activate

python tools/crop_template.py dismantle_ready --clickable
python tools/crop_template.py dismantle_empty
python tools/crop_template.py dismantle_confirm --clickable
python tools/crop_template.py dismantle_close --clickable
python tools/crop_template.py inventory_ready
```

For clickable templates:

1. Left-drag a distinctive visual region.
2. Right-click the exact button/X inside that region.
3. Press `S` to save.

For state-only templates, left-drag and press `S`. Press `Q` to cancel.

Run the `dismantle_confirm` command again for every visually different
foreground prompt. The tool appends variants under the same logical name.

Choose crops that:

- include stable surrounding UI, not just two or three text glyphs;
- exclude characters, particle effects, clocks, currencies, and changing item
  counts;
- are large enough to distinguish enabled and disabled buttons;
- for `inventory_ready`, use an area that the dismantle modal covers or changes,
  so the background cannot match before the modal has actually closed;
- remain at the same location whenever the PlayCover window has the same
  aspect ratio.

The default threshold is `0.90` and matching searches only within `0.8%` of
the recorded position. A different value can be captured with, for example:

```sh
python tools/crop_template.py dismantle_confirm --clickable \
  --threshold 0.86 --search-margin 0.012
```

Check all saved templates against the current live screen:

```sh
python tools/check_templates.py
open template_matches.png
```

The checker prints every raw score and draws accepted regions and click points.
It can also test an existing screenshot:

```sh
python tools/check_templates.py --input path/to/window.png
```

The generated `manifest.json` stores top-left-origin normalized positions, so
the same templates can be used at another window resolution with the same
layout. The workflow falls back to its previous OCR implementation until the
complete five-name set exists.
