import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./styles.css";

import { useAgent } from "agents/react";
import { Fragment, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import source from "./src/entry.py?raw";

const SOURCE_URL =
  "https://github.com/justanotherbyte/crosswind/tree/main/agents/examples/self-healing";

// Each trace kind points at the Python line that wrote it.
const KINDS = {
  run: { label: "run", tone: "command", marker: "await session.submit(prompt)" },
  tool: { label: "tool", tone: "live" },
  crash: { label: "crash", tone: "danger", marker: "self.ctx.abort(" },
  wake: { label: "wake", tone: "live", marker: "isinstance(event, SessionSnapshot)" },
  done: { label: "done", tone: "live", marker: "session.wait(event.operation_id)" },
  unanswered: { label: "failed", tone: "danger", marker: "session.wait(event.operation_id)" },
  aborted: { label: "aborted", tone: "neutral", marker: "session.wait(event.operation_id)" },
  needs_input: { label: "input", tone: "neutral", marker: "session.wait(event.operation_id)" },
};

// Each tab gets its own Durable Object, so visitors don't share a run.
function visitorName() {
  const name = sessionStorage.getItem("agent-name") ?? crypto.randomUUID().slice(0, 8);
  sessionStorage.setItem("agent-name", name);
  return name;
}

const SOURCE_LINES = source.trimEnd().split("\n");
const lineOf = (marker) => SOURCE_LINES.findIndex((line) => line.includes(marker));
// A tool row's text is unique in the source, so it finds its own line.
const lineOfEntry = (entry) => lineOf(KINDS[entry.kind]?.marker ?? entry.text);

const TOKENS =
  /(#.*$)|("""[^"]*"""|"[^"]*")|(@\w+)|\b(import|from|class|def|async|await|return|if|elif|for|in|and|not|or)\b/g;
const TOKEN_CLASSES = ["comment", "string", "decorator", "keyword"];

function highlight(line) {
  const parts = [];
  let cursor = 0;
  for (const match of line.matchAll(TOKENS)) {
    parts.push(line.slice(cursor, match.index));
    const group = match.slice(1).findIndex(Boolean);
    parts.push(
      <span key={match.index} className={`token-${TOKEN_CLASSES[group]}`}>
        {match[0]}
      </span>,
    );
    cursor = match.index + match[0].length;
  }
  parts.push(line.slice(cursor));
  return parts;
}

const seconds = (ms) => `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)}s`;
const timeOf = (entry) => Date.parse(entry.at);

// Consecutive trace rows from one instance: a run of its lifetime.
function groupByInstance(trace) {
  const groups = [];
  for (const [index, entry] of trace.entries()) {
    const last = groups.at(-1);
    if (last?.instance === entry.instance) last.rows.push({ entry, index });
    else groups.push({ instance: entry.instance, rows: [{ entry, index }] });
  }
  return groups.map((group, index) => ({
    ...group,
    start: timeOf(group.rows[0].entry),
    end: timeOf(group.rows.at(-1).entry),
    crashed: group.rows.at(-1).entry.kind === "crash",
    downtime:
      index > 0
        ? timeOf(group.rows[0].entry) - timeOf(groups[index - 1].rows.at(-1).entry)
        : 0,
  }));
}

function useClock(active) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

function Badge({ tone = "neutral", pulse, children }) {
  return (
    <span className={`badge tone-${tone}`}>
      <span className={pulse ? "square pulse" : "square"} aria-hidden="true" />
      {children}
    </span>
  );
}

function Lifeline({ groups, running, down, end }) {
  if (groups.length === 0) {
    return (
      <div className="lifeline">
        <div className="lifeline-grid" style={{ gridTemplateColumns: "1fr" }}>
          <div className="life idle" />
          <span>Waiting for a run</span>
        </div>
      </div>
    );
  }

  const last = groups.at(-1);
  const total = Math.max(end - groups[0].start, 1);
  const segments = groups.flatMap((group, index) => {
    const open = index === groups.length - 1 && running && !down;
    const state = group.crashed ? "died" : open ? "live" : "finished";
    const life = {
      key: group.instance,
      ms: (open ? end : group.end) - group.start,
      bar: <div className={group.crashed ? "life crashed" : "life"}>{open && <span className="life-cap pulse" />}</div>,
      label: (
        <div className="lifeline-cell">
          <code className={group.crashed ? "life-died" : undefined}>{group.instance}</code>
          <span className="life-state"> {state}</span>
        </div>
      ),
    };
    return group.downtime > 0 ? [gapSegment(`gap-${group.instance}`, group.downtime), life] : [life];
  });
  if (down) segments.push(gapSegment("gap-open", end - last.end));

  const columns = segments.map(({ ms }) => `minmax(0, ${Math.max(ms, 1)}fr)`);
  return (
    <div
      className="lifeline"
      role="img"
      aria-label={`${groups.length} instance${groups.length > 1 ? "s" : ""} served this turn over ${seconds(total)}`}
    >
      <div className="lifeline-grid" style={{ gridTemplateColumns: columns.join(" ") }}>
        {segments.map(({ key, bar }) => (
          <Fragment key={key}>{bar}</Fragment>
        ))}
        {segments.map(({ key, label }) => (
          <Fragment key={key}>{label}</Fragment>
        ))}
      </div>
    </div>
  );
}

function gapSegment(key, ms) {
  return {
    key,
    ms,
    bar: <div className="gap" />,
    label: (
      <div className="lifeline-cell">
        <span className="lifeline-down">down {seconds(ms)}</span>
      </div>
    ),
  };
}

function Arrow({ up }) {
  return (
    <svg viewBox="0 0 10 10" width="8" height="8" aria-hidden="true" style={up ? undefined : { rotate: "180deg" }}>
      <path d="M5 8.5v-7M2 4.5l3-3 3 3" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

function TraceRow({ row, origin, selected, onSelect }) {
  const { entry, index } = row;
  const kind = KINDS[entry.kind] ?? KINDS.tool;

  return (
    <li>
      <button
        type="button"
        data-index={index}
        className={selected ? "row selected" : "row"}
        aria-expanded={selected}
        onClick={() => onSelect(index)}
      >
        <span className={`square tone-${kind.tone}`} aria-hidden="true" />
        <span className={`row-kind tone-${kind.tone}`}>{kind.label}</span>
        <span className="row-text">{entry.text}</span>
        <time className="row-time" dateTime={entry.at}>
          +{seconds(timeOf(entry) - origin)}
        </time>
        {selected && (
          <span className="row-detail">
            entry.py:{lineOfEntry(entry) + 1} · {entry.at.slice(11, 23)} UTC
          </span>
        )}
      </button>
    </li>
  );
}

function Trace({ groups, selected, onSelect }) {
  if (groups.length === 0) {
    return (
      <div className="trace-empty">
        <p>No run yet.</p>
        <p>
          Press <kbd>Enter</kbd>. On its first call, the tool kills this Durable Object. Press Crash to kill it again during the run.
        </p>
      </div>
    );
  }

  const origin = groups[0].start;
  return (
    <div className="trace">
      {groups.map((group) => (
        <section key={group.instance} aria-label={`Instance ${group.instance}`}>
          <h2 className="trace-heading">
            <span>
              Instance <code>{group.instance}</code>
            </span>
            {group.downtime > 0 && <span className="trace-down">down {seconds(group.downtime)}</span>}
          </h2>
          <ol>
            {group.rows.map((row) => (
              <TraceRow
                key={row.index}
                row={row}
                origin={origin}
                selected={selected === row.index}
                onSelect={onSelect}
              />
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}

function Source({ line, touched }) {
  const pane = useRef(null);

  useEffect(() => {
    if (line < 0) return;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const target = pane.current.querySelector(`[data-line="${line}"]`);
    const top = target.offsetTop - pane.current.clientHeight / 2;
    pane.current.scrollTo({ top, behavior: reduced ? "auto" : "smooth" });
  }, [line]);

  return (
    <section className="source" aria-label="Agent source code">
      <header className="pane-header">
        <code>src/entry.py</code>
        <span>{SOURCE_LINES.length} lines</span>
      </header>
      <div className="source-body" ref={pane} tabIndex={0} role="region" aria-label="src/entry.py">
        <pre>
          <code>
            {SOURCE_LINES.map((text, index) => (
              <span key={index} data-line={index} className={`line${index === line ? " active" : ""}${touched.has(index) ? ` touched tone-${touched.get(index)}` : ""}`}>
                <span className="line-number" aria-hidden="true">
                  {index + 1}
                </span>
                <span className="line-text">{highlight(text)}</span>
              </span>
            ))}
          </code>
        </pre>
      </div>
    </section>
  );
}

function App() {
  const [name] = useState(visitorName);
  const [connection, setConnection] = useState("connecting");
  const [prompt, setPrompt] = useState("Migrate the database");
  const [pick, setPick] = useState(null);
  const [error, setError] = useState(null);
  const input = useRef(null);

  const agent = useAgent({
    agent: "self-healing",
    name,
    onOpen: () => setConnection("connected"),
    onClose: () => setConnection("reconnecting"),
  });

  const { status = "idle", crashes = 0, trace = [] } = agent.state ?? {};
  const running = status === "running";
  const connected = connection === "connected";
  const now = useClock(running);
  const groups = groupByInstance(trace);
  const last = groups.at(-1);
  const down = running && Boolean(last?.crashed);
  const end = last ? (running ? Math.max(now, last.end) : last.end) : now;
  const downtime = groups.reduce((sum, group) => sum + group.downtime, 0) + (down ? end - last.end : 0);
  const elapsed = last ? end - groups[0].start : 0;
  const touched = new Map(trace.map((entry) => [lineOfEntry(entry), (KINDS[entry.kind] ?? KINDS.tool).tone]));

  const selected = pick ?? trace.length - 1;
  const line = trace[selected] ? lineOfEntry(trace[selected]) : lineOf("async def migrate_database");

  function select(index) {
    setPick(index);
    if (matchMedia("(max-width: 860px)").matches) {
      document.querySelector(".source").scrollIntoView({ block: "nearest" });
    }
  }

  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === "/" && document.activeElement !== input.current) {
        event.preventDefault();
        input.current.focus();
        return;
      }

      const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
      const next = selected + step;
      if (!step || next < 0 || next >= trace.length) return;
      if (document.activeElement?.classList.contains("source-body")) return;
      event.preventDefault();
      setPick(next);
      document.querySelector(`[data-index="${next}"]`)?.focus();
    }
    addEventListener("keydown", onKeyDown);
    return () => removeEventListener("keydown", onKeyDown);
  }, [selected, trace.length]);

  async function run(event) {
    event.preventDefault();
    setPick(null);
    setError(null);
    try {
      await agent.stub.run(prompt);
    } catch (cause) {
      setError(cause.message);
    }
  }

  async function reset() {
    setPick(null);
    setError(null);
    try {
      await agent.stub.reset();
      input.current.focus();
    } catch (cause) {
      setError(cause.message);
    }
  }

  return (
    <div className="window">
      <header className="titlebar">
        <h1>
          <span className="mark" aria-hidden="true" />
          Self-healing agent
        </h1>
        <code className="path">SelfHealing/{name}</code>
        <div className="titlebar-end">
          <Badge tone={connected ? "live" : "neutral"} pulse={!connected}>
            {connection}
          </Badge>
          <a className="button" href={SOURCE_URL}>
            Source
          </a>
        </div>
      </header>

      <div className="panes">
        <main className="work">
          <form className="prompt" onSubmit={run}>
            <label className="visually-hidden" htmlFor="prompt">
              Task for the agent
            </label>
            <input
              id="prompt"
              ref={input}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              autoComplete="off"
              spellCheck="false"
              autoFocus
            />
            <kbd className="prompt-hint" aria-hidden="true">
              /
            </kbd>
            <button
              type="button"
              className="button prompt-reset"
              disabled={trace.length === 0 || running || !connected}
              onClick={reset}
            >
              Reset
            </button>
            <button
              type="button"
              className="button prompt-crash"
              disabled={!running || !connected}
              // The reset closes the socket, so this call never gets a reply.
              onClick={() => agent.stub.crash().catch(() => {})}
            >
              Crash
            </button>
            <button className="button primary" disabled={!prompt.trim() || running || !connected}>
              {running ? "Running" : trace.length ? "Run again" : "Run"}
              <kbd aria-hidden="true">Enter</kbd>
            </button>
          </form>
          {error && (
            <p className="error" role="alert">
              The agent refused: {error}
            </p>
          )}

          <Lifeline groups={groups} running={running} down={down} end={end} />
          <Trace groups={groups} selected={selected} onSelect={select} />
        </main>

        <Source line={line} touched={touched} />
      </div>

      <footer className="statusbar">
        <span aria-live="polite">
          status <b>{status}</b>
        </span>
        <span aria-live="polite">
          crashes <b>{crashes}</b>
        </span>
        <span>
          instances <b>{groups.length}</b>
        </span>
        <span>
          downtime <b>{seconds(downtime)}</b>
        </span>
        <span className="statusbar-elapsed">
          elapsed <b>{seconds(elapsed)}</b>
        </span>
        {trace.length > 0 && (
          <span className="statusbar-end">
            <kbd>
              <Arrow up />
            </kbd>
            <kbd>
              <Arrow />
            </kbd>
            trace
          </span>
        )}
      </footer>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
