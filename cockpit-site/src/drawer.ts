import { agoLabel, safeHttpsUrl } from "./format.ts";
import { median, promotionLeadTimes } from "./history.ts";
import type { EnvState, Snapshot } from "./status.ts";
import { cellStatus } from "./status.ts";
import { pill } from "./matrix.ts";

export const LINK_UNAVAILABLE = "link unavailable";

type Child = Node | string;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, ...kids: Child[]): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  n.append(...kids);
  return n;
}

/** An https-only anchor, or the plain text "link unavailable" when the URL does not pass the sanitizer. */
export function link(label: string, url: string | null | undefined): HTMLElement {
  const href = safeHttpsUrl(url);
  if (!href) return el("span", "link-unavailable", LINK_UNAVAILABLE);
  const a = el("a", "drawer-link", label);
  a.href = href;
  a.rel = "noopener noreferrer";
  a.target = "_blank";
  return a;
}

function when(iso: string | null, now: Date): string {
  return iso ? `${iso} (${agoLabel(iso, now)})` : "n/a";
}

const count = (n: number | null | undefined) => (n === null || n === undefined ? "n/a" : String(n));

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

function envSections(e: EnvState, now: Date): HTMLElement[] {
  const live = section("Live");
  row(live.dl, "SHA", e.live_sha ? el("code", "mono", e.live_sha) : "none");
  row(live.dl, "Live since", when(e.live_at, now));
  const pending = e.pending_commits;
  const compare: Child[] = [count(pending)];
  if ((pending ?? 0) > 0) compare.push(" ", link("Compare", e.pending_compare_url));
  row(live.dl, "Pending commits", ...compare);

  const gate = section("Gate");
  row(gate.dl, "State", e.gate.waiting === null ? "unknown" : e.gate.waiting ? "waiting for approval" : "not waiting");
  if (e.gate.since) row(gate.dl, "Since", when(e.gate.since, now));
  if (e.gate.run_url !== null) row(gate.dl, "Run", link("Open run", e.gate.run_url));

  const drift = section("Drift");
  row(drift.dl, "State", e.drift.open ? "open" : "none");
  row(drift.dl, "Resources", String(e.drift.resources));
  if (e.drift.since) row(drift.dl, "Since", when(e.drift.since, now));
  if (e.drift.issue_url !== null) row(drift.dl, "Issue", link("Open issue", e.drift.issue_url));

  const health = section("Health");
  row(health.dl, "Last result", e.health.last_result);
  row(health.dl, "Converged", e.converged === null ? "unknown" : e.converged ? "yes" : "no");
  row(health.dl, "Stop the line", e.health.stop_the_line ? "yes" : "no");
  return [live.box, gate.box, drift.box, health.box];
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
 * action is an https deep link to GitHub. Tab is trapped, Esc closes, focus returns to the cell.
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

  const dlg = el("dialog", "drawer");
  const titleId = "drawer-title";
  dlg.setAttribute("aria-labelledby", titleId);
  const h = el("h2", undefined, `${repo} · ${env}`);
  h.id = titleId;
  const close = el("button", "drawer-close", "Close");
  close.type = "button";
  const head = el("div", "drawer-head", h);
  if (e) head.append(pill(cellStatus(e)));
  head.append(close);
  dlg.append(head);

  if (r.error) dlg.append(el("p", "message message--error", `Cannot show this repository: ${r.error}`));
  if (e) {
    dlg.append(...envSections(e, now));
  } else dlg.append(el("p", "message", `${env} is not configured for ${repo}.`));
  if (e) dlg.setAttribute("aria-describedby", titleId);

  const fnd = section("Findings (repository)");
  row(fnd.dl, "Critical", count(r.findings?.critical));
  row(fnd.dl, "High", count(r.findings?.high));
  if (r.findings?.url != null) row(fnd.dl, "Code scanning", link("Open code scanning", r.findings.url));
  dlg.append(fnd.box);

  const lead = el("p", "lead-time", "Median dev→prod lead time: loading…");
  lead.setAttribute("role", "status");
  const leadBox = section("Promotion");
  leadBox.box.replaceChildren(el("h3", undefined, "Promotion"), lead);
  dlg.append(leadBox.box);
  if (opts.loadHistory) {
    opts.loadHistory().then(
      (hist) => {
        const times = promotionLeadTimes(hist, repo);
        const m = median(times);
        lead.textContent =
          m === null
            ? "Median dev→prod lead time: n/a (no promotions in the window)"
            : `Median dev→prod lead time: ${Math.round(m * 10) / 10} h (${times.length} promotions)`;
      },
      () => {
        lead.textContent = "Median dev→prod lead time: history unavailable";
      },
    );
  } else lead.textContent = "Median dev→prod lead time: history unavailable";

  let closed = false;
  const finish = () => {
    if (closed) return;
    closed = true;
    if (dlg.open && typeof dlg.close === "function") dlg.close();
    dlg.remove();
    if (opener && opener.isConnected) opener.focus();
  };
  close.addEventListener("click", finish);
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
