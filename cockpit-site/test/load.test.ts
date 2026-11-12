import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { loadSnapshot, SnapshotError, validateSnapshot } from "../src/load.ts";
import { ageLabel, isStale } from "../src/format.ts";
import { mount } from "../src/app.ts";

const good = readFileSync(join(import.meta.dirname, "./fixtures/snapshot.json"), "utf8");
const respond = (body: string, ok = true, status = 200) => (async () => ({ ok, status, text: async () => body })) as never;

describe("loadSnapshot", () => {
  it("accepts labels and rejects malformed ones", () => {
    const withLabels = (labels: unknown) => {
      const s = JSON.parse(good);
      s.repos[0].labels = labels;
      return s;
    };
    expect(validateSnapshot(withLabels([{ kind: "deviation", setting: "identity" }])).repos[0]!.labels).toHaveLength(1);
    expect(validateSnapshot(withLabels([])).repos[0]!.labels).toEqual([]);
    for (const bad of [{}, "x", [{ kind: "bad", setting: "identity" }], [{ kind: "deviation" }], [null]])
      expect(() => validateSnapshot(withLabels(bad))).toThrow(/invalid labels/);
  });
  it("accepts a valid snapshot", async () => {
    const s = await loadSnapshot("./snapshot.json", respond(good));
    expect(s.repos).toHaveLength(4);
  });
  it("rejects malformed JSON", async () => {
    await expect(loadSnapshot("./snapshot.json", respond("{nope"))).rejects.toThrow(/not valid JSON/);
  });
  it("rejects an unknown schema", async () => {
    const bad = JSON.stringify({ ...JSON.parse(good), schema: "ayasee.snapshot/v2" });
    await expect(loadSnapshot("./snapshot.json", respond(bad))).rejects.toThrow(/Unsupported snapshot schema/);
  });
  it("rejects a failed fetch (HTTP error and network error)", async () => {
    await expect(loadSnapshot("./snapshot.json", respond("", false, 404))).rejects.toThrow(/HTTP 404/);
    const boom = (async () => { throw new TypeError("offline"); }) as never;
    await expect(loadSnapshot("./snapshot.json", boom)).rejects.toBeInstanceOf(SnapshotError);
  });
  it("rejects structurally broken snapshots", () => {
    for (const v of [null, [], "x", { schema: "ayasee.snapshot/v1" }, { schema: "ayasee.snapshot/v1", generated_at: "yesterday", repos: [] },
      { schema: "ayasee.snapshot/v1", generated_at: "2026-11-06T09:00:00Z", repos: [{ repo: 1 }] }])
      expect(() => validateSnapshot(v)).toThrow(SnapshotError);
  });
});

describe("type checks", () => {
  const mutate = (f: (s: any) => void) => { const s = JSON.parse(good); f(s); return s; };
  it.each([
    ["pending_commits", (s: any) => { s.repos[0].envs[0].pending_commits = "3"; }],
    ["negative pending_commits", (s: any) => { s.repos[0].envs[0].pending_commits = -1; }],
    ["health.last_result", (s: any) => { s.repos[0].envs[0].health.last_result = "weird"; }],
    ["missing health", (s: any) => { delete s.repos[0].envs[0].health; }],
    ["findings", (s: any) => { s.repos[0].findings = null; }],
    ["findings count", (s: any) => { s.repos[0].findings.high = "many"; }],
  ])("rejects bad %s", (_n, f) => {
    expect(() => validateSnapshot(mutate(f))).toThrow(SnapshotError);
  });
});

describe("page CSP", () => {
  it("locks down base, forms and objects", () => {
    const html = readFileSync(join(import.meta.dirname, "../index.html"), "utf8");
    expect(html).toContain("default-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'");
  });
});

describe("age", () => {
  const now = new Date("2026-11-06T09:00:00Z");
  it("formats relative ages", () => {
    expect(ageLabel("2026-11-06T08:59:50Z", now)).toBe("just now");
    expect(ageLabel("2026-11-06T08:30:00Z", now)).toBe("30m");
    expect(ageLabel("2026-11-06T06:00:00Z", now)).toBe("3h");
    expect(ageLabel("2026-11-03T09:00:00Z", now)).toBe("3d");
  });
  it("is stale only after 2h", () => {
    expect(isStale("2026-11-06T07:00:01Z", now)).toBe(false);
    expect(isStale("2026-11-06T06:59:59Z", now)).toBe(true);
  });
});

describe("page", () => {
  beforeEach(() => {
    document.body.innerHTML = `<p id="age"></p><p id="stale" hidden></p><ul id="legend"></ul><main id="app"></main>`;
  });
  it("shows age, no staleness warning for a fresh snapshot, and the table", async () => {
    await mount(document, respond(good), new Date("2026-11-06T09:30:00Z"));
    expect(document.getElementById("age")?.textContent).toContain("30m ago");
    expect((document.getElementById("stale") as HTMLElement).hidden).toBe(true);
    expect(document.querySelector("table")).not.toBeNull();
    expect(document.querySelectorAll("#legend li")).toHaveLength(7);
  });
  it("warns when the snapshot is older than 2h", async () => {
    await mount(document, respond(good), new Date("2026-11-06T12:00:00Z"));
    const stale = document.getElementById("stale") as HTMLElement;
    expect(stale.hidden).toBe(false);
    expect(stale.textContent).toMatch(/older than 2 hours/);
  });
  it.each([
    ["malformed", "{nope"],
    ["unknown schema", JSON.stringify({ schema: "other/v9", generated_at: "2026-11-06T09:00:00Z", repos: [] })],
  ])("shows a message and no table for %s", async (_n, body) => {
    await mount(document, respond(body), new Date("2026-11-06T09:30:00Z"));
    expect(document.querySelector("table")).toBeNull();
    expect(document.querySelector("#app .message--error")?.textContent).toMatch(/Cannot show the snapshot/);
  });
  it("shows a message and no table when the fetch fails", async () => {
    await mount(document, respond("", false, 500), new Date());
    expect(document.querySelector("table")).toBeNull();
    expect(document.querySelector("#app .message--error")?.textContent).toContain("HTTP 500");
  });
});
