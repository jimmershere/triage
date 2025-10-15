"""pyx12-backed translator for HIPAA X12 healthcare files."""
from __future__ import annotations

import importlib
import io
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import AckRecord, TranslationOutcome, Translator, register

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PyX12Support:
    params_mod: object
    x12file_mod: object
    map_if_mod: object
    x12n_document_mod: object

    def _new_params(self):
        params_cls = getattr(self.params_mod, "params", None)
        if params_cls is None:
            raise RuntimeError("pyx12.params.params not available")
        return params_cls()

    def iter_segments(self, text: str):
        reader_cls = getattr(self.x12file_mod, "X12Reader", None)
        if reader_cls is None:
            reader_cls = getattr(self.x12file_mod, "x12file", None)
        if reader_cls is None:
            raise RuntimeError("pyx12.x12file does not expose X12Reader/x12file")
        stream = io.StringIO(text)
        reader = reader_cls(stream)
        get_seg = getattr(reader, "__iter__", None)
        if get_seg is None:
            get_seg = getattr(reader, "iter_segments", None)
        if get_seg is None:
            raise RuntimeError("pyx12 reader lacks iterator")
        for seg in get_seg():
            yield seg

    def generate_acks(
        self,
        text: str,
        *,
        job_uuid: uuid.UUID,
        trading_partner_id: str | None,
    ) -> list[AckRecord]:
        ack_records: list[AckRecord] = []
        ack_buffer = io.StringIO()
        html_buffer = io.StringIO()
        param = self._new_params()
        map_path = None
        param_get = getattr(param, "get", None)
        if callable(param_get):
            try:
                candidate = param_get("map_path")
            except Exception:
                candidate = None
            if candidate:
                try:
                    if Path(candidate).exists():
                        map_path = candidate
                except Exception:
                    map_path = None
        try:
            ok = self.x12n_document_mod.x12n_document(
                param=param,
                src_file=io.StringIO(text),
                fd_997=ack_buffer,
                fd_html=html_buffer,
                map_path=map_path,
            )
        except Exception as exc:
            diagnostic = f"pyx12 validation failed: {exc}"
            logger.warning("pyx12 x12n_document raised while processing job %s: %s", job_uuid, exc)
            return [AckRecord("NOTICE", diagnostic)]

        ack_text = ack_buffer.getvalue().strip()
        html_text = html_buffer.getvalue().strip()

        if ack_text:
            ack_type = "999" if "ST*999" in ack_text else "997"
            ack_records.append(AckRecord(ack_type, ack_text))

        if not ack_records and html_text:
            ack_records.append(AckRecord("NOTICE", html_text))

        if not ack_records:
            fallback_text = (
                "pyx12 validation succeeded but no acknowledgement content was produced"
                if ok
                else "pyx12 validation failed without acknowledgement content"
            )
            ack_records.append(AckRecord("NOTICE", fallback_text))

        return ack_records

_SUPPORT_ERROR: str | None = None

def _load_support() -> _PyX12Support | None:
    global _SUPPORT_ERROR
    try:
        params_mod = importlib.import_module("pyx12.params")
        x12file_mod = importlib.import_module("pyx12.x12file")
        map_if_mod = importlib.import_module("pyx12.map_if")
        x12n_document_mod = importlib.import_module("pyx12.x12n_document")
        return _PyX12Support(
            params_mod=params_mod,
            x12file_mod=x12file_mod,
            map_if_mod=map_if_mod,
            x12n_document_mod=x12n_document_mod,
        )
    except Exception as exc:
        _SUPPORT_ERROR = f"{type(exc).__name__}: {exc}"
        logger.info(
            "pyx12 translator disabled; ensure pyx12 and its maps are installed (%s)",
            _SUPPORT_ERROR,
        )
        return None

class PyX12Translator:
    name = "pyx12-x12"

    def __init__(self) -> None:
        self._support = _load_support()
        self._last_error = _SUPPORT_ERROR

    def handles(self, detected_type: str, text_sample: str) -> bool:
        return self._support is not None and detected_type.startswith("X12")

    def _extract_claims(self, text: str) -> tuple[list[dict], str | None]:
        support = self._support
        if support is None:
            raise RuntimeError("pyx12 support not loaded")
        claims: list[dict] = []
        isa_ctrl: str | None = None
        try:
            for seg in support.iter_segments(text):
                tag = None
                elements = None
                if isinstance(seg, (tuple, list)) and seg:
                    tag = str(seg[0]).strip().upper()
                    elements = seg
                else:
                    tag = str(getattr(seg, "tag", "")).strip().upper()
                    elements = list(getattr(seg, "elements", []))
                if not tag:
                    continue
                if tag == "ISA" and len(elements) >= 14:
                    isa_ctrl = str(elements[13])
                if tag == "CLM":
                    claim_id = str(elements[1]) if len(elements) > 1 else None
                    amount = None
                    if len(elements) > 2:
                        try:
                            amount = float(elements[2])
                        except Exception:
                            amount = None
                    claims.append({
                        "claim_id": claim_id,
                        "amount": amount,
                        "raw": "*".join(str(el) for el in elements if el is not None),
                    })
        except Exception as exc:
            logger.warning("pyx12 parsing failed; no claims extracted: %s", exc)
        return claims, isa_ctrl

    def translate(
        self,
        *,
        text: str,
        job_uuid,
        trading_partner_id: str | None,
        uploaded_by: str | None,
        filename: str,
    ) -> TranslationOutcome:
        support = self._support
        if support is None:
            raise RuntimeError("pyx12 support unavailable")
        claims, isa_ctrl = self._extract_claims(text)
        acknowledgements = support.generate_acks(text, job_uuid=job_uuid, trading_partner_id=trading_partner_id)
        if not acknowledgements:
            logger.info("pyx12 did not return acknowledgements; falling back to notice")
            acknowledgements = [AckRecord("NOTICE", "pyx12 failed to produce ack")] 
        return TranslationOutcome(
            file_type="X12_837_or_other",
            claims=claims,
            acknowledgements=acknowledgements,
        )

    def diagnostics(self) -> dict[str, object]:
        return {
            "name": self.name,
            "available": self._support is not None,
            "error": self._last_error,
        }


register(PyX12Translator())
