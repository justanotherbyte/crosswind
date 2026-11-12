// Home page package palette. Without this script the panes stay stacked and readable.
// Rows are vertical tabs: arrows move and preview, Enter opens the package docs.
(() => {
  const palette = document.querySelector(".cw-palette");
  if (!palette) return;

  const panes = [...palette.querySelectorAll(".cw-pane")];
  const side = document.createElement("div");
  side.className = "cw-palette__side";
  side.innerHTML = `
    <label class="cw-palette__filter">
      <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/></svg>
      <input type="text" placeholder="Filter packages" aria-label="Filter packages" autocomplete="off" spellcheck="false">
    </label>
    <div class="cw-palette__rows" role="tablist" aria-orientation="vertical" aria-label="Packages"></div>
    <p class="cw-palette__empty" role="status" hidden></p>
    <div class="cw-palette__hints" aria-hidden="true">
      <span><kbd>↑</kbd><kbd>↓</kbd> preview</span>
      <span><kbd>↵</kbd> open docs</span>
    </div>`;
  const input = side.querySelector("input");
  const list = side.querySelector("[role=tablist]");
  const empty = side.querySelector(".cw-palette__empty");

  const rows = panes.map((pane) => {
    const { name, pkg, kind } = pane.dataset;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "cw-palette__row";
    row.id = `row-${pane.id}`;
    row.setAttribute("role", "tab");
    row.setAttribute("aria-controls", pane.id);
    row.innerHTML = `<span class="cw-square" aria-hidden="true"></span><span class="cw-palette__name"></span><code></code><span class="cw-badge${kind === "CLI" ? " cw-badge--cli" : ""}"></span>`;
    row.querySelector(".cw-palette__name").textContent = name;
    row.querySelector("code").textContent = pkg;
    row.querySelector(".cw-badge").textContent = kind;
    pane.setAttribute("role", "tabpanel");
    pane.setAttribute("aria-labelledby", row.id);
    list.append(row);
    return { row, pane, text: pane.querySelector(".cw-pane__head").textContent.toLowerCase() };
  });

  const visible = () => rows.filter((r) => !r.row.hidden);
  const docsLink = (pane) => pane.querySelector(".cw-pane__links a");

  // `follow` is true when the hero carousel asked for the change, so it isn't sent back
  function select(item, follow) {
    if (selected() && selected() !== item) {
      palette.classList.add("has-switched");
      if (!follow) document.dispatchEvent(new CustomEvent("cw:pick", { detail: item.pane.dataset.name }));
    }
    for (const r of rows) {
      const on = r === item;
      r.row.setAttribute("aria-selected", on);
      r.row.tabIndex = on ? 0 : -1;
      r.pane.hidden = !on;
    }
  }
  const selected = () => rows.find((r) => !r.pane.hidden);

  function filter() {
    const q = input.value.trim().toLowerCase();
    for (const r of rows) r.row.hidden = !r.text.includes(q);
    const shown = visible();
    empty.hidden = shown.length > 0;
    empty.textContent = shown.length ? "" : `No package matches "${input.value.trim()}".`;
    if (shown.length && selected().row.hidden) select(shown[0]);
  }

  input.addEventListener("input", filter);
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && visible().length) {
      event.preventDefault();
      selected().row.focus();
    } else if (event.key === "Enter" && visible().length) {
      docsLink(selected().pane).click();
    } else if (event.key === "Escape") {
      input.value = "";
      filter();
    }
  });

  list.addEventListener("focusin", (event) => {
    const item = rows.find((r) => r.row === event.target);
    if (item) select(item);
  });
  list.addEventListener("click", (event) => {
    const item = rows.find((r) => r.row === event.target.closest("button"));
    if (item) select(item);
  });
  list.addEventListener("keydown", (event) => {
    const shown = visible();
    const i = shown.indexOf(selected());
    const target = {
      ArrowDown: shown[i + 1],
      ArrowUp: i ? shown[i - 1] : null,
      Home: shown[0],
      End: shown.at(-1),
    }[event.key];
    if (event.key === "Enter") {
      event.preventDefault();
      docsLink(selected().pane).click();
    } else if (event.key === "ArrowUp" && !i) {
      event.preventDefault();
      input.focus();
    } else if (target) {
      event.preventDefault();
      target.row.focus();
    }
  });

  const main = document.createElement("div");
  main.className = "cw-palette__main";
  main.append(...panes);
  palette.append(side, main);
  palette.classList.add("is-ready");
  select(rows[0]);

  document.addEventListener("cw:package", (event) => {
    const item = rows.find((r) => r.pane.dataset.name === event.detail);
    if (item && !item.row.hidden) select(item, true);
  });
})();

// The game focuses its input when it loads, which scrolls this page, so load it on click
document.querySelector(".cw-demo button")?.addEventListener("click", (event) => {
  const frame = document.createElement("iframe");
  frame.src = "https://agents-py-mosslight-valley.reachvishm8605.workers.dev/";
  frame.title = "Mosslight Valley demo game";
  frame.allow = "fullscreen; autoplay; gamepad";
  event.currentTarget.parentElement.replaceChildren(frame);
});

// Wynd pane: replays the transcript as a terminal session. The static transcript stays
// in the page for screen readers and is what shows without JS or with reduced motion.
(() => {
  const box = document.querySelector(".cw-term");
  const code = box?.querySelector("code");
  if (!code || matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const lines = code.innerHTML.split("\n");
  const live = code.cloneNode(false);
  live.setAttribute("aria-hidden", "true");
  code.classList.add("cw-term__sr");
  code.after(live);

  const pane = box.closest(".cw-pane");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "cw-term__control";
  pane.append(button);

  const caret = document.createElement("span");
  caret.className = "cw-term__caret";
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  const isBox = (text) => /^[│├┌└╭╰─]/.test(text);
  let run = 0;
  let pinned = true; // follow new output until the reader scrolls up

  box.addEventListener("scroll", () => {
    pinned = box.scrollHeight - box.scrollTop - box.clientHeight < 8;
  });
  const follow = () => {
    if (pinned) box.scrollTop = box.scrollHeight;
  };

  function addLine(html) {
    const line = document.createElement("span");
    line.className = "cw-term__line";
    line.innerHTML = html;
    live.append(line, "\n");
    return line;
  }

  async function play() {
    const id = ++run;
    const alive = () => id === run;
    live.replaceChildren();
    pinned = true;
    box.scrollTop = 0;
    button.textContent = "Skip";

    for (const html of lines) {
      const at = html.indexOf('<span class="cw-term__in">');
      if (at === -1) {
        const text = html.replace(/<[^>]+>/g, "");
        const pause = text === "​" ? 1400 : isBox(text) ? 12 : /Ready on|Starting local/.test(text) ? 700 : 70;
        await sleep(pause);
        if (!alive()) return;
        addLine(html);
        follow();
        continue;
      }
      // A line the user types: show what precedes the input, then type it out
      const typed = html.slice(at).replace(/<[^>]+>/g, "");
      const line = addLine(html.slice(0, at));
      const input = document.createElement("span");
      line.append(input, caret);
      follow();
      caret.classList.add("is-idle");
      await sleep(typed ? 650 : 900);
      caret.classList.remove("is-idle");
      for (const char of typed) {
        if (!alive()) return;
        input.textContent += char;
        await sleep(38 + Math.random() * 60);
      }
      await sleep(280);
      if (!alive()) return;
      caret.remove();
      await sleep(typed.startsWith("uvx") ? 500 : 120);
    }
    finish(id);
  }

  function finish(id) {
    if (id !== run) return;
    const line = addLine('<span class="cw-term__dim">$</span> ');
    caret.classList.add("is-idle");
    line.append(caret);
    follow();
    button.textContent = "Replay";
  }

  function skip() {
    const id = ++run;
    live.replaceChildren();
    for (const html of lines) addLine(html);
    pinned = true;
    finish(id);
  }

  button.addEventListener("click", () => (button.textContent === "Skip" ? skip() : play()));

  // Start the first time the pane is on screen; a hidden pane never intersects
  const seen = new IntersectionObserver(([entry]) => {
    if (!entry.isIntersecting) return;
    seen.disconnect();
    play();
  }, { threshold: 0.35 });
  seen.observe(box);
})();
