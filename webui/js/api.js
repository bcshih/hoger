// webui/js/api.js — fetch 包裝 + toast 通知
//
// api(path, options)：
//   - 預設用 JSON request/response（body 若是 plain object 會自動
//     JSON.stringify 並帶上 Content-Type）
//   - 非 2xx 時擲出 Error，訊息取自回應 body 的 `detail`（FastAPI 慣例），
//     取不到就退回 `HTTP {status}`
//   - 網路層失敗（server 沒開、DNS、CORS 等 fetch() 直接 reject 的情況）
//     統一轉成好懂的中文訊息，不把原始 TypeError 洩漏給呼叫端
//
// toast(message, type)：右下角通知，type 為 error/success/info，
// 數秒後自動淡出移除。

import { t } from "./i18n.js";

const DEFAULT_TIMEOUT_MS = 15000;

export async function api(path, options = {}) {
  const { method = "GET", body, headers = {}, timeoutMs = DEFAULT_TIMEOUT_MS } = options;

  const finalHeaders = { Accept: "application/json", ...headers };
  let finalBody = body;

  const isPlainBody =
    body !== undefined && body !== null && !(body instanceof FormData) && typeof body !== "string";
  if (isPlainBody) {
    finalHeaders["Content-Type"] = "application/json";
    finalBody = JSON.stringify(body);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response;
  try {
    response = await fetch(path, {
      method,
      headers: finalHeaders,
      body: finalBody,
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error(t("api.timeout", { path }));
    }
    throw new Error(t("api.unreachable", { path }));
  } finally {
    clearTimeout(timer);
  }

  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json().catch(() => null) : await response.text();

  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? payload.detail
        : typeof payload === "string" && payload
          ? payload
          : `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }

  return payload;
}

let toastSeq = 0;

export function toast(message, type = "info") {
  const stack = document.getElementById("toast-stack");
  if (!stack) return;

  const id = `toast-${++toastSeq}`;
  const el = document.createElement("div");
  el.className = "toast";
  el.dataset.type = type;
  el.id = id;
  el.setAttribute("role", type === "error" ? "alert" : "status");

  const dot = document.createElement("span");
  dot.className = "toast-dot";
  dot.setAttribute("aria-hidden", "true");

  const text = document.createElement("span");
  text.textContent = message;

  el.appendChild(dot);
  el.appendChild(text);
  stack.appendChild(el);

  const lifetimeMs = type === "error" ? 6000 : 3500;
  setTimeout(() => {
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 200);
  }, lifetimeMs);

  return id;
}
