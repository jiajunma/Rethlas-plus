"""M1 — event filename format parse/format round-trip (§3.2)."""

from __future__ import annotations

import pytest

from common.events.filenames import (
    FilenameError,
    escape_label,
    format_filename,
    parse_filename,
    parse_iso_ms,
)


def test_escape_label() -> None:
    assert escape_label("def:primary_object") == "def_primary_object"
    assert escape_label("thm:foo") == "thm_foo"


def test_round_trip_user_event() -> None:
    name = format_filename(
        iso_ms="20260423T143015.123",
        event_type="user.node_added",
        target="def:primary_object",
        actor="user:alice",
        seq=1,
        uid="a7b2c912d4f1e380",
    )
    assert name == (
        "20260423T143015.123--user.node_added--def_primary_object"
        "--user_alice--0001--a7b2c912d4f1e380.json"
    )

    parsed = parse_filename(name)
    assert parsed.iso_ms == "20260423T143015.123"
    assert parsed.event_type == "user.node_added"
    assert parsed.target == "def:primary_object"
    assert parsed.actor == "user:alice"
    assert parsed.seq == 1
    assert parsed.uid == "a7b2c912d4f1e380"


def test_round_trip_no_target() -> None:
    name = format_filename(
        iso_ms="20260423T143015.123",
        event_type="user.node_added",
        target=None,
        actor="user:alice",
        seq=1,
        uid="a7b2c912d4f1e380",
    )
    parsed = parse_filename(name)
    assert parsed.target is None


def test_round_trip_generator_dotted_instance() -> None:
    name = format_filename(
        iso_ms="20260423T144522.999",
        event_type="generator.batch_committed",
        target="thm:maximal_orbits",
        actor="generator:codex-gpt-5.4-xhigh",
        seq=12,
        uid="1f8e22c0b6a94d17",
    )
    assert (
        "generator_codex-gpt-5.4-xhigh" in name
    ), "dotted / hyphenated instance must survive escape round-trip"
    parsed = parse_filename(name)
    assert parsed.actor == "generator:codex-gpt-5.4-xhigh"
    assert parsed.seq == 12


@pytest.mark.parametrize(
    "bad_name",
    [
        # wrong extension
        "20260423T143015.123--user.node_added--none--user_alice--0001--abc0123456789abc.txt",
        # iso_ms malformed
        "2026-04-23T14:30:15.123--user.node_added--none--user_alice--0001--abc0123456789abc.json",
        # uid wrong length
        "20260423T143015.123--user.node_added--none--user_alice--0001--deadbeef.json",
        # uid has non-hex
        "20260423T143015.123--user.node_added--none--user_alice--0001--zzzz0123456789ab.json",
        # seq not zero-padded 4 digits
        "20260423T143015.123--user.node_added--none--user_alice--1--abcdef0123456789.json",
        # bad component count
        "20260423T143015.123--user.node_added--none--user_alice--0001.json",
    ],
)
def test_bad_filenames_raise(bad_name: str) -> None:
    with pytest.raises(FilenameError):
        parse_filename(bad_name)


def test_parse_iso_ms() -> None:
    dt = parse_iso_ms("20260423T143015.123")
    assert (dt.year, dt.month, dt.day) == (2026, 4, 23)
    assert (dt.hour, dt.minute, dt.second) == (14, 30, 15)
    assert dt.microsecond == 123000
    assert dt.tzinfo is not None  # UTC


def test_format_rejects_bad_iso_ms() -> None:
    with pytest.raises(FilenameError):
        format_filename(
            iso_ms="bad",
            event_type="user.node_added",
            target=None,
            actor="user:alice",
            seq=1,
            uid="a7b2c912d4f1e380",
        )


def test_format_rejects_uppercase_target_slug() -> None:
    with pytest.raises(FilenameError):
        format_filename(
            iso_ms="20260423T143015.123",
            event_type="user.node_added",
            target="thm:Main_Result",
            actor="user:alice",
            seq=1,
            uid="a7b2c912d4f1e380",
        )


def test_parse_rejects_uppercase_target_slug() -> None:
    with pytest.raises(FilenameError):
        parse_filename(
            "20260423T143015.123--user.node_added--thm_Main_Result--user_alice--0001--a7b2c912d4f1e380.json"
        )
