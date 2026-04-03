// CSS で強制的に選択可能にする
const style = document.createElement('style');
style.textContent = '* { user-select: text !important; -webkit-user-select: text !important; }';
document.addEventListener('DOMContentLoaded', () => document.head.appendChild(style));

// コピー系イベントの阻止を無効化（mousedown は除外：ページ操作を壊す恐れあり）
['copy', 'cut', 'contextmenu', 'selectstart'].forEach(event => {
  document.addEventListener(event, e => e.stopImmediatePropagation(), true);
});

// 動的に追加される要素にも対応（debounce でパフォーマンス改善）
let timer;
new MutationObserver(() => {
  clearTimeout(timer);
  timer = setTimeout(() => {
    document.querySelectorAll('*').forEach(el => {
      el.style.userSelect = 'text';
      el.style.webkitUserSelect = 'text';
    });
  }, 200);
}).observe(document.body || document.documentElement, { childList: true, subtree: true });
