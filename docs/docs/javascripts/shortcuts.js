// Ctrl+K or Cmd+K opens Material's search. Material already handles Esc, the arrow keys, and P and N.
(() => {
  const query = document.querySelector('[data-md-component="search-query"]');
  if (!query) return;
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  query.form.dataset.shortcut = mac ? "⌘K" : "Ctrl K";

  const hints = document.createElement("div");
  hints.className = "cw-search-hints";
  hints.setAttribute("aria-hidden", "true");
  hints.innerHTML = "<span><kbd>↑</kbd><kbd>↓</kbd> select</span><span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span>";
  document.querySelector(".md-search__output")?.append(hints);

  addEventListener("keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "k") return;
    event.preventDefault();
    query.focus(); // Material opens the search panel when its input takes focus
  });
})();
