# Detector dataset

Capture frames without sending input:

```sh
python -m autodnf_py capture --tag boss_loot --count 20 --interval 0.7
```

Capture varied boss rooms: centre/edge piles, a single remaining item,
partially hidden loot. Do not capture only one map or one character effect.

Annotate the saved images locally:

```sh
python tools/annotate.py dataset/images
```

Controls: draw **one box around each visible loot pile**; press `0` for `loot_pile`.
Shift-click a box to select it, then press
Delete/Backspace to remove it (without a selection, this removes the last box).
Press `s` to save, `n`/`p` for next/previous, and `q` to save and quit. Labels are written in YOLO format under
`dataset/pile-labels/`. This deliberately leaves the earlier individual-item
annotations in `dataset/labels/` untouched.

Only screenshots with at least one saved `loot_pile` box are included when the
dataset split is prepared, so you may annotate a useful subset rather than
every captured image.

For `loot_pile`, draw a tight box around the visible clustered dropped-item
labels/icons. If a pile is cut off by the left, right, or bottom of the playable
world area, let its box touch that edge and annotate only the visible portion.
Do not extend the box behind characters or outside the screen. At runtime, an
edge-touching detection tells the bot to move in that direction, rescan, and
then collect once the pile is fully visible.

The resulting images and labels can then be used to train a custom loot-item
detector (for example a small YOLO model). Keep screenshots private: they may
contain account or character information.

## Standard split and training

Frames captured close together are near duplicates. Keep a whole capture
session in exactly one split to avoid data leakage:

```sh
python tools/prepare_detector_dataset.py
python tools/train_detector.py --epochs 50
```

The split tool creates chronological, session-grouped `train`/`val`/`test`
directories under `dataset/yolo/`. With fewer than three independent capture
sessions, it falls back to a labelled bootstrap frame split; collect more
varied sessions before trusting its metrics. Training selects its model by
validation performance and reports metrics separately on the held-out test split.
