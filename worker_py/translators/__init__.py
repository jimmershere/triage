"""Translator registry for worker payload processing."""
from __future__ import annotations

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


# Import built-in translators to trigger registration side effects. Order matters:
# format-specific adapters register before generic fallbacks.
from . import x12_pyx12  # noqa: E402,F401
from . import edifact_bots  # noqa: E402,F401
from . import fallback  # noqa: E402,F401
