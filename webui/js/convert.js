// webui/js/convert.js — 「轉換」頁籤（Import & Convert）
//
// 階段（stage machine）：
//   1. import  — 選擇匯入方式：
//        a) 自動轉換（推薦）：任意 .gh -> POST /api/scan -> 進 scan 階段
//        b) 直接解析：檔案已含 RH_IN/Hops 標記 -> POST /api/import -> 進 review 階段
//   2. scan    — 掃描候選輸入/輸出，使用者勾選、命名 -> POST /api/convert
//        - 成功：manifest 塞進 review 階段（沿用既有 draft 編輯流程）
//        - 502（已標記但 Compute 離線）：scan 階段內顯示專用提示卡 + 「重新解析」
//          按鈕（用 gh_path 走 /api/import）
//   3. review  — 檢視 / 編輯解析出的草稿 manifest（id、display_name、description、
//      inputs[]、outputs[] 皆可微調）
//   4. done    — 註冊（POST /api/tools，status = draft | registered）
//
// 狀態存在模組級變數（STATE），單頁應用切頁籤離開再回來即重置為階段 1，
// 不需要持久化。所有 DOM 事件在 render 之後手動 addEventListener 綁定，
// 不使用 inline onclick。編輯欄位一律「DOM event -> 改 draft/candidate 物件」
// 單向流動，送出時才從物件組 payload。

import { api, toast } from "./api.js";
import { escapeHtml, kindBadge, validateId, bindEditableCells, idHint } from "./ui-common.js";
import { t } from "./i18n.js";

const NAME_PATTERN = /^[A-Za-z0-9_]+$/;
function nameHint() {
  return t("convert.nameHint");
}

// api.js 預設逾時只有 15 秒——大型 GH 檔案（數千物件）的掃描/標記/解析
// 遠超過這個值，必須逐呼叫放寬：
// - scan：GH_IO 遞迴掃描整棵定義樹
// - import：後端打 Rhino.Compute /io（其自身上限 300 秒）
// - convert：標記寫檔 + 寫後重掃驗證 + /io 解析，三段相加最久
const SCAN_TIMEOUT_MS = 180000;
const IMPORT_TIMEOUT_MS = 330000;
const CONVERT_TIMEOUT_MS = 420000;

// convert 逾時依勾選的標記數量分級（後端 /io 逾時同步分級，見
// routes.py）：輸入+輸出 >200 → 10 分鐘；>100 → 7 分鐘（即現行預設值）；
// 其他 → 維持 CONVERT_TIMEOUT_MS。
function convertTimeoutFor(totalMarks) {
  if (totalMarks > 200) return 600000;
  if (totalMarks > 100) return 420000;
  return CONVERT_TIMEOUT_MS;
}

// stage: "import" | "scan" | "review" | "done"
const STATE = {
  stage: "import",
  importMode: "file", // "file" | "path"
  scanMode: "auto", // "auto"（掃描勾選）| "direct"（既有 v1 直接解析）
  busy: false,
  computeDown: false, // /api/import 502 時開啟，顯示醒目提示（import 階段）
  ghioUnavailable: false, // /api/scan 501 時開啟，顯示說明卡（import 階段）
  draft: null, // 解析成功後的 manifest 草稿（可編輯）
  idError: "", // id 欄位驗證錯誤訊息
  registerResult: null, // { status } 註冊成功後顯示成功卡

  // scan 階段專用狀態
  scanData: null, // /api/scan 回應：{gh_path, scan, suggested_names}
  scanFileLabel: "", // 顯示用檔名
  scanBusy: false, // /api/convert 進行中
  scanConvertDown: false, // /api/convert 502（已標記已備份，Compute 離線）
  scanConvertBackupPath: "",
  scanReimportBusy: false, // 「重新解析」（/api/import）進行中
  inputRows: [], // [{guid, checked, name, candidate}]
  outputRows: [],

  // AI 深度解讀（task v3-B）狀態
  aiDescribeChecked: false, // 使用者是否勾選「AI 深度解讀」
  llmStatus: null, // /api/llm-status 回應：{provider, model, available, reason}
};

let root = null;

export function init(container) {
  root = container;
  STATE.stage = "import";
  STATE.importMode = "file";
  STATE.scanMode = "auto";
  STATE.busy = false;
  STATE.computeDown = false;
  STATE.ghioUnavailable = false;
  STATE.draft = null;
  STATE.idError = "";
  STATE.registerResult = null;
  STATE.scanData = null;
  STATE.scanFileLabel = "";
  STATE.scanBusy = false;
  STATE.scanConvertDown = false;
  STATE.scanConvertBackupPath = "";
  STATE.scanReimportBusy = false;
  STATE.inputRows = [];
  STATE.outputRows = [];
  STATE.aiDescribeChecked = false;
  STATE.llmStatus = null;
  render();
}

// 語言切換時由 app.js 呼叫：只重繪目前畫面（讀現有 STATE，不重置），
// 不是 init()——init() 會把整個轉換流程重置回第一步，語言切換不該有
// 這種副作用。
export function rerender() {
  if (root) render();
}

// ── render ───────────────────────────────────────────────────────────

function render() {
  root.innerHTML = `
    <div class="view-head">
      <span class="view-eyebrow">01 · Import &amp; Convert</span>
      <h2 class="view-title">${t("header.tab.convert")}</h2>
      <p class="view-desc">${t("convert.viewDesc")}</p>
    </div>
    <div id="convert-stage"></div>
  `;

  const stageEl = root.querySelector("#convert-stage");

  if (STATE.stage === "import") {
    stageEl.innerHTML = renderImportStage();
    bindImportStage(stageEl);
  } else if (STATE.stage === "scan") {
    stageEl.innerHTML = renderScanStage();
    bindScanStage(stageEl);
  } else if (STATE.stage === "review") {
    stageEl.innerHTML = renderReviewStage();
    bindReviewStage(stageEl);
  } else if (STATE.stage === "done") {
    stageEl.innerHTML = renderDoneStage();
    bindDoneStage(stageEl);
  }
}

// ── stage 1: import ──────────────────────────────────────────────────

function renderImportStage() {
  const scanModeCards = `
    <div class="scan-mode-cards" role="tablist" aria-label="${t("convert.modeTablistAria")}">
      <button type="button" class="scan-mode-card ${STATE.scanMode === "auto" ? "active" : ""}" data-scan-mode="auto">
        <span class="scan-mode-card-tag">${t("convert.tagRecommended")}</span>
        <span class="scan-mode-card-title">${t("convert.autoTitle")}</span>
        <span class="scan-mode-card-desc">${t("convert.autoDesc")}</span>
      </button>
      <button type="button" class="scan-mode-card ${STATE.scanMode === "direct" ? "active" : ""}" data-scan-mode="direct">
        <span class="scan-mode-card-tag scan-mode-card-tag-alt">${t("convert.tagAdvanced")}</span>
        <span class="scan-mode-card-title">${t("convert.directTitle")}</span>
        <span class="scan-mode-card-desc">${t("convert.directDesc")}</span>
      </button>
    </div>
  `;

  const modeToggle = `
    <div class="import-mode-toggle" role="tablist" aria-label="${t("convert.importModeAria")}">
      <button type="button" class="import-mode-btn ${STATE.importMode === "file" ? "active" : ""}" data-mode="file">
        ${t("convert.uploadFile")}
      </button>
      <button type="button" class="import-mode-btn ${STATE.importMode === "path" ? "active" : ""}" data-mode="path">
        ${t("convert.localPath")}
      </button>
    </div>
  `;

  let body;
  if (STATE.busy) {
    const busyText =
      STATE.scanMode === "auto" ? t("convert.scanningBusy") : t("convert.parsingBusy");
    body = `
      <div class="dropzone dropzone-busy" id="dropzone">
        <div class="dropzone-spinner" aria-hidden="true"></div>
        <p class="dropzone-title">${busyText}</p>
        <p class="dropzone-hint">${t("convert.pleaseWaitHint")}</p>
      </div>
    `;
  } else if (STATE.importMode === "file") {
    body = `
      <div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="${t("convert.dropzoneAria")}">
        <p class="dropzone-title">${t("convert.dropzoneTitle")}</p>
        <p class="dropzone-hint">${t("convert.or")}</p>
        <button type="button" class="btn btn-primary" id="browse-btn">${t("convert.browseFile")}</button>
        <input type="file" id="file-input" accept=".gh" hidden />
      </div>
    `;
  } else {
    body = `
      <div class="path-import">
        <label class="field-label" for="gh-path-input">${t("convert.localPathLabel")}</label>
        <div class="path-import-row">
          <input type="text" id="gh-path-input" class="input-text mono" placeholder="C:\\models\\example.gh" />
          <button type="button" class="btn btn-primary" id="path-submit-btn">${STATE.scanMode === "auto" ? t("convert.scanBtn") : t("convert.importBtn")}</button>
        </div>
        <p class="field-hint">${t("convert.pathHint")}</p>
      </div>
    `;
  }

  const computeBanner = STATE.computeDown
    ? `
      <div class="inline-alert" role="alert">
        <span class="inline-alert-icon" aria-hidden="true">&#9888;</span>
        <div>
          <strong>${t("convert.computeDownTitle")}</strong>
          <p>${t("convert.computeDownDesc")}</p>
        </div>
      </div>
    `
    : "";

  const ghioBanner = STATE.ghioUnavailable
    ? `
      <div class="inline-alert" role="alert">
        <span class="inline-alert-icon" aria-hidden="true">&#9888;</span>
        <div>
          <strong>${t("convert.ghioUnavailableTitle")}</strong>
          <p>${t("convert.ghioUnavailableDesc")}</p>
        </div>
      </div>
    `
    : "";

  return `
    <div class="import-panel">
      ${scanModeCards}
      ${modeToggle}
      ${ghioBanner}
      ${computeBanner}
      ${body}
    </div>
  `;
}

function bindImportStage(stageEl) {
  stageEl.querySelectorAll(".scan-mode-card").forEach((card) => {
    card.addEventListener("click", () => {
      if (STATE.busy) return;
      STATE.scanMode = card.dataset.scanMode;
      STATE.ghioUnavailable = false;
      STATE.computeDown = false;
      render();
    });
  });

  const modeBtns = stageEl.querySelectorAll(".import-mode-btn");
  modeBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      if (STATE.busy) return;
      STATE.importMode = btn.dataset.mode;
      render();
    });
  });

  const dropzone = stageEl.querySelector("#dropzone");
  if (dropzone && STATE.importMode === "file" && !STATE.busy) {
    dropzone.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      dropzone.classList.add("dropzone-hover");
    });
    dropzone.addEventListener("dragleave", () => {
      dropzone.classList.remove("dropzone-hover");
    });
    dropzone.addEventListener("drop", (ev) => {
      ev.preventDefault();
      dropzone.classList.remove("dropzone-hover");
      const file = ev.dataTransfer?.files?.[0];
      if (file) submitFile(file);
    });
    dropzone.addEventListener("click", () => {
      stageEl.querySelector("#file-input")?.click();
    });
    dropzone.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        stageEl.querySelector("#file-input")?.click();
      }
    });

    const browseBtn = stageEl.querySelector("#browse-btn");
    const fileInput = stageEl.querySelector("#file-input");
    browseBtn?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      fileInput?.click();
    });
    fileInput?.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (file) submitFile(file);
    });
  }

  if (STATE.importMode === "path" && !STATE.busy) {
    const pathInput = stageEl.querySelector("#gh-path-input");
    const pathBtn = stageEl.querySelector("#path-submit-btn");
    const submitPath = () => {
      const value = pathInput.value.trim();
      if (!value) {
        toast(t("convert.pathRequired"), "error");
        return;
      }
      submitGhPath(value);
    };
    pathBtn?.addEventListener("click", submitPath);
    pathInput?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        submitPath();
      }
    });
  }
}

function submitFile(file) {
  if (!file.name.toLowerCase().endsWith(".gh")) {
    toast(t("convert.onlyGhFiles"), "error");
    return;
  }
  if (STATE.scanMode === "auto") {
    submitFileForScan(file);
  } else {
    submitFileForImport(file);
  }
}

function submitGhPath(ghPath) {
  if (STATE.scanMode === "auto") {
    submitPathForScan(ghPath);
  } else {
    submitPathForImport(ghPath);
  }
}

// ── v1 直接解析路徑（既有邏輯，未變） ──────────────────────────────

async function submitFileForImport(file) {
  STATE.busy = true;
  STATE.computeDown = false;
  render();

  const formData = new FormData();
  formData.append("file", file, file.name);

  try {
    const manifest = await api("/api/import", {
      method: "POST",
      body: formData,
      timeoutMs: IMPORT_TIMEOUT_MS,
    });
    onImportSuccess(manifest);
  } catch (err) {
    onImportError(err);
  }
}

async function submitPathForImport(ghPath) {
  STATE.busy = true;
  STATE.computeDown = false;
  render();

  try {
    const manifest = await api("/api/import", {
      method: "POST",
      body: { gh_path: ghPath },
      timeoutMs: IMPORT_TIMEOUT_MS,
    });
    onImportSuccess(manifest);
  } catch (err) {
    onImportError(err);
  }
}

function onImportSuccess(manifest) {
  STATE.busy = false;
  STATE.draft = manifest;
  STATE.idError = "";
  STATE.stage = "review";
  render();
  toast(t("convert.importSuccessToast"), "success");
}

function onImportError(err) {
  STATE.busy = false;
  const message = err instanceof Error ? err.message : String(err);
  // api() 對非 2xx 一律用 detail 訊息包成 Error；502（Rhino.Compute 未啟動）
  // 的 detail 內容含「Rhino.Compute」字樣（見 hoger/api/routes.py），用這個
  // 特徵判斷是否顯示醒目的 compute-down 提示區塊，其餘一律走 toast。
  if (message.includes("Rhino.Compute") || message.includes("502")) {
    STATE.computeDown = true;
    render();
  } else {
    STATE.computeDown = false;
    render();
  }
  toast(message, "error");
}

// ── v2 自動轉換：掃描 ────────────────────────────────────────────────

async function submitFileForScan(file) {
  STATE.busy = true;
  STATE.ghioUnavailable = false;
  render();

  const formData = new FormData();
  formData.append("file", file, file.name);

  try {
    const scanData = await api("/api/scan", {
      method: "POST",
      body: formData,
      timeoutMs: SCAN_TIMEOUT_MS,
    });
    onScanSuccess(scanData, file.name);
  } catch (err) {
    onScanError(err);
  }
}

async function submitPathForScan(ghPath) {
  STATE.busy = true;
  STATE.ghioUnavailable = false;
  render();

  try {
    const scanData = await api("/api/scan", {
      method: "POST",
      body: { gh_path: ghPath },
      timeoutMs: SCAN_TIMEOUT_MS,
    });
    onScanSuccess(scanData, ghPath.replace(/\\/g, "/").split("/").pop());
  } catch (err) {
    onScanError(err);
  }
}

function onScanSuccess(scanData, fileLabel) {
  STATE.busy = false;
  STATE.scanData = scanData;
  STATE.scanFileLabel = fileLabel || scanData.gh_path;
  STATE.scanConvertDown = false;
  STATE.scanConvertBackupPath = "";

  const suggested = scanData.suggested_names || {};
  const inputs = scanData.scan?.inputs || [];
  const outputs = scanData.scan?.outputs || [];

  STATE.inputRows = inputs.map((cand) => ({
    guid: cand.instance_guid,
    checked: Array.isArray(cand.feeds) && cand.feeds.length > 0,
    name: suggested[cand.instance_guid] || "",
    candidate: cand,
  }));
  STATE.outputRows = outputs.map((cand) => ({
    guid: cand.instance_guid,
    checked: Array.isArray(cand.fed_by) && cand.fed_by.length > 0,
    name: suggested[cand.instance_guid] || "",
    candidate: cand,
  }));

  STATE.aiDescribeChecked = false;
  STATE.llmStatus = null;
  STATE.stage = "scan";
  render();
  toast(t("convert.scanSuccessToast"), "success");
  fetchLlmStatus();
}

async function fetchLlmStatus() {
  try {
    const status = await api("/api/llm-status");
    STATE.llmStatus = status;
  } catch (err) {
    // 查詢失敗（極少數情況，如後端剛好重啟）：視同不可用，checkbox 停用。
    STATE.llmStatus = { available: false, reason: t("convert.llmStatusQueryFailed"), provider: "", model: "" };
  }
  // 僅在仍處於 scan 階段時重繪，避免使用者已離開這個畫面後的過期更新。
  if (STATE.stage === "scan" && !STATE.scanConvertDown) {
    render();
  }
}

function onScanError(err) {
  STATE.busy = false;
  const message = err instanceof Error ? err.message : String(err);
  if (message.includes("GH_IO") || message.includes("501")) {
    STATE.ghioUnavailable = true;
    render();
  } else {
    STATE.ghioUnavailable = false;
    render();
  }
  toast(message, "error");
}

// ── AI 深度解讀選項（task v3-B） ────────────────────────────────────
//
// 掃描勾選階段底部（開始轉換按鈕上方）的選項列：checkbox 讓使用者選擇
// 是否把 GH 結構摘要送給 LLM 生成語意描述。可用性取決於 /api/llm-status
// （進入 scan 階段時 fetch，見 fetchLlmStatus），查詢完成前顯示載入中、
// 不可用時停用並顯示原因。

function renderAiDescribeOption() {
  const status = STATE.llmStatus;

  if (!status) {
    return `
      <div class="ai-describe-option">
        <label class="ai-describe-label ai-describe-label-loading">
          <input type="checkbox" disabled />
          <span>${t("convert.aiDescribeLoading")}</span>
        </label>
      </div>
    `;
  }

  if (!status.available) {
    return `
      <div class="ai-describe-option">
        <label class="ai-describe-label ai-describe-label-disabled">
          <input type="checkbox" disabled />
          <span>${t("convert.aiDescribeLabel")}</span>
        </label>
        <p class="ai-describe-hint ai-describe-hint-unavailable">${escapeHtml(status.reason || t("convert.aiDescribeUnavailableFallback"))}</p>
      </div>
    `;
  }

  const providerNote = `${escapeHtml(status.provider)}${status.model ? " / " + escapeHtml(status.model) : ""}`;

  return `
    <div class="ai-describe-option">
      <label class="ai-describe-label">
        <input type="checkbox" id="ai-describe-checkbox" ${STATE.aiDescribeChecked ? "checked" : ""} />
        <span>${t("convert.aiDescribeLabel")}</span>
      </label>
      <p class="ai-describe-hint">${t("convert.aiDescribeCurrentSetting", { provider: providerNote })}</p>
    </div>
  `;
}

// ── stage 2: scan（掃描勾選） ────────────────────────────────────────

function renderScanStage() {
  if (STATE.scanConvertDown) {
    return renderScanConvertDownCard();
  }

  const scan = STATE.scanData?.scan || {};
  const alreadyMarked = scan.already_marked_count || 0;
  const objectCount = scan.object_count ?? 0;

  const alreadyMarkedNote =
    alreadyMarked > 0
      ? `<p class="scan-summary-note">${t("convert.alreadyMarkedNote", { n: alreadyMarked })}</p>`
      : "";

  const nameUsage = computeNameUsage();

  const inputRowsHtml = STATE.inputRows.length
    ? STATE.inputRows.map((row, idx) => renderCandidateRow(row, idx, "input", nameUsage)).join("")
    : `<tr><td colspan="7" class="cell-empty">${t("convert.noCandidateInputs")}</td></tr>`;

  const outputRowsHtml = STATE.outputRows.length
    ? STATE.outputRows.map((row, idx) => renderCandidateRow(row, idx, "output", nameUsage)).join("")
    : `<tr><td colspan="5" class="cell-empty">${t("convert.noCandidateOutputs")}</td></tr>`;

  const checkedInputCount = STATE.inputRows.filter((r) => r.checked).length;
  const checkedOutputCount = STATE.outputRows.filter((r) => r.checked).length;
  const nothingChecked = checkedInputCount === 0 && checkedOutputCount === 0;

  const busyOverlay = STATE.scanBusy
    ? `
      <div class="scan-convert-busy" role="status">
        <div class="dropzone-spinner" aria-hidden="true"></div>
        <p>${t("convert.markingBusy")}${STATE.aiDescribeChecked ? t("convert.markingBusyAiSuffix") : ""}</p>
      </div>
    `
    : "";

  return `
    <div class="scan-panel">
      <div class="summary-card scan-summary-card">
        <div class="scan-summary-row">
          <div>
            <p class="scan-summary-filename mono">${escapeHtml(STATE.scanFileLabel)}</p>
            <p class="scan-summary-meta">${t("convert.scanSummaryMeta", { objectCount, alreadyMarked })}</p>
          </div>
        </div>
        ${alreadyMarkedNote}
      </div>

      <div class="table-section">
        <h3 class="table-section-title">${t("convert.candidateInputsTitle")} <span class="table-count">${STATE.inputRows.length}</span></h3>
        <div class="table-scroll">
          <table class="data-table scan-table">
            <thead>
              <tr>
                <th class="scan-th-check"><span class="sr-only">${t("convert.thCheckSr")}</span></th>
                <th>${t("common.thType")}</th>
                <th>Nickname</th>
                <th>${t("convert.thValueRange")}</th>
                <th>${t("convert.thFeeds")}</th>
                <th>${t("convert.thParamName")}</th>
                <th>${t("convert.thExistingMark")}</th>
              </tr>
            </thead>
            <tbody id="scan-inputs-tbody">${inputRowsHtml}</tbody>
          </table>
        </div>
      </div>

      <div class="table-section">
        <h3 class="table-section-title">${t("convert.candidateOutputsTitle")} <span class="table-count">${STATE.outputRows.length}</span></h3>
        <div class="table-scroll">
          <table class="data-table scan-table">
            <thead>
              <tr>
                <th class="scan-th-check"><span class="sr-only">${t("convert.thCheckSr")}</span></th>
                <th>${t("common.thType")}</th>
                <th>Nickname</th>
                <th>${t("convert.thFrom")}</th>
                <th>${t("convert.thParamName")}</th>
                <th>${t("convert.thExistingMark")}</th>
              </tr>
            </thead>
            <tbody id="scan-outputs-tbody">${outputRowsHtml}</tbody>
          </table>
        </div>
      </div>

      ${busyOverlay}

      ${renderAiDescribeOption()}

      <div class="review-actions">
        <button type="button" class="btn btn-ghost" id="scan-back-btn" ${STATE.scanBusy ? "disabled" : ""}>${t("common.back")}</button>
        <div class="review-actions-primary scan-actions-primary">
          <span class="scan-check-summary">${t("convert.scanCheckSummary", { inputs: checkedInputCount, outputs: checkedOutputCount })}</span>
          <button type="button" class="btn btn-primary" id="scan-convert-btn" ${STATE.scanBusy || nothingChecked ? "disabled" : ""}>${t("convert.startConvertBtn")}</button>
        </div>
      </div>
    </div>
  `;
}

function renderScanConvertDownCard() {
  return `
    <div class="scan-panel">
      <div class="inline-alert inline-alert-amber" role="alert">
        <span class="inline-alert-icon" aria-hidden="true">&#9888;</span>
        <div>
          <strong>${t("convert.scanConvertDownTitle")}</strong>
          <p>${t("convert.backupPathLabel")}<span class="mono">${escapeHtml(STATE.scanConvertBackupPath)}</span></p>
          <p>${t("convert.scanConvertDownDesc")}</p>
        </div>
      </div>
      <div class="review-actions">
        <button type="button" class="btn btn-ghost" id="scan-back-btn" ${STATE.scanReimportBusy ? "disabled" : ""}>${t("common.back")}</button>
        <button type="button" class="btn btn-primary" id="scan-reimport-btn" ${STATE.scanReimportBusy ? "disabled" : ""}>
          ${STATE.scanReimportBusy ? t("convert.reparsing") : t("convert.reparse")}
        </button>
      </div>
    </div>
  `;
}

function feedsToText(list, aKey, bKey) {
  if (!Array.isArray(list) || list.length === 0) return "";
  return list
    .map((f) => {
      const a = f?.[aKey] ?? "";
      const b = f?.[bKey] ?? "";
      return `${a}${a && b ? " / " : ""}${b}`;
    })
    .join(", ");
}

function computeNameUsage() {
  // 統計每個（trim 後）名稱在所有「勾選中」列的出現次數，用來標記重名列。
  const counts = new Map();
  [...STATE.inputRows, ...STATE.outputRows].forEach((row) => {
    if (!row.checked) return;
    const key = row.name.trim();
    if (!key) return;
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return counts;
}

function renderCandidateRow(row, idx, kind, nameUsage) {
  const cand = row.candidate;
  const nameKey = row.name.trim();
  const isDuplicate = nameKey !== "" && (nameUsage.get(nameKey) || 0) > 1;
  const isInvalidFormat = nameKey !== "" && !NAME_PATTERN.test(nameKey);
  const isEmpty = row.checked && nameKey === "";
  const nameInvalid = row.checked && (isDuplicate || isInvalidFormat || isEmpty);

  const existingMarkBadge = cand.existing_mark
    ? `<span class="existing-mark-badge mono">${escapeHtml(cand.existing_mark)}</span>`
    : `<span class="cell-muted">—</span>`;

  const nicknameCell = cand.nickname
    ? escapeHtml(cand.nickname)
    : `<span class="cell-muted">—</span>`;

  let valueRangeCell = `<span class="cell-muted">—</span>`;
  if (kind === "input") {
    const parts = [];
    if (cand.current_value !== null && cand.current_value !== undefined && cand.current_value !== "") {
      parts.push(escapeHtml(String(cand.current_value)));
    }
    if (cand.minimum !== null && cand.minimum !== undefined && cand.maximum !== null && cand.maximum !== undefined) {
      parts.push(`<span class="cell-muted">[${escapeHtml(String(cand.minimum))} – ${escapeHtml(String(cand.maximum))}]</span>`);
    }
    if (parts.length) valueRangeCell = parts.join(" ");
  }

  const feedsCell =
    kind === "input"
      ? feedsToText(cand.feeds, "component", "input")
      : feedsToText(cand.fed_by, "component", "output");
  const feedsDisplay = feedsCell ? escapeHtml(feedsCell) : `<span class="cell-muted">—</span>`;

  const nameFieldHtml = `
    <input type="text" class="cell-input mono scan-name-input ${nameInvalid ? "input-invalid" : ""}"
      data-kind="${kind}" data-idx="${idx}" value="${escapeHtml(row.name)}" placeholder="${t("convert.paramNamePlaceholder")}" />
  `;

  const checkboxHtml = `
    <input type="checkbox" class="scan-row-checkbox" data-kind="${kind}" data-idx="${idx}" ${row.checked ? "checked" : ""} />
  `;

  const extraCells =
    kind === "input"
      ? `
        <td>${valueRangeCell}</td>
        <td class="scan-feeds-cell">${feedsDisplay}</td>
      `
      : `
        <td class="scan-feeds-cell">${feedsDisplay}</td>
      `;

  return `
    <tr class="${row.checked ? "" : "scan-row-unchecked"}">
      <td class="scan-td-check">${checkboxHtml}</td>
      <td>${kindBadge(mapObjectTypeToKind(cand.object_type))}</td>
      <td>${nicknameCell}</td>
      ${extraCells}
      <td>${nameFieldHtml}</td>
      <td>${existingMarkBadge}</td>
    </tr>
  `;
}

// 掃描候選的 object_type 是 GH 元件名稱（"Number Slider" 等），不是既有
// kindBadge 認得的 kind 字串；這裡只做粗略分類讓徽章顏色有意義，不影響
// 實際送出的資料（送出時只送 guid + name，型別由後端 /io 解析決定）。
function mapObjectTypeToKind(objectType) {
  const t = (objectType || "").toLowerCase();
  if (t.includes("slider")) return "number";
  if (t.includes("toggle")) return "boolean";
  if (t.includes("panel")) return "string";
  if (t.includes("value list")) return "string";
  return "geometry";
}

function applyNameInput(input, stageEl) {
  const idx = Number(input.dataset.idx);
  const list = input.dataset.kind === "input" ? STATE.inputRows : STATE.outputRows;
  list[idx].name = input.value;
  render();
  // render() 重繪後原 input 已失焦重建；重新取得同一格並還原焦點與游標。
  const newTbody = stageEl.querySelector(
    input.dataset.kind === "input" ? "#scan-inputs-tbody" : "#scan-outputs-tbody"
  );
  const newInput = newTbody?.querySelector(`.scan-name-input[data-idx="${idx}"]`);
  if (newInput) {
    newInput.focus();
    const pos = newInput.value.length;
    newInput.setSelectionRange(pos, pos);
  }
}

function bindScanStage(stageEl) {
  if (STATE.scanConvertDown) {
    stageEl.querySelector("#scan-back-btn")?.addEventListener("click", () => {
      if (STATE.scanReimportBusy) return;
      backToImport();
    });
    stageEl.querySelector("#scan-reimport-btn")?.addEventListener("click", () => {
      reimportAfterMark();
    });
    return;
  }

  stageEl.querySelectorAll(".scan-row-checkbox").forEach((cb) => {
    cb.addEventListener("change", () => {
      const idx = Number(cb.dataset.idx);
      const list = cb.dataset.kind === "input" ? STATE.inputRows : STATE.outputRows;
      list[idx].checked = cb.checked;
      render();
    });
  });

  stageEl.querySelectorAll(".scan-name-input").forEach((input) => {
    // IME（中文/日文/韓文等）輸入法防護：組字（composition）進行中的
    // input 事件是每個候選字階段都會觸發的中間態，不是使用者確認的最終
    // 文字。若在組字中途就 render() 重繪，畫面會在候選字選字視窗還開著
    // 時被打斷，導致組字异常中斷或游標錯位。用 compositionstart/end 旗標
    // 讓組字進行中的 input 事件只更新 state、不觸發重繪；真正的重繪延後
    // 到 compositionend（使用者已確認這個字/詞）才做一次。
    let composing = false;

    input.addEventListener("compositionstart", () => {
      composing = true;
    });

    input.addEventListener("compositionend", () => {
      composing = false;
      applyNameInput(input, stageEl);
    });

    input.addEventListener("input", () => {
      if (composing) {
        // 組字中：先同步 state 的名稱（供驗證/重名檢查等邏輯讀到最新值），
        // 但不 render()，避免重繪打斷正在輸入法選字視窗中的組字。
        const idx = Number(input.dataset.idx);
        const list = input.dataset.kind === "input" ? STATE.inputRows : STATE.outputRows;
        list[idx].name = input.value;
        return;
      }
      applyNameInput(input, stageEl);
    });
  });

  stageEl.querySelector("#scan-back-btn")?.addEventListener("click", () => {
    if (STATE.scanBusy) return;
    backToImport();
  });

  stageEl.querySelector("#ai-describe-checkbox")?.addEventListener("change", (ev) => {
    STATE.aiDescribeChecked = ev.target.checked;
  });

  stageEl.querySelector("#scan-convert-btn")?.addEventListener("click", () => {
    submitConvert();
  });
}

function backToImport() {
  STATE.stage = "import";
  STATE.scanData = null;
  STATE.scanFileLabel = "";
  STATE.scanConvertDown = false;
  STATE.scanConvertBackupPath = "";
  STATE.inputRows = [];
  STATE.outputRows = [];
  render();
}

function validateScanSelection() {
  const checkedRows = [...STATE.inputRows, ...STATE.outputRows].filter((r) => r.checked);
  if (checkedRows.length === 0) {
    return t("convert.selectAtLeastOne");
  }
  const seen = new Map();
  for (const row of checkedRows) {
    const name = row.name.trim();
    if (!name) return t("convert.fillAllNames");
    if (!NAME_PATTERN.test(name)) return t("convert.nameFormatInvalid", { name, hint: nameHint() });
    if (seen.has(name)) return t("convert.nameDuplicate", { name });
    seen.set(name, true);
  }
  return "";
}

// 掃描物件數超過這個門檻時，勾選 AI 深度解讀會先跳確認（可能消耗大量
// token）。取消確認不擋轉換本身，只取消勾選繼續走規則式描述。
const AI_DESCRIBE_TOKEN_WARNING_OBJECT_COUNT = 300;

async function submitConvert() {
  const err = validateScanSelection();
  if (err) {
    toast(err, "error");
    render();
    return;
  }

  if (STATE.aiDescribeChecked) {
    const objectCount = STATE.scanData?.scan?.object_count ?? 0;
    if (objectCount > AI_DESCRIBE_TOKEN_WARNING_OBJECT_COUNT) {
      const proceed = window.confirm(t("convert.aiDescribeConfirm", { n: objectCount }));
      if (!proceed) {
        STATE.aiDescribeChecked = false;
      }
    }
  }

  STATE.scanBusy = true;
  render();

  const inputs = STATE.inputRows
    .filter((r) => r.checked)
    .map((r) => ({ guid: r.guid, name: r.name.trim() }));
  const outputs = STATE.outputRows
    .filter((r) => r.checked)
    .map((r) => ({ guid: r.guid, name: r.name.trim() }));

  try {
    const result = await api("/api/convert", {
      method: "POST",
      body: {
        gh_path: STATE.scanData.gh_path,
        inputs,
        outputs,
        ai_describe: STATE.aiDescribeChecked,
      },
      timeoutMs: convertTimeoutFor(inputs.length + outputs.length),
    });
    STATE.scanBusy = false;
    STATE.draft = result.manifest;
    STATE.idError = "";
    STATE.stage = "review";
    render();
    toast(t("convert.convertSuccessToast", { path: result.backup_path }), "success");
    if (result.ai_describe_error) {
      toast(t("convert.aiDescribeErrorToast", { error: result.ai_describe_error }), "error");
    }
  } catch (caught) {
    STATE.scanBusy = false;
    const message = caught instanceof Error ? caught.message : String(caught);
    if (message.includes("Rhino.Compute") || message.includes("502")) {
      const backupMatch = message.match(/backup_path[:：]\s*([^）)]+)/);
      STATE.scanConvertDown = true;
      STATE.scanConvertBackupPath = backupMatch ? backupMatch[1].trim() : "";
      render();
      toast(message, "error");
    } else {
      render();
      toast(message, "error");
    }
  }
}

async function reimportAfterMark() {
  STATE.scanReimportBusy = true;
  render();

  try {
    const manifest = await api("/api/import", {
      method: "POST",
      body: { gh_path: STATE.scanData.gh_path },
      timeoutMs: IMPORT_TIMEOUT_MS,
    });
    STATE.scanReimportBusy = false;
    STATE.draft = manifest;
    STATE.idError = "";
    STATE.stage = "review";
    STATE.scanConvertDown = false;
    render();
    toast(t("convert.reimportSuccessToast"), "success");
  } catch (caught) {
    STATE.scanReimportBusy = false;
    const message = caught instanceof Error ? caught.message : String(caught);
    render();
    toast(message, "error");
  }
}

// ── stage 3: review ──────────────────────────────────────────────────

function renderReviewStage() {
  const d = STATE.draft;
  const idErrorClass = STATE.idError ? "input-invalid" : "";

  const inputsRows = d.inputs.length
    ? d.inputs
        .map((input, idx) => {
          const isNumeric = input.kind === "number" || input.kind === "integer";
          const numericCells = isNumeric
            ? `
              <td>
                <input type="text" class="cell-input mono" data-field="default" data-idx="${idx}"
                  value="${escapeHtml(input.default ?? "")}" placeholder="—" />
              </td>
              <td>
                <input type="text" class="cell-input mono" data-field="minimum" data-idx="${idx}"
                  value="${escapeHtml(input.minimum ?? "")}" placeholder="—" />
              </td>
              <td>
                <input type="text" class="cell-input mono" data-field="maximum" data-idx="${idx}"
                  value="${escapeHtml(input.maximum ?? "")}" placeholder="—" />
              </td>
            `
            : `<td class="cell-muted">—</td><td class="cell-muted">—</td><td class="cell-muted">—</td>`;

          return `
            <tr>
              <td class="mono cell-param-name">${escapeHtml(input.param_name)}</td>
              <td>${kindBadge(input.kind)}</td>
              <td>${input.required ? `<span class="required-badge">${t("common.required")}</span>` : `<span class="cell-muted">${t("common.optional")}</span>`}</td>
              <td>
                <input type="text" class="cell-input" data-field="description" data-idx="${idx}"
                  value="${escapeHtml(input.description)}" placeholder="${t("common.descPlaceholder")}" />
              </td>
              ${numericCells}
            </tr>
          `;
        })
        .join("")
    : `<tr><td colspan="7" class="cell-empty">${t("convert.noInputParams")}</td></tr>`;

  const outputsRows = d.outputs.length
    ? d.outputs
        .map((output, idx) => `
          <tr>
            <td class="mono cell-param-name">${escapeHtml(output.param_name)}</td>
            <td>${kindBadge(output.kind)}</td>
            <td>
              <input type="text" class="cell-input" data-field="description" data-idx="${idx}"
                value="${escapeHtml(output.description)}" placeholder="${t("common.descPlaceholder")}" />
            </td>
            <td>
              <input type="text" class="cell-input" data-field="unit" data-idx="${idx}"
                value="${escapeHtml(output.unit)}" placeholder="${t("common.unitPlaceholder")}" />
            </td>
          </tr>
        `)
        .join("")
    : `<tr><td colspan="4" class="cell-empty">${t("convert.noOutputParams")}</td></tr>`;

  return `
    <div class="review-panel">
      <div class="summary-card">
        <div class="summary-grid">
          <div class="summary-field">
            <label class="field-label" for="draft-id">${t("common.toolId")}</label>
            <input type="text" id="draft-id" class="input-text mono ${idErrorClass}" value="${escapeHtml(d.id)}" />
            <p id="draft-id-msg" class="${STATE.idError ? "field-error" : "field-hint"}">${escapeHtml(STATE.idError || idHint())}</p>
          </div>
          <div class="summary-field">
            <label class="field-label" for="draft-display-name">${t("common.displayName")}</label>
            <input type="text" id="draft-display-name" class="input-text" value="${escapeHtml(d.display_name)}" />
          </div>
          <div class="summary-field summary-field-wide">
            <label class="field-label" for="draft-description">${t("common.description")}</label>
            <textarea id="draft-description" class="input-textarea" rows="2"
              placeholder="${t("common.descAutoFillHint")}">${escapeHtml(d.description)}</textarea>
          </div>
          <div class="summary-field summary-field-wide">
            <label class="field-label">${t("convert.sourceFileReadonly")}</label>
            <p class="readonly-value mono">${escapeHtml(d.gh_file)}</p>
          </div>
          ${
            d.auto_doc
              ? `
          <div class="summary-field summary-field-wide">
            <details class="auto-doc-details">
              <summary>${t("common.viewAutoDoc")}</summary>
              <pre class="schema-preview">${escapeHtml(d.auto_doc)}</pre>
            </details>
          </div>`
              : ""
          }
        </div>
      </div>

      <div class="table-section">
        <h3 class="table-section-title">${t("common.inputs")} <span class="table-count">${d.inputs.length}</span></h3>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>${t("common.thParamNameFull")}</th>
                <th>${t("common.thType")}</th>
                <th>${t("common.thRequired")}</th>
                <th>${t("common.thDescription")}</th>
                <th>${t("common.thDefault")}</th>
                <th>${t("common.thMin")}</th>
                <th>${t("common.thMax")}</th>
              </tr>
            </thead>
            <tbody id="inputs-tbody">${inputsRows}</tbody>
          </table>
        </div>
      </div>

      <div class="table-section">
        <h3 class="table-section-title">${t("common.outputs")} <span class="table-count">${d.outputs.length}</span></h3>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>${t("common.thParamNameFull")}</th>
                <th>${t("common.thType")}</th>
                <th>${t("common.thDescription")}</th>
                <th>${t("common.thUnit")}</th>
              </tr>
            </thead>
            <tbody id="outputs-tbody">${outputsRows}</tbody>
          </table>
        </div>
      </div>

      <div class="review-actions">
        <button type="button" class="btn btn-ghost" id="reimport-btn">${t("convert.reimport")}</button>
        <div class="review-actions-primary">
          <button type="button" class="btn btn-secondary" id="save-draft-btn">${t("convert.saveDraftBtn")}</button>
          <button type="button" class="btn btn-primary" id="register-btn">${t("convert.registerBtn")}</button>
        </div>
      </div>
    </div>
  `;
}

function bindReviewStage(stageEl) {
  const idInput = stageEl.querySelector("#draft-id");
  const idMsg = stageEl.querySelector("#draft-id-msg");
  idInput.addEventListener("input", () => {
    STATE.draft.id = idInput.value;
    STATE.idError = validateId(idInput.value);
    idInput.classList.toggle("input-invalid", Boolean(STATE.idError));
    // 訊息元素固定存在（render 時就放好），這裡只就地更新 class 與文字，
    // 不做動態插入——快速輸入下也不可能重複。
    idMsg.className = STATE.idError ? "field-error" : "field-hint";
    idMsg.textContent = STATE.idError || idHint();
  });

  stageEl.querySelector("#draft-display-name").addEventListener("input", (ev) => {
    STATE.draft.display_name = ev.target.value;
  });

  stageEl.querySelector("#draft-description").addEventListener("input", (ev) => {
    STATE.draft.description = ev.target.value;
  });

  bindEditableCells(stageEl.querySelector("#inputs-tbody"), STATE.draft.inputs, ["minimum", "maximum"]);
  bindEditableCells(stageEl.querySelector("#outputs-tbody"), STATE.draft.outputs, []);

  stageEl.querySelector("#reimport-btn").addEventListener("click", () => {
    if (window.confirm(t("convert.discardDraftConfirm"))) {
      STATE.stage = "import";
      STATE.draft = null;
      STATE.idError = "";
      STATE.computeDown = false;
      render();
    }
  });

  stageEl.querySelector("#save-draft-btn").addEventListener("click", () => {
    registerDraft("draft");
  });

  stageEl.querySelector("#register-btn").addEventListener("click", () => {
    registerDraft("registered");
  });
}

async function registerDraft(status) {
  // 防護：畫面上任何標紅的欄位（id 格式錯誤、min/max 非數字等）都擋下註冊。
  // 數字欄位輸入無效值時只標紅、不寫入 draft（見 bindEditableCells），若不
  // 擋下會靜默用舊值送出。
  if (root.querySelectorAll(".input-invalid").length > 0) {
    toast(t("common.fixInvalidFields"), "error");
    return;
  }

  const idErr = validateId(STATE.draft.id);
  if (idErr) {
    STATE.idError = idErr;
    render();
    toast(t("convert.fixIdFormat"), "error");
    return;
  }

  const now = new Date().toISOString();
  const payload = {
    ...STATE.draft,
    status,
    updated_at: now,
  };

  try {
    await api("/api/tools", { method: "POST", body: payload });
    STATE.registerResult = { status };
    STATE.stage = "done";
    render();
    toast(
      status === "registered" ? t("convert.registeredToast") : t("convert.savedDraftToast"),
      "success"
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    toast(message, "error");
  }
}

// ── stage 4: done ────────────────────────────────────────────────────

function renderDoneStage() {
  const isRegistered = STATE.registerResult?.status === "registered";
  const name = escapeHtml(STATE.draft?.display_name || "");
  const id = escapeHtml(STATE.draft?.id || "");
  const desc = isRegistered
    ? t("convert.doneDescRegistered", { name, id })
    : t("convert.doneDescDraft", { name, id });
  return `
    <div class="success-card">
      <span class="success-icon" aria-hidden="true">&#10003;</span>
      <h3>${isRegistered ? t("convert.doneRegisteredTitle") : t("convert.doneDraftTitle")}</h3>
      <p>${desc}</p>
      <div class="success-actions">
        <a class="btn btn-primary" href="#/manager">${t("convert.gotoManager")}</a>
        <button type="button" class="btn btn-ghost" id="import-another-btn">${t("convert.importAnother")}</button>
      </div>
    </div>
  `;
}

function bindDoneStage(stageEl) {
  stageEl.querySelector("#import-another-btn").addEventListener("click", () => {
    STATE.stage = "import";
    STATE.draft = null;
    STATE.idError = "";
    STATE.computeDown = false;
    STATE.registerResult = null;
    render();
  });
}
