"""Generate sample X12 835 remittance advice files used in tests.

The generated files are designed to satisfy the pyx12 pre-check validation
for the 005010X221A1 implementation guide.  This helper can be invoked
manually whenever we need to refresh the static fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

SEGMENT_TERMINATOR = "~"
ELEMENT_SEPARATOR = "*"
SUBELEMENT_SEPARATOR = ":"


@dataclass
class PaymentContext:
    today: date
    payer_id: str
    payee_id: str
    interchange_control: str
    group_control: str
    transaction_control: str

    @property
    def isa_segment(self) -> str:
        return (
            "ISA" + ELEMENT_SEPARATOR
            + "00" + ELEMENT_SEPARATOR + " " * 10 + ELEMENT_SEPARATOR
            + "00" + ELEMENT_SEPARATOR + " " * 10 + ELEMENT_SEPARATOR
            + "ZZ" + ELEMENT_SEPARATOR + f"{self.payer_id:<15}" + ELEMENT_SEPARATOR
            + "ZZ" + ELEMENT_SEPARATOR + f"{self.payee_id:<15}" + ELEMENT_SEPARATOR
            + self.today.strftime("%y%m%d") + ELEMENT_SEPARATOR
            + self.today.strftime("%H%M") + ELEMENT_SEPARATOR
            + "^" + ELEMENT_SEPARATOR
            + "00501" + ELEMENT_SEPARATOR
            + f"{self.interchange_control:>9}" + ELEMENT_SEPARATOR
            + "0" + ELEMENT_SEPARATOR
            + "T" + ELEMENT_SEPARATOR
            + ":" + SEGMENT_TERMINATOR
        )

    @property
    def gs_segment(self) -> str:
        return (
            "GS" + ELEMENT_SEPARATOR
            + "HP" + ELEMENT_SEPARATOR
            + "PAYOR" + ELEMENT_SEPARATOR
            + "PAYEE" + ELEMENT_SEPARATOR
            + self.today.strftime("%Y%m%d") + ELEMENT_SEPARATOR
            + self.today.strftime("%H%M") + ELEMENT_SEPARATOR
            + self.group_control + ELEMENT_SEPARATOR
            + "X" + ELEMENT_SEPARATOR
            + "005010X221A1" + SEGMENT_TERMINATOR
        )


def build_transaction(ctx: PaymentContext, claim_count: int) -> list[str]:
    segments: list[str] = []
    st_control = ctx.transaction_control
    segments.append(ELEMENT_SEPARATOR.join(["ST", "835", st_control]) + SEGMENT_TERMINATOR)

    total_payment = claim_count * 200.00
    segments.append(
        ELEMENT_SEPARATOR.join(
            [
                "BPR",
                "I",
                f"{total_payment:.2f}",
                "C",
                "ACH",
                "CTX",
                "01",
                "123456789",
                "DA",
                "123456789012",
                "1512345678",
                "",
                "01",
                "987654321",
                "DA",
                "987654321098",
                ctx.today.strftime("%Y%m%d"),
            ]
        )
        + SEGMENT_TERMINATOR
    )

    segments.append(ELEMENT_SEPARATOR.join(["TRN", "1", "1234567890", "1512345678"]) + SEGMENT_TERMINATOR)
    segments.append(ELEMENT_SEPARATOR.join(["DTM", "405", ctx.today.strftime("%Y%m%d")]) + SEGMENT_TERMINATOR)

    segments.extend(
        [
            ELEMENT_SEPARATOR.join([
                "N1",
                "PR",
                "PRIMARY HEALTH PLAN",
                "XV",
                "MCPAYERPLAN01",
            ])
            + SEGMENT_TERMINATOR,
            ELEMENT_SEPARATOR.join(["N3", "123 HEALTH ST"])
            + SEGMENT_TERMINATOR,
            ELEMENT_SEPARATOR.join(["N4", "METROPOLIS", "NY", "10101"])
            + SEGMENT_TERMINATOR,
            ELEMENT_SEPARATOR.join(
                ["PER", "BL", "EDI SUPPORT", "TE", "8005551212", "EM", "support@primaryhealth.com"]
            )
            + SEGMENT_TERMINATOR,
        ]
    )

    segments.extend(
        [
            ELEMENT_SEPARATOR.join(["N1", "PE", "PAYEE CLINIC", "XX", "1234567893"]) + SEGMENT_TERMINATOR,
            ELEMENT_SEPARATOR.join(["N3", "456 CLINIC AVE"])
            + SEGMENT_TERMINATOR,
            ELEMENT_SEPARATOR.join(["N4", "GOTHAM", "NY", "10001"])
            + SEGMENT_TERMINATOR,
            ELEMENT_SEPARATOR.join(["REF", "TJ", "987654321"])
            + SEGMENT_TERMINATOR,
        ]
    )

    for idx in range(1, claim_count + 1):
        claim_id = f"CLM{idx:06d}"
        patient_id = f"PM{idx:06d}"
        segments.append(ELEMENT_SEPARATOR.join(["LX", str(idx)]) + SEGMENT_TERMINATOR)
        segments.append(
            ELEMENT_SEPARATOR.join(
                [
                    "CLP",
                    f"{idx:06d}",
                    "1",
                    "250.00",
                    "200.00",
                    "50.00",
                    "MC",
                    claim_id,
                    "11",
                    "1",
                ]
            )
            + SEGMENT_TERMINATOR
        )
        segments.append(ELEMENT_SEPARATOR.join(["CAS", "CO", "45", "25.00"]) )
        segments[-1] += SEGMENT_TERMINATOR
        segments.append(ELEMENT_SEPARATOR.join(["CAS", "PR", "1", "25.00"]) )
        segments[-1] += SEGMENT_TERMINATOR
        segments.append(
            ELEMENT_SEPARATOR.join([
                "NM1",
                "QC",
                "1",
                "PATIENT",
                f"{idx:06d}",
                "",
                "",
                "",
                "MI",
                patient_id,
            ])
            + SEGMENT_TERMINATOR
        )
        segments.append(ELEMENT_SEPARATOR.join(["DTM", "232", ctx.today.strftime("%Y%m%d")]) + SEGMENT_TERMINATOR)
        segments.append(ELEMENT_SEPARATOR.join(["DTM", "233", ctx.today.strftime("%Y%m%d")]) + SEGMENT_TERMINATOR)
        segments.append(
            ELEMENT_SEPARATOR.join(
                [
                    "SVC",
                    SUBELEMENT_SEPARATOR.join(["HC", "99213"]),
                    "250.00",
                    "200.00",
                    "1",
                ]
            )
            + SEGMENT_TERMINATOR
        )
        segments.append(ELEMENT_SEPARATOR.join(["REF", "6R", f"{idx:06d}"]) + SEGMENT_TERMINATOR)
        segments.append(ELEMENT_SEPARATOR.join(["AMT", "B6", "200.00"]) + SEGMENT_TERMINATOR)

    segments.append(ELEMENT_SEPARATOR.join(["SE", str(len(segments) + 1), st_control]) + SEGMENT_TERMINATOR)
    return segments


def build_document(claim_count: int, interchange_control: int) -> str:
    ctx = PaymentContext(
        today=date(2023, 1, 5),
        payer_id="PAYORID",
        payee_id="PAYEEID",
        interchange_control=f"{interchange_control:09d}",
        group_control="1",
        transaction_control="0001",
    )

    segments = [ctx.isa_segment, ctx.gs_segment]
    segments.extend(build_transaction(ctx, claim_count))
    segments.append(ELEMENT_SEPARATOR.join(["GE", "1", ctx.group_control]) + SEGMENT_TERMINATOR)
    segments.append(
        ELEMENT_SEPARATOR.join(["IEA", "1", ctx.interchange_control]) + SEGMENT_TERMINATOR
    )
    return "".join(segments)


def write_sample(path: Path, claim_count: int, interchange_control: int) -> None:
    content = build_document(claim_count, interchange_control)
    path.write_text(content)
    print(f"Wrote {path} ({len(content)} bytes) with {claim_count} claims")


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    write_sample(base_dir / "x12_835_small.txt", claim_count=8, interchange_control=835)
    # Claim count selected to produce a file a little over 500 KB to exercise large file paths.
    write_sample(base_dir / "x12_835_large.x12", claim_count=2400, interchange_control=836)


if __name__ == "__main__":
    main()
