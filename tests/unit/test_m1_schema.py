"""M1 — event envelope schema validation (§3.4)."""

from __future__ import annotations

import pytest

from common.events.schema import SchemaError, validate_event_schema


def _valid_user_added() -> dict:
    return {
        "event_id": "20260423T143015.123-0001-a7b2c912d4f1e380",
        "type": "user.node_added",
        "actor": "user:alice",
        "ts": "2026-04-23T14:30:15.123+08:00",
        "target": "def:primary_object",
        "payload": {
            "kind": "definition",
            "statement": "A primary object is...",
            "remark": "",
            "source_note": "",
        },
    }


def test_valid_envelope_passes() -> None:
    validate_event_schema(_valid_user_added())


def test_missing_required_fields() -> None:
    body = _valid_user_added()
    del body["event_id"]
    with pytest.raises(SchemaError):
        validate_event_schema(body)


def test_bad_event_id() -> None:
    body = _valid_user_added()
    body["event_id"] = "not-matching-pattern"
    with pytest.raises(SchemaError):
        validate_event_schema(body)


def test_unknown_event_type() -> None:
    body = _valid_user_added()
    body["type"] = "user.node_deleted"
    with pytest.raises(SchemaError):
        validate_event_schema(body)


def test_bad_actor() -> None:
    body = _valid_user_added()
    body["actor"] = "alice"  # missing kind:
    with pytest.raises(SchemaError):
        validate_event_schema(body)


@pytest.mark.parametrize(
    "ts",
    [
        "2026-04-23T14:30:15.123+08:00",
        "2026-04-23T14:30:15.123Z",
        "2026-04-23T14:30:15Z",
        "2026-04-23T14:30:15+00:00",
    ],
)
def test_valid_ts_variants(ts: str) -> None:
    body = _valid_user_added()
    body["ts"] = ts
    validate_event_schema(body)


@pytest.mark.parametrize(
    "ts",
    [
        "2026/04/23 14:30:15",
        "not a date",
        "2026-04-23T14:30:15",  # no offset and no Z
    ],
)
def test_invalid_ts(ts: str) -> None:
    body = _valid_user_added()
    body["ts"] = ts
    with pytest.raises(SchemaError):
        validate_event_schema(body)


def test_payload_must_be_dict() -> None:
    body = _valid_user_added()
    body["payload"] = []
    with pytest.raises(SchemaError):
        validate_event_schema(body)


def test_target_optional_but_typed() -> None:
    body = _valid_user_added()
    del body["target"]
    validate_event_schema(body)
    body["target"] = 123
    with pytest.raises(SchemaError):
        validate_event_schema(body)
