import { openDrawer } from "./drawer.ts";
import { agoLabel, isStale, utcLabel } from "./format.ts";
import { loadHistory } from "./history.ts";
import { loadSnapshot, SnapshotError } from "./load.ts";
import type { SelectDetail } from "./matrix.ts";
import { pill, renderMatrix } from "./matrix.ts";
import type { Snapshot } from "./status.ts";
import { STATUS_META, STATUS_ORDER } from "./status.ts";

const SNAPSHOT_URL = "./snapshot.json";
const STALE_TEXT = "This snapshot is older than 2 hours. The collector may be failing; the data below may be out of date.";

interface State {
  snapshot: Snapshot | null;
  history: Promise<Snapshot[]> | null;
  fetchFn: typeof fetch;
  /** The current time; fixed when a test passes `now` to mount(). */
  clock: () => Date;
}

const states = new WeakMap<Document, State>();
const wired = new WeakSet<Element>();

const byId = (doc: Document, id: string) => doc.getElementById(id);

function renderLegend(doc: Document): void {
  const ul = byId(doc, "legend");
  if (!ul) return;
  const items = STATUS_ORDER.map((st) => {
    const li = doc.createElement("li");
    const help = doc.createElement("span");
    help.className = "legend-help";
    help.textContent = STATUS_META[st].help;
    li.append(pill(st), help);
    return li;
  });
  ul.replaceChildren(...items);

  // The legend is a popover: Escape and a click anywhere else close it.
  const wrap = ul.closest("details");
  if (!wrap || wired.has(wrap)) return;
  wired.add(wrap);
  doc.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || !wrap.open) return;
    const inside = wrap.contains(doc.activeElement);
    wrap.open = false;
    if (inside) wrap.querySelector("summary")?.focus();
  });
  doc.addEventListener("click", (ev) => {
    if (wrap.open && ev.target instanceof Node && !wrap.contains(ev.target)) wrap.open = false;
  });
}

/** Update the header age text and the staleness banner. Safe to call repeatedly. */
export function renderAge(doc: Document, s: Snapshot | null, now: Date): void {
  const age = byId(doc, "age");
  const stale = byId(doc, "stale");
  if (age) {
    // Relative age first, exact time once. Only touch the status region when the wording changes.
    const lead = s ? `Updated ${agoLabel(s.generated_at, now)}` : "No snapshot loaded";
    const exact = s ? utcLabel(s.generated_at) : "";
    const text = exact ? `${lead} · ${exact}` : lead;
    if (age.textContent !== text) {
      if (s) {
        const time = doc.createElement("time");
        time.dateTime = s.generated_at;
        time.textContent = exact;
        age.replaceChildren(`${lead} · `, time);
      } else age.textContent = lead;
    }
  }
  if (stale) {
    const old = s !== null && isStale(s.generated_at, now);
    stale.hidden = !old;
    const text = old ? STALE_TEXT : "";
    if (stale.textContent !== text) stale.textContent = text;
  }
}

function showError(doc: Document, app: HTMLElement, err: unknown): void {
  const msg = doc.createElement("p");
  msg.className = "message message--error";
  msg.setAttribute("role", "alert");
  const detail = err instanceof SnapshotError ? err.message : "Unexpected error while rendering.";
  msg.textContent = `Cannot show the snapshot. ${detail}`;
  app.replaceChildren(msg);
}

function drawerOpen(doc: Document): boolean {
  return doc.querySelector("dialog.drawer") !== null;
}

/** Render a snapshot, keeping keyboard focus on the same cell and the matrix scroll position. */
function show(doc: Document, app: HTMLElement, st: State, s: Snapshot): void {
  const now = st.clock();
  const active = doc.activeElement;
  const focusId = active instanceof HTMLElement && app.contains(active) ? active.id : "";
  const old = app.querySelector(".scroll");
  const at: [number, number] = old ? [old.scrollLeft, old.scrollTop] : [0, 0];
  renderAge(doc, s, now);
  renderMatrix(s, app, now);
  st.snapshot = s;
  st.history = null;
  const scroll = app.querySelector(".scroll");
  if (scroll) [scroll.scrollLeft, scroll.scrollTop] = at;
  if (focusId) byId(doc, focusId)?.focus({ preventScroll: true });
}

export async function mount(doc: Document, fetchFn: typeof fetch = fetch, now?: Date): Promise<Snapshot | null> {
  const st: State = { snapshot: null, history: null, fetchFn, clock: () => now ?? new Date() };
  states.set(doc, st);
  renderLegend(doc);
  const app = byId(doc, "app");
  if (!app) return null;
  if (!wired.has(app)) {
    wired.add(app);
    app.addEventListener("cockpit:select", (ev) => {
      const cur = states.get(doc);
      if (!cur?.snapshot) return;
      const d = (ev as CustomEvent<SelectDetail>).detail;
      cur.history ??= loadHistory("./", cur.fetchFn);
      const h = cur.history;
      openDrawer(cur.snapshot, d.repo, d.env, { loadHistory: () => h, now: cur.clock() });
    });
  }
  try {
    show(doc, app, st, await loadSnapshot(SNAPSHOT_URL, fetchFn));
    return st.snapshot;
  } catch (err) {
    showError(doc, app, err);
    renderAge(doc, null, st.clock());
    return null;
  }
}

/** Re-render the header age and staleness banner from the snapshot on screen. */
export function tick(doc: Document): void {
  const st = states.get(doc);
  if (st?.snapshot) renderAge(doc, st.snapshot, st.clock());
}

/**
 * Fetch the snapshot again and re-render. Never while the detail drawer is open, and a failed
 * fetch keeps a snapshot that is already on screen (its age and the stale banner stay honest).
 * Resolves true when the page was re-rendered.
 */
export async function refresh(doc: Document): Promise<boolean> {
  const st = states.get(doc);
  const app = byId(doc, "app");
  if (!st || !app || drawerOpen(doc)) return false;
  let s: Snapshot;
  try {
    s = await loadSnapshot(SNAPSHOT_URL, st.fetchFn);
  } catch (err) {
    if (!st.snapshot && states.get(doc) === st) showError(doc, app, err);
    return false;
  }
  // The drawer may have opened, or the page been mounted again, while the fetch was in flight.
  if (states.get(doc) !== st || drawerOpen(doc)) return false;
  try {
    show(doc, app, st, s);
    return true;
  } catch (err) {
    st.snapshot = null;
    showError(doc, app, err);
    renderAge(doc, null, st.clock());
    return false;
  }
}
