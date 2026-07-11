// webui/js/i18n.js — 極輕量 i18n：字典 + t()/getLang()/setLang()/onLangChange()
//
// 無 build step，字典直接內嵌於此檔。zh 是完整基準（保證涵蓋全部 key），
// en 缺 key 時 fallback 回 zh 並 console.warn（開發期抓漏用）。語言選擇
// 持久化在 localStorage["hoger-lang"]，預設 "zh"。
//
// 範圍：只涵蓋 UI chrome（按鈕/標題/表頭/placeholder/toast/confirm/狀態
// 文字/橫幅/提示）。後端回傳的資料內容（工具描述、auto_doc、錯誤
// detail、參數名、MCP schema 預覽裡鏡射 type_mapping 的欄位描述）刻意
// 不經過這層——那些是資料，不是介面。

const STORAGE_KEY = "hoger-lang";
const DEFAULT_LANG = "zh";

const MESSAGES = {
  zh: {
    // ── common ─────────────────────────────────────────────────────
    "common.back": "返回",
    "common.save": "儲存",
    "common.saving": "儲存中……",
    "common.saved": "已儲存",
    "common.delete": "刪除",
    "common.deleted": "已刪除",
    "common.loading": "載入中……",
    "common.loadingToolDef": "載入工具定義中……",
    "common.fixInvalidFields": "請先修正表格中標記為紅色的欄位",
    "common.idHint": "僅限小寫字母、數字、連字號（^[a-z0-9-]+$）。",
    "common.required": "必填",
    "common.optional": "選填",
    "common.descPlaceholder": "說明……",
    "common.unitPlaceholder": "單位……",
    "common.displayName": "顯示名稱",
    "common.description": "描述",
    "common.descAutoFillHint": "留空時將自動填入生成的說明，仍可自行修改",
    "common.viewAutoDoc": "查看自動生成的完整說明",
    "common.inputs": "輸入",
    "common.outputs": "輸出",
    "common.toolId": "工具 id",
    "common.statusRegistered": "已註冊",
    "common.statusDraft": "草稿",
    "common.thType": "型別",
    "common.thParamNameFull": "參數名稱",
    "common.thRequired": "必填",
    "common.thDescription": "描述",
    "common.thDefault": "預設值",
    "common.thMin": "最小值",
    "common.thMax": "最大值",
    "common.thUnit": "單位",
    "common.thValue": "值",
    "common.errors": "錯誤",
    "common.warnings": "警告",
    "common.withCount": "{title}（{n}）",
    "common.failed": "失敗",
    "common.success": "成功",
    "common.rawJson": "原始 JSON",

    // ── header ─────────────────────────────────────────────────────
    "header.subtitle": "GH → Hops + MCP 工具鏈",
    "header.computeBanner": "Rhino.Compute 未啟動——請先啟動 compute.geometry（localhost:5000）",
    "header.tabsAriaLabel": "主要頁籤",
    "header.tab.convert": "轉換",
    "header.tab.manager": "工具管理",
    "header.tab.tester": "測試",
    "header.langToggleAria": "切換語言",

    // ── lamp ───────────────────────────────────────────────────────
    "lamp.hogerTitle": "HOGER 服務狀態",
    "lamp.computeTitle": "Rhino.Compute 狀態",
    "lamp.checking": "檢查中",
    "lamp.ok": "正常",
    "lamp.bad": "異常",
    "lamp.notStarted": "未啟動",
    "lamp.noResponse": "無回應",
    "lamp.unknown": "未知",

    // ── toast ──────────────────────────────────────────────────────
    "toast.computeOffline": "Rhino.Compute 已離線",
    "toast.computeRecovered": "Rhino.Compute 已恢復連線",
    "toast.hogerUnreachable": "無法連線到 HOGER 服務",

    // ── api ────────────────────────────────────────────────────────
    "api.timeout": "請求逾時：{path}",
    "api.unreachable": "無法連線到 HOGER 服務，請確認後端是否已啟動（{path}）",

    // ── validate ───────────────────────────────────────────────────
    "validate.idRequired": "id 不可為空",
    "validate.idFormat": "格式不符：僅限小寫字母、數字、連字號",

    // ── convert ────────────────────────────────────────────────────
    "convert.viewDesc": "將 Grasshopper 檔案匯入並轉換為可執行的工具定義。",
    "convert.modeTablistAria": "轉換方式",
    "convert.tagRecommended": "推薦",
    "convert.autoTitle": "自動轉換",
    "convert.autoDesc": "任意 .gh 檔案皆可。系統會掃描候選輸入／輸出，由你勾選命名後自動加上 RH_IN / RH_OUT 標記（原檔自動備份）。",
    "convert.tagAdvanced": "進階",
    "convert.directTitle": "直接解析",
    "convert.directDesc": "檔案已包含 RH_IN / RH_OUT（Hops）標記時使用，直接呼叫 Rhino.Compute /io 解析，不經過掃描階段。",
    "convert.importModeAria": "匯入方式",
    "convert.uploadFile": "檔案上傳",
    "convert.localPath": "本機路徑",
    "convert.scanningBusy": "掃描中……讀取 GH_IO 候選輸入輸出",
    "convert.parsingBusy": "解析中……透過 Rhino.Compute /io 讀取",
    "convert.pleaseWaitHint": "請稍候，這可能需要幾秒鐘",
    "convert.dropzoneAria": "拖放或選擇 .gh 檔案",
    "convert.dropzoneTitle": "將 .gh 檔案拖放到這裡",
    "convert.or": "或",
    "convert.browseFile": "瀏覽檔案",
    "convert.localPathLabel": "本機絕對路徑（.gh）",
    "convert.pathHint": "請輸入伺服器可存取的絕對路徑。",
    "convert.scanBtn": "掃描",
    "convert.importBtn": "匯入",
    "convert.computeDownTitle": "Rhino.Compute 未啟動",
    "convert.computeDownDesc": "請先啟動 compute.geometry（localhost:5000）後再重試匯入。",
    "convert.ghioUnavailableTitle": "掃描功能目前不可用",
    "convert.ghioUnavailableDesc": "GH_IO.dll 不可用：需要本機安裝 Rhino 8（含 Grasshopper），或設定環境變數 HOGER_GHIO_DLL 指向 GH_IO.dll 的路徑。若檔案已含 RH_IN / RH_OUT 標記，可改用「直接解析」。",
    "convert.onlyGhFiles": "只支援 .gh 檔案",
    "convert.pathRequired": "請輸入 .gh 檔案的絕對路徑",
    "convert.importSuccessToast": "解析成功，請檢視並確認工具定義",
    "convert.llmStatusQueryFailed": "無法查詢 AI 解讀狀態",
    "convert.scanSuccessToast": "掃描完成，請勾選並命名要標記的參數",
    "convert.aiDescribeLoading": "AI 深度解讀（查詢可用性中……）",
    "convert.aiDescribeLabel": "AI 深度解讀（將 GH 結構摘要送給 LLM 生成語意描述）",
    "convert.aiDescribeUnavailableFallback": "目前不可用",
    "convert.aiDescribeCurrentSetting": "目前設定：{provider}",
    "convert.alreadyMarkedNote": "偵測到 {n} 個既有標記，重新標記會更新群組名稱。",
    "convert.noCandidateInputs": "未偵測到候選輸入",
    "convert.noCandidateOutputs": "未偵測到候選輸出",
    "convert.markingBusy": "正在標記檔案……已自動備份原檔",
    "convert.markingBusyAiSuffix": "……AI 解讀中",
    "convert.scanSummaryMeta": "物件總數 {objectCount} ・ 既有標記 {alreadyMarked}",
    "convert.candidateInputsTitle": "候選輸入",
    "convert.candidateOutputsTitle": "候選輸出",
    "convert.thCheckSr": "勾選",
    "convert.thValueRange": "目前值 / 範圍",
    "convert.thFeeds": "接到",
    "convert.thParamName": "參數名",
    "convert.thExistingMark": "既有標記",
    "convert.thFrom": "來自",
    "convert.scanCheckSummary": "將標記 {inputs} 個輸入、{outputs} 個輸出",
    "convert.startConvertBtn": "開始轉換",
    "convert.scanConvertDownTitle": "檔案已標記並備份，Rhino.Compute 目前離線",
    "convert.backupPathLabel": "備份路徑：",
    "convert.scanConvertDownDesc": "啟動 compute.geometry（localhost:5000）後，按下方按鈕重新解析已標記的檔案，不需要重新掃描或重新標記。",
    "convert.reparsing": "重新解析中……",
    "convert.reparse": "重新解析",
    "convert.paramNamePlaceholder": "參數名……",
    "convert.selectAtLeastOne": "請至少勾選一個輸入或輸出",
    "convert.fillAllNames": "請為所有勾選的列填入參數名",
    "convert.nameFormatInvalid": "參數名「{name}」格式不符：{hint}",
    "convert.nameHint": "僅限英數字與底線（^[A-Za-z0-9_]+$）。",
    "convert.nameDuplicate": "參數名「{name}」重複，請改為不同名稱",
    "convert.aiDescribeConfirm": "此定義較大（{n} 個物件），AI 解讀可能消耗大量 token。仍要啟用嗎？",
    "convert.convertSuccessToast": "轉換成功，已備份原檔至 {path}",
    "convert.aiDescribeErrorToast": "AI 解讀失敗，已使用規則式描述：{error}",
    "convert.reimportSuccessToast": "重新解析成功，請檢視並確認工具定義",
    "convert.noInputParams": "此定義沒有輸入參數",
    "convert.noOutputParams": "此定義沒有輸出參數",
    "convert.sourceFileReadonly": "來源檔案（唯讀）",
    "convert.reimport": "重新匯入",
    "convert.saveDraftBtn": "存為草稿",
    "convert.registerBtn": "註冊到 MCP",
    "convert.discardDraftConfirm": "確定要捨棄目前草稿並重新匯入嗎？",
    "convert.fixIdFormat": "請先修正工具 id 格式",
    "convert.registeredToast": "已註冊，MCP 工具清單已更新",
    "convert.savedDraftToast": "已存為草稿",
    "convert.doneRegisteredTitle": "已註冊到 MCP",
    "convert.doneDraftTitle": "已存為草稿",
    "convert.doneDescRegistered": "工具「{name}」（id: {id}）已加入 MCP 工具清單。",
    "convert.doneDescDraft": "工具「{name}」（id: {id}）已儲存，可稍後於工具管理頁面繼續編輯並註冊。",
    "convert.gotoManager": "前往工具管理",
    "convert.importAnother": "匯入下一個",

    // ── manager ────────────────────────────────────────────────────
    "manager.viewDesc": "檢視、編輯、刪除已建立的工具，並即時預覽 MCP Schema。",
    "manager.toolListTitle": "工具清單",
    "manager.refreshBtn": "重新整理",
    "manager.unsavedConfirm": "目前工具有未儲存的變更，切換後將遺失。確定要繼續嗎？",
    "manager.noToolsTag": "尚無工具",
    "manager.noToolsTitle": "尚無工具",
    "manager.noToolsDesc": "前往轉換區匯入第一個 .gh 檔案，即可在這裡管理。",
    "manager.gotoConvert": "前往轉換區",
    "manager.selectPrompt": "從左側清單選擇一個工具以檢視與編輯。",
    "manager.toolIdReadonly": "工具 id（唯讀）",
    "manager.statusLabel": "狀態",
    "manager.statusDraftOption": "草稿（draft）",
    "manager.statusRegisteredOption": "已註冊（registered）",
    "manager.statusHint": "draft 不會出現在 MCP 工具清單，只有 registered 狀態的工具會被 MCP client 看到。",
    "manager.paramNameReadonlyTitle": "param_name 唯讀，改名會破壞 MCP 引用",
    "manager.noInputParams": "此工具沒有輸入參數",
    "manager.noOutputParams": "此工具沒有輸出參數",
    "manager.schemaPreviewTitle": "MCP Schema 預覽（即時）",
    "manager.minGreaterThanMax": "參數「{name}」的最小值大於最大值",
    "manager.deleteConfirm": "確定要刪除工具「{name}」（id: {id}）嗎？此操作無法復原。",

    // ── tester ─────────────────────────────────────────────────────
    "tester.viewDesc": "選擇工具、填入參數並執行，檢視輸出與錯誤訊息。",
    "tester.selectToolLabel": "選擇工具",
    "tester.noToolsOption": "尚無工具，請先於轉換區建立",
    "tester.selectPlaceholderOption": "請選擇一個工具……",
    "tester.toolOptionRegistered": "{name}（{id}）",
    "tester.toolOptionDraft": "{name}（{id}）—— 草稿，請先在工具管理區註冊",
    "tester.selectHint": "只有已註冊（registered）的工具可以執行；草稿請先在「工具管理」區註冊。",
    "tester.noInputParamsCanRun": "此工具沒有輸入參數，可直接執行。",
    "tester.inputParamsTitle": "輸入參數",
    "tester.debugModeLabel": "debug 模式（回應含 raw）",
    "tester.elapsedInitial": "已耗時 0.0s",
    "tester.elapsedTemplate": "已耗時 {s}s",
    "tester.running": "執行中……",
    "tester.runBtn": "執行測試",
    "tester.computeDownHint": "Rhino.Compute 未啟動，請先啟動 compute.geometry（localhost:5000）後再執行。",
    "tester.integerPlaceholder": "整數……",
    "tester.numberPlaceholder": "數字……",
    "tester.filePathPlaceholder": "檔案路徑，例如 C:\\path\\to\\file",
    "tester.stringPlaceholder": "字串……",
    "tester.geometryModeAria": "幾何輸入方式",
    "tester.geometryPathMode": ".3dm 檔案路徑",
    "tester.geometryPathPlaceholder": "檔案路徑，例如 C:\\path\\to\\model.3dm",
    "tester.geometryLayerPlaceholder": "圖層名稱（選填）",
    "tester.geometryEncodedPlaceholder": "每行一筆 JSON，或整體貼上一個 JSON 陣列，例如：&#10;[\"{...}\", \"{...}\"]",
    "tester.geometryEncodedHint": "接受 JSON 陣列（rhino3dm 編碼字串組成），或每行一筆 JSON 字串。",
    "tester.missingRequiredToast": "必填參數未填寫：{names}",
    "tester.fixInvalidFieldsToast": "請修正標紅的欄位後再執行",
    "tester.runCompleteWithErrors": "執行完成，但有錯誤訊息",
    "tester.runComplete": "執行完成",
    "tester.runFailedTitle": "執行失敗",
    "tester.resultTitle": "執行結果",
    "tester.elapsedLabel": "耗時 {s}s",
    "tester.unitsLabel": "單位 {unit}",
    "tester.unitsWarningTitle": "模型單位注意",
    "tester.unitsWarningDesc": "模型單位為 {unit}，請確認幾何尺度。",
    "tester.noOutputs": "此工具沒有輸出。",
    "tester.objectCount": "{n} 個物件",
    "tester.writtenTo3dm": "已寫入 3dm",
    "tester.notWrittenTo3dm": "未寫入 3dm",
    "tester.emptyArray": "（空）",
    "tester.moreItemsExpand": "…（共 {n} 項，點擊展開）",
    "tester.result3dmTitle": "結果 .3dm 檔案",
    "tester.copyPathBtn": "複製路徑",
    "tester.copiedToast": "已複製路徑",
    "tester.copyFailedToast": "複製失敗，請手動選取路徑",
  },

  en: {
    // ── common ─────────────────────────────────────────────────────
    "common.back": "Back",
    "common.save": "Save",
    "common.saving": "Saving…",
    "common.saved": "Saved",
    "common.delete": "Delete",
    "common.deleted": "Deleted",
    "common.loading": "Loading…",
    "common.loadingToolDef": "Loading tool definition…",
    "common.fixInvalidFields": "Fix the fields highlighted in red first",
    "common.idHint": "Lowercase letters, digits, and hyphens only (^[a-z0-9-]+$).",
    "common.required": "Required",
    "common.optional": "Optional",
    "common.descPlaceholder": "Description…",
    "common.unitPlaceholder": "Unit…",
    "common.displayName": "Display Name",
    "common.description": "Description",
    "common.descAutoFillHint": "Leave blank to auto-fill a generated description — you can still edit it",
    "common.viewAutoDoc": "View full auto-generated description",
    "common.inputs": "Inputs",
    "common.outputs": "Outputs",
    "common.toolId": "Tool ID",
    "common.statusRegistered": "Registered",
    "common.statusDraft": "Draft",
    "common.thType": "Type",
    "common.thParamNameFull": "Parameter Name",
    "common.thRequired": "Required",
    "common.thDescription": "Description",
    "common.thDefault": "Default",
    "common.thMin": "Min",
    "common.thMax": "Max",
    "common.thUnit": "Unit",
    "common.thValue": "Value",
    "common.errors": "Errors",
    "common.warnings": "Warnings",
    "common.withCount": "{title} ({n})",
    "common.failed": "Failed",
    "common.success": "Success",
    "common.rawJson": "Raw JSON",

    // ── header ─────────────────────────────────────────────────────
    "header.subtitle": "GH → Hops + MCP Toolchain",
    "header.computeBanner": "Rhino.Compute is offline — start compute.geometry (localhost:5000) first",
    "header.tabsAriaLabel": "Main tabs",
    "header.tab.convert": "Convert",
    "header.tab.manager": "Tool Manager",
    "header.tab.tester": "Test",
    "header.langToggleAria": "Switch language",

    // ── lamp ───────────────────────────────────────────────────────
    "lamp.hogerTitle": "HOGER service status",
    "lamp.computeTitle": "Rhino.Compute status",
    "lamp.checking": "Checking",
    "lamp.ok": "OK",
    "lamp.bad": "Error",
    "lamp.notStarted": "Offline",
    "lamp.noResponse": "No Response",
    "lamp.unknown": "Unknown",

    // ── toast ──────────────────────────────────────────────────────
    "toast.computeOffline": "Rhino.Compute went offline",
    "toast.computeRecovered": "Rhino.Compute reconnected",
    "toast.hogerUnreachable": "Cannot connect to the HOGER service",

    // ── api ────────────────────────────────────────────────────────
    "api.timeout": "Request timed out: {path}",
    "api.unreachable": "Cannot connect to the HOGER service — is the backend running? ({path})",

    // ── validate ───────────────────────────────────────────────────
    "validate.idRequired": "ID cannot be empty",
    "validate.idFormat": "Invalid format: lowercase letters, digits, and hyphens only",

    // ── convert ────────────────────────────────────────────────────
    "convert.viewDesc": "Import a Grasshopper file and convert it into a runnable tool definition.",
    "convert.modeTablistAria": "Conversion method",
    "convert.tagRecommended": "Recommended",
    "convert.autoTitle": "Auto Convert",
    "convert.autoDesc": "Works with any .gh file. The system scans candidate inputs/outputs — you check and name the ones you want, and RH_IN / RH_OUT tags are added automatically (the original file is backed up first).",
    "convert.tagAdvanced": "Advanced",
    "convert.directTitle": "Direct Parse",
    "convert.directDesc": "Use this when the file already has RH_IN / RH_OUT (Hops) tags — it calls Rhino.Compute /io directly, skipping the scan stage.",
    "convert.importModeAria": "Import method",
    "convert.uploadFile": "Upload File",
    "convert.localPath": "Local Path",
    "convert.scanningBusy": "Scanning… reading candidate inputs/outputs via GH_IO",
    "convert.parsingBusy": "Parsing… reading via Rhino.Compute /io",
    "convert.pleaseWaitHint": "Please wait, this may take a few seconds",
    "convert.dropzoneAria": "Drag and drop or select a .gh file",
    "convert.dropzoneTitle": "Drop a .gh file here",
    "convert.or": "or",
    "convert.browseFile": "Browse Files",
    "convert.localPathLabel": "Local Absolute Path (.gh)",
    "convert.pathHint": "Enter an absolute path the server can access.",
    "convert.scanBtn": "Scan",
    "convert.importBtn": "Import",
    "convert.computeDownTitle": "Rhino.Compute Is Offline",
    "convert.computeDownDesc": "Start compute.geometry (localhost:5000), then retry the import.",
    "convert.ghioUnavailableTitle": "Scan Is Currently Unavailable",
    "convert.ghioUnavailableDesc": "GH_IO.dll is unavailable: install Rhino 8 (with Grasshopper) locally, or set the HOGER_GHIO_DLL environment variable to point at GH_IO.dll. If the file already has RH_IN / RH_OUT tags, use Direct Parse instead.",
    "convert.onlyGhFiles": "Only .gh files are supported",
    "convert.pathRequired": "Enter the absolute path to a .gh file",
    "convert.importSuccessToast": "Parsed successfully — review and confirm the tool definition",
    "convert.llmStatusQueryFailed": "Could not check AI interpretation availability",
    "convert.scanSuccessToast": "Scan complete — check and name the parameters to tag",
    "convert.aiDescribeLoading": "AI Deep Interpretation (checking availability…)",
    "convert.aiDescribeLabel": "AI Deep Interpretation (send a GH structure summary to an LLM for semantic descriptions)",
    "convert.aiDescribeUnavailableFallback": "Currently unavailable",
    "convert.aiDescribeCurrentSetting": "Current setting: {provider}",
    "convert.alreadyMarkedNote": "Detected {n} existing tag(s) — re-tagging will update their group names.",
    "convert.noCandidateInputs": "No candidate inputs detected",
    "convert.noCandidateOutputs": "No candidate outputs detected",
    "convert.markingBusy": "Tagging the file… the original has been backed up automatically",
    "convert.markingBusyAiSuffix": " … running AI interpretation",
    "convert.scanSummaryMeta": "{objectCount} objects total · {alreadyMarked} existing tags",
    "convert.candidateInputsTitle": "Candidate Inputs",
    "convert.candidateOutputsTitle": "Candidate Outputs",
    "convert.thCheckSr": "Select",
    "convert.thValueRange": "Current Value / Range",
    "convert.thFeeds": "Feeds",
    "convert.thParamName": "Parameter Name",
    "convert.thExistingMark": "Existing Tag",
    "convert.thFrom": "From",
    "convert.scanCheckSummary": "Will tag {inputs} input(s), {outputs} output(s)",
    "convert.startConvertBtn": "Start Conversion",
    "convert.scanConvertDownTitle": "File Tagged and Backed Up — Rhino.Compute Is Offline",
    "convert.backupPathLabel": "Backup path: ",
    "convert.scanConvertDownDesc": "After starting compute.geometry (localhost:5000), click below to re-parse the tagged file — no need to scan or tag again.",
    "convert.reparsing": "Re-parsing…",
    "convert.reparse": "Re-parse",
    "convert.paramNamePlaceholder": "Parameter name…",
    "convert.selectAtLeastOne": "Check at least one input or output",
    "convert.fillAllNames": "Enter a parameter name for every checked row",
    "convert.nameFormatInvalid": 'Parameter name "{name}" is invalid: {hint}',
    "convert.nameHint": "Letters, digits, and underscores only (^[A-Za-z0-9_]+$).",
    "convert.nameDuplicate": 'Parameter name "{name}" is duplicated — use a different name',
    "convert.aiDescribeConfirm": "This definition is large ({n} objects) — AI interpretation may consume a lot of tokens. Enable it anyway?",
    "convert.convertSuccessToast": "Conversion succeeded — original backed up to {path}",
    "convert.aiDescribeErrorToast": "AI interpretation failed, used rule-based description instead: {error}",
    "convert.reimportSuccessToast": "Re-parsed successfully — review and confirm the tool definition",
    "convert.noInputParams": "This definition has no input parameters",
    "convert.noOutputParams": "This definition has no output parameters",
    "convert.sourceFileReadonly": "Source File (read-only)",
    "convert.reimport": "Re-import",
    "convert.saveDraftBtn": "Save as Draft",
    "convert.registerBtn": "Register to MCP",
    "convert.discardDraftConfirm": "Discard the current draft and re-import?",
    "convert.fixIdFormat": "Fix the tool ID format first",
    "convert.registeredToast": "Registered — the MCP tool list has been updated",
    "convert.savedDraftToast": "Saved as draft",
    "convert.doneRegisteredTitle": "Registered to MCP",
    "convert.doneDraftTitle": "Saved as Draft",
    "convert.doneDescRegistered": 'Tool "{name}" (id: {id}) has been added to the MCP tool list.',
    "convert.doneDescDraft": 'Tool "{name}" (id: {id}) has been saved — continue editing and register it later from Tool Manager.',
    "convert.gotoManager": "Go to Tool Manager",
    "convert.importAnother": "Import Another",

    // ── manager ────────────────────────────────────────────────────
    "manager.viewDesc": "View, edit, and delete existing tools, with a live MCP schema preview.",
    "manager.toolListTitle": "Tool List",
    "manager.refreshBtn": "Refresh",
    "manager.unsavedConfirm": "This tool has unsaved changes that will be lost if you switch. Continue anyway?",
    "manager.noToolsTag": "No Tools Yet",
    "manager.noToolsTitle": "No Tools Yet",
    "manager.noToolsDesc": "Import your first .gh file from Convert to manage it here.",
    "manager.gotoConvert": "Go to Convert",
    "manager.selectPrompt": "Select a tool from the list on the left to view and edit it.",
    "manager.toolIdReadonly": "Tool ID (read-only)",
    "manager.statusLabel": "Status",
    "manager.statusDraftOption": "Draft",
    "manager.statusRegisteredOption": "Registered",
    "manager.statusHint": "Draft tools don't appear in the MCP tool list — only registered tools are visible to MCP clients.",
    "manager.paramNameReadonlyTitle": "param_name is read-only — renaming would break MCP references",
    "manager.noInputParams": "This tool has no input parameters",
    "manager.noOutputParams": "This tool has no output parameters",
    "manager.schemaPreviewTitle": "MCP Schema Preview (live)",
    "manager.minGreaterThanMax": 'Parameter "{name}" has a minimum greater than its maximum',
    "manager.deleteConfirm": 'Delete tool "{name}" (id: {id})? This cannot be undone.',

    // ── tester ─────────────────────────────────────────────────────
    "tester.viewDesc": "Select a tool, fill in its parameters, run it, and inspect the outputs and errors.",
    "tester.selectToolLabel": "Select Tool",
    "tester.noToolsOption": "No tools yet — create one in Convert first",
    "tester.selectPlaceholderOption": "Select a tool…",
    "tester.toolOptionRegistered": "{name} ({id})",
    "tester.toolOptionDraft": "{name} ({id}) — draft, register it in Tool Manager first",
    "tester.selectHint": "Only registered tools can be run — register drafts in Tool Manager first.",
    "tester.noInputParamsCanRun": "This tool has no input parameters — you can run it directly.",
    "tester.inputParamsTitle": "Input Parameters",
    "tester.debugModeLabel": "Debug Mode (response includes raw)",
    "tester.elapsedInitial": "Elapsed 0.0s",
    "tester.elapsedTemplate": "Elapsed {s}s",
    "tester.running": "Running…",
    "tester.runBtn": "Run Test",
    "tester.computeDownHint": "Rhino.Compute is offline — start compute.geometry (localhost:5000) before running.",
    "tester.integerPlaceholder": "Integer…",
    "tester.numberPlaceholder": "Number…",
    "tester.filePathPlaceholder": "File path, e.g. C:\\path\\to\\file",
    "tester.stringPlaceholder": "String…",
    "tester.geometryModeAria": "Geometry input method",
    "tester.geometryPathMode": ".3dm File Path",
    "tester.geometryPathPlaceholder": "File path, e.g. C:\\path\\to\\model.3dm",
    "tester.geometryLayerPlaceholder": "Layer name (optional)",
    "tester.geometryEncodedPlaceholder": "One JSON value per line, or paste a whole JSON array, e.g.:&#10;[\"{...}\", \"{...}\"]",
    "tester.geometryEncodedHint": "Accepts a JSON array of rhino3dm-encoded strings, or one JSON string per line.",
    "tester.missingRequiredToast": "Missing required parameters: {names}",
    "tester.fixInvalidFieldsToast": "Fix the fields highlighted in red before running",
    "tester.runCompleteWithErrors": "Run complete, but with errors",
    "tester.runComplete": "Run complete",
    "tester.runFailedTitle": "Run Failed",
    "tester.resultTitle": "Run Result",
    "tester.elapsedLabel": "Elapsed {s}s",
    "tester.unitsLabel": "Units {unit}",
    "tester.unitsWarningTitle": "Model Units Notice",
    "tester.unitsWarningDesc": "The model units are {unit} — double-check the geometry scale.",
    "tester.noOutputs": "This tool has no outputs.",
    "tester.objectCount": "{n} object(s)",
    "tester.writtenTo3dm": "written to .3dm",
    "tester.notWrittenTo3dm": "not written to .3dm",
    "tester.emptyArray": "(empty)",
    "tester.moreItemsExpand": "… ({n} items total, click to expand)",
    "tester.result3dmTitle": "Result .3dm File",
    "tester.copyPathBtn": "Copy Path",
    "tester.copiedToast": "Path copied",
    "tester.copyFailedToast": "Copy failed — please select the path manually",
  },
};

function readStoredLang() {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "en" ? "en" : DEFAULT_LANG;
  } catch {
    // localStorage 不可用（隱私模式等）：退回預設語言，僅本次 session 生效。
    return DEFAULT_LANG;
  }
}

let currentLang = readStoredLang();
const listeners = [];

export function getLang() {
  return currentLang;
}

export function setLang(lang) {
  const next = lang === "en" ? "en" : "zh";
  if (next === currentLang) return;
  currentLang = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // 略過：寫入失敗不影響本次切換生效，只是重新整理後不會記住。
  }
  listeners.forEach((cb) => cb(next));
}

// app.js 在此註冊：語言切換時重繪靜態區（頁籤/副標/橫幅/燈號）+ 當前 view。
export function onLangChange(cb) {
  listeners.push(cb);
}

function interpolate(template, vars) {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, key) => (
    Object.prototype.hasOwnProperty.call(vars, key) ? String(vars[key]) : match
  ));
}

export function t(key, vars) {
  const dict = MESSAGES[currentLang] || MESSAGES[DEFAULT_LANG];
  let template = dict[key];
  if (template === undefined) {
    if (currentLang !== DEFAULT_LANG) {
      console.warn(`[i18n] missing key "${key}" in "${currentLang}" locale, falling back to zh`);
    }
    template = MESSAGES[DEFAULT_LANG][key];
  }
  if (template === undefined) {
    console.warn(`[i18n] missing key "${key}" in all locales`);
    return key;
  }
  return interpolate(template, vars);
}
