// Home hero: a grid of tiny squares that morphs into one scene per package, in
// step with the heading carousel. Each scene is a small picture of what the package does.
(() => {
  const rotator = document.querySelector(".cw-rotator");
  const panel = document.querySelector(".cw-field");
  if (!rotator || !panel) return;

  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const EVERY = 6000;
  const MORPH = 0.9; // seconds
  const CELL = 8; // CSS pixels between squares
  const words = [...rotator.children];

  panel.innerHTML = `
    <div class="cw-field__stage"><canvas aria-hidden="true"></canvas></div>
    <div class="cw-field__foot">
      <code class="cw-field__pkg"></code>
      <div class="cw-field__controls">
        <div class="cw-field__dots" role="group" aria-label="Packages"></div>
        <button class="cw-field__pause" type="button"></button>
      </div>
    </div>`;
  const canvas = panel.querySelector("canvas");
  const ctx = canvas.getContext("2d");
  const pkg = panel.querySelector(".cw-field__pkg");
  const dots = panel.querySelector(".cw-field__dots");
  const pause = panel.querySelector(".cw-field__pause");

  // Colour ramps from the site tokens, so a palette change reaches the canvas
  const token = (name) => {
    const hex = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  };
  const ramp = (to) => {
    const from = token("--cw-border");
    return Array.from({ length: 33 }, (_, i) =>
      `rgb(${from.map((c, k) => Math.round(c + (to[k] - c) * (i / 32) ** 0.8)).join()})`);
  };
  const TEAL = ramp(token("--cw-accent"));
  const PURPLE = ramp(token("--cw-purple"));

  const clamp = (n) => Math.min(1, Math.max(0, n));
  const smooth = (a, b, n) => {
    const p = clamp((n - a) / (b - a));
    return p * p * (3 - 2 * p);
  };
  const hash = (a, b = 0) => {
    const s = Math.sin(a * 127.1 + b * 311.7) * 43758.5453;
    return s - Math.floor(s);
  };

  // Scenes draw into the current buffer through these helpers
  let cols = 0;
  let rows = 0;
  let values = new Float32Array(0);
  let tones = new Uint8Array(0);
  const put = (x, y, value, purple = false) => {
    const cx = Math.round(x);
    const cy = Math.round(y);
    if (cx < 0 || cy < 0 || cx >= cols || cy >= rows) return;
    const i = cy * cols + cx;
    if (value < values[i]) return;
    values[i] = value;
    tones[i] = purple ? 1 : 0;
  };
  const fill = (x, y, w, h, value, purple) => {
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) put(x + i, y + j, value, purple);
  };
  const outline = (x, y, w, h, value, purple) => {
    fill(x, y, w, 1, value, purple);
    fill(x, y + h - 1, w, 1, value, purple);
    fill(x, y, 1, h, value, purple);
    fill(x + w - 1, y, 1, h, value, purple);
  };
  const sprite = (lines, x, y, shade) => lines.forEach((line, j) => {
    for (let i = 0; i < line.length; i++) if (line[i] === "#") put(x + i, y + j, shade(x + i, y + j));
  });

  const CLOUD = [
    "..........#####..........",
    ".......###.....##........",
    "......#..........#.......",
    "....###...........###....",
    "...#.................##..",
    "..#....................#.",
    ".#......................#",
    ".#......................#",
    "..#....................#.",
    "...####################..",
  ];
  const BOLT = ["....##", "...##.", "..##..", ".#####", "...##.", "..##..", ".##..."];
  const WIND = [
    "...........####...........",
    "..........#....#..........",
    "...............#..........",
    "...............#....####..",
    "###############....#....#.",
    "........................#.",
    "########################..",
    "..........................",
    "################..........",
    "................#.........",
    "................#.........",
    "..........#....#..........",
    "...........####...........",
  ];

  const SCENES = {
    // A robot that broadcasts its state to the clients on either side
    Agents(t) {
      const cx = Math.floor(cols / 2);
      const cy = Math.floor(rows / 2) + 2;
      const mid = Math.floor(rows / 2);
      const blink = t % 3.4 < 0.14;
      fill(cx - 7, cy - 5, 15, 11, 0.2);
      outline(cx - 7, cy - 5, 15, 11, 0.9);
      fill(cx - 8, cy - 2, 1, 4, 0.7);
      fill(cx + 8, cy - 2, 1, 4, 0.7);
      fill(cx, cy - 8, 1, 3, 0.7);
      fill(cx - 1, cy - 10, 3, 2, 0.5 + 0.5 * Math.max(0, Math.sin(t * 3.2)), true);
      fill(cx - 4, blink ? cy - 1 : cy - 2, 3, blink ? 1 : 2, 1);
      fill(cx + 2, blink ? cy - 1 : cy - 2, 3, blink ? 1 : 2, 1);
      fill(cx - 2, cy + 3, 5, 1, 0.7);
      const reach = Math.min(23, cols / 2 - 4);
      const r = 10 + ((t * 9) % (reach + 4));
      for (const side of [-1, 1]) {
        for (let j = -9; j <= 9; j++) {
          const dx = Math.sqrt(Math.max(0, r * r - j * j));
          if (dx > 9.5) put(cx + side * dx, mid + j, 0.55 * (1 - (r - 10) / (reach + 4)) + 0.2);
        }
        for (const off of [-7, 0, 7]) {
          const x = cx + side * reach;
          const hit = Math.max(0, 1 - Math.abs(r - Math.hypot(reach, off)) / 2.5);
          fill(x - 2, mid + off - 2, 5, 4, 0.15 + hit * 0.45, true);
          outline(x - 2, mid + off - 2, 5, 4, 0.5 + hit * 0.5, true);
        }
      }
    },

    // Code running in a terminal, inside walls that nothing crosses
    Sandboxes(t) {
      const half = Math.min(25, Math.floor(cols / 2) - 2);
      const cx = Math.floor(cols / 2);
      const [left, right, top, bottom] = [cx - half, cx + half, 1, rows - 2];
      const march = Math.floor(t * 5);
      const dash = (n) => ((n + march) % 3 === 0 ? 0.2 : 0.8);
      for (let x = left; x <= right; x++) {
        put(x, top, dash(x), true);
        put(x, bottom, dash(right - x), true);
      }
      for (let y = top; y <= bottom; y++) {
        put(left, y, dash(y), true);
        put(right, y, dash(bottom - y), true);
      }
      const termW = 27;
      const termH = 13;
      const tx = cx - 13;
      const ty = Math.floor((rows - termH) / 2);
      fill(tx, ty, termW, termH, 0.14);
      outline(tx, ty, termW, termH, 0.85);
      fill(tx + 1, ty + 1, termW - 2, 1, 0.4);
      fill(tx + 2, ty + 1, 2, 1, 0.8);
      const lens = [14, 9, 18, 6];
      const typed = (t * 11) % 62;
      let used = 0;
      lens.forEach((len, line) => {
        const y = ty + 3 + line * 2;
        put(tx + 2, y, 0.6, true);
        const shown = Math.min(len, Math.max(0, typed - used));
        fill(tx + 4, y, shown, 1, 1);
        if (shown > 0 && shown < len && t % 0.5 < 0.3) put(tx + 4 + shown, y, 1, true);
        used += len;
      });
      // One grain bouncing around the terminal, turned back at the wall
      const span = (n) => (a, b) => {
        const p = (t * n) % (2 * (b - a));
        return a + (p < b - a ? p : 2 * (b - a) - p);
      };
      if (half > 18) {
        put(span(9)(left + 2, tx - 2), span(6)(top + 2, bottom - 2), 1);
        put(span(8)(tx + termW + 1, right - 2), span(7)(top + 2, bottom - 2), 1);
      }
    },

    // Shipping containers: a crane lowers one onto the stack and lifts it off again
    Containers(t) {
      const W = 13;
      const H = 5;
      const box = (x, y, value, led, purple) => {
        fill(x, y, W, H, value * 0.22, purple);
        outline(x, y, W, H, value, purple);
        for (let i = 2; i < W - 3; i += 2) fill(x + i, y + 1, 1, H - 2, value * 0.5, purple);
        fill(x + W - 3, y + 1, 1, H - 2, value * 0.8, purple);
        put(x + W - 2, y + 2, led);
      };
      const cx = Math.floor(cols / 2);
      const bottom = rows - 1 - H;
      const x0 = cx - Math.floor((W * 3 + 2) / 2);
      for (let i = 0; i < 3; i++) box(x0 + i * (W + 1), bottom, 0.7, hash(i, Math.floor(t * 0.7)) > 0.4 ? 1 : 0.2);
      for (let i = 0; i < 2; i++) box(x0 + i * (W + 1), bottom - H - 1, 0.7, hash(i + 5, Math.floor(t * 0.9)) > 0.4 ? 1 : 0.2, i === 1);
      const slot = x0 + 2 * (W + 1);
      const rest = bottom - H - 1;
      const c = t % 7;
      const lift = smooth(0, 2.2, c) * (1 - smooth(4.6, 6.8, c));
      const y = 3 + (rest - 3) * lift;
      fill(0, 0, cols, 1, 0.3);
      const hookX = slot + Math.floor(W / 2);
      fill(hookX, 1, 1, Math.max(0, Math.round(y) - 1), 0.55);
      box(slot, y, 1, lift > 0.98 ? 1 : 0.2);
    },

    // Requests fly into the Cloudflare cloud, and responses leave the other side
    Workers(t) {
      const x = Math.floor((cols - CLOUD[0].length) / 2);
      const y = Math.floor((rows - CLOUD.length) / 2);
      const beat = Math.max(0, Math.sin(t * 5)) ** 4;
      CLOUD.forEach((line, j) => fill(x + line.indexOf("#"), y + j, line.lastIndexOf("#") - line.indexOf("#") + 1, 1, 0.16));
      sprite(CLOUD, x, y, () => 0.95);
      sprite(BOLT, x + 9, y + 2, () => 0.7 + beat * 0.3);
      [3, 6, 9].forEach((lane, i) => {
        const inEnd = x + 2;
        const outStart = x + CLOUD[0].length - 2;
        for (let k = 0; k < 2; k++) {
          const p = (t * 0.55 + i * 0.31 + k * 0.5) % 1;
          fill(p * inEnd - 1, y + lane - 1, 2, 2, 0.5 + 0.5 * smooth(0, 0.3, p));
          fill(outStart + p * (cols - outStart), y + lane - 1, 2, 2, 1 - 0.5 * smooth(0.7, 1, p), true);
        }
      });
    },

    // Wind blowing the packages nothing imports off the stack
    Wynd(t) {
      const total = WIND[0].length + 16;
      const x = Math.max(1, Math.floor((cols - total) / 2));
      const y = Math.floor((rows - WIND.length) / 2);
      const head = (t * 22) % 46 - 6;
      sprite(WIND, x, y, (px) => 0.72 + 0.28 * Math.max(0, 1 - Math.abs(px - x - head) / 7));
      const sx = x + WIND[0].length + 2;
      const cycle = t % 5;
      for (let row = 0; row < 4; row++) {
        for (let col = 0; col < 2; col++) {
          const bx = sx + col * 5;
          const by = y + 1 + row * 3;
          const unused = hash(row, col + 1) > 0.5;
          if (!unused) {
            fill(bx, by, 4, 2, 0.9);
            continue;
          }
          const gone = smooth(0.8 + row * 0.15, 3 + row * 0.15, cycle) * (1 - smooth(4.6, 5, cycle));
          const fade = 1 - smooth(0.3, 1, gone);
          fill(bx + gone * 18, by - Math.sin(gone * 3) * 2, 4, 2, 0.8 * fade, true);
        }
      }
    },
  };
  const NAMES = {
    Agents: "cf-agents",
    Sandboxes: "crosswind-sandboxes",
    Containers: "containers-py",
    Workers: "crosswind-workers",
    Wynd: "wynd",
  };
  const order = words.slice(0, -1).map((w) => w.textContent);

  let scale = 1;
  let current = 0;
  let previous = 0;
  let morphStart = -9;
  let clock = reduced ? 8 : 0;
  let stopped = reduced;
  let held = 0;
  let pointer = null;

  function render(name) {
    values = new Float32Array(cols * rows);
    tones = new Uint8Array(cols * rows);
    SCENES[name](clock);
    return [values, tones];
  }

  function draw() {
    const m = reduced ? 1 : smooth(0, MORPH, clock - morphStart);
    const [to, toTones] = render(order[current]);
    const [from, fromTones] = m < 1 ? render(order[previous]) : [to, toTones];
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const i = y * cols + x;
        // Sweep the change across the grid so it reads as one motion
        const local = m < 1 ? smooth(0, 1, m * 1.6 - (x / cols) * 0.6) : 1;
        let value = from[i] + (to[i] - from[i]) * local;
        const purple = local < 0.5 ? fromTones[i] : toTones[i];
        if (pointer) value += Math.max(0, 1 - Math.hypot(x - pointer.x, y - pointer.y) / 5) * 0.6;
        value = clamp(value);
        const size = Math.round((1.2 + value * 4.6) * scale);
        ctx.fillStyle = (purple ? PURPLE : TEAL)[Math.round(value * 32)];
        ctx.fillRect(
          Math.round((x * CELL + CELL / 2) * scale - size / 2),
          Math.round((y * CELL + CELL / 2) * scale - size / 2),
          size,
          size,
        );
      }
    }
  }

  function resize() {
    const box = canvas.parentElement.getBoundingClientRect();
    scale = devicePixelRatio || 1;
    canvas.width = Math.round(box.width * scale);
    canvas.height = Math.round(box.height * scale);
    cols = Math.floor(box.width / CELL);
    rows = Math.floor(box.height / CELL);
    ctx.setTransform(1, 0, 0, 1, (canvas.width - cols * CELL * scale) / 2, (canvas.height - rows * CELL * scale) / 2);
    draw();
  }

  // Runs only while the panel is on screen and the carousel isn't paused
  let visible = true;
  let raf = 0;
  let last = 0;
  function frame(now) {
    clock += Math.min(0.05, (now - last) / 1000);
    last = now;
    draw();
    raf = requestAnimationFrame(frame);
  }
  function sync() {
    const run = visible && !stopped && !document.hidden;
    if (run && !raf) {
      last = performance.now();
      raf = requestAnimationFrame(frame);
    } else if (!run && raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
  }

  // The word list ends with a copy of the first word, so the loop always moves upward
  const moveWords = (index) => words.forEach((w) => (w.style.transform = `translateY(${-index * 100}%)`));
  words[0].addEventListener("transitionend", () => {
    if (!words[0].style.transform.includes(`-${order.length * 100}%`)) return;
    words.forEach((w) => (w.style.transition = "none"));
    moveWords(0);
    void rotator.offsetHeight; // commit the jump before transitions come back
    words.forEach((w) => (w.style.transition = ""));
  });

  const buttons = order.map((name, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cw-field__dot";
    b.setAttribute("aria-label", `Show ${name}`);
    b.title = name;
    b.addEventListener("click", () => {
      stopped = true;
      renderPause();
      show(i);
      sync();
    });
    dots.append(b);
    return b;
  });

  function show(index, wrap) {
    previous = current;
    current = index;
    morphStart = clock;
    moveWords(wrap ? order.length : index);
    pkg.textContent = NAMES[order[index]];
    document.dispatchEvent(new CustomEvent("cw:package", { detail: order[index] }));
    buttons.forEach((b, i) => b.setAttribute("aria-pressed", i === index));
    // A paused canvas still has to finish the change, so play out the morph
    if (stopped && !reduced) {
      const until = performance.now() + MORPH * 1000;
      let at = performance.now();
      const finish = (now) => {
        clock += (now - at) / 1000;
        at = now;
        draw();
        if (now < until && stopped) requestAnimationFrame(finish);
      };
      requestAnimationFrame(finish);
    } else {
      draw();
    }
  }

  function renderPause() {
    pause.setAttribute("aria-label", stopped ? "Play the animation" : "Pause the animation");
    pause.title = stopped ? "Play" : "Pause";
    pause.innerHTML = stopped
      ? '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.500v9l7-4.500Z"/></svg>'
      : '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5.500 3.500v9M10.500 3.500v9"/></svg>';
  }

  pause.addEventListener("click", () => {
    stopped = !stopped;
    renderPause();
    sync();
  });

  canvas.parentElement.addEventListener("pointermove", (event) => {
    if (reduced) return;
    const box = canvas.getBoundingClientRect();
    pointer = { x: (event.clientX - box.left) / CELL - 0.5, y: (event.clientY - box.top) / CELL - 0.5 };
    if (!raf) draw();
  });
  canvas.parentElement.addEventListener("pointerleave", () => {
    pointer = null;
    if (!raf) draw();
  });

  // Hold on one package while the reader points at or tabs through the hero's moving parts
  const preview = document.querySelector(".cw-palette");
  for (const target of [panel, preview, rotator.parentElement]) {
    target?.addEventListener("pointerenter", () => (held += 1));
    target?.addEventListener("pointerleave", () => (held = Math.max(0, held - 1)));
  }
  for (const target of [panel, preview]) {
    target?.addEventListener("focusin", () => (held += 1));
    target?.addEventListener("focusout", () => (held = Math.max(0, held - 1)));
  }

  // Picking a package in the preview moves the hero too, and stops the carousel there
  document.addEventListener("cw:pick", (event) => {
    stopped = true;
    renderPause();
    show(order.indexOf(event.detail));
    sync();
  });

  setInterval(() => {
    if (stopped || held || document.hidden || !visible) return;
    const next = (current + 1) % order.length;
    show(next, next === 0);
  }, EVERY);

  new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    sync();
  }).observe(panel);
  document.addEventListener("visibilitychange", sync);
  new ResizeObserver(resize).observe(canvas.parentElement);

  panel.hidden = false;
  pause.hidden = reduced;
  renderPause();
  resize();
  show(0);
  sync();
})();
