"""Access to the packaged ``producers.toml`` registry (ARCHITECTURE §3.5).

The file is shipped as package data inside :mod:`common` so that it is
resolvable via :mod:`importlib.resources` in both editable and wheel
installs, regardless of the caller's current working directory. Admission
and librarian both load it from here — **never** from the workspace.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

_PACKAGE_RESOURCE = "producers.toml"


def producers_toml_path() -> Path:
    """Return a filesystem path to the installed ``producers.toml``.

    Raises FileNotFoundError if the file was not packaged correctly
    (which would indicate a packaging regression — see M0 V4 test).
    """
    ref = resources.files(__package__).joinpath(_PACKAGE_RESOURCE)
    path = Path(str(ref))
    if not path.is_file():
        raise FileNotFoundError(
            f"producers.toml not found at packaged location: {path}. "
            "This indicates the package was installed without its data files."
        )
    return path


def producers_toml_bytes() -> bytes:
    """Return the raw bytes of the packaged ``producers.toml``.

    Prefer this over reading a filesystem path when you only need the
    contents (it works inside zipped / compressed distributions too).
    """
    return (resources.files(__package__) / _PACKAGE_RESOURCE).read_bytes()
