"""Fallback translators used when optional dependencies are missing."""
from __future__ import annotations

import decimal
import logging
import uuid

from . import AckRecord, TranslationOutcome, register, Translator
from ._ack_helpers import generate_simple_277ca, generate_simple_999

logger = logging.getLogger(__name__)


class SimpleX12Translator:
    name = "simple-x12"

    def handles(self, detected_type: str, text_sample: str) -> bool:
        return detected_type.startswith("X12")

    def _parse_claims(self, text: str) -> tuple[list[dict], str | None, str | None, str | None]:
        seg_term = "~"
        elem_sep = "*"
        if text.startswith("ISA") and len(text) >= 106:
            elem_sep = text[3]
            seg_term = text[105]
        segments = [segment for segment in text.replace("\r", "").split(seg_term) if segment.strip()]
        claims: list[dict] = []
        current: dict | None = None
        isa_ctrl = None
        gs_functional = None
        st_code = None
        for seg in segments:
            parts = seg.split(elem_sep)
            tag = parts[0].strip().upper()
            if tag == "ISA" and len(parts) >= 14:
                isa_ctrl = parts[13]
            elif tag == "GS" and len(parts) >= 2 and not gs_functional:
                gs_functional = parts[1]
            elif tag == "ST" and len(parts) >= 2 and not st_code:
                st_code = parts[1]
            if tag == "CLM":
                if current:
                    claims.append(current)
                claim_id = parts[1] if len(parts) > 1 else None
                amount = None
                if len(parts) > 2:
                    try:
                        amount = decimal.Decimal(parts[2])
                    except Exception:
                        amount = None
                current = {"claim_id": claim_id, "amount": amount, "raw": seg}
        if current:
            claims.append(current)
        return claims, isa_ctrl, gs_functional, st_code

    def translate(
        self,
        *,
        text: str,
        job_uuid,
        trading_partner_id: str | None,
        uploaded_by: str | None,
        filename: str,
    ) -> TranslationOutcome:
        claims, isa_ctrl, gs_functional, st_code = self._parse_claims(text)
        acknowledgements = [
            AckRecord(
                "999",
                generate_simple_999(
                    job_uuid=job_uuid,
                    trading_partner_id=trading_partner_id,
                    total_claims=len(claims),
                    isa_control=isa_ctrl,
                    gs_functional_code=gs_functional,
                    st_code=st_code,
                ),
            ),
            AckRecord(
                "277CA",
                generate_simple_277ca(
                    job_uuid=job_uuid,
                    trading_partner_id=trading_partner_id,
                    total_claims=len(claims),
                ),
            ),
        ]
        return TranslationOutcome(
            file_type="X12_837_or_other",
            claims=claims,
            acknowledgements=acknowledgements,
        )

    def diagnostics(self) -> dict[str, object]:
        return {"name": self.name, "available": True, "fallback": True}


class SimpleEdifactTranslator:
    name = "simple-edifact"

    def handles(self, detected_type: str, text_sample: str) -> bool:
        return detected_type.startswith("EDIFACT")

    def _parse_lines(self, text: str) -> tuple[list[dict], str | None]:
        seg_term = "'"
        elem_sep = "+"
        comp_sep = ":"
        segments = [segment for segment in text.replace("\r", "").split(seg_term) if segment.strip()]
        order_lines: list[dict] = []
        current: dict | None = None
        doc_no = None
        for seg in segments:
            parts = seg.split(elem_sep)
            tag = parts[0].strip().upper()
            if tag == "BGM" and len(parts) >= 3:
                doc_no = parts[2]
            if tag == "LIN":
                if current:
                    order_lines.append(current)
                line_no = None
                if len(parts) >= 2:
                    try:
                        line_no = int(parts[1])
                    except Exception:
                        line_no = None
                item_id = None
                if len(parts) >= 4:
                    comp = parts[3].split(comp_sep)
                    item_id = comp[0] if comp else None
                current = {"line_no": line_no, "item_id": item_id, "qty": None, "price": None, "raw": seg}
            elif tag == "QTY" and current:
                comp = parts[1].split(comp_sep) if len(parts) > 1 else []
                if len(comp) >= 2:
                    try:
                        current["qty"] = decimal.Decimal(comp[1])
                    except Exception:
                        pass
            elif tag == "PRI" and current:
                comp = parts[1].split(comp_sep) if len(parts) > 1 else []
                if len(comp) >= 2:
                    try:
                        current["price"] = decimal.Decimal(comp[1])
                    except Exception:
                        pass
        if current:
            order_lines.append(current)
        return order_lines, doc_no

    def translate(
        self,
        *,
        text: str,
        job_uuid,
        trading_partner_id: str | None,
        uploaded_by: str | None,
        filename: str,
    ) -> TranslationOutcome:
        lines, doc_no = self._parse_lines(text)
        contrl = "CONTRL-LIKE ACK\nDoc: {doc}\nAccepted lines: {count}\nStatus: ACCEPTED".format(
            doc=doc_no or "UNKNOWN",
            count=len(lines),
        )
        return TranslationOutcome(
            file_type="EDIFACT_ORDERS_or_other",
            order_lines=lines,
            acknowledgements=[AckRecord("CONTRL", contrl)],
        )

    def diagnostics(self) -> dict[str, object]:
        return {"name": self.name, "available": True, "fallback": True}


class UnknownTranslator:
    name = "unknown-format"

    def handles(self, detected_type: str, text_sample: str) -> bool:
        return detected_type == "UNKNOWN"

    def translate(
        self,
        *,
        text: str,
        job_uuid,
        trading_partner_id: str | None,
        uploaded_by: str | None,
        filename: str,
    ) -> TranslationOutcome:
        logger.info("No translator available for payload %s; returning notice", filename)
        return TranslationOutcome(
            file_type="UNKNOWN",
            acknowledgements=[AckRecord("NOTICE", "UNKNOWN FORMAT - no ack generated")],
        )


register(SimpleX12Translator())
register(SimpleEdifactTranslator())
register(UnknownTranslator())
