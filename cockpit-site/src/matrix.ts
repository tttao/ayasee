import { ageLabel, ageWords, safeHttpsUrl, shortSha } from "./format.ts";
import type { EnvState, Snapshot, Status } from "./status.ts";
import { cellStatus, STATUS_META, STATUS_ORDER, statusIcon, svgIcon } from "./status.ts";

export interface SelectDetail {
  repo: string;
  env: string;
}

type Repo = Snapshot["repos"][number];

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
  const p = el("span", `pill pill--${status}`);
  p.append(statusIcon(status), el("span", "pill-label", STATUS_META[status].label));
  return p;
}

/** Injective and id-safe: letters, digits and "-" pass through, everything else becomes _<hex>. */
const idPart = (s: string) => s.replace(/[^A-Za-z0-9-]/gu, (ch) => `_${ch.codePointAt(0)!.toString(16)}`);

/** Stable element id of the cell button for one repository and environment. */
export function cellId(repo: string, env: string): string {
  return `cell-${idPart(repo)}.${idPart(env)}`;
}

/** Stable element id of a repository's row header. */
export function repoId(repo: string): string {
  return `repo-${idPart(repo)}`;
}

/** 0 for an unreadable repository, then 1 + the position of its worst cell in STATUS_ORDER. */
function urgency(r: Repo): number {
  if (r.error) return 0;
  let worst = STATUS_ORDER.length;
  for (const e of r.envs) worst = Math.min(worst, STATUS_ORDER.indexOf(cellStatus(e)));
  return 1 + worst;
}

/**
 * Rows in triage order: unreadable repositories first, then by worst status in precedence order.
 * The sort is stable, so ties keep the snapshot's order.
 */
export function sortRepos(repos: readonly Repo[]): Repo[] {
  return [...repos].sort((a, b) => urgency(a) - urgency(b));
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function envCell(repo: string, e: EnvState, now: Date, id: string): HTMLButtonElement {
  const status = cellStatus(e);
  const b = el("button", `cell cell--${status}`);
  b.type = "button";
  b.id = id;
  b.dataset.repo = repo;
  b.dataset.env = e.env;
  b.dataset.status = status;
  b.tabIndex = -1;

  const body = el("span", "cell-body");
  body.append(pill(status));
  const said = [`${repo}, ${e.env}: ${STATUS_META[status].label}`];
  if (e.live_sha) {
    const sha = shortSha(e.live_sha);
    body.append(el("span", "sha", sha));
    said.push(`Live SHA ${sha}`);
  } else said.push("No live SHA");

  const pending = e.pending_commits ?? 0;
  if (pending > 0) {
    const commits = plural(pending, "commit", "commits");
    body.append(el("span", "pending", commits));
    said.push(`${commits} pending`);
  }
  if (e.gate?.waiting === true) {
    const age = e.gate.since ? ageLabel(e.gate.since, now) : "unknown";
    const words = ageWords(e.gate.since, now);
    body.append(
      el("span", "waiting-since", age === "unknown" ? "at gate" : age === "just now" ? "at gate just now" : `${age} at gate`),
    );
    said.push(words ? `Waiting at a gate for ${words}` : "Waiting at a gate");
  }
  b.append(body, svgIcon([["path", { d: "M4.5 2.5L8 6l-3.5 3.5" }]], "cell-chevron"));
  b.setAttribute("aria-label", `${said.join(". ")}.`);
  b.addEventListener("click", () => {
    b.dispatchEvent(
      new CustomEvent<SelectDetail>("cockpit:select", { bubbles: true, detail: { repo, env: e.env } }),
    );
  });
  return b;
}

function findingsCell(f: Repo["findings"] | undefined): HTMLTableCellElement {
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
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.append(el("span", "visually-hidden", " (opens in a new tab)"));
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

/** In-page link that moves keyboard focus (not only the scroll position) to its target. */
function jumpLink(targetId: string, cls: string): HTMLAnchorElement {
  const a = el("a", cls);
  a.href = `#${targetId}`;
  a.addEventListener("click", (ev) => {
    const target = document.getElementById(targetId);
    if (!target) return;
    ev.preventDefault();
    target.focus();
  });
  return a;
}

/**
 * The triage line: how many cells are in each status, most urgent first, each a link to the first
 * such cell in the table, then how many repositories could not be read.
 */
function summary(
  first: ReadonlyMap<Status, string>,
  counts: ReadonlyMap<Status, number>,
  errorIds: readonly string[],
): HTMLElement {
  const total = [...counts.values()].reduce((a, b) => a + b, 0);
  if (errorIds.length === 0 && total > 0 && counts.get("synced") === total) {
    return el("p", "summary", total === 1 ? "The one environment is synced." : `All ${total} environments are synced.`);
  }
  const ul = el("ul", "summary");
  ul.setAttribute("aria-label", "Environments by status");
  for (const st of STATUS_ORDER) {
    const n = counts.get(st) ?? 0;
    const id = first.get(st);
    if (n === 0 || !id) continue;
    const a = jumpLink(id, "summary-link");
    a.dataset.status = st;
    a.append(el("span", "summary-count", String(n)), pill(st));
    a.append(el("span", "visually-hidden", `: go to the first ${STATUS_META[st].label} cell`));
    const li = el("li");
    li.append(a);
    ul.append(li);
  }
  if (errorIds[0]) {
    const a = jumpLink(errorIds[0], "summary-link summary-link--error");
    a.textContent = `${plural(errorIds.length, "repository", "repositories")} unreadable`;
    const li = el("li");
    li.append(a);
    ul.append(li);
  }
  return ul;
}

export function renderMatrix(s: Snapshot, root: HTMLElement, now: Date = new Date()): void {
  if (s.repos.length === 0) {
    root.replaceChildren(el("p", "message", "No repositories in this snapshot."));
    return;
  }
  const repos = sortRepos(s.repos);
  const envNames = mergeEnvOrder(repos.map((r) => r.envs.map((e) => e.env)));

  const table = el("table", "matrix");
  table.createCaption().textContent = "Repositories by environment";
  const headRow = table.createTHead().insertRow();
  headRow.append(Object.assign(el("th", "repo-head", "Repository"), { scope: "col" }));
  for (const n of envNames) headRow.append(Object.assign(el("th", "env", n), { scope: "col" }));
  headRow.append(Object.assign(el("th", "findings-head", "Findings"), { scope: "col" }));

  // Ids come from snapshot strings: keep them unique even if the snapshot repeats a repository.
  const used = new Set<string>();
  const unique = (id: string): string => {
    let out = id;
    for (let i = 2; used.has(out); i++) out = `${id}.${i}`;
    used.add(out);
    return out;
  };

  const counts = new Map<Status, number>();
  const first = new Map<Status, string>();
  const errorIds: string[] = [];
  const grid: (HTMLButtonElement | null)[][] = [];
  const pos = new Map<HTMLElement, [number, number]>();
  const body = table.createTBody();
  for (const r of repos) {
    const tr = body.insertRow();
    tr.dataset.repo = r.repo;
    const th = el("th", "repo");
    th.scope = "row";
    th.id = unique(repoId(r.repo));
    // Prefer a line break after the owner ("acme/" + "lakehouse") over one mid-word.
    const name = el("span", "repo-name");
    const slash = r.repo.indexOf("/");
    if (slash > 0) name.append(r.repo.slice(0, slash + 1), document.createElement("wbr"), r.repo.slice(slash + 1));
    else name.textContent = r.repo;
    th.append(name);
    if (r.main_sha) th.append(el("span", "main-sha", shortSha(r.main_sha)));
    tr.append(th);
    const line: (HTMLButtonElement | null)[] = [];
    grid.push(line);
    if (r.error) {
      th.tabIndex = -1; // focus target of the summary's "unreadable" link
      errorIds.push(th.id);
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
        td.classList.add("env-cell--none");
        const dash = el("span", "none-mark", "—");
        dash.setAttribute("aria-hidden", "true");
        td.append(dash, el("span", "visually-hidden", "Not configured"));
        td.title = "Not configured for this repository";
        line.push(null);
      } else {
        const b = envCell(r.repo, e, now, unique(cellId(r.repo, name)));
        const st = b.dataset.status as Status;
        counts.set(st, (counts.get(st) ?? 0) + 1);
        if (!first.has(st)) first.set(st, b.id);
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
  wrap.setAttribute("aria-label", "Estate matrix");
  wrap.append(table);
  root.replaceChildren(
    summary(first, counts, errorIds),
    wrap,
    el("p", "hint", "Select a cell for detail and links into GitHub."),
  );

  // A scrolling region must be reachable by keyboard, but only while it actually scrolls.
  const syncOverflow = () => {
    if (wrap.scrollWidth > wrap.clientWidth || wrap.scrollHeight > wrap.clientHeight) wrap.tabIndex = 0;
    else wrap.removeAttribute("tabindex");
  };
  syncOverflow();
  if (typeof ResizeObserver === "function") {
    const ro = new ResizeObserver(syncOverflow);
    ro.observe(wrap);
    ro.observe(table);
  }
}
