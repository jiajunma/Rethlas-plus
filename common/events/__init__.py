"""Truth event IO (ARCHITECTURE §3)."""

from common.events.filenames import (
    FilenameError,
    escape_label,
    format_filename,
    parse_filename,
    parse_iso_ms,
)
from common.events.ids import EventIdAllocator, allocate_event_id
from common.events.io import atomic_write_event, event_sha256, read_event
from common.events.schema import (
    EVENT_TYPES,
    SchemaError,
    validate_event_schema,
)

__all__ = [
    "FilenameError",
    "escape_label",
    "format_filename",
    "parse_filename",
    "parse_iso_ms",
    "EventIdAllocator",
    "allocate_event_id",
    "atomic_write_event",
    "event_sha256",
    "read_event",
    "EVENT_TYPES",
    "SchemaError",
    "validate_event_schema",
]
