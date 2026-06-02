"""Deterministic claim and bundle identity derivation."""

from __future__ import annotations

import datetime as _dt
from typing import Iterable

from pydantic import BaseModel, ConfigDict, field_validator

from claimtrace.common.canonical import canonical_json
from claimtrace.common.hashing import hash_payload, sha256_hex


class ClaimKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    submitter_id: str
    subscriber_id: str
    patient_dob: _dt.date
    dos_start: _dt.date
    charge_amount_cents: int
    line_items_signature: str
    payer_id: str

    @field_validator("submitter_id", "subscriber_id", "line_items_signature", "payer_id")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return value.strip().upper()


def derive_claim_id(claim_fields: ClaimKey) -> str:
    payload = {
        "submitter_id": claim_fields.submitter_id,
        "subscriber_id": claim_fields.subscriber_id,
        "patient_dob": claim_fields.patient_dob,
        "dos_start": claim_fields.dos_start,
        "charge_amount_cents": claim_fields.charge_amount_cents,
        "line_items_signature": claim_fields.line_items_signature,
        "payer_id": claim_fields.payer_id,
    }
    return sha256_hex(canonical_json(payload))


def derive_bundle_id(claim_ids: Iterable[str]) -> str:
    normalized = sorted(str(claim_id).strip().lower() for claim_id in claim_ids)
    return sha256_hex(canonical_json(normalized))


def claim_hash(canonical_837_subset_or_json: bytes) -> str:
    return hash_payload(canonical_837_subset_or_json)
