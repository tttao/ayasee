import type { Env, AyaseeCockpitSnapshotV1 } from "./model.ts";

export type Snapshot = AyaseeCockpitSnapshotV1;
export type EnvState = Env;
export type Status = "synced" | "pending" | "waiting" | "drift" | "failed" | "held" | "unknown";

interface Rule {
  readonly status: Exclude<Status, "unknown">;
  /** Human-readable condition; mirrored in schema/fixtures/status-precedence.json. */
  readonly when: string;
  readonly test: (e: EnvState) => boolean;
}

/**
 * Precedence table, first match wins, `unknown` is the fallback. The order and
 * `when` text are shared with Python (Task 21) via
 * schema/fixtures/status-precedence.json.
 */
export const PRECEDENCE: readonly Rule[] = [
  { status: "held", when: "health.stop_the_line is true", test: (e) => e.health?.stop_the_line === true },
  { status: "failed", when: "health.last_result is failed", test: (e) => e.health?.last_result === "failed" },
  { status: "drift", when: "drift.open is true", test: (e) => e.drift?.open === true },
  { status: "waiting", when: "gate.waiting is true", test: (e) => e.gate?.waiting === true },
  { status: "pending", when: "pending_commits > 0", test: (e) => (e.pending_commits ?? 0) > 0 },
  {
    status: "synced",
    when: "health.last_result is applied or no_changes, and live_sha is set",
    test: (e) =>
      (e.health?.last_result === "applied" || e.health?.last_result === "no_changes") && !!e.live_sha,
  },
];

export const FALLBACK_STATUS: Status = "unknown";

/** Every status, most urgent first: the precedence order, then the fallback. */
export const STATUS_ORDER: readonly Status[] = [...PRECEDENCE.map((r) => r.status), FALLBACK_STATUS];

export function cellStatus(e: EnvState): Status {
  for (const rule of PRECEDENCE) if (rule.test(e)) return rule.status;
  return FALLBACK_STATUS;
}

/** Text label, icon and legend description for each status. Never colour alone. */
export const STATUS_META: Readonly<Record<Status, { label: string; icon: string; help: string }>> = {
  held: { label: "Held", icon: "■", help: "Stop-the-line is open: applies are blocked." },
  failed: { label: "Failed", icon: "✕", help: "The last apply failed." },
  drift: { label: "Drift", icon: "△", help: "Live infrastructure differs from the ledger (open drift issue)." },
  waiting: { label: "Waiting", icon: "◔", help: "A deployment is waiting at an approval gate." },
  pending: { label: "Pending", icon: "↑", help: "Commits are not yet applied here." },
  synced: { label: "Synced", icon: "✓", help: "Last apply succeeded and nothing is pending." },
  unknown: { label: "Unknown", icon: "?", help: "No usable data for this environment." },
};

const SVG_NS = "http://www.w3.org/2000/svg";

/** One drawn shape: an SVG tag and its attributes; `fill: true` paints it solid instead of stroking it. */
export type IconShape = readonly [tag: "path" | "rect" | "circle", attrs: Readonly<Record<string, string>>, fill?: boolean];

/**
 * A decorative 12x12 icon drawn in the current text colour. Built with createElementNS and
 * presentation attributes only, so it needs no font and passes the page's CSP.
 */
export function svgIcon(shapes: readonly IconShape[], cls: string): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  const set = (n: Element, attrs: Readonly<Record<string, string>>) => {
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  };
  set(svg, {
    class: cls,
    viewBox: "0 0 12 12",
    width: "12",
    height: "12",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.6",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
    focusable: "false",
  });
  for (const [tag, attrs, fill] of shapes) {
    const n = document.createElementNS(SVG_NS, tag);
    set(n, attrs);
    if (fill) set(n, { fill: "currentColor", stroke: "none" });
    svg.append(n);
  }
  return svg;
}

/** The drawn form of each status glyph; the text glyphs in STATUS_META stay for plain-text uses. */
const STATUS_SHAPES: Readonly<Record<Status, readonly IconShape[]>> = {
  held: [["rect", { x: "2", y: "2", width: "8", height: "8", rx: "1" }, true]],
  failed: [["path", { d: "M2.5 2.5l7 7M9.5 2.5l-7 7" }]],
  drift: [["path", { d: "M6 1.75L10.75 10.25H1.25Z" }]],
  waiting: [
    ["circle", { cx: "6", cy: "6", r: "4.75" }],
    ["path", { d: "M6 3.25V6l2 1.25" }],
  ],
  pending: [["path", { d: "M6 10.5V2M2.5 5.25L6 1.75l3.5 3.5" }]],
  synced: [["path", { d: "M2 6.5l2.75 2.75L10 3.25" }]],
  unknown: [
    ["path", { d: "M3.75 4a2.25 2.25 0 1 1 3.4 1.95C6.4 6.4 6 6.9 6 7.75" }],
    ["circle", { cx: "6", cy: "10.1", r: "0.95" }, true],
  ],
};

/** The status glyph as an inline SVG (aria-hidden): the non-colour channel, independent of fonts. */
export function statusIcon(status: Status): SVGSVGElement {
  return svgIcon(STATUS_SHAPES[status], `pill-icon pill-icon--${status}`);
}
