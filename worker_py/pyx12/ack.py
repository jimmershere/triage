# Local shim to satisfy "import pyx12.ack" in our translators.
from pathlib import Path
from ._cli import run_x12valid  # see below

def generate_ack(input_path: str, outdir: str) -> str:
    """Return path to 997/999 produced by pyx12's validator."""
    return run_x12valid(input_path, outdir)

# _cli.py (same folder)
import subprocess

def run_x12valid(infile: str, outdir: str) -> str:
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True)
    ack_path = out / (Path(infile).stem + ".ack")
    proc = subprocess.run(["x12valid", infile], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    if proc.stdout:
        ack_path.write_text(proc.stdout)
    return str(ack_path)
