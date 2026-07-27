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

Press **Control+T** at any time to stop a running workflow, even while the game
window has focus. The global shortcut uses the same Accessibility permission
required for game input.

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

## AI vision fallback (optional)

The deterministic OCR state machine remains the primary controller. When a
known **non-dungeon** state times out, an optional Gemini fallback can inspect
one compact screenshot and recommend `wait`, a harmless text-button click, a
popup close, or human help. It is never called after dungeon evidence is
detected.

Gemini 2.5 Flash-Lite is the default because it accepts image input and has a
free API tier. Create a key in Google AI Studio, keep it out of the repository,
and enable advice:

```sh
export GEMINI_API_KEY="your-key"
python -m autodnf_py run --battle --execute --debug --vision-fallback
```

Advice mode automatically performs only `wait`; suggested clicks are printed
and the workflow stops. To permit safe clicks that also pass local allow-list,
OCR uniqueness, coordinate, and confidence checks:

```sh
python -m autodnf_py run --battle --execute --debug \
  --vision-fallback --vision-auto-act
```

The fallback sends at most three screenshots per process, with at least 20
seconds between calls. These can be tightened without code changes:

```sh
export AUTODNF_VISION_MAX_CALLS=2
export AUTODNF_VISION_MIN_INTERVAL=30
export AUTODNF_VISION_MODEL=gemini-2.5-flash-lite
```

Free-tier screenshots may be used by Google to improve its products. Do not
enable the cloud fallback if the game window contains information you do not
want to send to an external API.

## Object-detector data

`capture` saves read-only game frames for the custom loot-pile detector. See
[dataset/README.md](dataset/README.md) for collection and annotation steps.

During reward collection, the workflow lazily loads
`runs/detector/loot_piles/weights/best.pt` and uses the highest-confidence
detection in the playable world area to center the pile. Bottom HUD detections
are ignored, and reward-specific OCR remains available as a fallback.
Every initial pile observation is confirmed on a fresh frame after 200 ms so
airborne drops can settle before repositioning and collection.
Full-map exploration uses phase correlation on the gameplay background to
detect horizontal camera translation and stop reliably at map edges despite
local character animation.
Reward guidance arrows are intentionally ignored because combat effects can
imitate their cyan colour. Pile repositioning uses a `0.38–0.62` centre band and
stops after at most four improving moves, or immediately on an overshoot or
non-improving move, to avoid left/right oscillation.
