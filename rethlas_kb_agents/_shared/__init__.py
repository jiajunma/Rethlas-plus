"""Shared helpers used by every agent's decoder (issue #9).

The "last balanced JSON blob" algorithm and the per-field coercion
helpers are identical across statement-verifier, proof-verifier,
proof-gap-filler, counterexample-hunter, and source-claim-verifier.
Living here keeps each agent's decoder focused on its own
schema-specific validation.
"""

from __future__ import annotations

from .json_decoder import (
    DecoderError,
    coerce_confidence,
    coerce_optional_string,
    coerce_str_list,
    find_last_json_blob,
    strip_for_parse,
)

__all__ = [
    "DecoderError",
    "coerce_confidence",
    "coerce_optional_string",
    "coerce_str_list",
    "find_last_json_blob",
    "strip_for_parse",
]
