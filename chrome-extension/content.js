// CSS で強制的に選択可能にする
const style = document.createElement('style');
style.textContent = '* { user-select: text !important; -webkit-user-select: text !important; }';
document.addEventListener('DOMContentLoaded', () => document.head.appendChild(style));

// コピー系イベントの阻止を無効化
['copy', 'cut', 'contextmenu', 'selectstart', 'mousedown'].forEach(event => {
  document.addEventListener(event, e => e.stopImmediatePropagation(), true);
});

// 動的に追加される要素にも対応
new MutationObserver(() => {
  document.querySelectorAll('*').forEach(el => {
    el.style.userSelect = 'text';
    el.style.webkitUserSelect = 'text';
  });
}).observe(document.body || document.documentElement, { childList: true, subtree: true });
