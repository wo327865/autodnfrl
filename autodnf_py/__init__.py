"""Root-level launcher shim for the Python AutoDNF implementation."""

from pathlib import Path

# Keep the implementation and its virtual environment isolated in
# python-autodnf/, while making `python -m autodnf_py` work from repo root.
_implementation = Path(__file__).resolve().parent.parent / "python-autodnf" / "autodnf_py"
__path__.append(str(_implementation))
