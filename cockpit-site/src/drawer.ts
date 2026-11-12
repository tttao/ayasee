import { ageLabel, agoLabel, safeHttpsUrl } from "./format.ts";
import { median, promotionLeadTimes } from "./history.ts";
import type { EnvState, Snapshot, Status } from "./status.ts";
import { cellStatus, STATUS_META } from "./status.ts";
import { pill } from "./matrix.ts";

export const LINK_UNAVAILABLE = "link unavailable";
export const NEW_TAB = "(opens in a new tab)";

type Child = Node | string;
type RepoState = Snapshot["repos"][number];

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, ...kids: Child[]): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  n.append(...kids);
  return n;
}

/**
 * An https-only anchor that opens in a new tab and says so to assistive tech. When the URL does
 * not pass the sanitizer it is muted text instead: "<what> link unavailable", in parentheses when
 * it follows a value (`after`).
 */
export function link(label: string, url: string | null | undefined, what = "", after = false): HTMLElement {
  const href = safeHttpsUrl(url);
  if (!href) {
    const text = what ? `${what} ${LINK_UNAVAILABLE}` : LINK_UNAVAILABLE;
    return el("span", "link-unavailable", after ? `(${text})` : text);
  }
  const a = el("a", "drawer-link", label, el("span", "sr-only", ` ${NEW_TAB}`));
  a.href = href;
  a.rel = "noopener noreferrer";
  a.target = "_blank";
  return a;
}

/** Relative age first, then the exact value in a <time> element. */
function when(iso: string | null, now: Date): Child[] {
  if (!iso) return ["n/a"];
  if (Number.isNaN(Date.parse(iso))) return [iso];
  const t = el("time", "exact-time", iso);
  t.dateTime = iso;
  return [`${agoLabel(iso, now)} `, t];
}

const count = (n: number | null | undefined) => (n === null || n === undefined ? "n/a" : String(n));
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function row(dl: HTMLElement, term: string, ...kids: Child[]): void {
  dl.append(el("dt", undefined, term), el("dd", undefined, ...kids));
}

function section(title: string): { box: HTMLElement; dl: HTMLElement } {
  const box = el("section", "drawer-section");
  box.append(el("h3", undefined, title));
  const dl = el("dl", "drawer-list");
  box.append(dl);
  return { box, dl };
}

type SectionKey = "live" | "gate" | "drift" | "health";

/** The section that explains each status; it is shown first. */
const STATUS_SECTION: Readonly<Record<Status, SectionKey | null>> = {
  held: "health",
  failed: "health",
  drift: "drift",
  waiting: "gate",
  pending: "live",
  synced: "live",
  unknown: null,
};

function envSections(e: EnvState, now: Date, status: Status): HTMLElement[] {
  const live = section("Live");
  row(live.dl, "SHA", e.live_sha ? el("code", "mono", e.live_sha) : "none");
  row(live.dl, "Live since", ...when(e.live_at, now));
  const pending = e.pending_commits;
  if (pending === null || pending === undefined) row(live.dl, "Pending commits", "n/a");
  else if (pending > 0)
    row(live.dl, "Pending commits", `${plural(pending, "commit")} `, link("Compare", e.pending_compare_url, "compare", true));
  else row(live.dl, "Pending commits", "none");

  const gate = section("Gate");
  row(gate.dl, "State", e.gate.waiting === null ? "unknown" : e.gate.waiting ? "waiting for approval" : "not waiting");
  if (e.gate.since) row(gate.dl, "Since", ...when(e.gate.since, now));
  if (e.gate.run_url !== null) row(gate.dl, "Run", link("Open run", e.gate.run_url, "run"));

  const drift = section("Drift");
  row(drift.dl, "State", e.drift.open ? "open" : "none");
  row(drift.dl, "Resources", String(e.drift.resources));
  if (e.drift.since) row(drift.dl, "Since", ...when(e.drift.since, now));
  if (e.drift.issue_url !== null) row(drift.dl, "Issue", link("Open issue", e.drift.issue_url, "issue"));

  const health = section("Health");
  row(health.dl, "Last result", e.health.last_result);
  row(health.dl, "Converged", e.converged === null ? "unknown" : e.converged ? "yes" : "no");
  row(health.dl, "Stop the line", e.health.stop_the_line ? "yes" : "no");

  const all: Record<SectionKey, HTMLElement> = { live: live.box, gate: gate.box, drift: drift.box, health: health.box };
  const first = STATUS_SECTION[status];
  const order = (Object.keys(all) as SectionKey[]).sort((a, b) => Number(b === first) - Number(a === first));
  return order.map((k) => all[k]);
}

/** One sentence: why the cell has this status, made specific with the snapshot's own data. */
export function statusReason(e: EnvState, status: Status, now: Date): string {
  const age = (iso: string | null) => {
    const a = ageLabel(iso, now);
    return a === "unknown" ? null : a;
  };
  switch (status) {
    case "waiting": {
      const a = age(e.gate.since);
      if (a === null) return "Waiting at the approval gate.";
      return a === "just now" ? "Reached the approval gate just now." : `Waiting at the approval gate for ${a}.`;
    }
    case "drift": {
      const a = age(e.drift.since);
      const open = a === null ? "Drift issue open" : a === "just now" ? "Drift issue opened just now" : `Drift issue open for ${a}`;
      return e.drift.resources > 0 ? `${open} on ${plural(e.drift.resources, "resource")}.` : `${open}.`;
    }
    case "pending": {
      const n = e.pending_commits ?? 0;
      return `${plural(n, "commit")} ${n === 1 ? "is" : "are"} not yet applied here.`;
    }
    case "held":
      return e.health.last_result === "failed"
        ? `${STATUS_META.held.help} ${STATUS_META.failed.help}`
        : STATUS_META.held.help;
    default:
      return STATUS_META[status].help;
  }
}

const REPO_NAME = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

/**
 * `https://<host>/<owner>/<repo>`, or null. The host is never guessed (GitHub Enterprise Server
 * has its own): it is taken from a link the collector already wrote for this same repository,
 * and only when that link's path starts with `/<owner>/<repo>/`.
 */
export function repoBaseUrl(r: RepoState): string | null {
  if (!REPO_NAME.test(r.repo) || r.repo.split("/").some((p) => p === "." || p === "..")) return null;
  const urls = [r.findings?.url, ...r.envs.flatMap((e) => [e.gate.run_url, e.drift.issue_url, e.pending_compare_url])];
  for (const raw of urls) {
    const href = safeHttpsUrl(raw);
    if (!href) continue;
    const u = new URL(href);
    if (u.username || u.password) continue;
    if (u.pathname.startsWith(`/${r.repo}/`)) return `${u.origin}/${r.repo}`;
  }
  return null;
}

const NO_LINK = "The snapshot carries no link for this.";
const STOP_LABEL_QUERY = "issues?q=" + encodeURIComponent("is:issue is:open label:ayasee:stop-the-line");

/** Where to go for this status: its primary outbound link, or the next step in words. */
function nextStep(r: RepoState, e: EnvState, status: Status): HTMLElement | null {
  const p = (...kids: Child[]) => el("p", "drawer-next", ...kids);
  switch (status) {
    case "waiting":
      return safeHttpsUrl(e.gate.run_url)
        ? p(link("Open the run waiting for approval", e.gate.run_url))
        : p(`${NO_LINK} Find the waiting run in the repository's Actions tab.`);
    case "drift":
      return safeHttpsUrl(e.drift.issue_url)
        ? p(link("Open the drift issue", e.drift.issue_url))
        : p(`${NO_LINK} Find the open drift issue in the repository's Issues tab.`);
    case "pending":
      return safeHttpsUrl(e.pending_compare_url)
        ? p(link("Compare the pending commits", e.pending_compare_url))
        : p(`${NO_LINK} Compare the live SHA with the main branch in the repository.`);
    case "failed": {
      const base = repoBaseUrl(r);
      return base
        ? p(`${NO_LINK} Find the failed run in the repository's Actions tab: `, link(`Actions for ${r.repo}`, `${base}/actions`))
        : p(`${NO_LINK} Find the failed run in the repository's Actions tab.`);
    }
    case "held": {
      const base = repoBaseUrl(r);
      return base
        ? p(
            `${NO_LINK} Find the issue that holds the line among the repository's open issues: `,
            link(`Open stop-the-line issues for ${r.repo}`, `${base}/${STOP_LABEL_QUERY}`),
          )
        : p(`${NO_LINK} Find the open issue labelled ayasee:stop-the-line in the repository's Issues tab.`);
    }
    default:
      return null;
  }
}

/** The promotion section: median lead time from the repo's first to its last configured environment. */
function promotionSection(r: RepoState, loadHistory: DrawerOptions["loadHistory"]): HTMLElement {
  const box = el("section", "drawer-section", el("h3", undefined, "Promotion"));
  const names = r.envs.map((x) => x.env);
  const from = names[0];
  const to = names[names.length - 1];
  const value = el("span", "lead-value");
  const note = el("span", "lead-note");
  const lead = el("p", "lead-time");
  lead.setAttribute("role", "status");
  box.append(lead);
  if (from === undefined || to === undefined || from === to) {
    lead.append("Lead time: ", value, " ", note);
    value.textContent = "n/a.";
    note.textContent = "It is measured from the first to the last environment, and this repository has fewer than two.";
    return box;
  }
  lead.append(`Median ${from} → ${to} lead time: `, value, " ", note);
  const unavailable = (why: string) => {
    value.textContent = "history unavailable.";
    note.textContent = why;
  };
  if (!loadHistory) {
    unavailable("This view was opened without a history source.");
    return box;
  }
  value.textContent = "loading…";
  loadHistory().then(
    (hist) => {
      const times = promotionLeadTimes(hist, r.repo, from, to);
      const m = median(times);
      const window = `the ${plural(hist.length, "snapshot")} of the published history window`;
      if (m !== null) {
        value.textContent = `${Math.round(m * 10) / 10} h`;
        note.textContent = `(${plural(times.length, "promotion")} in ${window}).`;
      } else {
        value.textContent = "n/a.";
        note.textContent =
          hist.length === 0
            ? "The published history window holds no snapshots yet."
            : `No commit was first live in ${from} and then in ${to} within ${window}.`;
      }
    },
    () =>
      unavailable(
        "The site could not load history/index.json. The collector publishes it, with the dated snapshots, only when it deploys this site with the pages renderer.",
      ),
  );
  return box;
}

function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), [tabindex]:not([tabindex='-1'])")];
}

function findOpener(repo: string, env: string): HTMLElement | null {
  const active = document.activeElement;
  const cells = [...document.querySelectorAll<HTMLElement>("button.cell")];
  const match = cells.find((c) => c.dataset.repo === repo && c.dataset.env === env);
  return match ?? (active instanceof HTMLElement && active !== document.body ? active : null);
}

export interface DrawerOptions {
  /** Loads the history window for the lead-time line; failures show "history unavailable". */
  loadHistory?: () => Promise<Snapshot[]>;
  now?: Date;
}

/**
 * Open the detail dialog for one cell. Read-only: a Close button is the only control; every other
 * action is an https deep link to GitHub. Tab is trapped; Esc, Close and a click on the backdrop
 * close it, and focus returns to the cell.
 */
export function openDrawer(
  snapshot: Snapshot,
  repo: string,
  env: string,
  opts: DrawerOptions = {},
): HTMLDialogElement | null {
  const r = snapshot.repos.find((x) => x.repo === repo);
  if (!r) return null;
  const now = opts.now ?? new Date();
  const e = r.envs.find((x) => x.env === env);
  const opener = findOpener(repo, env);
  for (const old of document.querySelectorAll("dialog.drawer")) old.remove();

  // The dialog itself has no padding, so a click whose target is the dialog is a backdrop click.
  const dlg = el("dialog", "drawer");
  const body = el("div", "drawer-body");
  dlg.append(body);
  const titleId = "drawer-title";
  dlg.setAttribute("aria-labelledby", titleId);
  const h = el("h2", undefined, `${repo} · ${env}`);
  h.id = titleId;
  const close = el("button", "drawer-close", "Close");
  close.type = "button";
  body.append(el("div", "drawer-head", h, close));

  if (e) {
    const status = cellStatus(e);
    const why = el("p", "drawer-why", statusReason(e, status, now));
    why.id = "drawer-why";
    dlg.setAttribute("aria-describedby", why.id);
    const lead = el("div", "drawer-lead", el("div", "drawer-status", pill(status), why));
    const next = nextStep(r, e, status);
    if (next) lead.append(next);
    body.append(lead, ...envSections(e, now, status));
  } else body.append(el("p", "message", `${env} is not configured for ${repo}.`));

  const fnd = section("Findings (repository)");
  row(fnd.dl, "Critical", count(r.findings?.critical));
  row(fnd.dl, "High", count(r.findings?.high));
  if (r.findings?.url != null) row(fnd.dl, "Code scanning", link("Open code scanning", r.findings.url, "code scanning"));
  body.append(fnd.box, promotionSection(r, opts.loadHistory));

  let closed = false;
  const finish = () => {
    if (closed) return;
    closed = true;
    if (dlg.open && typeof dlg.close === "function") dlg.close();
    dlg.remove();
    if (opener && opener.isConnected) opener.focus();
  };
  close.addEventListener("click", finish);
  // Backdrop: the press and the release must both land on the dialog element itself, so a text
  // selection dragged out of the panel does not close it.
  let pressedOnBackdrop = false;
  dlg.addEventListener("mousedown", (ev) => {
    pressedOnBackdrop = ev.target === dlg;
  });
  dlg.addEventListener("click", (ev) => {
    const press = pressedOnBackdrop;
    pressedOnBackdrop = false;
    if (ev.target === dlg && (press || ev.detail === 0)) finish();
  });
  dlg.addEventListener("cancel", (ev) => {
    ev.preventDefault();
    finish();
  });
  dlg.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      ev.preventDefault();
      finish();
    } else if (ev.key === "Tab") {
      const items = focusables(dlg);
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      if (ev.shiftKey && (document.activeElement === first || document.activeElement === dlg)) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    }
  });
  document.body.append(dlg);
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  close.focus();
  return dlg;
}
