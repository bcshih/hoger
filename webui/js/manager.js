// webui/js/manager.js — 「工具管理」頁籤（Tool Manager）
//
// 三個區塊：
//   1. 清單：GET /api/tools -> 卡片式清單，點卡片開編輯面板
//   2. 編輯面板：GET /api/tools/{id} 載入完整 manifest + mcp_schema，
//      可編輯 display_name/description/inputs[]/outputs[]/status，
//      id 與 param_name 唯讀（改名會破壞 MCP 引用）
//   3. MCP Schema 預覽：本地即時重算（不等儲存）——鏡射
//      hoger.core.type_mapping.to_json_schema() 的邏輯，只反映會進
//      inputSchema 的欄位（description/default/minimum/maximum/required/
//      type），足以讓使用者在儲存前確認 schema 長相；真正的權威值仍是
//      儲存後從後端 to_mcp_tool() 算出的版本——本地重算只是預覽用途。
//
// 沿用 convert.js 的模式：STATE 模組變數、render() 全量重繪、DOM event
// 手動綁定、input-invalid 驗證、共用 helper 來自 ui-common.js。

import { api, toast } from "./api.js";
import { escapeHtml, kindBadge, bindEditableCells } from "./ui-common.js";

const STATUS_LABELS = { registered: "已註冊", draft: "草稿" };

const STATE = {
  loading: false,
  tools: [], // list 摘要（/api/tools 回應）
  selectedId: null,
  detailLoading: false,
  manifest: null, // 編輯中的完整 manifest（可變）
  lastSavedManifest: null, // 最後載入/儲存成功時的深拷貝，供 isDirty() 比對
  mcpSchemaPreview: null, // 本地即時重算的 mcp_schema 預覽
  saving: false,
  deleting: false,
};

const UNSAVED_CONFIRM_MSG = "目前工具有未儲存的變更，切換後將遺失。確定要繼續嗎？";

// 編輯中的 manifest 與最後載入/儲存版本不一致 -> 有未儲存變更。
// 兩邊都來自同一份後端 JSON（一份直接持有、一份深拷貝），key 順序一致，
// 用 JSON.stringify 比對即可。
function isDirty() {
  if (!STATE.manifest || !STATE.lastSavedManifest) return false;
  return JSON.stringify(STATE.manifest) !== JSON.stringify(STATE.lastSavedManifest);
}

let root = null;

export function init(container) {
  root = container;
  STATE.loading = false;
  STATE.tools = [];
  STATE.selectedId = null;
  STATE.detailLoading = false;
  STATE.manifest = null;
  STATE.lastSavedManifest = null;
  STATE.mcpSchemaPreview = null;
  STATE.saving = false;
  STATE.deleting = false;
  render();
  loadList();
}

// ── render ───────────────────────────────────────────────────────────

function render() {
  root.innerHTML = `
    <div class="view-head">
      <span class="view-eyebrow">02 · Tool Manager</span>
      <h2 class="view-title">工具管理</h2>
      <p class="view-desc">檢視、編輯、刪除已建立的工具，並即時預覽 MCP Schema。</p>
    </div>
    <div class="manager-layout">
      <div class="manager-list-col">
        <div class="manager-list-head">
          <h3 class="table-section-title">工具清單 <span class="table-count">${STATE.tools.length}</span></h3>
          <button type="button" class="btn btn-ghost" id="refresh-btn" ${STATE.loading ? "disabled" : ""}>重新整理</button>
        </div>
        <div id="tool-list"></div>
      </div>
      <div class="manager-detail-col" id="tool-detail"></div>
    </div>
  `;

  renderList();
  renderDetail();

  root.querySelector("#refresh-btn").addEventListener("click", () => {
    if (isDirty() && !window.confirm(UNSAVED_CONFIRM_MSG)) return;
    loadList();
  });
}

function renderList() {
  const listEl = root.querySelector("#tool-list");
  if (!listEl) return;

  if (STATE.loading) {
    listEl.innerHTML = `<div class="manager-list-loading">載入中……</div>`;
    return;
  }

  if (STATE.tools.length === 0) {
    listEl.innerHTML = `
      <div class="empty-guide-card">
        <span class="placeholder-tag">尚無工具</span>
        <h3>尚無工具</h3>
        <p>前往轉換區匯入第一個 .gh 檔案，即可在這裡管理。</p>
        <a class="btn btn-primary" href="#/convert">前往轉換區</a>
      </div>
    `;
    return;
  }

  listEl.innerHTML = `
    <div class="tool-card-list">
      ${STATE.tools.map((t) => renderToolCard(t)).join("")}
    </div>
  `;

  listEl.querySelectorAll(".tool-card").forEach((card) => {
    card.addEventListener("click", () => {
      const id = card.dataset.id;
      if (id === STATE.selectedId) return;
      selectTool(id);
    });
  });
}

function renderToolCard(t) {
  const isActive = t.id === STATE.selectedId;
  const statusClass = t.status === "registered" ? "status-badge-registered" : "status-badge-draft";
  const statusLabel = STATUS_LABELS[t.status] || t.status;
  return `
    <button type="button" class="tool-card ${isActive ? "tool-card-active" : ""}" data-id="${escapeHtml(t.id)}">
      <div class="tool-card-top">
        <span class="tool-card-name">${escapeHtml(t.display_name)}</span>
        <span class="status-badge ${statusClass}">${escapeHtml(statusLabel)}</span>
      </div>
      <p class="tool-card-id mono">${escapeHtml(t.id)}</p>
      <div class="tool-card-meta">
        <span>輸入 <strong>${t.inputs_count}</strong></span>
        <span>輸出 <strong>${t.outputs_count}</strong></span>
        <span class="tool-card-updated">${escapeHtml(formatTimestamp(t.updated_at))}</span>
      </div>
    </button>
  `;
}

function formatTimestamp(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ── list loading ─────────────────────────────────────────────────────

async function loadList() {
  STATE.loading = true;
  render();
  try {
    const tools = await api("/api/tools");
    STATE.tools = tools;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    toast(message, "error");
  } finally {
    STATE.loading = false;
    render();
  }
}

async function selectTool(id) {
  if (isDirty() && !window.confirm(UNSAVED_CONFIRM_MSG)) return;
  STATE.selectedId = id;
  STATE.detailLoading = true;
  STATE.manifest = null;
  STATE.lastSavedManifest = null;
  STATE.mcpSchemaPreview = null;
  render();
  try {
    const data = await api(`/api/tools/${encodeURIComponent(id)}`);
    STATE.manifest = data.manifest;
    STATE.lastSavedManifest = JSON.parse(JSON.stringify(data.manifest));
    STATE.mcpSchemaPreview = data.mcp_schema;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    toast(message, "error");
    STATE.selectedId = null;
  } finally {
    STATE.detailLoading = false;
    render();
  }
}

// ── detail / edit panel ──────────────────────────────────────────────

function renderDetail() {
  const detailEl = root.querySelector("#tool-detail");
  if (!detailEl) return;

  if (!STATE.selectedId) {
    detailEl.innerHTML = `
      <div class="manager-detail-empty">
        <p>從左側清單選擇一個工具以檢視與編輯。</p>
      </div>
    `;
    return;
  }

  if (STATE.detailLoading || !STATE.manifest) {
    detailEl.innerHTML = `<div class="manager-list-loading">載入工具定義中……</div>`;
    return;
  }

  const m = STATE.manifest;

  const inputsRows = m.inputs.length
    ? m.inputs
        .map((input, idx) => {
          const isNumeric = input.kind === "number" || input.kind === "integer";
          const numericCells = isNumeric
            ? `
              <td>
                <input type="text" class="cell-input mono" data-group="inputs" data-field="default" data-idx="${idx}"
                  value="${escapeHtml(input.default ?? "")}" placeholder="—" />
              </td>
              <td>
                <input type="text" class="cell-input mono" data-group="inputs" data-field="minimum" data-idx="${idx}"
                  value="${escapeHtml(input.minimum ?? "")}" placeholder="—" />
              </td>
              <td>
                <input type="text" class="cell-input mono" data-group="inputs" data-field="maximum" data-idx="${idx}"
                  value="${escapeHtml(input.maximum ?? "")}" placeholder="—" />
              </td>
            `
            : `<td class="cell-muted">—</td><td class="cell-muted">—</td><td class="cell-muted">—</td>`;

          return `
            <tr>
              <td class="mono cell-param-name" title="param_name 唯讀，改名會破壞 MCP 引用">
                <span class="lock-icon" aria-hidden="true">&#128274;</span>${escapeHtml(input.param_name)}
              </td>
              <td>${kindBadge(input.kind)}</td>
              <td>
                <label class="required-toggle">
                  <input type="checkbox" data-group="inputs" data-field="required" data-idx="${idx}" ${input.required ? "checked" : ""} />
                  <span>${input.required ? "必填" : "選填"}</span>
                </label>
              </td>
              <td>
                <input type="text" class="cell-input" data-group="inputs" data-field="description" data-idx="${idx}"
                  value="${escapeHtml(input.description)}" placeholder="說明……" />
              </td>
              ${numericCells}
            </tr>
          `;
        })
        .join("")
    : `<tr><td colspan="7" class="cell-empty">此工具沒有輸入參數</td></tr>`;

  const outputsRows = m.outputs.length
    ? m.outputs
        .map((output, idx) => `
          <tr>
            <td class="mono cell-param-name" title="param_name 唯讀，改名會破壞 MCP 引用">
              <span class="lock-icon" aria-hidden="true">&#128274;</span>${escapeHtml(output.param_name)}
            </td>
            <td>${kindBadge(output.kind)}</td>
            <td>
              <input type="text" class="cell-input" data-group="outputs" data-field="description" data-idx="${idx}"
                value="${escapeHtml(output.description)}" placeholder="說明……" />
            </td>
            <td>
              <input type="text" class="cell-input" data-group="outputs" data-field="unit" data-idx="${idx}"
                value="${escapeHtml(output.unit)}" placeholder="單位……" />
            </td>
          </tr>
        `)
        .join("")
    : `<tr><td colspan="4" class="cell-empty">此工具沒有輸出參數</td></tr>`;

  const isRegistered = m.status === "registered";

  detailEl.innerHTML = `
    <div class="edit-panel">
      <div class="summary-card">
        <div class="summary-grid">
          <div class="summary-field">
            <label class="field-label">工具 id（唯讀）</label>
            <p class="readonly-value mono"><span class="lock-icon" aria-hidden="true">&#128274;</span>${escapeHtml(m.id)}</p>
          </div>
          <div class="summary-field">
            <label class="field-label" for="edit-display-name">顯示名稱</label>
            <input type="text" id="edit-display-name" class="input-text" value="${escapeHtml(m.display_name)}" />
          </div>
          <div class="summary-field summary-field-wide">
            <label class="field-label" for="edit-description">描述</label>
            <textarea id="edit-description" class="input-textarea" rows="2">${escapeHtml(m.description)}</textarea>
          </div>
          <div class="summary-field summary-field-wide">
            <label class="field-label">狀態</label>
            <div class="status-toggle">
              <label class="status-toggle-option">
                <input type="radio" name="status" value="draft" ${!isRegistered ? "checked" : ""} />
                <span>草稿（draft）</span>
              </label>
              <label class="status-toggle-option">
                <input type="radio" name="status" value="registered" ${isRegistered ? "checked" : ""} />
                <span>已註冊（registered）</span>
              </label>
            </div>
            <p class="field-hint">draft 不會出現在 MCP 工具清單，只有 registered 狀態的工具會被 MCP client 看到。</p>
          </div>
        </div>
      </div>

      <div class="table-section">
        <h3 class="table-section-title">輸入 <span class="table-count">${m.inputs.length}</span></h3>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>參數名稱</th>
                <th>型別</th>
                <th>必填</th>
                <th>描述</th>
                <th>預設值</th>
                <th>最小值</th>
                <th>最大值</th>
              </tr>
            </thead>
            <tbody id="edit-inputs-tbody">${inputsRows}</tbody>
          </table>
        </div>
      </div>

      <div class="table-section">
        <h3 class="table-section-title">輸出 <span class="table-count">${m.outputs.length}</span></h3>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>參數名稱</th>
                <th>型別</th>
                <th>描述</th>
                <th>單位</th>
              </tr>
            </thead>
            <tbody id="edit-outputs-tbody">${outputsRows}</tbody>
          </table>
        </div>
      </div>

      <div class="table-section">
        <h3 class="table-section-title">MCP Schema 預覽（即時）</h3>
        <pre class="schema-preview mono" id="schema-preview">${escapeHtml(JSON.stringify(STATE.mcpSchemaPreview, null, 2))}</pre>
      </div>

      <div class="review-actions">
        <button type="button" class="btn btn-ghost btn-danger" id="delete-btn" ${STATE.deleting ? "disabled" : ""}>刪除</button>
        <div class="review-actions-primary">
          <button type="button" class="btn btn-primary" id="save-btn" ${STATE.saving ? "disabled" : ""}>${STATE.saving ? "儲存中……" : "儲存"}</button>
        </div>
      </div>
    </div>
  `;

  bindDetail(detailEl);
}

function bindDetail(detailEl) {
  const m = STATE.manifest;

  detailEl.querySelector("#edit-display-name").addEventListener("input", (ev) => {
    m.display_name = ev.target.value;
    recomputeSchemaPreview();
  });

  detailEl.querySelector("#edit-description").addEventListener("input", (ev) => {
    m.description = ev.target.value;
    recomputeSchemaPreview();
  });

  detailEl.querySelectorAll('input[name="status"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      if (radio.checked) m.status = radio.value;
    });
  });

  detailEl.querySelectorAll('input[type="checkbox"][data-field="required"]').forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const idx = Number(checkbox.dataset.idx);
      const item = m.inputs[idx];
      if (!item) return;
      item.required = checkbox.checked;
      const label = checkbox.parentElement.querySelector("span");
      if (label) label.textContent = item.required ? "必填" : "選填";
      recomputeSchemaPreview();
    });
  });

  bindEditableCells(detailEl.querySelector("#edit-inputs-tbody"), m.inputs, ["minimum", "maximum"], () => {
    recomputeSchemaPreview();
  });
  bindEditableCells(detailEl.querySelector("#edit-outputs-tbody"), m.outputs, [], () => {
    recomputeSchemaPreview();
  });

  detailEl.querySelector("#save-btn").addEventListener("click", () => {
    saveManifest();
  });

  detailEl.querySelector("#delete-btn").addEventListener("click", () => {
    deleteManifest();
  });
}

// ── MCP schema 本地即時重算 ──────────────────────────────────────────
//
// 鏡射 hoger.core.type_mapping.to_json_schema() + hoger.core.manifest.to_mcp_tool()
// 的邏輯（Python 端為權威實作，這裡只是預覽用途，儲存後仍以後端重新 GET
// 或下次選取時的回應為準）。只涵蓋會進 inputSchema 的欄位：
// description/default/minimum/maximum/required/enum/type。

function inputToJsonSchema(input) {
  const kind = input.kind;

  if (kind === "number" || kind === "integer") {
    const schema = { type: kind };
    if (input.description) schema.description = input.description;
    if (input.default !== null && input.default !== undefined) schema.default = input.default;
    if (input.minimum !== null && input.minimum !== undefined) schema.minimum = input.minimum;
    if (input.maximum !== null && input.maximum !== undefined) schema.maximum = input.maximum;
    return schema;
  }

  if (kind === "boolean") {
    const schema = { type: "boolean" };
    if (input.description) schema.description = input.description;
    if (input.default !== null && input.default !== undefined) schema.default = input.default;
    return schema;
  }

  if (kind === "string") {
    const schema = { type: "string" };
    if (input.description) schema.description = input.description;
    if (input.default !== null && input.default !== undefined) schema.default = input.default;
    if (input.enum_values && input.enum_values.length) schema.enum = [...input.enum_values];
    return schema;
  }

  if (kind === "geometry") {
    const schema = {
      type: "object",
      properties: {
        file_3dm: { type: "string", description: "Rhino .3dm 檔案絕對路徑" },
        layer: { type: "string", description: "（選填）只取此圖層的物件" },
        encoded: { type: "array", items: { type: "string" }, description: "（替代）rhino3dm JSON 編碼的幾何物件列表" },
      },
    };
    if (input.description) schema.description = input.description;
    if (input.default !== null && input.default !== undefined) schema.default = input.default;
    return schema;
  }

  const schema = { type: "string" };
  if (input.description) schema.description = input.description;
  if (input.default !== null && input.default !== undefined) schema.default = input.default;
  return schema;
}

function manifestToMcpTool(m) {
  const description = m.description ? `${m.display_name} — ${m.description}` : m.display_name;
  const properties = {};
  m.inputs.forEach((input) => {
    properties[input.param_name] = inputToJsonSchema(input);
  });
  const required = m.inputs.filter((i) => i.required).map((i) => i.param_name);

  const inputSchema = { type: "object", properties };
  if (required.length) inputSchema.required = required;

  return { name: m.id, description, inputSchema };
}

function recomputeSchemaPreview() {
  STATE.mcpSchemaPreview = manifestToMcpTool(STATE.manifest);
  const pre = root.querySelector("#schema-preview");
  if (pre) pre.textContent = JSON.stringify(STATE.mcpSchemaPreview, null, 2);
}

// ── save / delete ────────────────────────────────────────────────────

async function saveManifest() {
  if (root.querySelectorAll(".input-invalid").length > 0) {
    toast("請先修正表格中標記為紅色的欄位", "error");
    return;
  }

  // min/max 交叉驗證：兩者皆有值時 minimum 不可大於 maximum。
  for (const input of STATE.manifest.inputs) {
    if (
      input.minimum !== null && input.minimum !== undefined &&
      input.maximum !== null && input.maximum !== undefined &&
      input.minimum > input.maximum
    ) {
      toast(`參數「${input.param_name}」的最小值大於最大值`, "error");
      return;
    }
  }

  STATE.saving = true;
  render();

  const now = new Date().toISOString();
  const payload = { ...STATE.manifest, updated_at: now };

  try {
    const saved = await api(`/api/tools/${encodeURIComponent(payload.id)}`, {
      method: "PUT",
      body: payload,
    });
    STATE.manifest = saved;
    STATE.lastSavedManifest = JSON.parse(JSON.stringify(saved));
    toast("已儲存", "success");
    await loadList();
    // loadList() 已呼叫 render()；重新載入此工具的 mcp_schema（改用後端權威值）
    try {
      const data = await api(`/api/tools/${encodeURIComponent(saved.id)}`);
      STATE.manifest = data.manifest;
      STATE.lastSavedManifest = JSON.parse(JSON.stringify(data.manifest));
      STATE.mcpSchemaPreview = data.mcp_schema;
    } catch {
      // 略過：清單已刷新，detail 顯示本地重算版本即可
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    toast(message, "error");
  } finally {
    STATE.saving = false;
    render();
  }
}

async function deleteManifest() {
  const m = STATE.manifest;
  if (!m) return;
  if (!window.confirm(`確定要刪除工具「${m.display_name}」（id: ${m.id}）嗎？此操作無法復原。`)) {
    return;
  }

  STATE.deleting = true;
  render();

  try {
    await api(`/api/tools/${encodeURIComponent(m.id)}`, { method: "DELETE" });
    toast("已刪除", "success");
    STATE.selectedId = null;
    STATE.manifest = null;
    STATE.lastSavedManifest = null;
    STATE.mcpSchemaPreview = null;
    await loadList();
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    toast(message, "error");
  } finally {
    STATE.deleting = false;
    render();
  }
}
