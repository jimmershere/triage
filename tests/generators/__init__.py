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

__all__ = [
    "generate_837p",
    "generate_837i",
    "generate_837d",
    "generate_835",
    "generate_270",
    "generate_271",
    "generate_276",
    "generate_278",
    "generate_adversarial_files",
    "wrap_multi_gs",
    "build_batched_isa",
    "generate_to_size",
    "SEG",
    "EL",
    "SUB",
    "REP",
]
