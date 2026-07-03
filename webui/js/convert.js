// webui/js/convert.js — 「轉換」頁籤（Import & Convert）
//
// 三階段流程：
//   1. 匯入（拖放 .gh / 選擇檔案 / 貼本機路徑）-> POST /api/import
//   2. 檢視 / 編輯解析出的草稿 manifest（id、display_name、description、
//      inputs[]、outputs[] 皆可微調）
//   3. 註冊（POST /api/tools，status = draft | registered）
//
// 狀態存在模組級變數（STATE），單頁應用切頁籤離開再回來即重置為階段 1，
// 不需要持久化。所有 DOM 事件在 render 之後手動 addEventListener 綁定，
// 不使用 inline onclick。編輯欄位一律「DOM event -> 改 draft 物件」單向流動，
// 送出註冊時才從 draft 物件組 payload。

import { api, toast } from "./api.js";
import { escapeHtml, kindBadge, validateId, bindEditableCells, ID_HINT } from "./ui-common.js";

// stage: "import" | "review" | "done"
const STATE = {
  stage: "import",
  importMode: "file", // "file" | "path"
  busy: false,
  computeDown: false, // 502 時開啟，顯示醒目提示
  draft: null, // 解析成功後的 manifest 草稿（可編輯）
  idError: "", // id 欄位驗證錯誤訊息
  registerResult: null, // { status } 註冊成功後顯示成功卡
};

let root = null;

export function init(container) {
  root = container;
  STATE.stage = "import";
  STATE.importMode = "file";
  STATE.busy = false;
  STATE.computeDown = false;
  STATE.draft = null;
  STATE.idError = "";
  STATE.registerResult = null;
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
    body = `
      <div class="dropzone dropzone-busy" id="dropzone">
        <div class="dropzone-spinner" aria-hidden="true"></div>
        <p class="dropzone-title">解析中……透過 Rhino.Compute /io 讀取</p>
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
          <button type="button" class="btn btn-primary" id="path-submit-btn">匯入</button>
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

  return `
    <div class="import-panel">
      ${modeToggle}
      ${computeBanner}
      ${body}
    </div>
  `;
}

function bindImportStage(stageEl) {
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

async function submitFile(file) {
  if (!file.name.toLowerCase().endsWith(".gh")) {
    toast("只支援 .gh 檔案", "error");
    return;
  }
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

async function submitGhPath(ghPath) {
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

// ── stage 2: review ──────────────────────────────────────────────────

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

// ── stage 3: done ────────────────────────────────────────────────────

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
