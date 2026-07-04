"""
hoger.ghio.loader — locate and lazily load GH_IO.dll via pythonnet.

GH_IO.dll ships with Rhino/Grasshopper (default location on Windows:
``C:\\Program Files\\Rhino 8\\Plug-ins\\Grasshopper\\GH_IO.dll``). The path can
be overridden with the ``HOGER_GHIO_DLL`` environment variable (see
``hoger.config.GHIO_DLL``).

Design constraints (see docs/superpowers/plans/2026-07-04-hoger-v2-auto-convert.md
section 0):

- Importing this module must NOT trigger a ``clr`` load. Loading pythonnet's
  CLR runtime is a heavyweight, one-way operation (it cannot be "undone" or
  retargeted within a process), so it must only happen lazily, on first use.
- ``clr.AddReference`` must only be called once per process. A module-level
  flag tracks whether the load has already been attempted.
- Availability checks are cached: once we know GH_IO is available (or not),
  repeated calls to :func:`is_available` do not re-attempt the load or
  re-emit the warning log.
"""
from __future__ import annotations

import logging
import os
import threading

from hoger import config

logger = logging.getLogger(__name__)


class GhioUnavailable(RuntimeError):
    """Raised when GH_IO.dll cannot be located or loaded via pythonnet."""


# Module-level state. `_attempted` guards against calling clr.AddReference
# more than once per process; `_available` / `_archive_class` cache the
# outcome so repeated calls are cheap and the warning is logged only once.
_lock = threading.Lock()
_attempted = False
_available = False
_archive_class = None
_warned = False


def _try_load() -> None:
    """Attempt the one-time clr load. Safe to call multiple times."""
    global _attempted, _available, _archive_class, _warned

    if _attempted:
        return

    with _lock:
        if _attempted:  # re-check inside the lock (race guard)
            return
        _attempted = True

        dll_path = config.GHIO_DLL

        if not dll_path or not os.path.isfile(dll_path):
            if not _warned:
                logger.warning(
                    "GH_IO.dll not found at %r (set HOGER_GHIO_DLL to override)",
                    dll_path,
                )
                _warned = True
            return

        try:
            import clr  # noqa: PLC0415 - deliberately deferred import

            clr.AddReference(dll_path)
            from GH_IO.Serialization import GH_Archive  # noqa: PLC0415

            _archive_class = GH_Archive
            _available = True
        except Exception:
            if not _warned:
                logger.warning(
                    "Failed to load GH_IO.dll from %r", dll_path, exc_info=True
                )
                _warned = True


def is_available() -> bool:
    """Return True if GH_IO.dll exists and was loaded successfully via pythonnet.

    The result is cached after the first call; the underlying clr load is
    only attempted once per process.
    """
    _try_load()
    return _available


def get_archive_class():
    """Return the GH_IO.Serialization.GH_Archive class.

    Raises:
        GhioUnavailable: if GH_IO.dll could not be located or loaded.
    """
    _try_load()
    if not _available or _archive_class is None:
        raise GhioUnavailable(
            f"GH_IO.dll is not available (looked for {config.GHIO_DLL!r}); "
            "set HOGER_GHIO_DLL to override the path."
        )
    return _archive_class


def _reset_for_tests() -> None:
    """Test-only helper to reset cached state so a fresh load can be attempted.

    Not part of the public API; used by tests that monkeypatch config.GHIO_DLL.
    """
    global _attempted, _available, _archive_class, _warned
    with _lock:
        _attempted = False
        _available = False
        _archive_class = None
        _warned = False
