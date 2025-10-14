"""pyx12-backed translator for HIPAA X12 healthcare files."""
from __future__ import annotations

import importlib
import io
import logging
import uuid
from dataclasses import dataclass
import subprocess
import shutil
import tempfile
from pathlib import Path

from . import AckRecord, TranslationOutcome, Translator, register

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PyX12Support:
    params_mod: object
    x12file_mod: object
    map_if_mod: object
    x12valid_path: str | None

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
        if not self.x12valid_path:
            raise RuntimeError("x12valid not found in PATH; install pyx12 in this venv")
        # Write the payload to a temp file (x12valid expects a filename).
        with tempfile.TemporaryDirectory() as td:
            in_path = Path(td) / "in.edi"
            in_path.write_text(text, encoding="utf-8", errors="ignore")

            # ``x12valid`` uses an ``argparse`` ``count`` action for ``--verbose``
            # that defaults to ``None``.  Older versions compare the value against
            # integers, so we always supply ``-v`` to coerce it to ``1`` and then
            # pair it with ``-q`` to keep logging noise down.
            cmd = [self.x12valid_path, "-v", "-q", str(in_path)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""

            ack_records: list[AckRecord] = []
            ack_candidates = (
                (".999", "999"),
                (".997", "997"),
                (".277", "277CA"),
            )
            for suffix, ack_type in ack_candidates:
                ack_path = Path(f"{in_path}{suffix}")
                if ack_path.exists():
                    ack_text = ack_path.read_text(encoding="utf-8", errors="ignore")
                    if ack_text:
                        ack_records.append(AckRecord(ack_type, ack_text))

            if not ack_records and proc.returncode == 0:
                ack_type = "999" if "ST*999" in stdout or "X231" in stdout else "997"
                ack_records.append(AckRecord(ack_type, stdout))

            if proc.returncode != 0:
                diagnostic = (stderr or stdout or f"x12valid failed with code {proc.returncode}").strip()
                first_line = diagnostic.splitlines()[0] if diagnostic else ""
                logger.warning(
                    "x12valid exited with %s while processing job %s: %s",
                    proc.returncode,
                    job_uuid,
                    first_line,
                )
                if not ack_records:
                    ack_records.append(AckRecord("NOTICE", diagnostic or "x12valid failed without diagnostic output"))

            if not ack_records:
                logger.info("x12valid did not emit acknowledgement content; falling back to notice")
                return [AckRecord("NOTICE", stdout or stderr or "pyx12 failed to produce acknowledgement data")]

            return ack_records

_SUPPORT_ERROR: str | None = None

def _load_support() -> _PyX12Support | None:
    global _SUPPORT_ERROR
    try:
        params_mod = importlib.import_module("pyx12.params")
        x12file_mod = importlib.import_module("pyx12.x12file")
        map_if_mod = importlib.import_module("pyx12.map_if")
        x12valid_path = shutil.which("x12valid")
        if not x12valid_path:
            raise RuntimeError("x12valid not found (pyx12 not installed in this environment)")
        return _PyX12Support(
            params_mod=params_mod,
            x12file_mod=x12file_mod,
            map_if_mod=map_if_mod,
            x12valid_path=x12valid_path,
        )
    except Exception as exc:
        _SUPPORT_ERROR = f"{type(exc).__name__}: {exc}"
        logger.info(
            "pyx12 translator disabled; ensure pyx12 is installed and x12valid is on PATH (%s)",
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
