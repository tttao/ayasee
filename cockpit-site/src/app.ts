import { openDrawer } from "./drawer.ts";
import { agoLabel, isStale } from "./format.ts";
import { loadHistory } from "./history.ts";
import { loadSnapshot, SnapshotError } from "./load.ts";
import type { SelectDetail } from "./matrix.ts";
import { pill, renderMatrix } from "./matrix.ts";
import type { Snapshot, Status } from "./status.ts";
import { STATUS_META } from "./status.ts";


const byId = (doc: Document, id: string) => doc.getElementById(id);

function renderLegend(doc: Document): void {
  const ul = byId(doc, "legend");
  if (!ul) return;
  const items = (Object.keys(STATUS_META) as Status[]).map((st) => {
    const li = doc.createElement("li");
    const help = doc.createElement("span");
    help.className = "legend-help";
    help.textContent = STATUS_META[st].help;
    li.append(pill(st), help);
    return li;
  });
  ul.replaceChildren(...items);
}

/** Update the header age text and the staleness banner. Safe to call repeatedly. */
export function renderAge(doc: Document, s: Snapshot | null, now: Date): void {
  const age = byId(doc, "age");
  const stale = byId(doc, "stale");
  if (age) age.textContent = s ? `Snapshot ${s.generated_at} (${agoLabel(s.generated_at, now)})` : "No snapshot loaded";
  if (stale) {
    const old = s !== null && isStale(s.generated_at, now);
    stale.hidden = !old;
    stale.textContent = old
      ? `This snapshot is older than 2 hours (${agoLabel(s.generated_at, now)}). The collector may be failing; the data below may be out of date.`
      : "";
  }
}

export async function mount(doc: Document, fetchFn: typeof fetch = fetch, now: Date = new Date()): Promise<Snapshot | null> {
  renderLegend(doc);
  const app = byId(doc, "app");
  if (!app) return null;
  try {
    const s = await loadSnapshot("./snapshot.json", fetchFn);
    renderAge(doc, s, now);
    renderMatrix(s, app, now);
    let history: Promise<Snapshot[]> | null = null;
    app.addEventListener("cockpit:select", (ev) => {
      const d = (ev as CustomEvent<SelectDetail>).detail;
      history ??= loadHistory("./", fetchFn);
      const h = history;
      openDrawer(s, d.repo, d.env, { loadHistory: () => h, now });
    });
    return s;
  } catch (err) {
    const msg = doc.createElement("p");
    msg.className = "message message--error";
    msg.setAttribute("role", "alert");
    const detail = err instanceof SnapshotError ? err.message : "Unexpected error while rendering.";
    msg.textContent = `Cannot show the snapshot. ${detail}`;
    app.replaceChildren(msg);
    renderAge(doc, null, now);
    return null;
  }
}
