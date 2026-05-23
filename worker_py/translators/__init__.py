"""Translator registry for worker payload processing."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence


@dataclass(slots=True)
class AckRecord:
    """A generated acknowledgement artifact."""

    ack_type: str
    content: str | None


@dataclass(slots=True)
class TranslationOutcome:
    """Normalized translation result returned by translators."""

    file_type: str
    claims: Sequence[dict] = ()
    order_lines: Sequence[dict] = ()
    acknowledgements: Sequence[AckRecord] = ()


class Translator(Protocol):
    """Interface implemented by format-specific translators."""

    name: str

    def handles(self, detected_type: str, text_sample: str) -> bool:
        """Return True when the translator supports the detected type/text."""

    def translate(
        self,
        *,
        text: str,
        job_uuid,
        trading_partner_id: str | None,
        uploaded_by: str | None,
        filename: str,
    ) -> TranslationOutcome:
        """Parse the payload and construct acknowledgements."""


_registry: list[Translator] = []


def register(translator: Translator) -> None:
    if translator not in _registry:
        _registry.append(translator)


def iter_translators() -> Iterable[Translator]:
    return tuple(_registry)


def select_translator(detected_type: str, text: str) -> Translator | None:
    for translator in _registry:
        try:
            if translator.handles(detected_type, text):
                return translator
        except Exception:
            continue
    return None


def translator_diagnostics(detected_type: str, text: str = "") -> list[dict[str, object]]:
    """Return debug information about registered translators."""

    results: list[dict[str, object]] = []
    for translator in _registry:
        info: dict[str, object] = {"name": getattr(translator, "name", translator.__class__.__name__)}
        diagnostics = getattr(translator, "diagnostics", None)
        if callable(diagnostics):
            try:
                extra = diagnostics()
                if isinstance(extra, dict):
                    info.update(extra)
            except Exception as exc:
                info["diagnostics_error"] = f"{type(exc).__name__}: {exc}"
        try:
            info["can_handle"] = bool(translator.handles(detected_type, text))
        except Exception as exc:
            info["can_handle"] = False
            info["handles_error"] = f"{type(exc).__name__}: {exc}"
        results.append(info)
    return results


# Import built-in translators to trigger registration side effects. Order matters:
# format-specific adapters register before generic fallbacks.
logger = logging.getLogger(__name__)

for _module in ("x12_pyx12", "edifact_bots", "fallback"):
    try:
        __import__(f"{__name__}.{_module}", fromlist=[_module])
    except Exception as exc:
        logger.warning("translator module %s unavailable: %s", _module, exc)
