# HOGER v2 — 任意 GH 檔案自動轉換 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 使用者匯入**普通** GH 檔案（滑桿/Toggle/Panel/幾何參數，無任何 Hops 標記），HOGER 掃描候選輸入輸出 → 使用者在 Web UI 勾選命名 → HOGER 自動在原檔加上 `RH_IN:`/`RH_OUT:` 群組（先備份）→ 沿用 v1 的 /io→manifest→MCP/Hops 全流程。

**Architecture:** 新增 `hoger/ghio/` 套件（pythonnet + GH_IO.dll 讀寫 .gh archive）：scanner（枚舉候選+接線）與 marker（注入群組+備份）。manifest 增加 `compute_name` 欄位分離「AI 看到的乾淨參數名」與「compute 注入用的完整名」。UI 轉換區插入「掃描勾選」階段。

**Tech Stack:** pythonnet 3.1、GH_IO.dll（Rhino 8 隨附）、其餘沿用 v1。

---

## 0. Spike 已驗證的事實（2026-07-04，Rhino.Compute 8.11 實測；勿重新推導）

1. **GH_IO 載入**：`clr.AddReference(r"C:\Program Files\Rhino 8\Plug-ins\Grasshopper\GH_IO.dll")`；讀檔 `GH_Archive().ReadFromFile(path)`；`Serialize_Xml()` 可轉 XML 研究。
2. **pythonnet 陷阱**：`GH_Chunk.FindChunk/CreateChunk/get_Chunks()` 宣告回傳唯讀介面，Python 端看不到寫入方法——必須用 `System.Reflection.MethodInfo.Invoke()` 繞過（可重用 helper 原型在 `scratch/spike_v2/ghio_helpers.py`）。
3. **GH_Group chunk 結構**（ground truth 來自真實檔案）：Object chunk 的 items = GUID（固定 `c552a431-af5b-46a9-a8a4-0fcbc27ef596`）+ Name="Group"；Container items = Border(int 1)/Colour(ARGB)/Description/`ID`（indexed gh_guid，每成員一筆，值=成員 InstanceGuid）/`ID_Count`/InstanceGuid（新 GUID）/Name="Group"/`NickName`（**標記寫這裡**）；Container 下一個空 Attributes chunk。
4. **/io 判定規則**：Group 的 **NickName** 含大小寫敏感子字串 `RH_IN`/`RH_OUT` 即被辨識（不需冒號、不需前綴位置）；**整個 NickName 原樣**成為 /io 的 `Name`。把標記寫在 Name 欄位無效；直接改參數自身 NickName（不包群組）無效。HOGER 一律產生嚴格 `RH_IN:<name>` 格式。
5. **/io 回傳**：slider 的 Min/Max → `Minimum`/`Maximum`；當前值 → `Default`（形狀為 DataTree：`{"ParamName":..., "InnerTree":{"{0}":[{"type":"System.Double","data":"3.0"}]}}`——**注意 manifest_from_io 要解開這種 Default**，v1 只處理裸值）。Panel 輸出 ParamType="Text"。
6. **/grasshopper 注入**：ParamName **必須完全等於 /io 的 Name**（含 `RH_IN:` 前綴），裸名字被靜默忽略（無 error）。不注入 → 用滑桿存檔當下的 Value。
7. **掃描接線**：必須**遞迴**掃整棵 DefinitionObjects chunk 樹（巢狀在元件內部的參數的 `Source`/`InstanceGuid` 深埋 sub-chunk）；`scratch/spike_v2/v1_enumerate_graph.py` 是可用原型（36 物件實檔驗證過）。
8. Value List：手邊所有檔案皆無此元件，enum 欄位位置仍未知——待使用者提供參考檔（不阻塞本計畫）。

---

## 1. 檔案結構（新增/修改）

```
hoger/ghio/
├── __init__.py
├── loader.py       # GH_IO.dll 定位（HOGER_GHIO_DLL 可覆寫）+ clr 一次性初始化 + 可用性偵測
├── ghio_helpers.py # reflection-based chunk 讀寫 helper（自 spike 原型正式化）
├── scanner.py      # scan_gh(path) -> ScanResult（候選輸入/輸出 + 接線 + 既有標記）
└── marker.py       # apply_marks(path, input_marks, output_marks) -> 備份 + 注入群組
hoger/core/manifest.py   # InputSpec/OutputSpec + compute_name；manifest_from_io 解 DataTree 形 Default、剝 RH_IN:
hoger/core/executor.py   # build_trees 用 spec.compute_name or spec.param_name
hoger/core/results.py    # parse 比對時同時剝 RH_OUT:（既有）——確認 compute_name 一致性
hoger/api/routes.py      # POST /api/scan、POST /api/convert
webui/js/convert.js      # 插入掃描勾選階段
tests/fixtures/plain_slider_panel.gh   # 最小普通檔案 fixture（marker 產出後入版控）
tests/test_ghio_*.py     # skip-if-no-GH_IO 保護；/io 實測部分掛 integration marker
```

**ghio 測試策略**：GH_IO.dll 存在與否用 `loader.is_available()` 偵測，測試模組頂部 `pytest.importorskip` 式 skip（本機有 Rhino 8 → 會跑；CI 無 → skip 不 fail）。需要 Rhino.Compute 在線的驗證（/io 認標記）掛 `integration` marker。

---

## 2. 模組介面

### ghio/scanner.py
```python
@dataclass
class InputCandidate:
    instance_guid: str
    object_type: str      # "Number Slider" | "Boolean Toggle" | "Panel" | "Value List" | <param Name>
    nickname: str
    current_value: str | None
    minimum: float | None
    maximum: float | None
    feeds: list[dict]     # [{"component": "...", "input": "_grid_size"}]
    existing_mark: str | None   # 已在 RH_IN 群組中 → 該群組 NickName

@dataclass
class OutputCandidate:  # 無下游的參數/Panel
    instance_guid: str; object_type: str; nickname: str
    fed_by: list[dict]; existing_mark: str | None

def scan_gh(path) -> ScanResult(inputs, outputs, already_marked_count, object_count)
```
候選輸入 = Number Slider / Boolean Toggle / Panel（有下游者）/ Value List / 頂層懸空參數（有下游、無上游）。候選輸出 = 無下游的 Panel/參數。`suggested name` 由 UI 端算（優先 feeds 的 input 名，次之 nickname）。

### ghio/marker.py
```python
def apply_marks(path, input_marks: list[{"guid","name"}], output_marks, backup=True) -> MarkResult
# 1) backup=True → 同目錄存 {stem}.{YYYYmmdd_HHMMSS}.bak
# 2) 冪等：目標物件若已在「HOGER 樣式群組」（NickName 匹配 ^RH_(IN|OUT):）中 → 改該群組 NickName，不疊加新群組
# 3) 群組 NickName 一律 f"RH_IN:{name}" / f"RH_OUT:{name}"；name 需匹配 ^[A-Za-z0-9_]+$（拒絕含 RH_IN/RH_OUT 子字串的 name 防誤判）
# 4) 寫檔後重讀驗證（archive 可再開、群組存在）→ MarkResult(backup_path, marked)
```

### manifest.py 變更
```python
class InputSpec: compute_name: str | None = None   # /io 原始 Name（含 RH_IN:）；None → 用 param_name
class OutputSpec: compute_name: str | None = None
# manifest_from_io：Name 含 "RH_IN:" 前綴 → param_name=剝前綴、compute_name=原樣；輸出同理 RH_OUT:
# Default 解 DataTree 形：dict 且含 InnerTree → 取第一 branch 第一 item 的 data（json.loads 過），失敗 → None
```
executor.build_trees：tree 的 ParamName 用 `spec.compute_name or spec.param_name`。results.parse 比對 key 同樣先試 compute_name 再 param_name。

### API
```
POST /api/scan     body {"gh_path"} 或 multipart .gh（存 GH_FILES_DIR）→ ScanResult JSON
                   GH_IO 不可用 → 501 + 指引；掃描失敗 → 422
POST /api/convert  body {"gh_path", "inputs":[{"guid","name"}], "outputs":[...]}
                   → apply_marks → io_query → manifest_from_io → 回 draft manifest + backup_path（不落地 manifest）
```

### UI convert.js 階段改為
匯入 →（若 /io 直接有輸入輸出：跳掃描，維持 v1 路徑）→ **掃描勾選**（候選表：物件類型 badge、目前值/範圍、接到哪裡、勾選框、參數名輸入（預填建議名）、既有標記顯示）→ 轉換（顯示備份路徑）→ 檢視編輯（v1 既有）→ 註冊。

---

## 3. 分階段任務

### Task A: ghio loader + helpers + scanner（TDD；fixture 用 comfort 檔複本產出的最小檔）
### Task B: ghio marker（TDD + integration 級 /io 實測：標記後 /io 認得、注入生效）
### Task C: manifest compute_name + DataTree Default 解析 + executor/results 接線（TDD、向後相容既有 284 測試）
### Task D: API /api/scan + /api/convert（TDD）
### Task E: UI 掃描勾選階段（Preview 實測）
### Task F: USAGE.md 第 2 節重寫 + 端到端實機驗證（comfort 檔：掃描→轉換→註冊→測試區執行）+ 全量測試

---

## 4. 風險

| 風險 | 對策 |
|---|---|
| pythonnet 介面窄化 | reflection helper（spike 已解，原型可用） |
| 使用者檔案損壞 | 寫檔前強制 .bak；寫後重讀驗證；原檔僅在 marker 一處觸碰 |
| 名稱含 RH_IN 子字串誤判 | marker 對 name 白名單驗證 `^[A-Za-z0-9_]+$` |
| GH_IO.dll 不存在（無 Rhino 機器） | loader.is_available() + API 501 + 測試 skip |
| Default 為 DataTree 形 | manifest_from_io 專門解析 + fixture 鎖定 |
