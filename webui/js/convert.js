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
import { escapeHtml, kindBadge, validateId, bindEditableCells, ID_HINT } from "./ui-common.js";

const NAME_PATTERN = /^[A-Za-z0-9_]+$/;
const NAME_HINT = "僅限英數字與底線（^[A-Za-z0-9_]+$）。";

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
  render();
}

// ── render ───────────────────────────────────────────────────────────

function render() {
  root.innerHTML = `
    <div class="view-head">
      <span class="view-eyebrow">01 · Import &amp; Convert</span>
      <h2 class="view-title">轉換</h2>
      <p class="view-desc">將 Grasshopper 檔案匯入並轉換為可執行的工具定義。</p>
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
    <div class="scan-mode-cards" role="tablist" aria-label="轉換方式">
      <button type="button" class="scan-mode-card ${STATE.scanMode === "auto" ? "active" : ""}" data-scan-mode="auto">
        <span class="scan-mode-card-tag">推薦</span>
        <span class="scan-mode-card-title">自動轉換</span>
        <span class="scan-mode-card-desc">任意 .gh 檔案皆可。系統會掃描候選輸入／輸出，
          由你勾選命名後自動加上 RH_IN / RH_OUT 標記（原檔自動備份）。</span>
      </button>
      <button type="button" class="scan-mode-card ${STATE.scanMode === "direct" ? "active" : ""}" data-scan-mode="direct">
        <span class="scan-mode-card-tag scan-mode-card-tag-alt">進階</span>
        <span class="scan-mode-card-title">直接解析</span>
        <span class="scan-mode-card-desc">檔案已包含 RH_IN / RH_OUT（Hops）標記時使用，
          直接呼叫 Rhino.Compute /io 解析，不經過掃描階段。</span>
      </button>
    </div>
  `;

  const modeToggle = `
    <div class="import-mode-toggle" role="tablist" aria-label="匯入方式">
      <button type="button" class="import-mode-btn ${STATE.importMode === "file" ? "active" : ""}" data-mode="file">
        檔案上傳
      </button>
      <button type="button" class="import-mode-btn ${STATE.importMode === "path" ? "active" : ""}" data-mode="path">
        本機路徑
      </button>
    </div>
  `;

  let body;
  if (STATE.busy) {
    const busyText =
      STATE.scanMode === "auto" ? "掃描中……讀取 GH_IO 候選輸入輸出" : "解析中……透過 Rhino.Compute /io 讀取";
    body = `
      <div class="dropzone dropzone-busy" id="dropzone">
        <div class="dropzone-spinner" aria-hidden="true"></div>
        <p class="dropzone-title">${busyText}</p>
        <p class="dropzone-hint">請稍候，這可能需要幾秒鐘</p>
      </div>
    `;
  } else if (STATE.importMode === "file") {
    body = `
      <div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="拖放或選擇 .gh 檔案">
        <p class="dropzone-title">將 .gh 檔案拖放到這裡</p>
        <p class="dropzone-hint">或</p>
        <button type="button" class="btn btn-primary" id="browse-btn">瀏覽檔案</button>
        <input type="file" id="file-input" accept=".gh" hidden />
      </div>
    `;
  } else {
    body = `
      <div class="path-import">
        <label class="field-label" for="gh-path-input">本機絕對路徑（.gh）</label>
        <div class="path-import-row">
          <input type="text" id="gh-path-input" class="input-text mono" placeholder="C:\\models\\example.gh" />
          <button type="button" class="btn btn-primary" id="path-submit-btn">${STATE.scanMode === "auto" ? "掃描" : "匯入"}</button>
        </div>
        <p class="field-hint">請輸入伺服器可存取的絕對路徑。</p>
      </div>
    `;
  }

  const computeBanner = STATE.computeDown
    ? `
      <div class="inline-alert" role="alert">
        <span class="inline-alert-icon" aria-hidden="true">&#9888;</span>
        <div>
          <strong>Rhino.Compute 未啟動</strong>
          <p>請先啟動 compute.geometry（localhost:5000）後再重試匯入。</p>
        </div>
      </div>
    `
    : "";

  const ghioBanner = STATE.ghioUnavailable
    ? `
      <div class="inline-alert" role="alert">
        <span class="inline-alert-icon" aria-hidden="true">&#9888;</span>
        <div>
          <strong>掃描功能目前不可用</strong>
          <p>GH_IO.dll 不可用：需要本機安裝 Rhino 8（含 Grasshopper），
            或設定環境變數 HOGER_GHIO_DLL 指向 GH_IO.dll 的路徑。
            若檔案已含 RH_IN / RH_OUT 標記，可改用「直接解析」。</p>
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
        toast("請輸入 .gh 檔案的絕對路徑", "error");
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
    toast("只支援 .gh 檔案", "error");
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
    const manifest = await api("/api/import", { method: "POST", body: formData });
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
  toast("解析成功，請檢視並確認工具定義", "success");
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
    const scanData = await api("/api/scan", { method: "POST", body: formData });
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

  STATE.stage = "scan";
  render();
  toast("掃描完成，請勾選並命名要標記的參數", "success");
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
      ? `<p class="scan-summary-note">偵測到 ${alreadyMarked} 個既有標記，重新標記會更新群組名稱。</p>`
      : "";

  const nameUsage = computeNameUsage();

  const inputRowsHtml = STATE.inputRows.length
    ? STATE.inputRows.map((row, idx) => renderCandidateRow(row, idx, "input", nameUsage)).join("")
    : `<tr><td colspan="7" class="cell-empty">未偵測到候選輸入</td></tr>`;

  const outputRowsHtml = STATE.outputRows.length
    ? STATE.outputRows.map((row, idx) => renderCandidateRow(row, idx, "output", nameUsage)).join("")
    : `<tr><td colspan="5" class="cell-empty">未偵測到候選輸出</td></tr>`;

  const checkedInputCount = STATE.inputRows.filter((r) => r.checked).length;
  const checkedOutputCount = STATE.outputRows.filter((r) => r.checked).length;
  const nothingChecked = checkedInputCount === 0 && checkedOutputCount === 0;

  const busyOverlay = STATE.scanBusy
    ? `
      <div class="scan-convert-busy" role="status">
        <div class="dropzone-spinner" aria-hidden="true"></div>
        <p>正在標記檔案……已自動備份原檔</p>
      </div>
    `
    : "";

  return `
    <div class="scan-panel">
      <div class="summary-card scan-summary-card">
        <div class="scan-summary-row">
          <div>
            <p class="scan-summary-filename mono">${escapeHtml(STATE.scanFileLabel)}</p>
            <p class="scan-summary-meta">物件總數 ${objectCount} ・ 既有標記 ${alreadyMarked}</p>
          </div>
        </div>
        ${alreadyMarkedNote}
      </div>

      <div class="table-section">
        <h3 class="table-section-title">候選輸入 <span class="table-count">${STATE.inputRows.length}</span></h3>
        <div class="table-scroll">
          <table class="data-table scan-table">
            <thead>
              <tr>
                <th class="scan-th-check"><span class="sr-only">勾選</span></th>
                <th>型別</th>
                <th>Nickname</th>
                <th>目前值 / 範圍</th>
                <th>接到</th>
                <th>參數名</th>
                <th>既有標記</th>
              </tr>
            </thead>
            <tbody id="scan-inputs-tbody">${inputRowsHtml}</tbody>
          </table>
        </div>
      </div>

      <div class="table-section">
        <h3 class="table-section-title">候選輸出 <span class="table-count">${STATE.outputRows.length}</span></h3>
        <div class="table-scroll">
          <table class="data-table scan-table">
            <thead>
              <tr>
                <th class="scan-th-check"><span class="sr-only">勾選</span></th>
                <th>型別</th>
                <th>Nickname</th>
                <th>來自</th>
                <th>參數名</th>
                <th>既有標記</th>
              </tr>
            </thead>
            <tbody id="scan-outputs-tbody">${outputRowsHtml}</tbody>
          </table>
        </div>
      </div>

      ${busyOverlay}

      <div class="review-actions">
        <button type="button" class="btn btn-ghost" id="scan-back-btn" ${STATE.scanBusy ? "disabled" : ""}>返回</button>
        <div class="review-actions-primary scan-actions-primary">
          <span class="scan-check-summary">將標記 ${checkedInputCount} 個輸入、${checkedOutputCount} 個輸出</span>
          <button type="button" class="btn btn-primary" id="scan-convert-btn" ${STATE.scanBusy || nothingChecked ? "disabled" : ""}>開始轉換</button>
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
          <strong>檔案已標記並備份，Rhino.Compute 目前離線</strong>
          <p>備份路徑：<span class="mono">${escapeHtml(STATE.scanConvertBackupPath)}</span></p>
          <p>啟動 compute.geometry（localhost:5000）後，按下方按鈕重新解析已標記的檔案，
            不需要重新掃描或重新標記。</p>
        </div>
      </div>
      <div class="review-actions">
        <button type="button" class="btn btn-ghost" id="scan-back-btn" ${STATE.scanReimportBusy ? "disabled" : ""}>返回</button>
        <button type="button" class="btn btn-primary" id="scan-reimport-btn" ${STATE.scanReimportBusy ? "disabled" : ""}>
          ${STATE.scanReimportBusy ? "重新解析中……" : "重新解析"}
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
      data-kind="${kind}" data-idx="${idx}" value="${escapeHtml(row.name)}" placeholder="參數名……" />
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
    return "請至少勾選一個輸入或輸出";
  }
  const seen = new Map();
  for (const row of checkedRows) {
    const name = row.name.trim();
    if (!name) return "請為所有勾選的列填入參數名";
    if (!NAME_PATTERN.test(name)) return `參數名「${name}」格式不符：${NAME_HINT}`;
    if (seen.has(name)) return `參數名「${name}」重複，請改為不同名稱`;
    seen.set(name, true);
  }
  return "";
}

async function submitConvert() {
  const err = validateScanSelection();
  if (err) {
    toast(err, "error");
    render();
    return;
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
      body: { gh_path: STATE.scanData.gh_path, inputs, outputs },
    });
    STATE.scanBusy = false;
    STATE.draft = result.manifest;
    STATE.idError = "";
    STATE.stage = "review";
    render();
    toast(`轉換成功，已備份原檔至 ${result.backup_path}`, "success");
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
    });
    STATE.scanReimportBusy = false;
    STATE.draft = manifest;
    STATE.idError = "";
    STATE.stage = "review";
    STATE.scanConvertDown = false;
    render();
    toast("重新解析成功，請檢視並確認工具定義", "success");
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
              <td>${input.required ? '<span class="required-badge">必填</span>' : '<span class="cell-muted">選填</span>'}</td>
              <td>
                <input type="text" class="cell-input" data-field="description" data-idx="${idx}"
                  value="${escapeHtml(input.description)}" placeholder="說明……" />
              </td>
              ${numericCells}
            </tr>
          `;
        })
        .join("")
    : `<tr><td colspan="7" class="cell-empty">此定義沒有輸入參數</td></tr>`;

  const outputsRows = d.outputs.length
    ? d.outputs
        .map((output, idx) => `
          <tr>
            <td class="mono cell-param-name">${escapeHtml(output.param_name)}</td>
            <td>${kindBadge(output.kind)}</td>
            <td>
              <input type="text" class="cell-input" data-field="description" data-idx="${idx}"
                value="${escapeHtml(output.description)}" placeholder="說明……" />
            </td>
            <td>
              <input type="text" class="cell-input" data-field="unit" data-idx="${idx}"
                value="${escapeHtml(output.unit)}" placeholder="單位……" />
            </td>
          </tr>
        `)
        .join("")
    : `<tr><td colspan="4" class="cell-empty">此定義沒有輸出參數</td></tr>`;

  return `
    <div class="review-panel">
      <div class="summary-card">
        <div class="summary-grid">
          <div class="summary-field">
            <label class="field-label" for="draft-id">工具 id</label>
            <input type="text" id="draft-id" class="input-text mono ${idErrorClass}" value="${escapeHtml(d.id)}" />
            <p id="draft-id-msg" class="${STATE.idError ? "field-error" : "field-hint"}">${escapeHtml(STATE.idError || ID_HINT)}</p>
          </div>
          <div class="summary-field">
            <label class="field-label" for="draft-display-name">顯示名稱</label>
            <input type="text" id="draft-display-name" class="input-text" value="${escapeHtml(d.display_name)}" />
          </div>
          <div class="summary-field summary-field-wide">
            <label class="field-label" for="draft-description">描述</label>
            <textarea id="draft-description" class="input-textarea" rows="2">${escapeHtml(d.description)}</textarea>
          </div>
          <div class="summary-field summary-field-wide">
            <label class="field-label">來源檔案（唯讀）</label>
            <p class="readonly-value mono">${escapeHtml(d.gh_file)}</p>
          </div>
        </div>
      </div>

      <div class="table-section">
        <h3 class="table-section-title">輸入 <span class="table-count">${d.inputs.length}</span></h3>
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
            <tbody id="inputs-tbody">${inputsRows}</tbody>
          </table>
        </div>
      </div>

      <div class="table-section">
        <h3 class="table-section-title">輸出 <span class="table-count">${d.outputs.length}</span></h3>
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
            <tbody id="outputs-tbody">${outputsRows}</tbody>
          </table>
        </div>
      </div>

      <div class="review-actions">
        <button type="button" class="btn btn-ghost" id="reimport-btn">重新匯入</button>
        <div class="review-actions-primary">
          <button type="button" class="btn btn-secondary" id="save-draft-btn">存為草稿</button>
          <button type="button" class="btn btn-primary" id="register-btn">註冊到 MCP</button>
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
    idMsg.textContent = STATE.idError || ID_HINT;
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
    if (window.confirm("確定要捨棄目前草稿並重新匯入嗎？")) {
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
    toast("請先修正表格中標記為紅色的欄位", "error");
    return;
  }

  const idErr = validateId(STATE.draft.id);
  if (idErr) {
    STATE.idError = idErr;
    render();
    toast("請先修正工具 id 格式", "error");
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
      status === "registered" ? "已註冊，MCP 工具清單已更新" : "已存為草稿",
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
  return `
    <div class="success-card">
      <span class="success-icon" aria-hidden="true">&#10003;</span>
      <h3>${isRegistered ? "已註冊到 MCP" : "已存為草稿"}</h3>
      <p>工具「${escapeHtml(STATE.draft?.display_name || "")}」（id: <span class="mono">${escapeHtml(STATE.draft?.id || "")}</span>）${isRegistered ? "已加入 MCP 工具清單。" : "已儲存，可稍後於工具管理頁面繼續編輯並註冊。"}</p>
      <div class="success-actions">
        <a class="btn btn-primary" href="#/manager">前往工具管理</a>
        <button type="button" class="btn btn-ghost" id="import-another-btn">匯入下一個</button>
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
