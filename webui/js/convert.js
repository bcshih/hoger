// webui/js/convert.js — 「轉換」頁籤（Import & Convert）
//
// Task 5.1 骨架階段：僅渲染佔位卡片，驗證路由切換可運作。
// 實際的上傳 / gh_path 匯入 / manifest 預覽由 Task 5.2 填入。

export function init(container) {
  container.innerHTML = `
    <div class="view-head">
      <span class="view-eyebrow">01 · Import &amp; Convert</span>
      <h2 class="view-title">轉換</h2>
      <p class="view-desc">將 Grasshopper 檔案匯入並轉換為可執行的工具定義。</p>
    </div>
    <div class="placeholder-card">
      <span class="placeholder-tag">敬請期待</span>
      <h2>本區功能將於後續版本啟用</h2>
      <p>上傳 .gh 檔案或指定本機路徑、呼叫 Rhino.Compute 解析輸入輸出、預覽並建立工具 manifest 的介面，將在 Task 5.2 完成。</p>
    </div>
  `;
}
