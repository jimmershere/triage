"""837 implementation-guide validation for all three claim variants:

- **837P** Professional   — ASC X12N 005010X222A1 (service lines in SV1)
- **837I** Institutional  — ASC X12N 005010X223A2 (service lines in SV2)
- **837D** Dental         — ASC X12N 005010X224A2 (service lines in SV3)

The three guides share the BHT / hierarchical-loop / claim structure, so a
single variant-aware walker drives all of them. Each variant differs in its
implementation version, CLM05-2 facility qualifier, whether a diagnosis is
required, and which service-line segment carries the lines.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..model import (
    ClaimProjection,
    Severity,
    ServiceLineProjection,
    SnipType,
    ValidationIssue,
    ValidationReport,
)
from ..parser import Segment, Transaction

_BALANCE_TOLERANCE = Decimal("0.00")

_HL_BILLING = "20"
_HL_SUBSCRIBER = "22"
_HL_PATIENT = "23"

_ICD10_DX_QUALIFIERS = {"ABK", "ABF", "ABJ", "APR", "BK", "BF", "BJ"}


@dataclass(frozen=True)
class Variant:
    """Per-variant configuration for the 837 walker."""

    code: str               # "P", "I", "D"
    name: str               # human-readable
    version_token: str      # implementation convention substring
    tr3: str                # full TR3 citation
    facility_qualifier: str  # required CLM05-2 value
    diagnosis_required: bool
    service_segment: str    # "SV1", "SV2", "SV3"


VARIANT_837P = Variant(
    "P", "Professional", "005010X222",
    "ASC X12N 005010X222A1 (837P) TR3", "B", True, "SV1",
)
VARIANT_837I = Variant(
    "I", "Institutional", "005010X223",
    "ASC X12N 005010X223A2 (837I) TR3", "A", True, "SV2",
)
VARIANT_837D = Variant(
    "D", "Dental", "005010X224",
    "ASC X12N 005010X224A2 (837D) TR3", "B", False, "SV3",
)


def _to_decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


class _ClaimWalker:
    """Single-pass, variant-aware state machine over one 837 transaction."""

    def __init__(
        self, txn: Transaction, report: ValidationReport, variant: Variant
    ) -> None:
        self.txn = txn
        self.report = report
        self.variant = variant
        self.claims: list[ClaimProjection] = []

        self._billing_name: str | None = None
        self._billing_npi: str | None = None
        self._billing_tax_id: str | None = None
        self._rendering_npi: str | None = None
        self._subscriber_last: str | None = None
        self._subscriber_first: str | None = None
        self._subscriber_id: str | None = None
        self._subscriber_dob: str | None = None
        self._subscriber_gender: str | None = None
        self._filing_indicator: str | None = None
        self._patient_last: str | None = None
        self._patient_first: str | None = None
        self._patient_dob: str | None = None
        self._patient_gender: str | None = None
        self._payer_name: str | None = None
        self._payer_id: str | None = None
        self._current_hl_level: str | None = None

        self._claim: ClaimProjection | None = None
        self._claim_seg: Segment | None = None
        self._line: ServiceLineProjection | None = None
        self._claim_diag_count = 0
        self._claim_has_service_date = False
        self._claim_line_charge = Decimal("0")
        self._claim_refs: set[str] = set()

    # -- issue helper -------------------------------------------------------

    def _issue(
        self,
        snip: SnipType,
        severity: Severity,
        code: str,
        message: str,
        *,
        seg: Segment | None = None,
        segment_id: str | None = None,
        element: int | None = None,
        component: int | None = None,
        loop: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        self.report.add(
            ValidationIssue(
                snip_type=snip,
                severity=severity,
                code=code,
                message=message,
                segment_id=segment_id or (seg.seg_id if seg else None),
                segment_position=seg.position if seg else None,
                element_position=element,
                component_position=component,
                loop_id=loop,
                transaction_set=self.txn.set_code,
                transaction_control=self.txn.control_number,
                claim_id=self._claim.claim_id if self._claim else None,
                expected=expected,
                actual=actual,
                spec_ref=self.variant.tr3,
            )
        )

    # -- main walk ----------------------------------------------------------

    def run(self) -> list[ClaimProjection]:
        self._validate_header()
        for seg in self.txn.segments:
            handler = getattr(self, f"_seg_{seg.seg_id}", None)
            if handler is not None:
                handler(seg)
        self._close_claim()
        self._validate_required_presence()
        return self.claims

    def _validate_header(self) -> None:
        txn = self.txn
        if txn.set_code != "837":
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.ST01.SET",
                f"ST01 must be 837 for a claim transaction; found '{txn.set_code}'.",
                seg=txn.st, element=1, expected="837", actual=txn.set_code,
            )
        version = txn.implementation_version or ""
        if self.variant.version_token not in version:
            self._issue(
                SnipType.GUIDE_SPECIFIC,
                Severity.WARNING if version else Severity.ERROR,
                "GUIDE.ST03.VERSION",
                f"ST03 should reference implementation convention "
                f"{self.variant.version_token} (837{self.variant.code}); "
                f"found '{version or '<empty>'}'.",
                seg=txn.st, element=3,
                expected=self.variant.version_token, actual=version,
            )

    # -- segment handlers ---------------------------------------------------

    def _seg_BHT(self, seg: Segment) -> None:
        if seg.elem(1) != "0019":
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.BHT01.STRUCTURE",
                f"BHT01 must be 0019 (hierarchical structure); found "
                f"'{seg.elem(1)}'.",
                seg=seg, element=1, expected="0019", actual=seg.elem(1),
            )
        if seg.elem(2) not in ("00", "18"):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.BHT02.PURPOSE",
                f"BHT02 transaction purpose must be 00 or 18; found "
                f"'{seg.elem(2)}'.",
                seg=seg, element=2, expected="00 or 18", actual=seg.elem(2),
            )
        if not seg.has_elem(3):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.BHT03.REF",
                "BHT03 originator application transaction identifier is required.",
                seg=seg, element=3,
            )
        if seg.elem(6) not in ("31", "CH", "RP"):
            self._issue(
                SnipType.SITUATIONAL, Severity.WARNING, "SIT.BHT06.CLAIM_TYPE",
                f"BHT06 claim/encounter identifier should be 31, CH or RP; "
                f"found '{seg.elem(6)}'.",
                seg=seg, element=6, expected="31, CH or RP", actual=seg.elem(6),
            )

    def _seg_HL(self, seg: Segment) -> None:
        self._current_hl_level = seg.elem(3)
        if self._current_hl_level == _HL_BILLING:
            self._billing_name = None
            self._billing_npi = None
            self._billing_tax_id = None
        elif self._current_hl_level == _HL_SUBSCRIBER:
            self._subscriber_last = None
            self._subscriber_first = None
            self._subscriber_id = None
            self._patient_last = None
            self._patient_first = None

    def _seg_SBR(self, seg: Segment) -> None:
        self._filing_indicator = seg.elem(9) or self._filing_indicator
        if not seg.has_elem(1):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.SBR01.PAYER_SEQ",
                "SBR01 payer responsibility sequence number code is required.",
                seg=seg, element=1, loop="2000B",
            )

    def _seg_NM1(self, seg: Segment) -> None:
        entity = seg.elem(1)
        if entity == "41":
            self._check_name_entity(seg, "submitter", "1000A")
        elif entity == "40":
            self._check_name_entity(seg, "receiver", "1000B")
        elif entity == "85":
            self._billing_name = seg.elem(3)
            if seg.elem(8) == "XX":
                self._billing_npi = seg.elem(9)
            self._check_provider_npi(seg, "billing provider", "2010AA")
        elif entity == "82":
            if seg.elem(8) == "XX":
                self._rendering_npi = seg.elem(9)
            self._check_provider_npi(seg, "rendering provider", "2310B")
        elif entity == "IL":
            self._subscriber_last = seg.elem(3)
            self._subscriber_first = seg.elem(4)
            self._subscriber_id = seg.elem(9)
            if not seg.has_elem(9):
                self._issue(
                    SnipType.REQUIREMENT, Severity.ERROR, "REQ.NM109.SUBSCRIBER_ID",
                    "NM109 subscriber primary identifier is required in loop 2010BA.",
                    seg=seg, element=9, loop="2010BA",
                )
        elif entity == "QC":
            self._patient_last = seg.elem(3)
            self._patient_first = seg.elem(4)
        elif entity == "PR":
            self._payer_name = seg.elem(3)
            self._payer_id = seg.elem(9)
            if not seg.has_elem(9):
                self._issue(
                    SnipType.REQUIREMENT, Severity.ERROR, "REQ.NM109.PAYER_ID",
                    "NM109 payer identifier is required in loop 2010BB.",
                    seg=seg, element=9, loop="2010BB",
                )

    def _seg_DMG(self, seg: Segment) -> None:
        if self._current_hl_level == _HL_PATIENT:
            self._patient_dob = seg.elem(2) or self._patient_dob
            self._patient_gender = seg.elem(3) or self._patient_gender
        else:
            self._subscriber_dob = seg.elem(2) or self._subscriber_dob
            self._subscriber_gender = seg.elem(3) or self._subscriber_gender

    def _seg_REF(self, seg: Segment) -> None:
        qualifier = seg.elem(1)
        if qualifier in ("EI", "SY") and self._current_hl_level == _HL_BILLING:
            self._billing_tax_id = seg.elem(2)
        if self._claim is not None and qualifier:
            self._claim_refs.add(qualifier)

    def _seg_CLM(self, seg: Segment) -> None:
        self._close_claim()
        self._claim_seg = seg
        self._claim_refs = set()
        self._claim_diag_count = 0
        self._claim_has_service_date = False
        self._claim_line_charge = Decimal("0")

        facility = seg.comp(5, 1)
        facility_qualifier = seg.comp(5, 2)
        frequency = seg.comp(5, 3)
        institutional = self.variant.code == "I"

        claim = ClaimProjection(
            claim_id=seg.elem(1) or None,
            patient_control_number=seg.elem(1) or None,
            total_charge=seg.elem(2) or None,
            place_of_service=None if institutional else (facility or None),
            facility_code=facility_qualifier or None,
            type_of_bill=facility if institutional else None,
            claim_frequency_code=frequency or None,
            provider_signature_on_file=seg.elem(6) or None,
            filing_indicator_code=self._filing_indicator,
            billing_provider_name=self._billing_name,
            billing_provider_npi=self._billing_npi,
            billing_provider_tax_id=self._billing_tax_id,
            rendering_provider_npi=self._rendering_npi,
            subscriber_last_name=self._subscriber_last,
            subscriber_first_name=self._subscriber_first,
            subscriber_id=self._subscriber_id,
            patient_last_name=self._patient_last or self._subscriber_last,
            patient_first_name=self._patient_first or self._subscriber_first,
            patient_dob=self._patient_dob or self._subscriber_dob,
            patient_gender=self._patient_gender or self._subscriber_gender,
            payer_name=self._payer_name,
            payer_id=self._payer_id,
        )
        self._claim = claim

        if not seg.has_elem(1):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.CLM01.ID",
                "CLM01 claim submitter identifier is required.",
                seg=seg, element=1, loop="2300",
            )
        elif len(seg.elem(1)) > 38:
            self._issue(
                SnipType.GUIDE_SPECIFIC, Severity.ERROR, "GUIDE.CLM01.LENGTH",
                f"CLM01 must not exceed 38 characters; found {len(seg.elem(1))}.",
                seg=seg, element=1, loop="2300",
            )
        total = _to_decimal(seg.elem(2))
        if total is None:
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.CLM02.CHARGE",
                f"CLM02 total claim charge amount is required and must be "
                f"numeric; found '{seg.elem(2)}'.",
                seg=seg, element=2, loop="2300",
            )
        elif total < 0:
            self._issue(
                SnipType.GUIDE_SPECIFIC, Severity.ERROR, "GUIDE.CLM02.NEGATIVE",
                f"CLM02 total claim charge must not be negative; found {total}.",
                seg=seg, element=2, loop="2300",
            )
        if facility_qualifier and facility_qualifier != self.variant.facility_qualifier:
            self._issue(
                SnipType.GUIDE_SPECIFIC, Severity.ERROR, "GUIDE.CLM05.QUALIFIER",
                f"CLM05-2 facility code qualifier must be "
                f"'{self.variant.facility_qualifier}' for 837{self.variant.code}; "
                f"found '{facility_qualifier}'.",
                seg=seg, element=5, component=2, loop="2300",
                expected=self.variant.facility_qualifier, actual=facility_qualifier,
            )
        if institutional and facility and not (3 <= len(facility) <= 4 and facility.isdigit()):
            self._issue(
                SnipType.GUIDE_SPECIFIC, Severity.ERROR, "GUIDE.CLM05.TYPE_OF_BILL",
                f"CLM05-1 type of bill must be a 3-4 digit NUBC code for 837I; "
                f"found '{facility}'.",
                seg=seg, element=5, component=1, loop="2300", actual=facility,
            )

    def _seg_HI(self, seg: Segment) -> None:
        if self._claim is None:
            return
        for idx in range(1, seg.max_element + 1):
            components = seg.components(idx)
            if not components or not components[0]:
                continue
            qualifier = components[0]
            code = components[1] if len(components) > 1 else ""
            if qualifier not in _ICD10_DX_QUALIFIERS:
                continue
            if not code:
                self._issue(
                    SnipType.REQUIREMENT, Severity.ERROR, "REQ.HI.DX_CODE",
                    f"HI{idx:02d} diagnosis qualifier '{qualifier}' has no "
                    "diagnosis code.",
                    seg=seg, element=idx, component=2, loop="2300",
                )
                continue
            self._claim_diag_count += 1
            self._claim.diagnosis_codes.append(code)

    def _seg_LX(self, seg: Segment) -> None:
        self._flush_line()
        self._line = ServiceLineProjection(line_no=seg.elem(1) or None)

    # --- service-line segments: professional / institutional / dental ---

    def _seg_SV1(self, seg: Segment) -> None:
        if self._claim is None:
            return
        line = self._ensure_line()
        line.procedure_qualifier = seg.comp(1, 1) or None
        line.procedure_code = seg.comp(1, 2) or None
        line.modifiers = [seg.comp(1, c) for c in range(3, 7) if seg.comp(1, c)]
        line.charge_amount = seg.elem(2) or None
        line.unit_basis = seg.elem(3) or None
        line.units = seg.elem(4) or None
        line.place_of_service = seg.elem(5) or self._claim.place_of_service
        line.diagnosis_pointers = [p for p in seg.components(7) if p]

        if not seg.has_elem(1):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.SV101.PROCEDURE",
                "SV101 composite medical procedure identifier is required.",
                seg=seg, element=1, loop="2400",
            )
        self._register_charge(seg, 2, "SV102")
        if not seg.has_elem(4):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.SV104.UNITS",
                "SV104 service unit count is required.",
                seg=seg, element=4, loop="2400",
            )
        self._check_diagnosis_pointers(seg, line)

    def _seg_SV2(self, seg: Segment) -> None:
        if self._claim is None:
            return
        line = self._ensure_line()
        line.revenue_code = seg.elem(1) or None
        line.procedure_code = seg.comp(2, 2) or seg.elem(2) or None
        line.procedure_qualifier = seg.comp(2, 1) or None
        line.modifiers = [seg.comp(2, c) for c in range(3, 7) if seg.comp(2, c)]
        line.charge_amount = seg.elem(3) or None
        line.unit_basis = seg.elem(4) or None
        line.units = seg.elem(5) or None

        if not seg.has_elem(1):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.SV201.REVENUE",
                "SV201 revenue code is required on an institutional service line.",
                seg=seg, element=1, loop="2400",
            )
        self._register_charge(seg, 3, "SV203")

    def _seg_SV3(self, seg: Segment) -> None:
        if self._claim is None:
            return
        line = self._ensure_line()
        line.procedure_qualifier = seg.comp(1, 1) or None
        line.procedure_code = seg.comp(1, 2) or None
        line.modifiers = [seg.comp(1, c) for c in range(3, 7) if seg.comp(1, c)]
        line.charge_amount = seg.elem(2) or None
        line.place_of_service = seg.elem(3) or self._claim.place_of_service

        if not seg.has_elem(1):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.SV301.PROCEDURE",
                "SV301 composite dental procedure identifier is required.",
                seg=seg, element=1, loop="2400",
            )
        self._register_charge(seg, 2, "SV302")

    def _seg_DTP(self, seg: Segment) -> None:
        qualifier = seg.elem(1)
        value = seg.elem(3)
        if qualifier == "472":
            self._claim_has_service_date = True
            if self._line is not None:
                self._line.service_date = value
        elif qualifier == "434" and self._claim is not None:
            # Institutional statement-covers period (situational service date).
            self._claim_has_service_date = True
            parts = value.split("-") if value else []
            if parts:
                self._claim.statement_from_date = parts[0]
            if len(parts) > 1:
                self._claim.statement_to_date = parts[1]

    # -- claim / line lifecycle --------------------------------------------

    def _ensure_line(self) -> ServiceLineProjection:
        if self._line is None:
            self._line = ServiceLineProjection()
        return self._line

    def _register_charge(self, seg: Segment, element: int, ref: str) -> None:
        charge = _to_decimal(seg.elem(element))
        if charge is None:
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.LINE.CHARGE",
                f"{ref} line item charge amount is required and must be "
                f"numeric; found '{seg.elem(element)}'.",
                seg=seg, element=element, loop="2400",
            )
        else:
            self._claim_line_charge += charge

    def _check_diagnosis_pointers(
        self, seg: Segment, line: ServiceLineProjection
    ) -> None:
        for ptr in line.diagnosis_pointers:
            try:
                pos = int(ptr)
            except ValueError:
                self._issue(
                    SnipType.SITUATIONAL, Severity.ERROR, "SIT.SV107.POINTER",
                    f"SV107 diagnosis code pointer '{ptr}' is not numeric.",
                    seg=seg, element=7, loop="2400",
                )
                continue
            if self._claim_diag_count and not (1 <= pos <= self._claim_diag_count):
                self._issue(
                    SnipType.SITUATIONAL, Severity.ERROR, "SIT.SV107.POINTER_RANGE",
                    f"SV107 diagnosis pointer {pos} does not reference any of the "
                    f"{self._claim_diag_count} diagnosis code(s) on the claim.",
                    seg=seg, element=7, loop="2400",
                    expected=f"1-{self._claim_diag_count}", actual=str(pos),
                )

    def _flush_line(self) -> None:
        if self._line is not None and self._claim is not None:
            self._claim.service_lines.append(self._line)
        self._line = None

    def _close_claim(self) -> None:
        self._flush_line()
        if self._claim is None:
            return
        claim = self._claim
        claim_seg = self._claim_seg

        if self.variant.diagnosis_required and self._claim_diag_count == 0:
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.HI.MISSING",
                "Claim has no HI health-care diagnosis code (loop 2300 HI required).",
                seg=claim_seg, segment_id="HI", loop="2300",
            )
        if not claim.service_lines:
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.LX.MISSING",
                "Claim has no service line (loop 2400 required).",
                seg=claim_seg, segment_id="LX", loop="2400",
            )
        if not self._claim_has_service_date:
            self._issue(
                SnipType.SITUATIONAL, Severity.ERROR, "SIT.DTP472.MISSING",
                "No service date (DTP*472 / DTP*434) found for the claim.",
                seg=claim_seg, segment_id="DTP", loop="2400",
            )
        total = _to_decimal(claim.total_charge)
        if total is not None and claim.service_lines:
            diff = abs(total - self._claim_line_charge)
            if diff > _BALANCE_TOLERANCE:
                self._issue(
                    SnipType.LINE_BALANCING, Severity.ERROR, "BAL.CLM02.LINE_SUM",
                    f"CLM02 total charge {total} does not equal the sum of "
                    f"service-line charges {self._claim_line_charge} "
                    f"(difference {diff}).",
                    seg=claim_seg, segment_id="CLM", element=2, loop="2300",
                    expected=str(total), actual=str(self._claim_line_charge),
                )
        if claim.claim_frequency_code in ("7", "8") and "F8" not in self._claim_refs:
            self._issue(
                SnipType.SITUATIONAL, Severity.ERROR, "SIT.REF.F8_REQUIRED",
                f"Claim frequency '{claim.claim_frequency_code}' "
                "(replacement/void) requires REF*F8 payer claim control number.",
                seg=claim_seg, segment_id="REF", loop="2300",
            )

        self.claims.append(claim)
        self._claim = None
        self._claim_seg = None

    # -- shared presence / format checks -----------------------------------

    def _check_name_entity(self, seg: Segment, label: str, loop: str) -> None:
        if not seg.has_elem(3):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.NM103.NAME",
                f"NM103 {label} name is required in loop {loop}.",
                seg=seg, element=3, loop=loop,
            )

    def _check_provider_npi(self, seg: Segment, label: str, loop: str) -> None:
        if seg.elem(8) == "XX":
            ident = seg.elem(9)
            if ident and not (ident.isdigit() and len(ident) == 10):
                self._issue(
                    SnipType.GUIDE_SPECIFIC, Severity.ERROR, "GUIDE.NM109.NPI_FORMAT",
                    f"NM109 {label} NPI must be exactly 10 digits; found "
                    f"'{ident}'.",
                    seg=seg, element=9, loop=loop, actual=ident,
                )

    def _validate_required_presence(self) -> None:
        segs = self.txn.segments
        if not any(s.seg_id == "BHT" for s in segs):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.BHT.MISSING",
                "BHT beginning-of-hierarchical-transaction segment is required.",
                segment_id="BHT",
            )
        if not any(s.seg_id == "NM1" and s.elem(1) == "41" for s in segs):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.SUBMITTER.MISSING",
                "Loop 1000A submitter name (NM1*41) is required.",
                segment_id="NM1", loop="1000A",
            )
        if not any(s.seg_id == "NM1" and s.elem(1) == "40" for s in segs):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.RECEIVER.MISSING",
                "Loop 1000B receiver name (NM1*40) is required.",
                segment_id="NM1", loop="1000B",
            )
        if not any(s.seg_id == "NM1" and s.elem(1) == "85" for s in segs):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.BILLING.MISSING",
                "Loop 2010AA billing provider name (NM1*85) is required.",
                segment_id="NM1", loop="2010AA",
            )
        if not any(s.seg_id == "CLM" for s in segs):
            self._issue(
                SnipType.REQUIREMENT, Severity.ERROR, "REQ.CLM.MISSING",
                "Transaction contains no claim (loop 2300 CLM required).",
                segment_id="CLM", loop="2300",
            )


def _select_variant(txn: Transaction) -> Variant:
    version = txn.implementation_version or ""
    if VARIANT_837I.version_token in version:
        return VARIANT_837I
    if VARIANT_837D.version_token in version:
        return VARIANT_837D
    return VARIANT_837P


def validate_837(txn: Transaction, report: ValidationReport) -> list[ClaimProjection]:
    """Validate one 837 transaction, auto-selecting the P/I/D variant."""
    return _ClaimWalker(txn, report, _select_variant(txn)).run()


def validate_837p(txn: Transaction, report: ValidationReport) -> list[ClaimProjection]:
    return _ClaimWalker(txn, report, VARIANT_837P).run()


def validate_837i(txn: Transaction, report: ValidationReport) -> list[ClaimProjection]:
    return _ClaimWalker(txn, report, VARIANT_837I).run()


def validate_837d(txn: Transaction, report: ValidationReport) -> list[ClaimProjection]:
    return _ClaimWalker(txn, report, VARIANT_837D).run()
