"""Fallback translators used when optional dependencies are missing."""
from __future__ import annotations

import decimal
import logging
import time
import uuid

from . import AckRecord, TranslationOutcome, register, Translator

logger = logging.getLogger(__name__)


class SimpleX12Translator:
    name = "simple-x12"

    def handles(self, detected_type: str, text_sample: str) -> bool:
        return detected_type.startswith("X12")

    def _parse_claims(self, text: str) -> tuple[list[dict], str | None]:
        seg_term = "~"
        elem_sep = "*"
        if text.startswith("ISA") and len(text) >= 106:
            elem_sep = text[3]
            seg_term = text[105]
        segments = [segment for segment in text.replace("\r", "").split(seg_term) if segment.strip()]
        claims: list[dict] = []
        current: dict | None = None
        isa_ctrl = None
        for seg in segments:
            parts = seg.split(elem_sep)
            tag = parts[0].strip().upper()
            if tag == "ISA" and len(parts) >= 14:
                isa_ctrl = parts[13]
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
        return claims, isa_ctrl

    def _safe_component(self, value: str | None, fallback: str) -> str:
        import re

        if not value:
            return fallback
        cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value.strip())
        return cleaned or fallback

    def _control_from_uuid(self, job_uuid: uuid.UUID, offset: int = 0) -> str:
        base = job_uuid.int % (10 ** 9)
        value = (base + offset) % (10 ** 9)
        return f"{value:09d}"

    def _make_999(self, job_uuid: uuid.UUID, trading_partner_id: str | None, total_claims: int, isa_ctrl: str | None) -> str:
        ctrl = (isa_ctrl or self._control_from_uuid(job_uuid))[:9].rjust(9, "0")
        gs_ctrl = self._control_from_uuid(job_uuid, 1)
        partner_raw = self._safe_component(trading_partner_id, "HEDI-RECV").upper()
        partner_padded = partner_raw[:15].rjust(15)
        app_receiver = partner_raw[:12] or "RECEIVER"
        date_short = time.strftime("%y%m%d")
        time_short = time.strftime("%H%M")
        segments = [
            f"ISA*00*          *00*          *ZZ*HEDI999       *ZZ*{partner_padded}*{date_short}*{time_short}*^*00501*{ctrl}*0*T*:~",
            f"GS*FA*HEDI*{app_receiver}*20{date_short}*{time_short}*{gs_ctrl}*X*005010X231A1~",
            "ST*999*0001*005010X231A1~",
            "AK1*HC*0001~",
            "AK2*837*0001~",
            "AK5*A~",
            "AK9*A*1*1*1~",
            "SE*7*0001~",
            f"GE*1*{gs_ctrl}~",
            f"IEA*1*{ctrl}~",
        ]
        return "\n".join(segments)

    def _make_277ca(self, job_uuid: uuid.UUID, trading_partner_id: str | None, total_claims: int) -> str:
        ctrl = self._control_from_uuid(job_uuid, 2)
        gs_ctrl = self._control_from_uuid(job_uuid, 3)
        partner_raw = self._safe_component(trading_partner_id, "HEDI-RECV").upper()
        partner_padded = partner_raw[:15].rjust(15)
        partner_short = partner_raw[:12] or "RECEIVER"
        date_full = time.strftime("%Y%m%d")
        time_short = time.strftime("%H%M")
        segments = [
            f"ISA*00*          *00*          *ZZ*HEDI277       *ZZ*{partner_padded}*{date_full[2:]}*{time_short}*^*00501*{ctrl}*0*T*:~",
            f"GS*HN*HEDI*{partner_short}*{date_full}*{time_short}*{gs_ctrl}*X*005010X214~",
            "ST*277*0001*005010X214~",
            f"BHT*0085*08*{ctrl}*{date_full}*{time_short}~",
            "HL*1**20*1~",
            "NM1*PR*2*HEDI HEALTH*****PI*HEDI277~",
            "HL*2*1*21*0~",
            f"NM1*41*2*{partner_short or 'RECEIVER'}*****46*{partner_short or 'RECEIVER'}~",
            f"TRN*1*{ctrl}*{partner_short or 'RECEIVER'}~",
            f"STC*A1:19*{date_full}*U*{max(total_claims,1)}*CLM~",
            "SE*9*0001~",
            f"GE*1*{gs_ctrl}~",
            f"IEA*1*{ctrl}~",
        ]
        return "\n".join(segments)

    def translate(
        self,
        *,
        text: str,
        job_uuid,
        trading_partner_id: str | None,
        uploaded_by: str | None,
        filename: str,
    ) -> TranslationOutcome:
        claims, isa_ctrl = self._parse_claims(text)
        acknowledgements = [
            AckRecord("999", self._make_999(job_uuid, trading_partner_id, len(claims), isa_ctrl)),
            AckRecord("277CA", self._make_277ca(job_uuid, trading_partner_id, len(claims))),
        ]
        return TranslationOutcome(
            file_type="X12_837_or_other",
            claims=claims,
            acknowledgements=acknowledgements,
        )


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
