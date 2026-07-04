"""
hoger.ghio — GH_IO.dll access layer.

Provides:
- loader: locate/load GH_IO.dll via pythonnet (lazy, cached, no import-time clr load)
- ghio_helpers: reflection-based chunk/item read+write helpers
- scanner: recursively scan a .gh file's DefinitionObjects tree for
  input/output candidates and existing RH_IN/RH_OUT marks

Importing this package (or any of its submodules) must NOT trigger a clr
load. The pythonnet `clr.AddReference` call only happens on first call to
loader.is_available() or loader.get_archive_class().
"""
