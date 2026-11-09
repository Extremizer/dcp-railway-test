"""EXTREMIZER OEM Cache Bridge v1 — isolated, NOT mounted in production.

No network, file, DB, DCP, Chrome or Railway access. Pure validation/transport
functions only. The producer takes a result from the existing authenticated
DCP Parts parser. Dealer price is NEVER serialized as public MSRP.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
from typing import Any, MutableSet

SCHEMA = "extremizer.oem.identity.v1"
PATH = "/internal/oem-cache-bridge/v1"  # Future endpoint; NOT registered.
DOMAIN = b"extremizer:oem-cache-bridge:v1"
MAX_BODY_BYTES = 2048
MAX_SKEW_SECONDS = 60
MAX_MSRP_AGE_SECONDS = 24 * 3600
PILOT_MANUFACTURER = "Arctic Cat"
PILOT_OEM = "0627-084"
SOURCE_PATTERN = re.compile(r"(live_dom|server_html):(current_oem|previous_oem)_([A-Z0-9-]{4,40})\Z")
OEM_PATTERN = re.compile(r"[A-Z0-9-]{4,40}\Z")
NONCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,96}\Z")
REF_PATTERN = re.compile(r"[A-Za-z0-9_./:-]{3,160}\Z")
PUBLIC_MSRP_SOURCE_KINDS = frozenset({
    "validated_public_msrp",
    "validated_public_msrp_cache",
    "live_public_form_metadata",
})
IDENTITY_KEYS = frozenset({
    "schema", "manufacturer", "query_oem", "current_oem", "catalog",
    "identity_source", "identity_verified_at",
})
PROOF_KEYS = frozenset({
    "manufacturer", "current_oem", "msrp_usd", "source_kind",
    "source_ref", "verified_at", "attestation",
})
ENVELOPE_KEYS = frozenset({"identity", "public_msrp"})


class BridgeRejected(ValueError):
    """An explicit fail-closed decision; details must not contain raw inputs."""


def _fields(value: Any) -> dict:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return value
    raise BridgeRejected("invalid_producer_result")


def _oem(value: Any) -> str:
    if not isinstance(value, str) or not OEM_PATTERN.fullmatch(value):
        raise BridgeRejected("invalid_oem")
    return value


def _at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise BridgeRejected("invalid_verification_time")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise BridgeRejected("invalid_verification_time") from None
    if dt.tzinfo is None:
        raise BridgeRejected("naive_verification_time")
    return dt.astimezone(timezone.utc)


def _utc(now: datetime | None = None) -> datetime:
    value = now if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise BridgeRejected("naive_clock")
    return value.astimezone(timezone.utc)


def identity_from_dcp(dealer_result: Any, *, now: datetime | None = None) -> dict:
    """Only exact authenticated Parts evidence; strip all dealer-price fields."""
    r = _fields(dealer_result)
    if (
        r.get("status") != "FOUND"
        or r.get("authorized") is not True
        or r.get("http_status") != 200
    ):
        raise BridgeRejected("parts_result_not_verified")
    manufacturer = r.get("manufacturer")
    query = _oem(r.get("query_oem"))
    if manufacturer != PILOT_MANUFACTURER or query != PILOT_OEM:
        raise BridgeRejected("pilot_identity_out_of_scope")
    if _oem(r.get("matched_sku")) != query:
        raise BridgeRejected("matched_sku_mismatch")
    current = _oem(r.get("current_oem"))
    source = r.get("source")
    match = SOURCE_PATTERN.fullmatch(source) if isinstance(source, str) else None
    if not match or match.group(3) != query:
        raise BridgeRejected("unapproved_parts_source")
    if match.group(2) == "current_oem" and current != query:
        raise BridgeRejected("unverified_replacement")
    if match.group(2) == "previous_oem" and current == query:
        raise BridgeRejected("invalid_previous_oem")
    return {
        "schema": SCHEMA,
        "manufacturer": manufacturer,
        "query_oem": query,
        "current_oem": current,
        "catalog": "Parts",
        "identity_source": source,
        "identity_verified_at": _utc(now).isoformat(timespec="seconds"),
    }


def _validate_identity(identity: Any, *, now: datetime) -> dict:
    if not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS:
        raise BridgeRejected("invalid_identity_shape")
    if identity.get("schema") != SCHEMA or identity.get("catalog") != "Parts":
        raise BridgeRejected("invalid_identity_contract")
    if identity.get("manufacturer") != PILOT_MANUFACTURER:
        raise BridgeRejected("unapproved_manufacturer")
    query = _oem(identity.get("query_oem"))
    current = _oem(identity.get("current_oem"))
    if query != PILOT_OEM:
        raise BridgeRejected("unapproved_oem")
    src = identity.get("identity_source")
    m = SOURCE_PATTERN.fullmatch(src) if isinstance(src, str) else None
    if not m or m.group(3) != query:
        raise BridgeRejected("unverified_identity_source")
    if (m.group(2) == "current_oem" and current != query) or (
        m.group(2) == "previous_oem" and current == query
    ):
        raise BridgeRejected("inconsistent_identity_source")
    checked = _at(identity.get("identity_verified_at"))
    age = (now - checked).total_seconds()
    if age < -MAX_SKEW_SECONDS or age > MAX_MSRP_AGE_SECONDS:
        raise BridgeRejected("stale_identity")
    return identity


def _validate_public_proof(proof: Any, identity: dict, *, now: datetime) -> dict:
    """Separate, independently supplied PUBLIC MSRP provenance, never DCP DP."""
    if not isinstance(proof, dict) or set(proof) != PROOF_KEYS:
        raise BridgeRejected("invalid_public_msrp_shape")
    if (
        proof.get("manufacturer") != identity["manufacturer"]
        or proof.get("current_oem") != identity["current_oem"]
        or proof.get("attestation") != "SEPARATELY_VERIFIED_PUBLIC_MSRP"
    ):
        raise BridgeRejected("public_msrp_identity_or_attestation_missing")
    if proof.get("source_kind") not in PUBLIC_MSRP_SOURCE_KINDS:
        raise BridgeRejected("public_msrp_source_not_trusted")
    ref = proof.get("source_ref")
    if not isinstance(ref, str) or not REF_PATTERN.fullmatch(ref):
        raise BridgeRejected("public_msrp_source_ref_missing")
    price = proof.get("msrp_usd")
    if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
        raise BridgeRejected("public_msrp_invalid")
    checked = _at(proof.get("verified_at"))
    age = (now - checked).total_seconds()
    if age < -MAX_SKEW_SECONDS or age > MAX_MSRP_AGE_SECONDS:
        raise BridgeRejected("public_msrp_stale")
    return proof


def envelope(identity: dict, *, public_msrp: dict | None = None,
             now: datetime | None = None) -> dict:
    observed = _utc(now)
    _validate_identity(identity, now=observed)
    if public_msrp is not None:
        _validate_public_proof(public_msrp, identity, now=observed)
    return {"identity": identity, "public_msrp": public_msrp}


def _body(value: dict) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_BODY_BYTES:
        raise BridgeRejected("request_too_large")
    return raw


def _key(root_key: bytes) -> bytes:
    if not isinstance(root_key, bytes) or len(root_key) < 16:
        raise BridgeRejected("bridge_secret_required")
    return hmac.new(root_key, DOMAIN, hashlib.sha256).digest()


def _signature(root_key: bytes, method: str, timestamp: int,
               nonce: str, body: bytes) -> str:
    message = (SCHEMA + "\n" + method + "\n" + PATH + "\n"
               + str(timestamp) + "\n" + nonce + "\n"
               + hashlib.sha256(body).hexdigest()).encode("ascii")
    return hmac.new(_key(root_key), message, hashlib.sha256).hexdigest()


def sign_for_future_https(value: dict, root_key: bytes, *,
                          nonce: str, timestamp: int) -> tuple[bytes, dict]:
    """Build bytes/headers only. DOES NOT SEND an HTTP request."""
    if not NONCE_PATTERN.fullmatch(nonce) or type(timestamp) is not int:
        raise BridgeRejected("invalid_transport_metadata")
    body = _body(value)
    return body, {
        "X-OEM-Bridge-Timestamp": str(timestamp),
        "X-OEM-Bridge-Nonce": nonce,
        "X-OEM-Bridge-Signature": _signature(root_key, "POST", timestamp, nonce, body),
    }


def _unique_json(pairs: list) -> dict:
    out = {}
    for key, value in pairs:
        if key in out:
            raise BridgeRejected("duplicate_json_key")
        out[key] = value
    return out


def verify_and_plan(body: bytes, headers: dict, root_key: bytes, *,
                    used_nonces: MutableSet[str],
                    now: datetime | None = None) -> dict:
    """Future server verifier. used_nonces MUST be persistent/atomic in PROD.

    No DB action occurs here. Returned cache_args target the existing
    upsert_oem_catalog_cache ONLY after separately verified public MSRP.
    """
    observed = _utc(now)
    if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
        raise BridgeRejected("invalid_body")
    if not isinstance(headers, dict):
        raise BridgeRejected("invalid_headers")
    try:
        timestamp = int(headers["X-OEM-Bridge-Timestamp"])
        nonce = headers["X-OEM-Bridge-Nonce"]
        signature = headers["X-OEM-Bridge-Signature"]
    except (KeyError, ValueError, TypeError):
        raise BridgeRejected("invalid_signed_headers") from None
    if (not isinstance(nonce, str) or not NONCE_PATTERN.fullmatch(nonce)
        or not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature)):
        raise BridgeRejected("invalid_signed_headers")
    if abs(timestamp - int(observed.timestamp())) > MAX_SKEW_SECONDS:
        raise BridgeRejected("stale_signature")
    expected = _signature(root_key, "POST", timestamp, nonce, body)
    if not hmac.compare_digest(signature, expected):
        raise BridgeRejected("signature_mismatch")
    try:
        data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_json)
    except (ValueError, UnicodeDecodeError):
        raise BridgeRejected("invalid_json") from None
    if not isinstance(data, dict) or set(data) != ENVELOPE_KEYS:
        raise BridgeRejected("invalid_envelope_shape")
    identity = _validate_identity(data["identity"], now=observed)
    proof = data["public_msrp"]
    if proof is not None:
        _validate_public_proof(proof, identity, now=observed)
    if nonce in used_nonces:
        raise BridgeRejected("replayed_request")
    used_nonces.add(nonce)  # TEST ONLY. PROD: unique insert in same DB tx.
    identity_row = {
        "manufacturer": identity["manufacturer"],
        "query_oem": identity["query_oem"],
        "current_oem": identity["current_oem"],
        "catalog": "Parts",
        "identity_source": identity["identity_source"],
        "verified_at": identity["identity_verified_at"],
    }
    if proof is None:
        return {"action": "IDENTITY_ONLY_NO_PRICE_WRITE",
                "identity_row": identity_row, "cache_args": None}
    cache_result = {
        "status": "FOUND",
        "manufacturer": identity["manufacturer"],
        "catalog": "Parts",
        "query_oem": identity["query_oem"],
        "oem": identity["current_oem"],
        "item_sku": identity["current_oem"],
        "previous_oems": (
            [identity["query_oem"]]
            if identity["current_oem"] != identity["query_oem"] else []
        ),
    }
    return {
        "action": "VERIFIED_PUBLIC_MSRP_CACHE_UPSERT_READY",
        "identity_row": identity_row,
        "cache_args": {
            "result": cache_result,
            "verified_msrp_usd": proof["msrp_usd"],
            "source_kind": proof["source_kind"],
            "source_ref": proof["source_ref"],
            "verified_at": proof["verified_at"],
        },
    }
