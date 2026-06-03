"""EDI test file generators package.

This package will be populated by a sibling agent with generator modules for
each X12 transaction type. The load test runner expects the following interface:

    from tests.generators import generate_file

    # Generate an X12 file of approximately target_bytes for the given type
    content: bytes = generate_file(
        tx_type="837p",           # one of: 837p, 837i, 837d, 835, 270, 271, 276, 278
        target_bytes=524288,      # approximate target file size
        adversarial=False,        # if True, generate intentionally malformed content
    )

Until the real generators are in place, this module provides a stub that
generates minimal valid-looking X12 content padded to the target size.
"""

from typing import Optional


# X12 delimiters
ELEMENT_SEP = "*"
SEGMENT_TERM = "~"
COMPONENT_SEP = ":"
REPETITION_SEP = "^"


def _minimal_isa_gs_header(tx_type: str) -> str:
    """Generate minimal ISA/GS/ST envelope header segments."""
    tx_map = {
        "837p": ("837", "005010X222A1", "HC"),
        "837i": ("837", "005010X223A2", "HC"),
        "837d": ("837", "005010X224A2", "HC"),
        "835": ("835", "005010X221A1", "HP"),
        "270": ("270", "005010X279A1", "HS"),
        "271": ("271", "005010X279A1", "HB"),
        "276": ("276", "005010X212", "HN"),
        "278": ("278", "005010X217", "HI"),
    }
    st_code, impl_ref, gs_code = tx_map.get(tx_type, ("837", "005010X222A1", "HC"))

    isa = (
        f"ISA*00*          *00*          "
        f"*ZZ*SENDER         *ZZ*RECEIVER       "
        f"*230101*1200*{REPETITION_SEP}*00501*000000001*0*P*{COMPONENT_SEP}{SEGMENT_TERM}"
    )
    gs = (
        f"GS*{gs_code}*SENDER*RECEIVER*20230101*1200*1*X*{impl_ref}{SEGMENT_TERM}"
    )
    st = f"ST*{st_code}*0001*{impl_ref}{SEGMENT_TERM}"
    return isa + "\n" + gs + "\n" + st + "\n"


def _minimal_trailer() -> str:
    """Generate minimal SE/GE/IEA trailer segments."""
    return (
        f"SE*3*0001{SEGMENT_TERM}\n"
        f"GE*1*1{SEGMENT_TERM}\n"
        f"IEA*1*000000001{SEGMENT_TERM}\n"
    )


def generate_file(
    tx_type: str,
    target_bytes: int = 524288,
    adversarial: bool = False,
) -> bytes:
    """Generate a test X12 file of approximately target_bytes.

    This is a stub implementation. The sibling generator agent will replace this
    with realistic X12 content that includes proper claim loops, NPI numbers,
    diagnosis codes, etc.

    Args:
        tx_type: Transaction type (837p, 837i, 837d, 835, 270, 271, 276, 278)
        target_bytes: Approximate target file size in bytes
        adversarial: If True, generate intentionally malformed content

    Returns:
        bytes: The generated X12 file content
    """
    tx_type = tx_type.lower()

    if adversarial:
        return _generate_adversarial(tx_type, target_bytes)

    header = _minimal_isa_gs_header(tx_type)
    trailer = _minimal_trailer()
    envelope_size = len(header.encode()) + len(trailer.encode())

    # Pad with repeated CLM/CLP segments to reach target size
    padding_needed = max(0, target_bytes - envelope_size)
    pad_segment = f"NTE*ADD*{'X' * 76}{SEGMENT_TERM}\n"
    pad_bytes = len(pad_segment.encode())
    repeat_count = (padding_needed // pad_bytes) + 1

    body = pad_segment * repeat_count
    content = header + body[:padding_needed] + trailer
    return content.encode("ascii", errors="replace")


def _generate_adversarial(tx_type: str, target_bytes: int) -> bytes:
    """Generate intentionally malformed X12 for adversarial testing."""
    # Truncated file — missing trailer
    header = _minimal_isa_gs_header(tx_type)
    return header[:min(len(header), target_bytes)].encode("ascii", errors="replace")
