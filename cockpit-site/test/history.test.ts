import { describe, expect, it, vi } from "vitest";
import { loadHistory, median, promotionLeadTime, promotionLeadTimes } from "../src/history.ts";
import type { Snapshot } from "../src/status.ts";

const env = (name: string, sha: string | null, at: string | null) => ({
  env: name,
  live_sha: sha,
  live_at: at,
  converged: true,
  pending_commits: 0,
  pending_compare_url: null,
  gate: { waiting: false, since: null, run_url: null },
  drift: { open: false, since: null, issue_url: null, resources: 0 },
  health: { last_result: "applied" as const, stop_the_line: false },
});
const snap = (at: string, envs: ReturnType<typeof env>[], repo = "acme/a"): Snapshot => ({
  schema: "ayasee.snapshot/v1",
  generated_at: at,
  repos: [{ repo, main_sha: null, findings: { critical: 0, high: 0, url: null }, envs }],
});
const H = (n: number) => new Date(Date.UTC(2026, 10, 1) + n * 3_600_000).toISOString();

describe("promotionLeadTime", () => {
  it("is the median hours dev→prod: odd count", () => {
    const h = [
      snap(H(1), [env("dev", "s1", H(0)), env("prod", null, null)]),
      snap(H(10), [env("dev", "s2", H(5)), env("prod", "s1", H(2))]), // s1: 2h
      snap(H(20), [env("dev", "s3", H(10)), env("prod", "s2", H(15))]), // s2: 10h
      snap(H(40), [env("dev", "s4", H(30)), env("prod", "s3", H(40))]), // s3: 30h
    ];
    expect(promotionLeadTimes(h, "acme/a").sort((a, b) => a - b)).toEqual([2, 10, 30]);
    expect(promotionLeadTime(h, "acme/a")).toBe(10);
  });

  it("averages the middle two for an even count", () => {
    const h = [
      snap(H(10), [env("dev", "s1", H(0)), env("prod", "s1", H(2))]),
      snap(H(20), [env("dev", "s2", H(5)), env("prod", "s2", H(15))]),
    ];
    expect(promotionLeadTime(h, "acme/a")).toBe(6);
  });

  it("uses the first time a SHA was live, not later observations", () => {
    const h = [
      snap(H(1), [env("dev", "s1", H(0)), env("prod", null, null)]),
      snap(H(9), [env("dev", "s1", H(0)), env("prod", "s1", H(8))]),
      snap(H(30), [env("dev", "s1", H(0)), env("prod", "s1", H(8))]),
    ];
    expect(promotionLeadTime(h, "acme/a")).toBe(8);
  });

  it("is null when nothing was promoted", () => {
    expect(promotionLeadTime([], "acme/a")).toBeNull();
    expect(promotionLeadTime([snap(H(1), [env("dev", "s1", H(0))])], "acme/a")).toBeNull();
    expect(median([])).toBeNull();
  });

  it("ignores a SHA that reached prod without being seen in dev, and other repos", () => {
    const h = [
      snap(H(10), [env("dev", "s2", H(1)), env("prod", "sX", H(5))]),
      snap(H(11), [env("dev", "s9", H(0)), env("prod", "s9", H(3))], "acme/other"),
    ];
    expect(promotionLeadTime(h, "acme/a")).toBeNull();
    expect(promotionLeadTime(h, "acme/other")).toBe(3);
  });

  it("measures between the two named environments, in that order", () => {
    const h = [
      snap(H(1), [env("sandbox", "s1", H(0)), env("staging", "s1", H(1)), env("live", null, null)]),
      snap(H(9), [env("sandbox", "s1", H(0)), env("staging", "s1", H(1)), env("live", "s1", H(7))]),
    ];
    expect(promotionLeadTimes(h, "acme/a")).toEqual([]); // no env is named dev or prod
    expect(promotionLeadTimes(h, "acme/a", "sandbox", "live")).toEqual([7]);
    expect(promotionLeadTime(h, "acme/a", "staging", "live")).toBe(6);
    expect(promotionLeadTimes(h, "acme/a", "live", "sandbox")).toEqual([]); // wrong direction
    expect(promotionLeadTimes(h, "acme/a", "live", "live")).toEqual([]);
  });

  it("falls back to generated_at when live_at is missing", () => {
    const h = [snap(H(1), [env("dev", "s1", null)]), snap(H(4), [env("prod", "s1", null)])];
    expect(promotionLeadTime(h, "acme/a")).toBe(3);
  });
});

describe("loadHistory", () => {
  const body = (at: string) => JSON.stringify(snap(at, [env("dev", "s1", at)]));
  function mockFetch(index: unknown, files: Record<string, string>) {
    return vi.fn(async (url: string) => {
      if (url === "./history/index.json") return new Response(JSON.stringify(index));
      const f = files[url.replace("./", "")];
      return f === undefined ? new Response("no", { status: 404 }) : new Response(f);
    }) as unknown as typeof fetch;
  }

  it("fetches every listed snapshot (the index window is authoritative), skips failures, oldest first", async () => {
    const index = [
      "history/2026/11/06/0900.json", // unsorted on purpose
      "history/2026/09/01/0000.json", // far older than 30 days: still fetched
      "history/2026/11/04/1000.json",
      "history/2026/11/05/1000.json", // 404
      "../escape.json",
      "https://evil.example/x.json",
    ];
    const f = mockFetch(index, {
      "history/2026/09/01/0000.json": body("2026-09-01T00:00:00Z"),
      "history/2026/11/04/1000.json": body("2026-11-04T10:00:00Z"),
      "history/2026/11/06/0900.json": body("2026-11-06T09:00:00Z"),
    });
    const out = await loadHistory("./", f);
    expect(out.map((s) => s.generated_at)).toEqual([
      "2026-09-01T00:00:00Z",
      "2026-11-04T10:00:00Z",
      "2026-11-06T09:00:00Z",
    ]);
    const urls = (f as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(urls.some((u: string) => u.includes("evil") || u.includes(".."))).toBe(false);
  });

  it("skips invalid snapshots and caps at the newest 500", async () => {
    const index: string[] = [];
    for (let i = 0; i < 600; i++) {
      const d = new Date(Date.UTC(2026, 10, 5, 0, 0) + i * 60_000);
      const p = (n: number) => String(n).padStart(2, "0");
      index.push(`history/2026/11/${p(d.getUTCDate())}/${p(d.getUTCHours())}${p(d.getUTCMinutes())}.json`);
    }
    const f = vi.fn(async (url: string) =>
      url.endsWith("index.json") ? new Response(JSON.stringify(index)) : new Response("{}"),
    ) as unknown as typeof fetch;
    const out = await loadHistory("./", f);
    expect(out).toEqual([]); // all invalid
    expect((f as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(501);
  });

  it("rejects when the index is missing or not a list", async () => {
    await expect(loadHistory("./", mockFetch({}, {}))).rejects.toThrow();
    const f = vi.fn(async () => new Response("", { status: 404 })) as unknown as typeof fetch;
    await expect(loadHistory("./", f)).rejects.toThrow();
  });
});
