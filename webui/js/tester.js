// webui/js/tester.js — 「測試」頁籤（Test Harness）
//
// 三個區塊：
//   1. 工具選擇：GET /api/tools -> 下拉選單（只有 registered 可選，draft 停用
//      並註明原因）；選定後 GET /api/tools/{id} 取得完整 manifest 供動態表單使用。
//   2. 動態輸入表單：依 manifest.inputs 逐一產生控件（number/integer 視有無
//      min+max 决定 slider 或純數字輸入；boolean 用 toggle；string 依
//      enum_values 決定 select 或 text；geometry 是「檔案路徑」/「encoded JSON」
//      二擇一）。表單值存在 STATE.formValues（依 param_name 索引），送出前
//      掃描 required 缺漏與 .input-invalid。
//   3. 執行 + 結果檢視：POST /api/tools/{id}/run，前端自算耗時計時器（後端
//      run 本身可能長達 600s，timeoutMs 故意設較寬裕的 620000）。結果分
//      摘要／errors／warnings／outputs 表／result_3dm／原始 JSON 五個區塊。
//
// 沿用 convert.js / manager.js 的模式：STATE 模組變數、render() 全量重繪、
// DOM event 手動綁定、input-invalid 防護、共用 helper 來自 ui-common.js。
// 不使用 inline onclick、不接觸外部資源。

import { api, toast } from "./api.js";
import { escapeHtml, kindBadge } from "./ui-common.js";
import { t, getLang } from "./i18n.js";

const RUN_TIMEOUT_MS = 620000; // 後端 evaluate timeout 600s，留一些餘裕

const STATE = {
  loadingTools: false,
  tools: [], // /api/tools 摘要列表
  selectedId: null,
  loadingManifest: false,
  manifest: null, // 選定工具的完整 manifest
  formValues: {}, // param_name -> 表單值（型別依 kind 而異）
  geometryMode: {}, // param_name -> "path" | "encoded"（geometry 專用）
  debugMode: false,
  running: false,
  runStartedAt: null, // 開始執行的 timestamp，計時器用
  elapsedTickMs: 0, // 前端計時器目前顯示的毫秒數
  result: null, // 成功回應（含 outputs/errors/warnings/...）
  runError: null, // 執行失敗（400/404/其他）的錯誤訊息
  computeOk: true, // 由 pollComputeHealth() 更新，燈紅時停用執行按鈕
};

let root = null;
let tickTimer = null;
let healthTimer = null;

export function init(container) {
  root = container;
  STATE.loadingTools = false;
  STATE.tools = [];
  STATE.selectedId = null;
  STATE.loadingManifest = false;
  STATE.manifest = null;
  STATE.formValues = {};
  STATE.geometryMode = {};
  STATE.debugMode = false;
  STATE.running = false;
  STATE.runStartedAt = null;
  STATE.elapsedTickMs = 0;
  STATE.result = null;
  STATE.runError = null;
  STATE.computeOk = true;

  stopTick();
  stopHealthPoll();

  render();
  loadTools();
  pollComputeHealth();
  healthTimer = setInterval(pollComputeHealth, 10000);

  // 頁籤切走時清掉計時器/輪詢，避免背景累積（app.js 每次切換頁籤都會呼叫
  // init() 重繪 view，但不會呼叫任何解構鉤子——這裡用一個一次性的
  // hashchange 監聽器自行清理，只清一次即可）。
  const cleanup = () => {
    stopTick();
    stopHealthPoll();
    window.removeEventListener("hashchange", cleanup);
  };
  window.addEventListener("hashchange", cleanup, { once: true });
}

// 語言切換時由 app.js 呼叫：只重繪（讀現有 STATE），保留已選工具、已填
// 表單值、執行結果等，不重置任何東西、不重啟計時器或健康輪詢。
export function rerender() {
  if (root) render();
}

function stopTick() {
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
}

function stopHealthPoll() {
  if (healthTimer) {
    clearInterval(healthTimer);
    healthTimer = null;
  }
}

async function pollComputeHealth() {
  try {
    const health = await api("/api/health");
    STATE.computeOk = Boolean(health.compute);
  } catch {
    STATE.computeOk = false;
  }
  // 只重繪執行按鈕區域即可，但為求簡單且此頁面重繪成本不高，直接整頁重繪
  // 會打斷使用者正在輸入的欄位焦點；改為只更新按鈕的 disabled 狀態與提示。
  updateRunButtonState();
}

function updateRunButtonState() {
  const btn = root?.querySelector("#run-btn");
  const hint = root?.querySelector("#compute-down-hint");
  if (!btn) return;
  const shouldDisable = !STATE.computeOk || STATE.running || !STATE.manifest;
  btn.disabled = shouldDisable;
  if (hint) hint.hidden = STATE.computeOk;
}

// ── render ───────────────────────────────────────────────────────────

function render() {
  root.innerHTML = `
    <div class="view-head">
      <span class="view-eyebrow">03 · Test Harness</span>
      <h2 class="view-title">${t("header.tab.tester")}</h2>
      <p class="view-desc">${t("tester.viewDesc")}</p>
    </div>
    <div class="tester-layout">
      <div class="tester-select-card">
        <label class="field-label" for="tool-select">${t("tester.selectToolLabel")}</label>
        <select id="tool-select" class="input-text" ${STATE.loadingTools ? "disabled" : ""}>
          ${renderToolOptions()}
        </select>
        <p class="field-hint">${t("tester.selectHint")}</p>
      </div>
      <div id="tester-form-area"></div>
      <div id="tester-result-area"></div>
    </div>
  `;

  root.querySelector("#tool-select").addEventListener("change", (ev) => {
    const id = ev.target.value;
    if (!id) {
      STATE.selectedId = null;
      STATE.manifest = null;
      renderFormArea();
      renderResultArea();
      return;
    }
    selectTool(id);
  });

  renderFormArea();
  renderResultArea();
}

function renderToolOptions() {
  if (STATE.loadingTools) {
    return `<option value="">${t("common.loading")}</option>`;
  }
  if (STATE.tools.length === 0) {
    return `<option value="">${t("tester.noToolsOption")}</option>`;
  }
  const placeholder = `<option value="">${t("tester.selectPlaceholderOption")}</option>`;
  const options = STATE.tools
    .map((tool) => {
      const isRegistered = tool.status === "registered";
      const label = isRegistered
        ? t("tester.toolOptionRegistered", { name: tool.display_name, id: tool.id })
        : t("tester.toolOptionDraft", { name: tool.display_name, id: tool.id });
      const selected = tool.id === STATE.selectedId ? "selected" : "";
      return `<option value="${escapeHtml(tool.id)}" ${isRegistered ? "" : "disabled"} ${selected}>${escapeHtml(label)}</option>`;
    })
    .join("");
  return placeholder + options;
}

// ── tool loading ─────────────────────────────────────────────────────

async function loadTools() {
  STATE.loadingTools = true;
  render();
  try {
    STATE.tools = await api("/api/tools");
  } catch (err) {
    toast(errMsg(err), "error");
  } finally {
    STATE.loadingTools = false;
    render();
  }
}

async function selectTool(id) {
  STATE.selectedId = id;
  STATE.loadingManifest = true;
  STATE.manifest = null;
  STATE.formValues = {};
  STATE.geometryMode = {};
  STATE.result = null;
  STATE.runError = null;
  renderFormArea();
  renderResultArea();

  try {
    const data = await api(`/api/tools/${encodeURIComponent(id)}`);
    STATE.manifest = data.manifest;
    initFormValues(data.manifest);
  } catch (err) {
    toast(errMsg(err), "error");
    STATE.selectedId = null;
    STATE.manifest = null;
  } finally {
    STATE.loadingManifest = false;
    renderFormArea();
    renderResultArea();
  }
}

function initFormValues(manifest) {
  const values = {};
  const modes = {};
  for (const input of manifest.inputs) {
    if (input.kind === "geometry") {
      values[input.param_name] = { file_3dm: "", layer: "", encoded: "" };
      modes[input.param_name] = "path";
    } else if (input.kind === "boolean") {
      values[input.param_name] = input.default === true;
    } else {
      values[input.param_name] = input.default ?? "";
    }
  }
  STATE.formValues = values;
  STATE.geometryMode = modes;
}

function errMsg(err) {
  return err instanceof Error ? err.message : String(err);
}

// ── form area ────────────────────────────────────────────────────────

function renderFormArea() {
  const area = root.querySelector("#tester-form-area");
  if (!area) return;

  if (!STATE.selectedId) {
    area.innerHTML = "";
    return;
  }

  if (STATE.loadingManifest || !STATE.manifest) {
    area.innerHTML = `<div class="manager-list-loading">${t("common.loadingToolDef")}</div>`;
    return;
  }

  const m = STATE.manifest;
  const fieldsHtml = m.inputs.length
    ? m.inputs.map((input) => renderField(input)).join("")
    : `<p class="field-hint">${t("tester.noInputParamsCanRun")}</p>`;

  area.innerHTML = `
    <div class="tester-form-card">
      <h3 class="table-section-title">${t("tester.inputParamsTitle")} <span class="table-count">${m.inputs.length}</span></h3>
      <div class="tester-fields">${fieldsHtml}</div>

      <div class="tester-run-row">
        <label class="tester-debug-toggle">
          <input type="checkbox" id="debug-mode-checkbox" ${STATE.debugMode ? "checked" : ""} />
          <span>${t("tester.debugModeLabel")}</span>
        </label>
        <div class="tester-run-primary">
          <span id="run-timer" class="tester-run-timer" ${STATE.running ? "" : "hidden"}>${t("tester.elapsedInitial")}</span>
          <button type="button" class="btn btn-primary" id="run-btn">
            ${STATE.running ? `<span class="btn-spinner" aria-hidden="true"></span>${t("tester.running")}` : t("tester.runBtn")}
          </button>
        </div>
      </div>
      <p id="compute-down-hint" class="field-error" ${STATE.computeOk ? "hidden" : ""}>
        ${t("tester.computeDownHint")}
      </p>
    </div>
  `;

  bindFormArea(area);
  updateRunButtonState();
}

function renderField(input) {
  const requiredMark = input.required ? `<span class="required-badge">${t("common.required")}</span>` : "";
  const header = `
    <div class="tester-field-head">
      <span class="mono tester-field-name">${escapeHtml(input.param_name)}</span>
      ${kindBadge(input.kind)}
      ${requiredMark}
    </div>
    ${input.description ? `<p class="field-hint tester-field-desc">${escapeHtml(input.description)}</p>` : ""}
  `;

  let control;
  if (input.kind === "number" || input.kind === "integer") {
    control = renderNumberControl(input);
  } else if (input.kind === "boolean") {
    control = renderBooleanControl(input);
  } else if (input.kind === "string") {
    control = renderStringControl(input);
  } else if (input.kind === "geometry") {
    control = renderGeometryControl(input);
  } else {
    control = renderStringControl(input);
  }

  return `
    <div class="tester-field" data-param="${escapeHtml(input.param_name)}">
      ${header}
      ${control}
    </div>
  `;
}

function hasRange(input) {
  return (
    input.minimum !== null && input.minimum !== undefined &&
    input.maximum !== null && input.maximum !== undefined
  );
}

function renderNumberControl(input) {
  const value = STATE.formValues[input.param_name];
  const step = input.kind === "integer" ? "1" : "any";
  if (hasRange(input)) {
    return `
      <div class="tester-range-row">
        <span class="tester-range-endpoint mono">${escapeHtml(input.minimum)}</span>
        <input type="range" class="tester-slider" data-role="slider" data-param="${escapeHtml(input.param_name)}"
          min="${escapeHtml(input.minimum)}" max="${escapeHtml(input.maximum)}" step="${step}"
          value="${escapeHtml(value === "" ? input.minimum : value)}" />
        <span class="tester-range-endpoint mono">${escapeHtml(input.maximum)}</span>
        <input type="number" class="input-text mono tester-range-number" data-role="number" data-param="${escapeHtml(input.param_name)}"
          min="${escapeHtml(input.minimum)}" max="${escapeHtml(input.maximum)}" step="${step}"
          value="${escapeHtml(value)}" />
      </div>
    `;
  }
  return `
    <input type="number" class="input-text mono" data-role="number" data-param="${escapeHtml(input.param_name)}"
      step="${step}" value="${escapeHtml(value)}" placeholder="${input.kind === "integer" ? t("tester.integerPlaceholder") : t("tester.numberPlaceholder")}" />
  `;
}

function renderBooleanControl(input) {
  const checked = STATE.formValues[input.param_name] === true;
  return `
    <label class="toggle">
      <input type="checkbox" data-role="boolean" data-param="${escapeHtml(input.param_name)}" ${checked ? "checked" : ""} />
      <span class="toggle-track"><span class="toggle-thumb"></span></span>
      <span class="toggle-label">${checked ? "true" : "false"}</span>
    </label>
  `;
}

function isPathLikeParam(input) {
  if (input.param_type === "FilePath") return true;
  const name = (input.param_name || "").toLowerCase();
  return name.includes("epw") || name.includes("path") || name.includes("file");
}

function renderStringControl(input) {
  const value = STATE.formValues[input.param_name];
  if (input.enum_values && input.enum_values.length) {
    const options = input.enum_values
      .map((opt) => `<option value="${escapeHtml(opt)}" ${opt === value ? "selected" : ""}>${escapeHtml(opt)}</option>`)
      .join("");
    return `
      <select class="input-text" data-role="string" data-param="${escapeHtml(input.param_name)}">
        <option value="" ${value ? "" : "selected"}>——</option>
        ${options}
      </select>
    `;
  }
  const placeholder = isPathLikeParam(input) ? t("tester.filePathPlaceholder") : t("tester.stringPlaceholder");
  return `
    <input type="text" class="input-text mono" data-role="string" data-param="${escapeHtml(input.param_name)}"
      value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" />
  `;
}

function renderGeometryControl(input) {
  const mode = STATE.geometryMode[input.param_name] || "path";
  const value = STATE.formValues[input.param_name] || { file_3dm: "", layer: "", encoded: "" };

  return `
    <div class="tester-geometry" data-param="${escapeHtml(input.param_name)}">
      <div class="import-mode-toggle" role="tablist" aria-label="${t("tester.geometryModeAria")}">
        <button type="button" class="import-mode-btn geometry-mode-btn ${mode === "path" ? "active" : ""}" data-mode="path" data-param="${escapeHtml(input.param_name)}">
          ${t("tester.geometryPathMode")}
        </button>
        <button type="button" class="import-mode-btn geometry-mode-btn ${mode === "encoded" ? "active" : ""}" data-mode="encoded" data-param="${escapeHtml(input.param_name)}">
          encoded JSON
        </button>
      </div>
      <div class="tester-geometry-body">
        ${
          mode === "path"
            ? `
              <input type="text" class="input-text mono" data-role="geometry-path" data-param="${escapeHtml(input.param_name)}"
                value="${escapeHtml(value.file_3dm)}" placeholder="${t("tester.geometryPathPlaceholder")}" />
              <input type="text" class="input-text" data-role="geometry-layer" data-param="${escapeHtml(input.param_name)}"
                value="${escapeHtml(value.layer)}" placeholder="${t("tester.geometryLayerPlaceholder")}" />
            `
            : `
              <textarea class="input-textarea mono" data-role="geometry-encoded" data-param="${escapeHtml(input.param_name)}"
                rows="4" placeholder="${escapeHtml(t("tester.geometryEncodedPlaceholder"))}">${escapeHtml(value.encoded)}</textarea>
              <p class="field-hint">${t("tester.geometryEncodedHint")}</p>
            `
        }
      </div>
    </div>
  `;
}

// ── form binding ─────────────────────────────────────────────────────

function bindFormArea(area) {
  // number（無範圍）
  area.querySelectorAll('input[type="number"][data-role="number"]:not(.tester-range-number)').forEach((el) => {
    el.addEventListener("input", () => {
      setNumberValue(el.dataset.param, el.value);
      el.classList.toggle("input-invalid", el.value !== "" && isNaN(Number(el.value)));
    });
  });

  // number + slider 連動（有範圍）
  area.querySelectorAll(".tester-field").forEach((fieldEl) => {
    const slider = fieldEl.querySelector('[data-role="slider"]');
    const number = fieldEl.querySelector('[data-role="number"].tester-range-number');
    if (!slider || !number) return;
    const param = slider.dataset.param;
    slider.addEventListener("input", () => {
      number.value = slider.value;
      setNumberValue(param, slider.value);
    });
    number.addEventListener("input", () => {
      const num = Number(number.value);
      const invalid = number.value === "" || isNaN(num);
      number.classList.toggle("input-invalid", invalid);
      if (!invalid) {
        const clamped = Math.min(Math.max(num, Number(slider.min)), Number(slider.max));
        slider.value = String(clamped);
      }
      setNumberValue(param, number.value);
    });
  });

  // boolean toggle
  area.querySelectorAll('input[data-role="boolean"]').forEach((el) => {
    el.addEventListener("change", () => {
      STATE.formValues[el.dataset.param] = el.checked;
      const labelEl = el.closest(".toggle")?.querySelector(".toggle-label");
      if (labelEl) labelEl.textContent = el.checked ? "true" : "false";
    });
  });

  // string（select 或 text）
  area.querySelectorAll('[data-role="string"]').forEach((el) => {
    el.addEventListener("input", () => {
      STATE.formValues[el.dataset.param] = el.value;
    });
    el.addEventListener("change", () => {
      STATE.formValues[el.dataset.param] = el.value;
    });
  });

  // geometry: 模式切換
  area.querySelectorAll(".geometry-mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const param = btn.dataset.param;
      STATE.geometryMode[param] = btn.dataset.mode;
      renderFormArea();
    });
  });

  // geometry: path / layer / encoded 欄位
  area.querySelectorAll('[data-role="geometry-path"]').forEach((el) => {
    el.addEventListener("input", () => {
      const v = STATE.formValues[el.dataset.param] || { file_3dm: "", layer: "", encoded: "" };
      v.file_3dm = el.value;
      STATE.formValues[el.dataset.param] = v;
    });
  });
  area.querySelectorAll('[data-role="geometry-layer"]').forEach((el) => {
    el.addEventListener("input", () => {
      const v = STATE.formValues[el.dataset.param] || { file_3dm: "", layer: "", encoded: "" };
      v.layer = el.value;
      STATE.formValues[el.dataset.param] = v;
    });
  });
  area.querySelectorAll('[data-role="geometry-encoded"]').forEach((el) => {
    el.addEventListener("input", () => {
      const v = STATE.formValues[el.dataset.param] || { file_3dm: "", layer: "", encoded: "" };
      v.encoded = el.value;
      STATE.formValues[el.dataset.param] = v;
    });
  });

  area.querySelector("#debug-mode-checkbox")?.addEventListener("change", (ev) => {
    STATE.debugMode = ev.target.checked;
  });

  area.querySelector("#run-btn")?.addEventListener("click", () => {
    runTool();
  });
}

function setNumberValue(param, rawValue) {
  if (rawValue === "") {
    STATE.formValues[param] = "";
    return;
  }
  const num = Number(rawValue);
  STATE.formValues[param] = isNaN(num) ? rawValue : num;
}

// ── validation + payload building ───────────────────────────────────

// 解析 encoded textarea 內容：優先嘗試整體 parse 成 JSON array；失敗則
// 逐行 parse（略過空白行），容錯處理使用者手動貼上的兩種常見格式。
function parseEncodedTextarea(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: true, list: [] };

  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) {
      return { ok: true, list: parsed.map((x) => (typeof x === "string" ? x : JSON.stringify(x))) };
    }
    // 單一 JSON 物件（非陣列）：視為一筆
    return { ok: true, list: [typeof parsed === "string" ? parsed : JSON.stringify(parsed)] };
  } catch {
    // 退回逐行解析
  }

  const lines = trimmed.split("\n").map((l) => l.trim()).filter(Boolean);
  const list = [];
  for (const line of lines) {
    try {
      JSON.parse(line); // 僅驗證是合法 JSON，原始字串照樣送出
      list.push(line);
    } catch {
      return { ok: false, list: [] };
    }
  }
  return { ok: true, list };
}

function buildArgsPayload() {
  const m = STATE.manifest;
  const args = {};
  const invalidFields = [];
  const missingRequired = [];

  for (const input of m.inputs) {
    const name = input.param_name;
    const value = STATE.formValues[name];

    if (input.kind === "number" || input.kind === "integer") {
      if (value === "" || value === null || value === undefined) {
        if (input.required) missingRequired.push(name);
        continue;
      }
      if (typeof value !== "number" || isNaN(value)) {
        invalidFields.push(name);
        continue;
      }
      args[name] = value;
      continue;
    }

    if (input.kind === "boolean") {
      args[name] = value === true;
      continue;
    }

    if (input.kind === "string") {
      if (value === "" || value === null || value === undefined) {
        if (input.required) missingRequired.push(name);
        continue;
      }
      args[name] = value;
      continue;
    }

    if (input.kind === "geometry") {
      const mode = STATE.geometryMode[name] || "path";
      const v = value || { file_3dm: "", layer: "", encoded: "" };
      if (mode === "path") {
        if (!v.file_3dm.trim()) {
          if (input.required) missingRequired.push(name);
          continue;
        }
        const geom = { file_3dm: v.file_3dm.trim() };
        if (v.layer && v.layer.trim()) geom.layer = v.layer.trim();
        args[name] = geom;
      } else {
        const parsedResult = parseEncodedTextarea(v.encoded || "");
        if (!parsedResult.ok) {
          invalidFields.push(name);
          continue;
        }
        if (parsedResult.list.length === 0) {
          if (input.required) missingRequired.push(name);
          continue;
        }
        args[name] = { encoded: parsedResult.list };
      }
      continue;
    }

    // 其他未知 kind：原樣送出非空字串
    if (value !== "" && value !== null && value !== undefined) {
      args[name] = value;
    } else if (input.required) {
      missingRequired.push(name);
    }
  }

  return { args, invalidFields, missingRequired };
}

function markFieldInvalid(param) {
  const fieldEl = root.querySelector(`.tester-field[data-param="${cssEscape(param)}"]`);
  fieldEl?.classList.add("tester-field-invalid");
}

function clearFieldInvalidMarks() {
  root.querySelectorAll(".tester-field-invalid").forEach((el) => el.classList.remove("tester-field-invalid"));
}

// CSS.escape 在部份極舊環境可能不存在；手動做最小替代，只需處理
// attribute selector 內可能出現的引號字元（param_name 實務上是識別字，
// 通常不含特殊字元，這裡純粹是防禦性寫法）。
function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(value);
  return String(value).replace(/["\\]/g, "\\$&");
}

// ── run ──────────────────────────────────────────────────────────────

async function runTool() {
  if (!STATE.manifest || STATE.running) return;

  clearFieldInvalidMarks();
  const { args, invalidFields, missingRequired } = buildArgsPayload();

  if (invalidFields.length > 0) {
    invalidFields.forEach(markFieldInvalid);
    toast(t("tester.fixInvalidFieldsToast"), "error");
    return;
  }
  if (missingRequired.length > 0) {
    missingRequired.forEach(markFieldInvalid);
    const separator = getLang() === "en" ? ", " : "、";
    toast(t("tester.missingRequiredToast", { names: missingRequired.join(separator) }), "error");
    return;
  }

  STATE.running = true;
  STATE.result = null;
  STATE.runError = null;
  STATE.runStartedAt = Date.now();
  STATE.elapsedTickMs = 0;
  renderFormArea();
  renderResultArea();

  stopTick();
  tickTimer = setInterval(() => {
    STATE.elapsedTickMs = Date.now() - STATE.runStartedAt;
    const timerEl = root.querySelector("#run-timer");
    if (timerEl) timerEl.textContent = t("tester.elapsedTemplate", { s: (STATE.elapsedTickMs / 1000).toFixed(1) });
  }, 100);

  const debugSuffix = STATE.debugMode ? "?debug=true" : "";

  try {
    const result = await api(`/api/tools/${encodeURIComponent(STATE.manifest.id)}/run${debugSuffix}`, {
      method: "POST",
      body: { args },
      timeoutMs: RUN_TIMEOUT_MS,
    });
    STATE.result = result;
    const hasRunErrors = result.errors && result.errors.length;
    toast(hasRunErrors ? t("tester.runCompleteWithErrors") : t("tester.runComplete"), hasRunErrors ? "error" : "success");
  } catch (err) {
    STATE.runError = errMsg(err);
    toast(STATE.runError, "error");
  } finally {
    stopTick();
    STATE.running = false;
    renderFormArea();
    renderResultArea();
  }
}

// ── result area ──────────────────────────────────────────────────────

function renderResultArea() {
  const area = root.querySelector("#tester-result-area");
  if (!area) return;

  if (!STATE.result && !STATE.runError) {
    area.innerHTML = "";
    return;
  }

  if (STATE.runError) {
    area.innerHTML = `
      <div class="tester-result-card">
        <div class="inline-alert" role="alert">
          <span class="inline-alert-icon" aria-hidden="true">&#9888;</span>
          <div>
            <strong>${t("tester.runFailedTitle")}</strong>
            <p>${escapeHtml(STATE.runError)}</p>
          </div>
        </div>
      </div>
    `;
    return;
  }

  const r = STATE.result;
  const hasErrors = Array.isArray(r.errors) && r.errors.length > 0;
  const elapsedS = (r.elapsed_ms / 1000).toFixed(2);
  const unitsWarning =
    r.modelunits && r.modelunits !== "Meters"
      ? `
        <div class="inline-alert inline-alert-amber">
          <span class="inline-alert-icon" aria-hidden="true">&#9888;</span>
          <div>
            <strong>${t("tester.unitsWarningTitle")}</strong>
            <p>${t("tester.unitsWarningDesc", { unit: escapeHtml(r.modelunits) })}</p>
          </div>
        </div>
      `
      : "";

  area.innerHTML = `
    <div class="tester-result-card">
      <h3 class="table-section-title">${t("tester.resultTitle")}</h3>

      <div class="tester-summary-row">
        <span class="status-badge ${hasErrors ? "status-badge-draft" : "status-badge-registered"}">
          ${hasErrors ? t("common.failed") : t("common.success")}
        </span>
        <span class="tester-summary-item mono">${t("tester.elapsedLabel", { s: elapsedS })}</span>
        ${r.modelunits ? `<span class="tester-summary-item mono">${t("tester.unitsLabel", { unit: escapeHtml(r.modelunits) })}</span>` : ""}
      </div>

      ${unitsWarning}
      ${renderMessageList(t("common.errors"), r.errors, "inline-alert")}
      ${renderMessageList(t("common.warnings"), r.warnings, "inline-alert inline-alert-amber")}

      <div class="table-section">
        <h3 class="table-section-title">${t("common.outputs")}</h3>
        ${renderOutputsTable(r.outputs)}
      </div>

      ${renderResult3dm(r.result_3dm)}

      <details class="tester-raw-details">
        <summary>${t("common.rawJson")}</summary>
        <pre class="schema-preview mono">${escapeHtml(JSON.stringify(r, null, 2))}</pre>
      </details>
    </div>
  `;

  bindResultArea(area);
}

function renderMessageList(title, list, cardClass) {
  if (!Array.isArray(list) || list.length === 0) return "";
  const items = list
    .map(
      (msg) => `
        <div class="${cardClass}">
          <span class="inline-alert-icon" aria-hidden="true">&#9888;</span>
          <div><p>${escapeHtml(String(msg))}</p></div>
        </div>
      `
    )
    .join("");
  return `
    <div class="tester-message-group">
      <p class="field-label">${escapeHtml(t("common.withCount", { title, n: list.length }))}</p>
      ${items}
    </div>
  `;
}

function renderOutputsTable(outputs) {
  const entries = Object.entries(outputs || {});
  if (entries.length === 0) {
    return `<p class="field-hint">${t("tester.noOutputs")}</p>`;
  }

  const manifestOutputs = STATE.manifest?.outputs || [];
  const kindByName = Object.fromEntries(manifestOutputs.map((o) => [o.param_name, o.kind]));

  const rows = entries
    .map(([name, value]) => {
      const kind = kindByName[name] || "unknown";
      return `
        <tr>
          <td class="mono cell-param-name">${escapeHtml(name)}</td>
          <td>${kindBadge(kind)}</td>
          <td>${renderOutputValue(name, kind, value)}</td>
        </tr>
      `;
    })
    .join("");

  return `
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>${t("common.thParamNameFull")}</th>
            <th>${t("common.thType")}</th>
            <th>${t("common.thValue")}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderOutputValue(name, kind, value) {
  if (kind === "geometry" && value && typeof value === "object") {
    const count = value.count ?? 0;
    const inDm = value.in_3dm ? t("tester.writtenTo3dm") : t("tester.notWrittenTo3dm");
    return `<span>${t("tester.objectCount", { n: count })}</span> <span class="cell-muted">（${escapeHtml(inDm)}）</span>`;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return `<span class="cell-muted">${t("tester.emptyArray")}</span>`;
    const joined = value.map((v) => String(v)).join(", ");
    if (value.length > 10) {
      const preview = value.slice(0, 10).map((v) => String(v)).join(", ");
      const detailId = `output-list-${cssIdSafe(name)}`;
      return `
        <details class="tester-list-details" id="${detailId}">
          <summary>${escapeHtml(preview)}, ${t("tester.moreItemsExpand", { n: value.length })}</summary>
          <p class="tester-list-full mono">${escapeHtml(joined)}</p>
        </details>
      `;
    }
    return `<span>${escapeHtml(joined)}</span>`;
  }

  if (value === null || value === undefined) {
    return `<span class="cell-muted">—</span>`;
  }

  if (typeof value === "object") {
    return `<span class="mono">${escapeHtml(JSON.stringify(value))}</span>`;
  }

  return `<span>${escapeHtml(String(value))}</span>`;
}

function cssIdSafe(name) {
  return String(name).replace(/[^a-zA-Z0-9_-]/g, "_");
}

function renderResult3dm(path) {
  if (!path) return "";
  return `
    <div class="table-section">
      <h3 class="table-section-title">${t("tester.result3dmTitle")}</h3>
      <div class="tester-result-path-row">
        <p class="readonly-value mono tester-result-path">${escapeHtml(path)}</p>
        <button type="button" class="btn btn-ghost" id="copy-path-btn" data-path="${escapeHtml(path)}">${t("tester.copyPathBtn")}</button>
      </div>
    </div>
  `;
}

function bindResultArea(area) {
  area.querySelector("#copy-path-btn")?.addEventListener("click", async (ev) => {
    const path = ev.currentTarget.dataset.path;
    await copyToClipboard(path);
  });
}

async function copyToClipboard(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      toast(t("tester.copiedToast"), "success");
      return;
    }
    throw new Error("clipboard API unavailable");
  } catch {
    // fallback：暫時建立一個 textarea 執行舊式 execCommand 複製
    try {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      toast(t("tester.copiedToast"), "success");
    } catch {
      toast(t("tester.copyFailedToast"), "error");
    }
  }
}
