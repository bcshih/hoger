"""
tests/test_ghio_helpers_write.py — ghio_helpers 寫入函式的 roundtrip 測試。

v2-B（marker）依賴 create_chunk + set_string/set_int32/set_guid 能正確寫檔。
流程：fixture 複本 → create_chunk + set_* → WriteToFile → 重新 ReadFromFile →
讀回驗證三種值精確相等。任何 boxing/overload bug 都會在這裡先爆。
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from hoger.ghio import loader

if not loader.is_available():
    pytest.skip("GH_IO.dll not available", allow_module_level=True)

from hoger.ghio import ghio_helpers as gh  # noqa: E402

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "plain_slider_panel.gh"

TEST_GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


@pytest.fixture
def fixture_copy(tmp_path):
    dst = tmp_path / "plain_slider_panel.gh"
    shutil.copy2(FIXTURE_PATH, dst)
    return dst


def _open(path):
    archive_cls = loader.get_archive_class()
    archive = archive_cls()
    assert archive.ReadFromFile(str(path))
    return archive


def test_write_roundtrip_string_int_guid(fixture_copy, tmp_path):
    # -- write phase --
    archive = _open(fixture_copy)
    root = archive.get_GetRootNode()
    chunk = gh.create_chunk(root, "HogerWriteTest")
    gh.set_string(chunk, "StrItem", "hello 中文 ünïcode")
    gh.set_int32(chunk, "IntItem", 42)
    gh.set_guid(chunk, "GuidItem", TEST_GUID)

    out_path = tmp_path / "written.gh"
    assert archive.WriteToFile(str(out_path), True, False)

    # -- read-back phase (fresh archive object) --
    verify = _open(out_path)
    vroot = verify.get_GetRootNode()
    vchunk = gh.find_chunk(vroot, "HogerWriteTest")
    assert vchunk is not None
    assert gh.get_string(vchunk, "StrItem") == "hello 中文 ünïcode"
    assert gh.get_int32(vchunk, "IntItem") == 42
    assert str(gh.get_guid(vchunk, "GuidItem")) == TEST_GUID


def test_write_roundtrip_indexed_items(fixture_copy, tmp_path):
    """Indexed (int32-overload) writers — the overload most prone to boxing
    bugs, since it routes through the (String, Int32, T) reflection path."""
    archive = _open(fixture_copy)
    root = archive.get_GetRootNode()
    chunk = gh.create_chunk(root, "HogerIndexedTest")
    guid_a = str(uuid.uuid4())
    guid_b = str(uuid.uuid4())
    gh.set_string(chunk, "Multi", "first", 0)
    gh.set_string(chunk, "Multi", "second", 1)
    gh.set_int32(chunk, "Nums", 7, 0)
    gh.set_int32(chunk, "Nums", -13, 1)
    gh.set_guid(chunk, "IDs", guid_a, 0)
    gh.set_guid(chunk, "IDs", guid_b, 1)

    out_path = tmp_path / "written_indexed.gh"
    assert archive.WriteToFile(str(out_path), True, False)

    verify = _open(out_path)
    vchunk = gh.find_chunk(verify.get_GetRootNode(), "HogerIndexedTest")
    assert vchunk is not None
    assert gh.get_string(vchunk, "Multi", 0) == "first"
    assert gh.get_string(vchunk, "Multi", 1) == "second"
    assert gh.get_int32(vchunk, "Nums", 0) == 7
    assert gh.get_int32(vchunk, "Nums", 1) == -13
    assert str(gh.get_guid(vchunk, "IDs", 0)) == guid_a
    assert str(gh.get_guid(vchunk, "IDs", 1)) == guid_b


def test_create_chunk_with_index_roundtrip(fixture_copy, tmp_path):
    """create_chunk(parent, name, index) — the indexed overload used by the
    marker when appending Object chunks to DefinitionObjects."""
    archive = _open(fixture_copy)
    root = archive.get_GetRootNode()
    c0 = gh.create_chunk(root, "IndexedChunk", 0)
    c1 = gh.create_chunk(root, "IndexedChunk", 1)
    gh.set_string(c0, "Tag", "zero")
    gh.set_string(c1, "Tag", "one")

    out_path = tmp_path / "written_chunks.gh"
    assert archive.WriteToFile(str(out_path), True, False)

    verify = _open(out_path)
    vroot = verify.get_GetRootNode()
    v0 = gh.find_chunk(vroot, "IndexedChunk", 0)
    v1 = gh.find_chunk(vroot, "IndexedChunk", 1)
    assert v0 is not None and v1 is not None
    assert gh.get_string(v0, "Tag") == "zero"
    assert gh.get_string(v1, "Tag") == "one"


def test_invoke_bad_argument_type_message():
    """#3: _invoke must fail with an informative TypeError when an argument
    cannot be converted to the declared CLR type."""
    archive = _open(FIXTURE_PATH)
    root = archive.get_GetRootNode()
    with pytest.raises(TypeError, match=r"SetInt32.*argument.*Int32"):
        gh.set_int32(root, "Bad", object())  # object() is not int-convertible