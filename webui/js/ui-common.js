// webui/js/ui-common.js — 共用 UI helper（自 convert.js 抽出，供 manager.js 共用）
//
// 內容：
//   - escapeHtml：所有插值進 innerHTML 的字串都要過這關，防 XSS
//   - KIND_LABELS / kindBadge：input/output 型別徽章渲染
//   - bindEditableCells：資料表 inline 編輯欄位的事件繫結
//     （numericFields 內的欄位會嘗試轉 number，空字串轉 null，
//     非數字時標記 input-invalid 且不寫回資料，呼叫端送出前應掃描
//     `.input-invalid` 作為最後一道防護）
//
// 純函式 + 少量繫結邏輯，不持有模組級狀態，可安全被多個頁籤 import。

export const ID_PATTERN = /^[a-z0-9-]+$/;
export const ID_HINT = "僅限小寫字母、數字、連字號（^[a-z0-9-]+$）。";

export const KIND_LABELS = {
  number: "number",
  integer: "integer",
  boolean: "boolean",
  string: "string",
  geometry: "geometry",
};

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

export function kindBadge(kind) {
  const label = KIND_LABELS[kind] || kind || "unknown";
  return `<span class="kind-badge" data-kind="${escapeHtml(kind || "unknown")}">${escapeHtml(label)}</span>`;
}

export function validateId(value) {
  if (!value) return "id 不可為空";
  if (!ID_PATTERN.test(value)) return "格式不符：僅限小寫字母、數字、連字號";
  return "";
}

// numericFields: 欄位名要嘗試轉成 number（空字串轉 null）。
// 呼叫端可傳入 onChange(item, field, value) 在每次成功寫入後收到通知
// （manager.js 用它來即時重算 MCP schema 預覽；convert.js 不需要則省略）。
export function bindEditableCells(tbody, list, numericFields, onChange) {
  if (!tbody) return;
  tbody.querySelectorAll(".cell-input").forEach((input) => {
    input.addEventListener("input", () => {
      const idx = Number(input.dataset.idx);
      const field = input.dataset.field;
      const item = list[idx];
      if (!item) return;

      if (field === "default") {
        // default 欄位解析：空字串存 null；可轉數字則存 number，否則原樣
        // 存字串（default 的合法型別依 kind 而異，這裡不強制、交後端驗證）。
        const raw = input.value.trim();
        item.default = raw === "" ? null : isNaN(Number(raw)) ? raw : Number(raw);
        onChange?.(item, field, item.default);
        return;
      }

      if (numericFields.includes(field)) {
        const raw = input.value.trim();
        if (raw === "") {
          item[field] = null;
          input.classList.remove("input-invalid");
          onChange?.(item, field, null);
          return;
        }
        const num = Number(raw);
        if (isNaN(num)) {
          input.classList.add("input-invalid");
          return;
        }
        input.classList.remove("input-invalid");
        item[field] = num;
        onChange?.(item, field, num);
        return;
      }

      item[field] = input.value;
      onChange?.(item, field, input.value);
    });
  });
}
