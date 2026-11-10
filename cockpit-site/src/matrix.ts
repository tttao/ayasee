import { agoLabel, safeHttpsUrl, shortSha } from "./format.ts";
import type { EnvState, Snapshot, Status } from "./status.ts";
import { cellStatus, STATUS_META } from "./status.ts";

export interface SelectDetail {
  repo: string;
  env: string;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  cls?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

export function pill(status: Status): HTMLElement {
  const meta = STATUS_META[status];
  const p = el("span", `pill pill--${status}`);
  const icon = el("span", "pill-icon", meta.icon);
  icon.setAttribute("aria-hidden", "true");
  p.append(icon, el("span", "pill-label", meta.label));
  return p;
}

function envCell(repo: string, e: EnvState, now: Date): HTMLButtonElement {
  const status = cellStatus(e);
  const b = el("button", `cell cell--${status}`);
  b.type = "button";
  b.dataset.repo = repo;
  b.dataset.env = e.env;
  b.dataset.status = status;
  b.tabIndex = -1;

  const sha = shortSha(e.live_sha);
  b.append(el("span", "sha", sha), pill(status));
  const parts = [`${repo}, ${e.env}: ${STATUS_META[status].label}`, sha === "—" ? "no live SHA" : `live ${sha}`];

  const pending = e.pending_commits ?? 0;
  if (pending > 0) {
    b.append(el("span", "pending", `${pending} pending`));
    parts.push(`${pending} pending`);
  }
  if (e.gate?.waiting === true) {
    const age = e.gate.since ? agoLabel(e.gate.since, now).replace(/ ago$/, "") : "";
    const w = age && age !== "unknown" ? `waiting ${age}` : "waiting";
    b.append(el("span", "waiting-since", w));
    parts.push(w);
  }
  b.setAttribute("aria-label", parts.join(", "));
  b.addEventListener("click", () => {
    b.dispatchEvent(
      new CustomEvent<SelectDetail>("cockpit:select", { bubbles: true, detail: { repo, env: e.env } }),
    );
  });
  return b;
}

function findingsCell(f: Snapshot["repos"][number]["findings"] | undefined): HTMLTableCellElement {
  const td = el("td", "findings");
  const c = f?.critical ?? null;
  const h = f?.high ?? null;
  if (c === null && h === null) {
    td.textContent = "n/a";
    return td;
  }
  const text = `critical ${c ?? "n/a"} · high ${h ?? "n/a"}`;
  const href = safeHttpsUrl(f?.url);
  if (href) {
    const a = el("a", "findings-link", text);
    a.href = href;
    a.rel = "noopener noreferrer";
    td.append(a);
  } else td.textContent = text;
  if ((c ?? 0) > 0) td.classList.add("findings--critical");
  else if ((h ?? 0) > 0) td.classList.add("findings--high");
  return td;
}

/**
 * Ordered merge of each repo's env sequence (R59). Every env not yet seen is
 * inserted right after its predecessor in that repo's sequence (first when it has
 * none). Conflicting orders keep the earlier repo's order; the result is deterministic.
 */
export function mergeEnvOrder(sequences: readonly (readonly string[])[]): string[] {
  const out: string[] = [];
  for (const seq of sequences) {
    let prev: string | null = null;
    for (const env of seq) {
      if (!out.includes(env)) out.splice(prev === null ? 0 : out.indexOf(prev) + 1, 0, env);
      prev = env;
    }
  }
  return out;
}

export function renderMatrix(s: Snapshot, root: HTMLElement, now: Date = new Date()): void {
  const envNames = mergeEnvOrder(s.repos.map((r) => r.envs.map((e) => e.env)));

  const table = el("table", "matrix");
  table.createCaption().textContent = "Repositories by environment";
  const headRow = table.createTHead().insertRow();
  headRow.append(Object.assign(el("th", "repo-head", "Repository"), { scope: "col" }));
  for (const n of envNames) headRow.append(Object.assign(el("th", "env", n), { scope: "col" }));
  headRow.append(Object.assign(el("th", "findings-head", "Findings"), { scope: "col" }));

  const grid: (HTMLButtonElement | null)[][] = [];
  const pos = new Map<HTMLElement, [number, number]>();
  const body = table.createTBody();
  for (const r of s.repos) {
    const tr = body.insertRow();
    tr.dataset.repo = r.repo;
    const th = el("th", "repo");
    th.scope = "row";
    th.append(el("span", "repo-name", r.repo), el("span", "main-sha", shortSha(r.main_sha)));
    tr.append(th);
    const line: (HTMLButtonElement | null)[] = [];
    grid.push(line);
    if (r.error) {
      const td = el("td", "repo-error", `Cannot show this repository: ${r.error}`);
      td.colSpan = envNames.length + 1;
      tr.append(td);
      continue;
    }
    envNames.forEach((name, c) => {
      const e = r.envs.find((x) => x.env === name);
      const td = el("td", "env-cell");
      if (!e) {
        td.dataset.repo = r.repo;
        td.dataset.env = name;
        td.textContent = "—";
        td.title = "Not configured for this repository";
        line.push(null);
      } else {
        const b = envCell(r.repo, e, now);
        td.append(b);
        line.push(b);
        pos.set(b, [grid.length - 1, c]);
      }
      tr.append(td);
    });
    tr.append(findingsCell(r.findings));
  }

  // Roving tabindex: one tab stop, arrow keys move within the grid.
  const all = [...pos.keys()] as HTMLButtonElement[];
  const activate = (b: HTMLButtonElement) => {
    for (const x of all) x.tabIndex = x === b ? 0 : -1;
  };
  if (all[0]) activate(all[0]);
  table.addEventListener("focusin", (ev) => {
    const t = ev.target;
    if (t instanceof HTMLButtonElement && pos.has(t)) activate(t);
  });
  table.addEventListener("keydown", (ev) => {
    const t = ev.target;
    const at = t instanceof HTMLElement ? pos.get(t) : undefined;
    if (!at) return;
    const [r, c] = at;
    const scan = (dr: number, dc: number): HTMLButtonElement | null => {
      for (let i = r + dr, j = c + dc; i >= 0 && i < grid.length; i += dr, j += dc) {
        const row = grid[i]!;
        if (j < 0 || j >= envNames.length) return null;
        if (row[j]) return row[j]!;
      }
      return null;
    };
    let next: HTMLButtonElement | null | undefined;
    switch (ev.key) {
      case "ArrowRight": next = scan(0, 1); break;
      case "ArrowLeft": next = scan(0, -1); break;
      case "ArrowDown": next = scan(1, 0); break;
      case "ArrowUp": next = scan(-1, 0); break;
      case "Home": next = grid[r]!.find((x) => x); break;
      case "End": next = [...grid[r]!].reverse().find((x) => x); break;
      default: return;
    }
    ev.preventDefault();
    if (next) {
      activate(next);
      next.focus();
    }
  });

  const wrap = el("div", "scroll");
  wrap.setAttribute("role", "region");
  wrap.setAttribute("aria-label", "Estate matrix (scrolls horizontally)");
  wrap.tabIndex = 0;
  wrap.append(table);
  root.replaceChildren(wrap);
}
