# AutoDNF

Python automation for DNF Mobile running in PlayCover. The tool uses macOS
Vision OCR, Quartz input events, and an optional local loot-pile detector.

## Quick start

```sh
cd python-autodnf
source .venv/bin/activate
python -m autodnf_py run --execute --battle --debug
```

The terminal needs **Screen Recording** and **Accessibility** permissions in
macOS Privacy & Security. Press **Control+T** at any time to stop a workflow.

See [python-autodnf/README.md](python-autodnf/README.md) for setup, supported
commands, maintenance automation, and optional vision fallback configuration.
