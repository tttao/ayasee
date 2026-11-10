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
