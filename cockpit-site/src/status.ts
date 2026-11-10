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
