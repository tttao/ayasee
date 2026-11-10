import type { Snapshot } from "./status.ts";

export const SCHEMA = "ayasee.snapshot/v1";

export class SnapshotError extends Error {}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

const RESULTS = ["applied", "failed", "no_changes", "unknown"];
const isCount = (v: unknown) => v === null || (typeof v === "number" && Number.isInteger(v) && v >= 0);
const isBoolOrNull = (v: unknown) => v === null || typeof v === "boolean";

function checkEnv(e: Record<string, unknown>): void {
  const bad = (what: string) => new SnapshotError(`The snapshot has an invalid ${what}.`);
  if (!isCount(e.pending_commits ?? null)) throw bad("pending_commits");
  if (!isObj(e.gate) || !isBoolOrNull(e.gate.waiting ?? null)) throw bad("gate");
  if (!isObj(e.drift) || typeof e.drift.open !== "boolean") throw bad("drift");
  if (!isObj(e.health) || typeof e.health.stop_the_line !== "boolean" || !RESULTS.includes(e.health.last_result as string))
    throw bad("health");
}

/** Structural and type validation of everything the renderer reads; nullable fields may be null. Strings are only ever rendered as text. */
export function validateSnapshot(v: unknown): Snapshot {
  if (!isObj(v)) throw new SnapshotError("The snapshot is not a JSON object.");
  if (v.schema !== SCHEMA)
    throw new SnapshotError(`Unsupported snapshot schema ${JSON.stringify(v.schema ?? null)}; this page reads ${SCHEMA}.`);
  if (typeof v.generated_at !== "string" || Number.isNaN(Date.parse(v.generated_at)))
    throw new SnapshotError("The snapshot has no valid generated_at timestamp.");
  if (!Array.isArray(v.repos)) throw new SnapshotError("The snapshot has no repos list.");
  for (const r of v.repos) {
    if (!isObj(r) || typeof r.repo !== "string" || !Array.isArray(r.envs))
      throw new SnapshotError("The snapshot contains a malformed repo entry.");
    if (!isObj(r.findings) || !isCount(r.findings.critical ?? null) || !isCount(r.findings.high ?? null))
      throw new SnapshotError("The snapshot has invalid findings counts.");
    for (const e of r.envs) {
      if (!isObj(e) || typeof e.env !== "string") throw new SnapshotError("The snapshot contains a malformed environment entry.");
      checkEnv(e);
    }
  }
  return v as unknown as Snapshot;
}

export async function loadSnapshot(url: string, fetchFn: typeof fetch = fetch): Promise<Snapshot> {
  let text: string;
  try {
    const res = await fetchFn(url, { cache: "no-store" });
    if (!res.ok) throw new SnapshotError(`Could not fetch ${url} (HTTP ${res.status}).`);
    text = await res.text();
  } catch (err) {
    if (err instanceof SnapshotError) throw err;
    throw new SnapshotError(`Could not fetch ${url}: ${err instanceof Error ? err.message : String(err)}.`);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new SnapshotError(`${url} is not valid JSON.`);
  }
  return validateSnapshot(parsed);
}
