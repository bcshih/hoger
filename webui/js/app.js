// webui/js/app.js — 頁籤路由 + 健康狀態列 + 進入點
//
// Hash 路由：#/convert（預設）、#/manager、#/tester。重新整理時保留目前
// hash；未知或空 hash 一律導向 #/convert。健康狀態列每 10 秒輪詢
// /api/health，兩顆燈（HOGER / Rhino.Compute）各自反映後端與 compute 的
// 可用性；fetch 本身失敗（HOGER 掛了）時兩顆燈都轉紅並提示。

import { api, toast } from "./api.js";
import { init as initConvert } from "./convert.js";
import { init as initManager } from "./manager.js";
import { init as initTester } from "./tester.js";

const ROUTES = {
  convert: { title: "轉換", init: initConvert },
  manager: { title: "工具管理", init: initManager },
  tester: { title: "測試", init: initTester },
};
const DEFAULT_ROUTE = "convert";
const HEALTH_POLL_MS = 10000;

const view = document.getElementById("view");
const tabs = Array.from(document.querySelectorAll(".tab[data-route]"));

function currentRouteFromHash() {
  const hash = window.location.hash.replace(/^#\/?/, "");
  return ROUTES[hash] ? hash : DEFAULT_ROUTE;
}

function renderRoute() {
  const route = currentRouteFromHash();

  // hash 正規化：空值或未知值一律導向預設頁籤（replace，不留歷史紀錄）。
  const expectedHash = `#/${route}`;
  if (window.location.hash !== expectedHash) {
    window.history.replaceState(null, "", expectedHash);
  }

  tabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.route === route);
  });

  view.innerHTML = "";
  document.title = `${ROUTES[route].title} · HOGER`;
  ROUTES[route].init(view);
}

window.addEventListener("hashchange", renderRoute);

// ── 健康狀態列 ───────────────────────────────────────────────────────

const lampHoger = document.getElementById("lamp-hoger");
const lampHogerText = document.getElementById("lamp-hoger-text");
const lampCompute = document.getElementById("lamp-compute");
const lampComputeText = document.getElementById("lamp-compute-text");
const computeBanner = document.getElementById("compute-banner");

function setLamp(button, textEl, state, label) {
  button.dataset.state = state;
  textEl.textContent = label;
}

let computeWasDown = null; // null = 尚未檢查過，避免啟動時就跳一次 toast

async function pollHealth() {
  try {
    const health = await api("/api/health");

    setLamp(lampHoger, lampHogerText, health.hoger ? "ok" : "bad", health.hoger ? "正常" : "異常");

    const computeOk = Boolean(health.compute);
    setLamp(lampCompute, lampComputeText, computeOk ? "ok" : "bad", computeOk ? "正常" : "未啟動");
    computeBanner.hidden = computeOk;

    if (computeWasDown === false && !computeOk) {
      toast("Rhino.Compute 已離線", "error");
    } else if (computeWasDown === true && computeOk) {
      toast("Rhino.Compute 已恢復連線", "success");
    }
    computeWasDown = !computeOk;
  } catch (err) {
    // fetch 本身失敗：HOGER 後端沒回應，兩顆燈都轉紅。
    setLamp(lampHoger, lampHogerText, "bad", "無回應");
    setLamp(lampCompute, lampComputeText, "bad", "未知");
    computeBanner.hidden = false;
    if (computeWasDown !== "hoger-down") {
      toast("無法連線到 HOGER 服務", "error");
    }
    computeWasDown = "hoger-down";
  }
}

renderRoute();
pollHealth();
setInterval(pollHealth, HEALTH_POLL_MS);
