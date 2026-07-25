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
python -m autodnf_py party --execute
python -m autodnf_py battle --execute
```

Commands are dry-run by default. `--execute` is required before any click or
keyboard input is sent.

`run` completes the town-to-party formation path. `battle` resumes only from an
already active dungeon. The battle routine is intentionally conservative and
stops on an unrecognized state.
