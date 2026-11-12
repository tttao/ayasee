import { SnapshotError, validateSnapshot } from "./load.ts";
import type { Snapshot } from "./status.ts";

export const MAX_HISTORY = 500;
const BATCH = 20;

/** Relative paths such as `history/2026/11/06/0930.json`: no scheme, no `..`, no leading slash. */
const SAFE_PATH = /^[A-Za-z0-9_-][A-Za-z0-9_.-]*(\/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*\.json$/;
const PATH_TIME = /(\d{4})\/(\d{2})\/(\d{2})\/(\d{2})(\d{2})\.json$/;

function pathTime(path: string): number {
  const m = PATH_TIME.exec(path);
  if (!m) return NaN;
  return Date.UTC(+m[1]!, +m[2]! - 1, +m[3]!, +m[4]!, +m[5]!);
}

async function fetchJson(url: string, fetchFn: typeof fetch): Promise<unknown> {
  const res = await fetchFn(url, { cache: "no-store" });
  if (!res.ok) throw new SnapshotError(`HTTP ${res.status}`);
  return JSON.parse(await res.text());
}

/**
 * Fetch `<baseUrl>history/index.json` (a JSON array of paths relative to `baseUrl`) and the
 * snapshots it lists. The index window is authoritative (R61): there is no client-side day filter.
 * Paths are ordered by the time in the path and at most the newest 500 are fetched; failures are
 * skipped. Returns the snapshots oldest first.
 */
export async function loadHistory(
  baseUrl: string,
  fetchFn: typeof fetch = fetch,
): Promise<Snapshot[]> {
  const base = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  const index = await fetchJson(`${base}history/index.json`, fetchFn);
  if (!Array.isArray(index)) throw new SnapshotError("history/index.json is not a list.");
  const paths = index
    .filter((p): p is string => typeof p === "string" && SAFE_PATH.test(p))
    .filter((p) => !Number.isNaN(pathTime(p)))
    .sort((a, b) => pathTime(a) - pathTime(b))
    .slice(-MAX_HISTORY);
  const out: Snapshot[] = [];
  for (let i = 0; i < paths.length; i += BATCH) {
    const got = await Promise.all(
      paths.slice(i, i + BATCH).map(async (p) => {
        try {
          return validateSnapshot(await fetchJson(`${base}${p}`, fetchFn));
        } catch {
          return null;
        }
      }),
    );
    for (const s of got) if (s) out.push(s);
  }
  return out.sort((a, b) => Date.parse(a.generated_at) - Date.parse(b.generated_at));
}

/** Earliest time each SHA was live in `env` for `repo`: `live_at`, else the snapshot time. */
function firstLive(history: readonly Snapshot[], repo: string, env: string): Map<string, number> {
  const first = new Map<string, number>();
  for (const snap of history) {
    const e = snap.repos.find((r) => r.repo === repo)?.envs.find((x) => x.env === env);
    if (!e?.live_sha) continue;
    let t = Date.parse(e.live_at ?? "");
    if (Number.isNaN(t)) t = Date.parse(snap.generated_at);
    if (Number.isNaN(t)) continue;
    const prev = first.get(e.live_sha);
    if (prev === undefined || t < prev) first.set(e.live_sha, t);
  }
  return first;
}

/**
 * Hours from first live in `from` to first live in `to`, per SHA seen in both (in that order).
 * The caller names the two environments, normally the first and last configured for the repo.
 */
export function promotionLeadTimes(history: readonly Snapshot[], repo: string, from = "dev", to = "prod"): number[] {
  if (from === to) return [];
  const start = firstLive(history, repo, from);
  const end = firstLive(history, repo, to);
  const out: number[] = [];
  for (const [sha, tEnd] of end) {
    const tStart = start.get(sha);
    if (tStart !== undefined && tEnd >= tStart) out.push((tEnd - tStart) / 3_600_000);
  }
  return out;
}

export function median(xs: readonly number[]): number | null {
  if (xs.length === 0) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
}

/** Median hours from `from` to `to` for the same SHA across the window, or null when none promoted. */
export function promotionLeadTime(history: readonly Snapshot[], repo: string, from = "dev", to = "prod"): number | null {
  return median(promotionLeadTimes(history, repo, from, to));
}
