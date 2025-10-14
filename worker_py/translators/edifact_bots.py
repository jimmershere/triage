"""Optional bots-backed translator for EDIFACT files."""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from . import AckRecord, TranslationOutcome, Translator, register

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _BotsSupport:
    parser_mod: object
    ack_mod: object | None

    def parse(self, text: str) -> list[dict]:
        parser_cls = getattr(self.parser_mod, "EdifactParser", None) or getattr(self.parser_mod, "Parser", None)
        if parser_cls is None:
            raise RuntimeError("bots parser does not expose EdifactParser/Parser")
        parser = parser_cls()
        parse_from_string = getattr(parser, "parsestring", None) or getattr(parser, "parse", None)
        if parse_from_string is None:
            raise RuntimeError("bots parser lacks parse method")
        document = parse_from_string(text)
        lines: list[dict] = []
        doc_no = None
        try:
            iterator = getattr(document, "tree", None) or getattr(document, "children", None)
            segments = iterator if iterator is not None else []
        except Exception:
            segments = []
        if isinstance(segments, list):
            iterable = segments
        else:
            iterable = list(getattr(segments, "__iter__", lambda: [])())
        for node in iterable:
            tag = str(getattr(node, "tag", "") or getattr(node, "record_id", "")).upper()
            elements = getattr(node, "children", []) or getattr(node, "elements", [])
            if tag == "BGM" and elements:
                doc_no = getattr(elements[1], "value", None) if len(elements) > 1 else None
            if tag == "LIN":
                line: dict = {
                    "line_no": None,
                    "item_id": None,
                    "qty": None,
                    "price": None,
                    "raw": getattr(node, "value", "LIN"),
                }
                try:
                    if elements:
                        line["line_no"] = int(getattr(elements[0], "value", 0))
                except Exception:
                    pass
                lines.append(line)
        if doc_no is not None and lines:
            lines[0]["doc_no"] = doc_no
        return lines

    def build_contrl(self, *, doc_no: str | None, count: int) -> str | None:
        if not self.ack_mod:
            return None
        contrl_func = getattr(self.ack_mod, "generate_contrl", None) or getattr(self.ack_mod, "contrl_generate", None)
        if contrl_func is None:
            return None
        return contrl_func(doc_no=doc_no, accepted=count)


def _load_support() -> _BotsSupport | None:
    try:
        parser_mod = importlib.import_module("bots.parsers.edifact")
    except Exception as exc:
        logger.info(
            "bots EDIFACT parser unavailable; install the optional 'bots' package to enable EDIFACT translations (%s)",
            exc,
        )
        return None
    try:
        ack_mod = importlib.import_module("bots.acknowledge.edifact")
    except Exception:
        ack_mod = None
    return _BotsSupport(parser_mod=parser_mod, ack_mod=ack_mod)


class BotsEdifactTranslator:
    name = "bots-edifact"

    def __init__(self) -> None:
        self._support = _load_support()

    def handles(self, detected_type: str, text_sample: str) -> bool:
        return self._support is not None and detected_type.startswith("EDIFACT")

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
            raise RuntimeError("bots support unavailable")
        lines = support.parse(text)
        doc_no = None
        if lines and "doc_no" in lines[0]:
            doc_no = lines[0].pop("doc_no")
        contrl_text = support.build_contrl(doc_no=doc_no, count=len(lines))
        acknowledgements = []
        if contrl_text:
            acknowledgements.append(AckRecord("CONTRL", contrl_text))
        return TranslationOutcome(
            file_type="EDIFACT_ORDERS_or_other",
            order_lines=lines,
            acknowledgements=acknowledgements,
        )


register(BotsEdifactTranslator())
