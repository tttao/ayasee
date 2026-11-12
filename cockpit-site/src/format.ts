export const STALE_AFTER_MS = 2 * 60 * 60 * 1000;

/** Short relative age such as "just now", "30m", "3h", "2d"; "unknown" for unparseable input. */
export function ageLabel(iso: string | null | undefined, now: Date): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return "unknown";
  const s = Math.max(0, Math.floor((now.getTime() - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function agoLabel(iso: string | null | undefined, now: Date): string {
  const a = ageLabel(iso, now);
  return a === "just now" || a === "unknown" ? a : `${a} ago`;
}

/** The same age in words for accessible names: "2 hours", "1 day"; "" when unknown. */
export function ageWords(iso: string | null | undefined, now: Date): string {
  const a = ageLabel(iso, now);
  if (a === "unknown") return "";
  if (a === "just now") return "less than a minute";
  const n = Number.parseInt(a, 10);
  const unit = { m: "minute", h: "hour", d: "day" }[a.slice(-1)] ?? "";
  return `${n} ${unit}${n === 1 ? "" : "s"}`;
}

/** Exact time for display, always UTC: "2026-11-06 09:00 UTC"; the input unchanged if unparseable. */
export function utcLabel(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return `${new Date(t).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function isStale(iso: string, now: Date): boolean {
  return now.getTime() - Date.parse(iso) > STALE_AFTER_MS;
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : "—";
}

/** Only absolute https links are rendered as anchors (blocks javascript: and data: URLs). */
export function safeHttpsUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}
