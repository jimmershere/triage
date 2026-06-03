"""Generate EDI test files at every size step for load testing.

Produces a matrix of transaction types × file sizes, including multi-part
(multiple GS groups in one ISA) and batched (multiple ISA interchanges)
variants.

CLI usage:
    python -m tests.generators.generate_matrix \\
        --output-dir ./loadtest_files \\
        --sizes 512k,1mb,5mb \\
        --types 837p,835 \\
        --dry-run
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time

from .edi_common import wrap_multi_gs, build_batched_isa

# ---------------------------------------------------------------------------
# Size step definitions
# ---------------------------------------------------------------------------
MB = 1024 * 1024
GB = 1024 * MB

SIZE_STEPS = [
    (512 * 1024, "512k"),
    (1 * MB, "1mb"),
    (5 * MB, "5mb"),
    (10 * MB, "10mb"),
    (50 * MB, "50mb"),
    (100 * MB, "100mb"),
    (1 * GB, "1gb"),
    (5 * GB, "5gb"),
]

TYPES = ["837p", "837i", "837d", "835", "270", "271", "276", "278"]

# ---------------------------------------------------------------------------
# Generator dispatch — lazy imports to avoid circular deps
# ---------------------------------------------------------------------------

def _get_generator(tx_type: str):
    """Return (generator_func, count_kwarg, functional_id_code, impl_version) for a type."""
    if tx_type == "837p":
        from .generate_837p import generate_837p, _build_837p_claims
        return generate_837p, _build_837p_claims, "claim_count", "HC", "005010X222A1"
    elif tx_type == "837i":
        from .generate_837i import generate_837i, _build_837i_claims
        return generate_837i, _build_837i_claims, "claim_count", "HC", "005010X223A2"
    elif tx_type == "837d":
        from .generate_837d import generate_837d, _build_837d_claims
        return generate_837d, _build_837d_claims, "claim_count", "HC", "005010X224A2"
    elif tx_type == "835":
        from .generate_835 import generate_835, _build_835_claims
        return generate_835, _build_835_claims, "claim_count", "HP", "005010X221A1"
    elif tx_type == "270":
        from .generate_270 import generate_270, _build_270_members
        return generate_270, _build_270_members, "member_count", "HS", "005010X279A1"
    elif tx_type == "271":
        from .generate_271 import generate_271, _build_271_members
        return generate_271, _build_271_members, "member_count", "HB", "005010X279A1"
    elif tx_type == "276":
        from .generate_276 import generate_276, _build_276_inquiries
        return generate_276, _build_276_inquiries, "inquiry_count", "HN", "005010X212"
    elif tx_type == "278":
        from .generate_278 import generate_278, _build_278_auths
        return generate_278, _build_278_auths, "auth_count", "HI", "005010X217"
    else:
        raise ValueError(f"Unknown transaction type: {tx_type}")


def _generate_multi_gs(tx_type: str, target_bytes: int) -> str:
    """Generate a multi-GS file: multiple ST..SE transaction sets in separate GS groups."""
    gen_func, build_func, count_kwarg, func_id, impl_ver = _get_generator(tx_type)

    # Generate 3 smaller transactions that together approximate target_bytes
    part_size = target_bytes // 3
    random.seed(42)
    parts = []
    for i in range(3):
        result = gen_func(target_bytes=part_size)
        # Extract just the ST..SE portion (strip ISA/GS/GE/IEA)
        segments = result.split("~")
        st_start = None
        se_end = None
        for j, seg in enumerate(segments):
            if seg.startswith("ST*"):
                st_start = j
            if seg.startswith("SE*"):
                se_end = j
        if st_start is not None and se_end is not None:
            tx_content = "~".join(segments[st_start:se_end + 1]) + "~"
            parts.append(tx_content)

    if not parts:
        return gen_func(target_bytes=target_bytes)

    return wrap_multi_gs(
        parts,
        functional_id_code=func_id,
        implementation_version=impl_ver,
    )


def _generate_batched(tx_type: str, target_bytes: int) -> str:
    """Generate a batched file: multiple complete ISA..IEA interchanges."""
    gen_func = _get_generator(tx_type)[0]

    # Generate 3 interchanges
    part_size = target_bytes // 3
    random.seed(42)
    parts = []
    for _ in range(3):
        parts.append(gen_func(target_bytes=part_size))

    return build_batched_isa(parts)


def _parse_sizes(size_str: str) -> list[tuple[int, str]]:
    """Parse comma-separated size labels into (bytes, label) tuples."""
    valid = {label: (sz, label) for sz, label in SIZE_STEPS}
    result = []
    for s in size_str.split(","):
        s = s.strip().lower()
        if s in valid:
            result.append(valid[s])
        else:
            raise ValueError(
                f"Unknown size '{s}'. Valid: {', '.join(valid.keys())}"
            )
    return result


def _parse_types(type_str: str) -> list[str]:
    """Parse comma-separated type labels."""
    result = []
    for t in type_str.split(","):
        t = t.strip().lower()
        if t in TYPES:
            result.append(t)
        else:
            raise ValueError(
                f"Unknown type '{t}'. Valid: {', '.join(TYPES)}"
            )
    return result


def generate_matrix(
    output_dir: str,
    sizes: list[tuple[int, str]] | None = None,
    types: list[str] | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Generate the size-stepped matrix of test files.

    Args:
        output_dir: Directory for generated files.
        sizes: List of (bytes, label) tuples. Defaults to all SIZE_STEPS.
        types: List of transaction type strings. Defaults to all TYPES.
        dry_run: If True, only print what would be generated.

    Returns:
        List of generated file paths.
    """
    sizes = sizes or SIZE_STEPS
    types = types or TYPES
    generated = []

    for tx_type in types:
        for size_bytes, size_label in sizes:
            # Disk space warning for large sizes
            if size_bytes >= 100 * MB:
                # Each type × size generates 3 files (base + multi_gs + batched)
                total_est = size_bytes * 3
                print(
                    f"WARNING: Generating {tx_type}_{size_label} files "
                    f"(~{total_est / GB:.1f} GB total). "
                    "This may take significant time and disk space."
                )

            base_name = f"{tx_type}_{size_label}"

            # Standard file
            path_std = os.path.join(output_dir, f"{base_name}.x12")
            if dry_run:
                print(f"[dry-run] Would generate: {path_std} ({size_bytes:,} bytes)")
            else:
                random.seed(42)
                gen_func = _get_generator(tx_type)[0]
                t0 = time.time()
                content = gen_func(target_bytes=size_bytes)
                elapsed = time.time() - t0
                os.makedirs(output_dir, exist_ok=True)
                with open(path_std, "w") as f:
                    f.write(content)
                actual = len(content.encode())
                print(
                    f"Generated {path_std}: "
                    f"{actual:,} bytes ({elapsed:.1f}s)"
                )
            generated.append(path_std)

            # Multi-GS variant
            path_multi = os.path.join(output_dir, f"{base_name}_multi_gs.x12")
            if dry_run:
                print(f"[dry-run] Would generate: {path_multi} ({size_bytes:,} bytes)")
            else:
                t0 = time.time()
                content = _generate_multi_gs(tx_type, size_bytes)
                elapsed = time.time() - t0
                with open(path_multi, "w") as f:
                    f.write(content)
                actual = len(content.encode())
                print(
                    f"Generated {path_multi}: "
                    f"{actual:,} bytes ({elapsed:.1f}s)"
                )
            generated.append(path_multi)

            # Batched variant
            path_batch = os.path.join(output_dir, f"{base_name}_batched.x12")
            if dry_run:
                print(f"[dry-run] Would generate: {path_batch} ({size_bytes:,} bytes)")
            else:
                t0 = time.time()
                content = _generate_batched(tx_type, size_bytes)
                elapsed = time.time() - t0
                with open(path_batch, "w") as f:
                    f.write(content)
                actual = len(content.encode())
                print(
                    f"Generated {path_batch}: "
                    f"{actual:,} bytes ({elapsed:.1f}s)"
                )
            generated.append(path_batch)

    return generated


def main():
    parser = argparse.ArgumentParser(
        description="Generate EDI test files at every size step for load testing."
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write generated files.",
    )
    parser.add_argument(
        "--sizes",
        default=None,
        help=(
            "Comma-separated size labels. "
            f"Valid: {', '.join(label for _, label in SIZE_STEPS)}. "
            "Default: all sizes."
        ),
    )
    parser.add_argument(
        "--types",
        default=None,
        help=(
            "Comma-separated transaction types. "
            f"Valid: {', '.join(TYPES)}. "
            "Default: all types."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be generated without writing files.",
    )

    args = parser.parse_args()

    sizes = _parse_sizes(args.sizes) if args.sizes else None
    types = _parse_types(args.types) if args.types else None

    generate_matrix(
        output_dir=args.output_dir,
        sizes=sizes,
        types=types,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
