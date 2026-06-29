"""ERA (835) restore domain — immutable storage, byte-exact re-delivery,
reconstruction, and reversal/replacement modelling.

This package is intentionally dependency-light (stdlib + ``claimtrace.common``)
so it can be exercised by the unit-test suite without a database or broker. The
database-backed persistence and HTTP/RabbitMQ surfaces live in ``api`` and
``worker_py`` and build on top of the pure model defined here.

The 835 "restore" feature set (Workstream 5) has a precise X12-native meaning:

* **Re-delivery** — re-transmit the *exact* stored 835 byte-for-byte, reusing
  the same TRN. Never regenerate-and-alter.
* **Reconstruction** — rebuild an 835 from the stored structured CLP/CAS/SVC/
  PLB/BPR/TRN data when the original artifact is lost, reproducing identical
  balancing (SVC02-ΣCAS=SVC03; CLP03-ΣCAS=CLP04; BPR02=ΣCLP04 net of PLB) and
  the original adjustment codes, clearly marked as a reconstruction.
* **Reversal/replacement** — model a downstream reversal (CLP02=22, all amounts
  negated, original CARC/RARC echoed) and the follow-on replacement as new
  immutable events, preserving reversal-then-correction ordering.

All operations are read-only with respect to remittance amounts — Triage
re-delivers/reconstructs/reverses, it never silently mutates remittance data.
"""
from __future__ import annotations

from .balancing import BalanceReport, balance_report, claim_adjustment_total
from .builder import build_835, build_reversal, reconstruct_835
from .model import Adjustment, Claim, Delimiters, Era835, Segment, Service
from .parser import parse_835

__all__ = [
    "Adjustment",
    "BalanceReport",
    "Claim",
    "Delimiters",
    "Era835",
    "Segment",
    "Service",
    "balance_report",
    "build_835",
    "build_reversal",
    "claim_adjustment_total",
    "parse_835",
    "reconstruct_835",
]
