"""EDI test file generator library.

Provides generators for all supported X12 transaction types, adversarial
test files, and a size-stepped matrix generator for load testing.
"""
from .generate_837p import generate_837p
from .generate_837i import generate_837i
from .generate_837d import generate_837d
from .generate_835 import generate_835
from .generate_270 import generate_270
from .generate_271 import generate_271
from .generate_276 import generate_276
from .generate_278 import generate_278
from .adversarial import generate_adversarial_files
from .edi_common import (
    SEG, EL, SUB, REP,
    wrap_multi_gs,
    build_batched_isa,
    generate_to_size,
)

# ---------------------------------------------------------------------------
# Unified generate_file interface for the load test runner
# ---------------------------------------------------------------------------

_GENERATORS = {
    "837p": generate_837p,
    "837i": generate_837i,
    "837d": generate_837d,
    "835": generate_835,
    "270": generate_270,
    "271": generate_271,
    "276": generate_276,
    "278": generate_278,
}


def generate_file(
    tx_type: str,
    target_bytes: int = 512 * 1024,
    adversarial: bool = False,
) -> bytes:
    """Generate an X12 test file as bytes.

    This is the unified entry point used by the load test runner.

    Args:
        tx_type: Transaction type key (e.g. '837p', '835', '270').
        target_bytes: Approximate target file size in bytes.
        adversarial: If True, generate a structurally invalid file
                     for negative testing.
    """
    if adversarial:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            files = generate_adversarial_files(td)
            # Return a representative adversarial file
            import os
            # Pick a type-relevant adversarial file, defaulting to shell_injection
            for name in ("shell_injection.x12", "truncated_mid_segment.x12", "empty_file.x12"):
                path = os.path.join(td, name)
                if os.path.exists(path):
                    return open(path, "rb").read()
            # Fallback: return first non-empty adversarial file
            for name in sorted(files):
                path = os.path.join(td, name)
                if os.path.exists(path):
                    content = open(path, "rb").read()
                    if content:
                        return content
        return b""

    gen = _GENERATORS.get(tx_type)
    if gen is None:
        raise ValueError(f"Unknown transaction type: {tx_type!r}. Valid: {sorted(_GENERATORS)}")

    content = gen(target_bytes=target_bytes)
    if isinstance(content, str):
        content = content.encode("utf-8")
    return content


__all__ = [
    "generate_837p",
    "generate_837i",
    "generate_837d",
    "generate_835",
    "generate_270",
    "generate_271",
    "generate_276",
    "generate_278",
    "generate_file",
    "generate_adversarial_files",
    "wrap_multi_gs",
    "build_batched_isa",
    "generate_to_size",
    "SEG",
    "EL",
    "SUB",
    "REP",
]
