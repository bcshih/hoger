"""
hoger.ghio.ghio_helpers — reflection-based GH_IO chunk/item read+write helpers.

Background — pythonnet interface-narrowing problem
----------------------------------------------------
``GH_Chunk.FindChunk()``, ``GH_Chunk.CreateChunk()``, and ``GH_Chunk.get_Chunks()``
are declared (in the GH_IO.dll metadata) to return ``GH_IReader`` / ``GH_IChunk``
— read-only interfaces — even though the concrete runtime object returned is
always a full ``GH_Chunk`` (which also implements ``GH_IWriter`` and therefore
has ``CreateChunk``/``SetString``/etc.). pythonnet resolves members using the
*declared* return type of the call that produced the object, not its runtime
type. That means code like::

    child = parent.FindChunk("Foo")   # declared to return GH_IChunk
    child.CreateChunk("Bar")          # AttributeError: GH_IChunk has no CreateChunk

fails even though ``child`` is, at runtime, perfectly capable of it.

The workaround used throughout this module: look up the real CLR ``MethodInfo``
via ``System.Reflection`` on the object's *runtime* type (``obj.GetType()``)
and invoke it directly with ``MethodInfo.Invoke(obj, args)``. Reflection does
not care about pythonnet's static wrapper type — it operates on the runtime
type, so the write methods are always visible this way.

This is a direct formalization of the workaround prototyped in
``scratch/spike_v2/ghio_helpers.py`` (validated against a real 36-object .gh
file); see docs/superpowers/plans/2026-07-04-hoger-v2-auto-convert.md section 0.

All functions here assume pythonnet's ``clr`` has already been loaded (via
``hoger.ghio.loader``) before they are called; they import ``System`` lazily
on first use to avoid triggering a clr load at module-import time.
"""
from __future__ import annotations

from typing import Any, Iterable

_System = None


def _system():
    """Lazily import System (only valid after clr.AddReference has run)."""
    global _System
    if _System is None:
        import System  # noqa: PLC0415

        _System = System
    return _System


def _invoke(parent: Any, method_name: str, arg_types: list, args: list) -> Any:
    """Look up `method_name` on parent's runtime type via reflection and invoke it.

    This is the core workaround: `parent.GetType()` returns the *actual* CLR
    type (e.g. GH_Chunk), bypassing whatever narrower interface pythonnet
    decided the reference is statically typed as.

    Each arg is explicitly boxed as its declared CLR type before going into
    the `object[]` args array. This matters for numeric types in particular:
    MethodInfo.Invoke does not coerce a boxed Python int (PyInt) into
    System.Int32 automatically the way a direct/statically-typed call would,
    and raises ArgumentException ("object of type PyInt cannot be converted
    to Int32") otherwise.
    """
    System = _system()
    t = parent.GetType()
    m = t.GetMethod(method_name, System.Array[System.Type](arg_types))
    if m is None:
        raise AttributeError(
            f"{t.FullName} has no method {method_name}({[a.__name__ for a in arg_types]})"
        )
    boxed = [
        arg if isinstance(arg, arg_type) else arg_type(arg)
        for arg, arg_type in zip(args, arg_types)
    ]
    return m.Invoke(parent, System.Array[System.Object](boxed))


# ── chunk navigation ────────────────────────────────────────────────


def find_chunk(parent: Any, name: str, index: int = -1) -> Any:
    """FindChunk via reflection so the result keeps GH_Chunk (writable) identity.

    Returns None if no such chunk exists (mirrors GH_IO's own FindChunk).
    """
    System = _system()
    if index < 0:
        return _invoke(parent, "FindChunk", [System.String], [name])
    return _invoke(parent, "FindChunk", [System.String, System.Int32], [name, index])


def create_chunk(parent: Any, name: str, index: int = -1) -> Any:
    """CreateChunk via reflection; returns a real GH_Chunk with full write API."""
    System = _system()
    if index < 0:
        return _invoke(parent, "CreateChunk", [System.String], [name])
    return _invoke(
        parent, "CreateChunk", [System.String, System.Int32], [name, index]
    )


def chunks_of(parent: Any) -> list:
    """Return the list of child chunks of `parent` (get_Chunks())."""
    return list(parent.get_Chunks())


# ── item access ─────────────────────────────────────────────────────


def items_of(chunk: Any) -> list:
    """Return the list of items (key/value entries) directly on `chunk`.

    GH_IO exposes this as the `Items` property on GH_Chunk.
    """
    return list(chunk.Items)


def item_exists(parent: Any, name: str, index: int = -1) -> bool:
    System = _system()
    if index < 0:
        return bool(_invoke(parent, "ItemExists", [System.String], [name]))
    return bool(
        _invoke(parent, "ItemExists", [System.String, System.Int32], [name, index])
    )


def get_string(parent: Any, name: str, index: int = -1):
    System = _system()
    if index < 0:
        return _invoke(parent, "GetString", [System.String], [name])
    return _invoke(parent, "GetString", [System.String, System.Int32], [name, index])


def get_guid(parent: Any, name: str, index: int = -1):
    System = _system()
    if index < 0:
        return _invoke(parent, "GetGuid", [System.String], [name])
    return _invoke(parent, "GetGuid", [System.String, System.Int32], [name, index])


def get_int32(parent: Any, name: str, index: int = -1):
    System = _system()
    if index < 0:
        return _invoke(parent, "GetInt32", [System.String], [name])
    return _invoke(parent, "GetInt32", [System.String, System.Int32], [name, index])


def get_double(parent: Any, name: str, index: int = -1):
    System = _system()
    if index < 0:
        return _invoke(parent, "GetDouble", [System.String], [name])
    return _invoke(parent, "GetDouble", [System.String, System.Int32], [name, index])


def get_boolean(parent: Any, name: str, index: int = -1):
    System = _system()
    if index < 0:
        return _invoke(parent, "GetBoolean", [System.String], [name])
    return _invoke(
        parent, "GetBoolean", [System.String, System.Int32], [name, index]
    )


# ── item writers (minimal set for marker use, v2-B) ────────────────


def set_string(parent: Any, name: str, value: str, index: int = -1):
    System = _system()
    if index < 0:
        return _invoke(parent, "SetString", [System.String, System.String], [name, value])
    return _invoke(
        parent,
        "SetString",
        [System.String, System.Int32, System.String],
        [name, index, value],
    )


def set_guid(parent: Any, name: str, value, index: int = -1):
    """Set a GUID item. `value` may be a str or a System.Guid."""
    System = _system()
    guid_value = value if not isinstance(value, str) else System.Guid(value)
    if index < 0:
        return _invoke(parent, "SetGuid", [System.String, System.Guid], [name, guid_value])
    return _invoke(
        parent,
        "SetGuid",
        [System.String, System.Int32, System.Guid],
        [name, index, guid_value],
    )


def set_int32(parent: Any, name: str, value: int, index: int = -1):
    System = _system()
    if index < 0:
        return _invoke(parent, "SetInt32", [System.String, System.Int32], [name, value])
    return _invoke(
        parent,
        "SetInt32",
        [System.String, System.Int32, System.Int32],
        [name, index, value],
    )


def guid(s: str):
    """Build a System.Guid from a string."""
    return _system().Guid(s)
