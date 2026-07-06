"""
hoger/core/describe.py — 自動描述生成（task v3-A）。

HOGER 不是 AI Agent、不呼叫任何 LLM。這個模組的職責是把「只有 HOGER
拿得到的結構性事實」——元件清單（scanner.ScanResult.component_inventory）、
每個參數的接線（candidate 的 feeds/fed_by）、值域與目前值（candidate 的
current_value/minimum/maximum，或 InputSpec 自己的 default/minimum/maximum）
——組成人類可讀、且對後續 AI 調用端有用的描述文字。

設計原則：
- **確定性**：同樣的 manifest + scan 輸入，永遠產生同樣的字串。不做任何
  隨機化、不呼叫外部服務、不依賴牆上時鐘。
- **不覆寫使用者輸入**：本模組只負責「生成」，覆寫與否的決定在呼叫端
  （hoger/api/routes.py 只在 description 欄位為空時才填入這裡生成的文字）。
- **鴨子型別 candidate**：candidate 參數接受 dict（dataclasses.asdict()
  轉出的 scanner.InputCandidate/OutputCandidate 形狀）或 None（無法對應到
  掃描候選時的退化情境，例如直接解析路徑掃到的 Hops 檔案）。
"""

from __future__ import annotations

from typing import Any, Optional

from hoger.core.manifest import InputSpec, OutputSpec, ToolManifest

# ── 已知函式庫前綴 ────────────────────────────────────────────────────
#
# (Name 前綴, 庫名, 用途描述)。用來從 component_inventory 的 key
# （元件的 Name，例如 "LB Outdoor Solar MRT"）判斷這個工具用了哪些已知的
# Grasshopper 外掛生態系，藉此揭示這個工具的「用途類別」（見 describe_tool）。
KNOWN_LIBRARIES: list[tuple[str, str, str]] = [
    ("LB ", "Ladybug", "氣候/環境分析（日照、輻射、舒適度）"),
    ("HB ", "Honeybee", "建築能源與日光模擬"),
    ("Karamba", "Karamba3D", "結構分析"),
    ("Galapagos", "Galapagos", "演化最佳化"),
    ("Tunny", "Tunny", "多目標最佳化"),
    ("Wallacei", "Wallacei", "演化多目標最佳化"),
    ("Kangaroo", "Kangaroo", "物理模擬/找形"),
]

_MAX_AUTO_DOC_CHARS = 3000
_MAX_FEEDS_LISTED = 3
_MAX_TOP_COMPONENTS = 5
_MIN_TOP_COMPONENTS = 3

# ── 純顯示/組織用元件 denylist ───────────────────────────────────────
#
# 這些元件不參與計算邏輯（顏色樣本、預覽、面板、中繼、註解、群組），
# 出現在「主要元件」列舉裡對 AI 理解工具用途沒有幫助、甚至誤導
# （例如「主要元件：Colour Swatch、Custom Preview」）。_top_components()
# 先過濾再取 top N；元件總數的計算不受影響（它們仍是定義的一部分）。
DISPLAY_ONLY_COMPONENTS: frozenset = frozenset(
    {
        "Colour Swatch",
        "Custom Preview",
        "Preview",
        "Panel",
        "Relay",
        "Scribble",
        "Group",
    }
)


def _get(candidate: Optional[dict], key: str, default: Any = None) -> Any:
    if not candidate:
        return default
    value = candidate.get(key, default)
    return value if value is not None else default


def _feeds_phrase(feeds: list, component_key: str, slot_key: str) -> str:
    """把 feeds/fed_by 列表組成「餵給/由 X 的 Y」風格片語清單（最多 3 筆）。

    - comp == slot 時只 render 元件名：滑桿餵給同名中繼參數是 GH 常態，
      「Radius／Radius」的斜線重複只是雜訊，寫「Radius」就夠了。
    - 重複的 (comp, slot) pair 去重：同一物件對同一元件同一腳位接多條線
      （fan-out）會在 feeds 裡出現多筆一樣的紀錄，列舉一次即可。
      先去重、再取前 _MAX_FEEDS_LISTED 筆（去重後名額讓給不同的接線）。
    """
    seen: set = set()
    parts = []
    for f in feeds:
        comp = f.get(component_key) or "?"
        slot = f.get(slot_key) or "?"
        pair = (comp, slot)
        if pair in seen:
            continue
        seen.add(pair)
        parts.append(comp if comp == slot else f"{comp}／{slot}")
        if len(parts) >= _MAX_FEEDS_LISTED:
            break
    return "、".join(parts)


# ── describe_input ───────────────────────────────────────────────────


def _value_context_input(spec: InputSpec, candidate: Optional[dict]) -> str:
    """從 candidate（優先）或 spec 自身欄位組出值語境描述。"""
    kind = spec.kind

    current_value = _get(candidate, "current_value")
    minimum = _get(candidate, "minimum", spec.minimum)
    maximum = _get(candidate, "maximum", spec.maximum)
    if current_value is None and spec.default is not None:
        current_value = spec.default

    if kind in ("number", "integer"):
        parts = []
        if current_value is not None:
            parts.append(f"目前值 {current_value}")
        if minimum is not None and maximum is not None:
            parts.append(f"範圍 {minimum}–{maximum}")
        elif minimum is not None:
            parts.append(f"最小值 {minimum}")
        elif maximum is not None:
            parts.append(f"最大值 {maximum}")
        if not parts:
            return "數值參數（未偵測到目前值或範圍）。"
        return "，".join(parts) + "。"

    if kind == "boolean":
        if current_value is not None:
            return f"布林開關，目前 {current_value}。"
        return "布林開關（True/False）。"

    if kind == "string":
        if spec.enum_values:
            return "可選值：" + "、".join(spec.enum_values) + "。"
        if current_value:
            return f"文字參數，目前值：{current_value}。"
        return "文字參數。"

    if kind == "geometry":
        return "幾何輸入（.3dm 路徑或 encoded rhino3dm JSON 皆可）。"

    return "參數。"


def describe_input(spec: InputSpec, candidate: Optional[dict]) -> str:
    """
    產生單一輸入參數的描述：接線語境（餵給哪個元件的哪個輸入） + 值語境。

    candidate 為 None（無法對應掃描候選，例如直接解析路徑或 Hops 檔案）
    時，退化為只有值語境（從 spec 的 default/minimum/maximum 推得）。
    """
    feeds = _get(candidate, "feeds", []) or []
    value_context = _value_context_input(spec, candidate)

    if feeds:
        phrase = _feeds_phrase(feeds, "component", "input")
        wiring = f"餵給 {phrase} 的輸入。"
        return f"{wiring} {value_context}"

    return value_context


# ── describe_output ──────────────────────────────────────────────────


def _kind_note_output(kind: str) -> str:
    if kind == "geometry":
        return "會寫入結果 .3dm。"
    if kind == "string":
        return "以 AttributeUserText 附著於結果 .3dm。"
    if kind in ("number", "integer", "boolean"):
        return "以 JSON outputs 回傳。"
    return "以 JSON outputs 回傳。"


def describe_output(spec: OutputSpec, candidate: Optional[dict]) -> str:
    """
    產生單一輸出參數的描述：來源語境（由哪個元件的哪個輸出餵入） + kind 說明。
    """
    fed_by = _get(candidate, "fed_by", []) or []
    kind_note = _kind_note_output(spec.kind)

    if fed_by:
        phrase = _feeds_phrase(fed_by, "component", "output")
        return f"由 {phrase} 輸出餵入。{kind_note}"

    return kind_note


# ── describe_tool ────────────────────────────────────────────────────


def _detect_libraries(inventory: dict[str, int]) -> list[str]:
    found = []
    for prefix, lib_name, purpose in KNOWN_LIBRARIES:
        if any(name.startswith(prefix) for name in inventory):
            found.append(f"{lib_name}（{purpose}）")
    return found


def _top_components(inventory: dict[str, int], n: int) -> list[str]:
    """依頻率取前 n 個元件名，先排除 DISPLAY_ONLY_COMPONENTS（純顯示/
    組織用元件不代表工具的計算用途，見 denylist 註解）。"""
    ranked = sorted(
        (kv for kv in inventory.items() if kv[0] not in DISPLAY_ONLY_COMPONENTS),
        key=lambda kv: (-kv[1], kv[0]),
    )
    return [name for name, _count in ranked[:n]]


def _kind_counts(specs: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in specs:
        counts[s.kind] = counts.get(s.kind, 0) + 1
    return counts


def _kind_breakdown_phrase(specs: list) -> str:
    counts = _kind_counts(specs)
    return "、".join(f"{n} {kind}" for kind, n in sorted(counts.items(), key=lambda kv: kv[0]))


def describe_tool(manifest: ToolManifest, inventory: Optional[dict[str, int]]) -> str:
    """
    產生工具層級描述（2-4 句）：轉換來源聲明 + 規模 + 已知庫 + 輸入輸出摘要。
    """
    inventory = inventory or {}
    sentences = []

    sentences.append(
        f"「{manifest.display_name}」由 Grasshopper 檔案自動轉換而成，透過 Rhino.Compute 執行。"
    )

    total_components = sum(inventory.values())
    if total_components > 0:
        libs = _detect_libraries(inventory)
        top_n = _MAX_TOP_COMPONENTS if len(inventory) > _MIN_TOP_COMPONENTS else len(inventory)
        top = _top_components(inventory, top_n)
        scale_sentence = f"定義包含 {total_components} 個元件"
        if top:
            scale_sentence += "，主要元件：" + "、".join(top)
        scale_sentence += "。"
        sentences.append(scale_sentence)
        if libs:
            sentences.append("偵測到使用以下已知函式庫：" + "、".join(libs) + "。")

    n_in = len(manifest.inputs)
    n_out = len(manifest.outputs)
    in_phrase = _kind_breakdown_phrase(manifest.inputs) if manifest.inputs else ""
    out_phrase = _kind_breakdown_phrase(manifest.outputs) if manifest.outputs else ""
    io_sentence = f"{n_in} 個輸入"
    if in_phrase:
        io_sentence += f"（{in_phrase}）"
    io_sentence += f" → {n_out} 個輸出"
    if out_phrase:
        io_sentence += f"（{out_phrase}）"
    io_sentence += "。"
    sentences.append(io_sentence)

    return " ".join(sentences)


# ── build_auto_doc ───────────────────────────────────────────────────


def _candidate_index(scan_dict: Optional[dict], key: str) -> dict[str, dict]:
    """
    以 existing_mark 為 key，把 scan_dict["inputs"/"outputs"] 的 candidate
    dict 索引起來，供 build_auto_doc 用 spec.compute_name 對回 candidate。
    existing_mark 為 None 的 candidate 不索引（無法對回任何 spec）。
    """
    index: dict[str, dict] = {}
    if not scan_dict:
        return index
    for cand in scan_dict.get(key, []) or []:
        mark = cand.get("existing_mark")
        if mark:
            index[mark] = cand
    return index


def _find_candidate(spec, index: dict[str, dict]) -> Optional[dict]:
    name = spec.compute_name or spec.param_name
    return index.get(name)


def _input_table_row(spec: InputSpec, candidate: Optional[dict]) -> str:
    required = "是" if spec.required else "否"
    default = spec.default if spec.default is not None else "—"
    if spec.minimum is not None or spec.maximum is not None:
        rng = f"{spec.minimum if spec.minimum is not None else '—'}–{spec.maximum if spec.maximum is not None else '—'}"
    else:
        rng = "—"
    influence = describe_input(spec, candidate)
    return f"| {spec.param_name} | {spec.kind} | {required} | {default} | {rng} | {influence} |"


def _output_row(spec: OutputSpec, candidate: Optional[dict]) -> str:
    influence = describe_output(spec, candidate)
    return f"- **{spec.param_name}**（{spec.kind}）：{influence}"


def build_auto_doc(manifest: ToolManifest, scan_dict: Optional[dict]) -> str:
    """
    產生完整 markdown 自動文件。上限約 3000 字元，超過時截斷元件清單部分
    （用於 describe_tool 的主要元件列舉），並在尾端加註記。
    """
    inventory = (scan_dict or {}).get("component_inventory") if scan_dict else None

    input_index = _candidate_index(scan_dict, "inputs")
    output_index = _candidate_index(scan_dict, "outputs")

    lines = []
    lines.append("## 工具說明")
    lines.append(describe_tool(manifest, inventory))
    lines.append("")

    lines.append("## 輸入參數")
    if manifest.inputs:
        lines.append("| 名稱 | 型別 | 必填 | 預設 | 範圍 | 影響 |")
        lines.append("|---|---|---|---|---|---|")
        for spec in manifest.inputs:
            candidate = _find_candidate(spec, input_index)
            lines.append(_input_table_row(spec, candidate))
    else:
        lines.append("（此工具沒有輸入參數）")
    lines.append("")

    lines.append("## 輸出")
    if manifest.outputs:
        for spec in manifest.outputs:
            candidate = _find_candidate(spec, output_index)
            lines.append(_output_row(spec, candidate))
    else:
        lines.append("（此工具沒有輸出）")
    lines.append("")

    lines.append("## 呼叫提示")
    lines.append(
        "- 幾何參數（kind = geometry）接受兩種格式：`file_3dm`（Rhino .3dm 檔案絕對路徑）"
        "或 `encoded`（rhino3dm JSON 編碼的幾何物件列表）。"
    )
    lines.append(
        "- 未提供的參數，若標記為選填，將使用 GH 檔存檔時的滑桿/開關/預設值"
        "（即轉換當下 GH 檔案中的目前值，不是執行時的即時狀態）。"
    )

    text = "\n".join(lines)

    if len(text) > _MAX_AUTO_DOC_CHARS:
        truncated = text[: _MAX_AUTO_DOC_CHARS - 1].rstrip()
        text = truncated + "…"

    return text


# ── build_graph_digest（task v3-B） ──────────────────────────────────


def _digest_feeds_line(feeds: list, component_key: str, slot_key: str) -> str:
    """跟 _feeds_phrase 不同：完整列出所有 feeds，不截斷、不去重——LLM
    自己判斷哪些接線有意義，digest 的職責是提供完整事實，不是精簡文字。"""
    parts = []
    for f in feeds:
        comp = f.get(component_key) or "?"
        slot = f.get(slot_key) or "?"
        parts.append(comp if comp == slot else f"{comp}/{slot}")
    return ", ".join(parts)


def build_graph_digest(manifest: ToolManifest, scan_dict: Optional[dict]) -> str:
    """
    產生給 LLM 的緊湊結構事實 digest：純文字、每行一個事實，不過濾、
    不截斷——這是 LLM 解讀的原始材料，跟 build_auto_doc（給人看的
    markdown 文件，會過濾顯示元件、截斷 feeds 列舉）用途不同。

    包含：工具名、元件清單（含次數，全部列出）、每個輸入（名稱/型別/
    值域/目前值/完整 feeds 清單）、每個輸出（fed_by）、物件總數。
    """
    scan_dict = scan_dict or {}
    inventory = scan_dict.get("component_inventory") or {}
    object_count = scan_dict.get("object_count")

    input_index = _candidate_index(scan_dict, "inputs")
    output_index = _candidate_index(scan_dict, "outputs")

    lines: list[str] = []
    lines.append(f"工具名稱: {manifest.display_name}")

    if object_count is not None:
        lines.append(f"物件總數: {object_count}")

    if inventory:
        lines.append("元件清單（名稱: 次數，不過濾）:")
        for name, count in sorted(inventory.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  - {name}: {count}")

    lines.append(f"輸入參數（共 {len(manifest.inputs)} 個）:")
    for spec in manifest.inputs:
        candidate = _find_candidate(spec, input_index)
        parts = [f"名稱={spec.param_name}", f"型別={spec.kind}"]
        current_value = _get(candidate, "current_value")
        if current_value is None and spec.default is not None:
            current_value = spec.default
        if current_value is not None:
            parts.append(f"目前值={current_value}")
        minimum = _get(candidate, "minimum", spec.minimum)
        maximum = _get(candidate, "maximum", spec.maximum)
        if minimum is not None:
            parts.append(f"最小值={minimum}")
        if maximum is not None:
            parts.append(f"最大值={maximum}")
        feeds = _get(candidate, "feeds", []) or []
        if feeds:
            parts.append(f"接到=[{_digest_feeds_line(feeds, 'component', 'input')}]")
        lines.append("  - " + "; ".join(parts))

    lines.append(f"輸出（共 {len(manifest.outputs)} 個）:")
    for spec in manifest.outputs:
        candidate = _find_candidate(spec, output_index)
        parts = [f"名稱={spec.param_name}", f"型別={spec.kind}"]
        fed_by = _get(candidate, "fed_by", []) or []
        if fed_by:
            parts.append(f"來自=[{_digest_feeds_line(fed_by, 'component', 'output')}]")
        lines.append("  - " + "; ".join(parts))

    return "\n".join(lines)
