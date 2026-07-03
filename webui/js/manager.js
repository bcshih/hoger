// webui/js/manager.js — 「工具管理」頁籤（Tool Manager）
//
// Task 5.1 骨架階段：僅渲染佔位卡片，驗證路由切換可運作。
// 實際的工具清單 / CRUD / MCP 設定匯出由 Task 5.3 填入。

export function init(container) {
  container.innerHTML = `
    <div class="view-head">
      <span class="view-eyebrow">02 · Tool Manager</span>
      <h2 class="view-title">工具管理</h2>
      <p class="view-desc">檢視、編輯、刪除已建立的工具，並匯出 MCP 設定。</p>
    </div>
    <div class="placeholder-card">
      <span class="placeholder-tag">敬請期待</span>
      <h2>本區功能將於後續版本啟用</h2>
      <p>工具清單瀏覽、manifest 編輯、刪除與 MCP client 設定片段匯出的介面，將在 Task 5.3 完成。</p>
    </div>
  `;
}
