// webui/js/app.js — 頁籤路由 + 健康狀態列 + 進入點
//
// Hash 路由：#/convert（預設）、#/manager、#/tester。重新整理時保留目前
// hash；未知或空 hash 一律導向 #/convert。健康狀態列每 10 秒輪詢
// /api/health，兩顆燈（HOGER / Rhino.Compute）各自反映後端與 compute 的
// 可用性；fetch 本身失敗（HOGER 掛了）時兩顆燈都轉紅並提示。

import { api, toast } from "./api.js";
import { init as initConvert, rerender as rerenderConvert } from "./convert.js";
import { init as initManager, rerender as rerenderManager } from "./manager.js";
import { init as initTester, rerender as rerenderTester } from "./tester.js";
import { t, getLang, setLang, onLangChange } from "./i18n.js";

const ROUTES = {
  convert: { titleKey: "header.tab.convert", init: initConvert, rerender: rerenderConvert },
  manager: { titleKey: "header.tab.manager", init: initManager, rerender: rerenderManager },
  tester: { titleKey: "header.tab.tester", init: initTester, rerender: rerenderTester },
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
  document.title = `${t(ROUTES[route].titleKey)} · HOGER`;
  ROUTES[route].init(view);
}

window.addEventListener("hashchange", renderRoute);

// ── i18n：靜態 UI（header/頁籤/橫幅/燈號提示）+ 語言切換按鈕 ──────────

const langToggleBtn = document.getElementById("lang-toggle-btn");
const langToggleText = document.getElementById("lang-toggle-text");
const hdrSubtitle = document.getElementById("hdr-subtitle");
const computeBannerText = document.getElementById("compute-banner-text");
const mainTabsNav = document.getElementById("main-tabs");

function applyStaticI18n() {
  document.documentElement.lang = getLang() === "en" ? "en" : "zh-Hant";

  hdrSubtitle.textContent = t("header.subtitle");
  computeBannerText.textContent = t("header.computeBanner");
  mainTabsNav.setAttribute("aria-label", t("header.tabsAriaLabel"));

  document.getElementById("tab-name-convert").textContent = t("header.tab.convert");
  document.getElementById("tab-name-manager").textContent = t("header.tab.manager");
  document.getElementById("tab-name-tester").textContent = t("header.tab.tester");

  lampHoger.title = t("lamp.hogerTitle");
  lampCompute.title = t("lamp.computeTitle");

  langToggleText.textContent = getLang() === "en" ? "中" : "EN";
  langToggleBtn.title = t("header.langToggleAria");
  langToggleBtn.setAttribute("aria-label", t("header.langToggleAria"));

  const route = currentRouteFromHash();
  document.title = `${t(ROUTES[route].titleKey)} · HOGER`;

  refreshLampTexts();
}

langToggleBtn.addEventListener("click", () => {
  setLang(getLang() === "en" ? "zh" : "en");
});

onLangChange(() => {
  applyStaticI18n();
  const route = currentRouteFromHash();
  ROUTES[route].rerender?.();
});

// ── 健康狀態列 ───────────────────────────────────────────────────────

const lampHoger = document.getElementById("lamp-hoger");
const lampHogerText = document.getElementById("lamp-hoger-text");
const lampCompute = document.getElementById("lamp-compute");
const lampComputeText = document.getElementById("lamp-compute-text");
const computeBanner = document.getElementById("compute-banner");

// 首次健康輪詢完成前的預設 labelKey，讓 applyStaticI18n() 在頁面剛載入
// （尤其語言為 en 時）也能正確顯示「Checking」而非殘留 HTML 裡的中文字。
lampHoger.dataset.labelKey = "lamp.checking";
lampCompute.dataset.labelKey = "lamp.checking";

function setLamp(button, textEl, state, labelKey) {
  button.dataset.state = state;
  button.dataset.labelKey = labelKey;
  textEl.textContent = t(labelKey);
}

// 語言切換時重繪目前的燈號文字（用上次已知的 label key，不必等下一次輪詢）。
function refreshLampTexts() {
  if (lampHoger.dataset.labelKey) lampHogerText.textContent = t(lampHoger.dataset.labelKey);
  if (lampCompute.dataset.labelKey) lampComputeText.textContent = t(lampCompute.dataset.labelKey);
}

let computeWasDown = null; // null = 尚未檢查過，避免啟動時就跳一次 toast

async function pollHealth() {
  try {
    const health = await api("/api/health");

    setLamp(lampHoger, lampHogerText, health.hoger ? "ok" : "bad", health.hoger ? "lamp.ok" : "lamp.bad");

    const computeOk = Boolean(health.compute);
    setLamp(lampCompute, lampComputeText, computeOk ? "ok" : "bad", computeOk ? "lamp.ok" : "lamp.notStarted");
    computeBanner.hidden = computeOk;

    if (computeWasDown === false && !computeOk) {
      toast(t("toast.computeOffline"), "error");
    } else if (computeWasDown === true && computeOk) {
      toast(t("toast.computeRecovered"), "success");
    }
    computeWasDown = !computeOk;
  } catch (err) {
    // fetch 本身失敗：HOGER 後端沒回應，兩顆燈都轉紅。
    setLamp(lampHoger, lampHogerText, "bad", "lamp.noResponse");
    setLamp(lampCompute, lampComputeText, "bad", "lamp.unknown");
    computeBanner.hidden = false;
    if (computeWasDown !== "hoger-down") {
      toast(t("toast.hogerUnreachable"), "error");
    }
    computeWasDown = "hoger-down";
  }
}

applyStaticI18n();
renderRoute();
pollHealth();
setInterval(pollHealth, HEALTH_POLL_MS);
