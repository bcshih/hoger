// webui/js/tester.js — 「測試」頁籤（Test Harness）
//
// Task 5.1 骨架階段：僅渲染佔位卡片，驗證路由切換可運作。
// 實際的參數表單 / 執行 / 結果檢視由 Task 5.4 填入。

export function init(container) {
  container.innerHTML = `
    <div class="view-head">
      <span class="view-eyebrow">03 · Test Harness</span>
      <h2 class="view-title">測試</h2>
      <p class="view-desc">選擇工具、填入參數並執行，檢視輸出與錯誤訊息。</p>
    </div>
    <div class="placeholder-card">
      <span class="placeholder-tag">敬請期待</span>
      <h2>本區功能將於後續版本啟用</h2>
      <p>依 manifest 動態產生參數表單、呼叫工具執行、顯示輸出結果與除錯資訊的介面，將在 Task 5.4 完成。</p>
    </div>
  `;
}
