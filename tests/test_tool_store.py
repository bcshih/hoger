"""
tests/test_tool_store.py — hoger.store.tool_store 的單元測試。

tool_store.py 負責：
1. save(manifest): 寫入 {tools_dir}/{manifest.id}.json，更新 manifest.updated_at
2. get(tool_id): 讀取並解析 manifest
3. list_tools(): 列出所有 manifest，按 updated_at 降冪排序；損壞檔案 log warning 並跳過
4. delete(tool_id): 刪除工具檔案

測試全部用 tmp_path 當 tools_dir，保證隔離。
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hoger.core.manifest import InputSpec, OutputSpec, ToolManifest
from hoger.store.tool_store import ToolNotFound, delete, get, list_tools, save


def make_manifest(tool_id: str, display_name: str = None, **kwargs) -> ToolManifest:
    """建造測試 manifest 的 helper。"""
    if display_name is None:
        display_name = tool_id.replace("-", " ").title()

    now = datetime.now(timezone.utc).isoformat()

    return ToolManifest(
        id=tool_id,
        display_name=display_name,
        description=kwargs.get("description", ""),
        gh_file=kwargs.get("gh_file", f"{display_name}.gh"),
        status=kwargs.get("status", "draft"),
        inputs=kwargs.get("inputs", []),
        outputs=kwargs.get("outputs", []),
        created_at=kwargs.get("created_at", now),
        updated_at=kwargs.get("updated_at", now),
    )


class TestSaveRoundtrip:
    """save -> get 往返測試"""

    def test_save_get_roundtrip(self, tmp_path):
        """save 和 get 完整往返，欄位相等（除 updated_at 被更新）"""
        manifest = make_manifest("test-tool", "Test Tool")

        # save
        saved_path = save(manifest, tools_dir=tmp_path)

        # get
        retrieved = get("test-tool", tools_dir=tmp_path)

        # 驗證：除 updated_at 外全部欄位相等
        assert retrieved.id == manifest.id
        assert retrieved.display_name == manifest.display_name
        assert retrieved.description == manifest.description
        assert retrieved.gh_file == manifest.gh_file
        assert retrieved.status == manifest.status
        assert retrieved.inputs == manifest.inputs
        assert retrieved.outputs == manifest.outputs
        assert retrieved.created_at == manifest.created_at
        # updated_at 被 save 更新，不必等於原值
        assert isinstance(retrieved.updated_at, str)


class TestSaveOutput:
    """save 輸出驗證"""

    def test_save_returns_absolute_path(self, tmp_path):
        """save 回傳絕對路徑"""
        manifest = make_manifest("test-tool")
        path = save(manifest, tools_dir=tmp_path)

        assert isinstance(path, str)
        # 應該是絕對路徑
        path_obj = Path(path)
        assert path_obj.is_absolute()

    def test_save_file_exists_with_correct_name(self, tmp_path):
        """save 建立的檔案名稱為 {id}.json"""
        manifest = make_manifest("test-tool")
        saved_path = save(manifest, tools_dir=tmp_path)

        # 檔案應該存在
        assert Path(saved_path).exists()
        # 檔名應該是 test-tool.json
        assert Path(saved_path).name == "test-tool.json"
        # 應在 tmp_path 目錄下
        assert Path(saved_path).parent == tmp_path

    def test_save_creates_valid_json(self, tmp_path):
        """save 的檔案內容是合法 JSON"""
        manifest = make_manifest("test-tool")
        saved_path = save(manifest, tools_dir=tmp_path)

        with open(saved_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 應該能解析成 dict
        assert isinstance(data, dict)
        assert "id" in data
        assert data["id"] == "test-tool"

    def test_save_preserves_chinese_characters(self, tmp_path):
        """save 用 ensure_ascii=False，中文欄位不被 escape"""
        manifest = make_manifest(
            "chinese-tool",
            display_name="中文工具",
            description="這是一個中文描述"
        )
        saved_path = save(manifest, tools_dir=tmp_path)

        # 讀取檔案內容（二進位），檢查是否含有原始中文字節（不是 \uXXXX 形式）
        with open(saved_path, "rb") as f:
            content = f.read()

        # UTF-8 編碼的中文應該能解碼，且不能是 escape 序列
        text = content.decode("utf-8")
        assert "中文工具" in text
        assert "這是一個中文描述" in text
        # 驗證沒有被 escape（ensure_ascii=False 的證據）
        assert "\\u" not in text or text.count("\\u") == 0  # 允許其他 escape，但中文應該直接出現


class TestSaveUpdatesTimestamp:
    """save 更新 updated_at"""

    def test_save_updates_updated_at(self, tmp_path):
        """save 前後 updated_at 不同"""
        old_time = "2020-01-01T00:00:00+00:00"
        manifest = make_manifest("test-tool", updated_at=old_time)

        # save
        save(manifest, tools_dir=tmp_path)

        # get 回來
        retrieved = get("test-tool", tools_dir=tmp_path)

        # updated_at 應該被更新，不等於原值
        assert retrieved.updated_at != old_time
        # 應該是合法的 ISO 8601 格式
        assert "T" in retrieved.updated_at
        assert "+" in retrieved.updated_at or "Z" in retrieved.updated_at or "-" in retrieved.updated_at[-6:]


class TestGet:
    """get 測試"""

    def test_get_not_found(self, tmp_path):
        """get 不存在的工具 -> ToolNotFound"""
        with pytest.raises(ToolNotFound) as exc_info:
            get("nonexistent", tools_dir=tmp_path)

        assert exc_info.value.args[0] == "nonexistent"

    def test_get_existing_tool(self, tmp_path):
        """get 存在的工具"""
        manifest = make_manifest("test-tool", description="A test tool")
        save(manifest, tools_dir=tmp_path)

        retrieved = get("test-tool", tools_dir=tmp_path)
        assert retrieved.id == "test-tool"
        assert retrieved.description == "A test tool"


class TestToolIdValidation:
    """tool_id 格式驗證：路徑逃逸與非 kebab-case 輸入一律 ToolNotFound"""

    def test_get_path_traversal_forward_slash(self, tmp_path):
        """get('../evil') -> ToolNotFound（不逃出 tools_dir）"""
        with pytest.raises(ToolNotFound):
            get("../evil", tools_dir=tmp_path)

    def test_delete_path_traversal_backslash(self, tmp_path):
        """delete('..\\evil') -> ToolNotFound（不逃出 tools_dir）"""
        with pytest.raises(ToolNotFound):
            delete("..\\evil", tools_dir=tmp_path)

    def test_get_uppercase_underscore_invalid(self, tmp_path):
        """get('A_B')：大寫與底線不合法 -> ToolNotFound"""
        with pytest.raises(ToolNotFound):
            get("A_B", tools_dir=tmp_path)

    def test_traversal_does_not_touch_outside_file(self, tmp_path):
        """路徑逃逸的 delete 不會刪到 tools_dir 之外的檔案"""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")

        with pytest.raises(ToolNotFound):
            delete("../outside", tools_dir=tools_dir)

        assert outside.exists()

    def test_get_trailing_newline_invalid(self, tmp_path):
        """get('tool\\n')：re.match 的 '$' 會放過尾端換行，必須用 fullmatch 擋下 -> ToolNotFound"""
        with pytest.raises(ToolNotFound):
            get("tool-id\n", tools_dir=tmp_path)

    def test_save_path_traversal_id_raises_tool_not_found(self, tmp_path):
        """save()：manifest.id 是使用者輸入（API 允許客戶端送 manifest），
        path traversal id（'../x'）必須在 save() 開頭就被擋下 -> ToolNotFound"""
        manifest = make_manifest("placeholder")
        manifest.id = "../x"
        with pytest.raises(ToolNotFound):
            save(manifest, tools_dir=tmp_path)

    def test_save_trailing_newline_id_raises_tool_not_found(self, tmp_path):
        """save()：manifest.id 含尾端換行（'tool\\n'）必須被擋下 -> ToolNotFound"""
        manifest = make_manifest("placeholder")
        manifest.id = "tool-id\n"
        with pytest.raises(ToolNotFound):
            save(manifest, tools_dir=tmp_path)


class TestListTools:
    """list_tools 測試"""

    def test_list_tools_empty_dir(self, tmp_path):
        """空目錄 -> []"""
        result = list_tools(tools_dir=tmp_path)
        assert result == []

    def test_list_tools_sorted_by_updated_at_descending(self, tmp_path):
        """list_tools 按 updated_at 降冪排序"""
        # 建立 3 個工具，更新時間遞增
        t1 = "2020-01-01T10:00:00+00:00"
        t2 = "2020-01-01T11:00:00+00:00"
        t3 = "2020-01-01T12:00:00+00:00"

        m1 = make_manifest("tool-a", updated_at=t1)
        m2 = make_manifest("tool-b", updated_at=t2)
        m3 = make_manifest("tool-c", updated_at=t3)

        save(m1, tools_dir=tmp_path)
        save(m2, tools_dir=tmp_path)
        save(m3, tools_dir=tmp_path)

        result = list_tools(tools_dir=tmp_path)

        # 應該有 3 個
        assert len(result) == 3
        # 應該按 updated_at 降冪（最新的在前）
        assert result[0].id == "tool-c"
        assert result[1].id == "tool-b"
        assert result[2].id == "tool-a"

    def test_list_tools_skips_broken_json(self, tmp_path, caplog):
        """list_tools 遇到損壞 JSON 檔案 -> log warning 並跳過"""
        # 建立好的工具
        m_good = make_manifest("tool-good")
        save(m_good, tools_dir=tmp_path)

        # 直接寫入損壞的 JSON
        bad_json_path = tmp_path / "tool-bad.json"
        bad_json_path.write_text("{ invalid json", encoding="utf-8")

        # list_tools 應該只回傳好的
        result = list_tools(tools_dir=tmp_path)
        assert len(result) == 1
        assert result[0].id == "tool-good"

        # 應該有 warning log
        assert any("warning" in record.levelname.lower() for record in caplog.records)


class TestDelete:
    """delete 測試"""

    def test_delete_removes_file(self, tmp_path):
        """delete 刪除檔案"""
        manifest = make_manifest("test-tool")
        saved_path = save(manifest, tools_dir=tmp_path)

        # 驗證檔案存在
        assert Path(saved_path).exists()

        # delete
        delete("test-tool", tools_dir=tmp_path)

        # 檔案應該被刪除
        assert not Path(saved_path).exists()

    def test_delete_not_found(self, tmp_path):
        """delete 不存在的工具 -> ToolNotFound"""
        with pytest.raises(ToolNotFound) as exc_info:
            delete("nonexistent", tools_dir=tmp_path)

        assert exc_info.value.args[0] == "nonexistent"

    def test_delete_then_get_not_found(self, tmp_path):
        """delete 後 get -> ToolNotFound"""
        manifest = make_manifest("test-tool")
        save(manifest, tools_dir=tmp_path)

        delete("test-tool", tools_dir=tmp_path)

        with pytest.raises(ToolNotFound):
            get("test-tool", tools_dir=tmp_path)


class TestComplexManifest:
    """複雜 manifest 的 roundtrip 測試"""

    def test_manifest_with_inputs_outputs(self, tmp_path):
        """包含 inputs/outputs 的 manifest roundtrip"""
        inputs = [
            InputSpec(param_name="param_a", kind="number", required=True),
            InputSpec(param_name="param_b", kind="string", required=False, default="default"),
        ]
        outputs = [
            OutputSpec(param_name="result", kind="geometry"),
        ]

        manifest = make_manifest(
            "complex-tool",
            inputs=inputs,
            outputs=outputs
        )

        save(manifest, tools_dir=tmp_path)
        retrieved = get("complex-tool", tools_dir=tmp_path)

        assert len(retrieved.inputs) == 2
        assert retrieved.inputs[0].param_name == "param_a"
        assert retrieved.inputs[1].default == "default"
        assert len(retrieved.outputs) == 1
        assert retrieved.outputs[0].param_name == "result"


class TestDefaultToolsDir:
    """測試 tools_dir=None 時使用 config.TOOLS_DIR"""

    def test_save_with_default_tools_dir(self, tmp_path, monkeypatch):
        """save 不提供 tools_dir 時，應使用 config.TOOLS_DIR"""
        # 模擬 config.TOOLS_DIR
        import hoger.config as config
        monkeypatch.setattr(config, "TOOLS_DIR", tmp_path)

        manifest = make_manifest("test-tool")
        saved_path = save(manifest)  # 不提供 tools_dir

        # 應該在 tmp_path 下
        assert Path(saved_path).parent == tmp_path

    def test_get_with_default_tools_dir(self, tmp_path, monkeypatch):
        """get 不提供 tools_dir 時，應使用 config.TOOLS_DIR"""
        import hoger.config as config
        monkeypatch.setattr(config, "TOOLS_DIR", tmp_path)

        manifest = make_manifest("test-tool")
        save(manifest, tools_dir=tmp_path)

        # get 不提供 tools_dir
        retrieved = get("test-tool")
        assert retrieved.id == "test-tool"
