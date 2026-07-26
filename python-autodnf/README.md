# Python AutoDNF

Independent Python implementation of the PlayCover automation. It uses macOS
Vision OCR and Quartz events rather than image crops or an external OCR engine.

## Setup

```sh
cd python-autodnf
source .venv/bin/activate
pip install -r requirements.txt
```

The terminal needs **Screen Recording** and **Accessibility** permissions in
macOS Privacy & Security settings.

## Commands

```sh
python -m autodnf_py scan
python -m autodnf_py run
python -m autodnf_py run --execute
python -m autodnf_py run --battle --execute
python -m autodnf_py party --execute
python -m autodnf_py battle --execute
python -m autodnf_py capture --tag boss_loot --count 20 --interval 0.7
```

Commands are dry-run by default. `--execute` is required before any click or
keyboard input is sent.

`run` completes the town-to-party formation path. `run --battle` continues
through the dungeon, returns to town when fatigue is exhausted, switches to
another character with at least 10 fatigue, and repeats until none remain.
`battle` resumes only the current character from an already active dungeon.
The battle routine is intentionally conservative and stops on an unrecognized
state.

## Object-detector data

`capture` saves read-only game frames for a custom loot/arrow detector. See
[dataset/README.md](dataset/README.md) for collection and annotation steps.

During reward collection, the workflow lazily loads
`runs/detector/loot_piles/weights/best.pt` and uses the highest-confidence
detection in the playable world area to center the pile. Bottom HUD detections
are ignored, and reward-specific OCR remains available as a fallback.
Full-map exploration uses phase correlation on the gameplay background to
detect horizontal camera translation and stop reliably at map edges despite
local character animation.
