import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { EnvState } from "../src/status.ts";
import { cellStatus, PRECEDENCE } from "../src/status.ts";

interface Case {
  name: string;
  in: {
    stop_the_line: boolean;
    last_result: EnvState["health"]["last_result"];
    drift_open: boolean;
    waiting: boolean | null;
    pending_commits: number | null;
    live_sha: string | null;
  };
  expect: string;
}
const fixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../schema/fixtures/status-precedence.json"), "utf8"),
) as { order: string[]; fallback: string; rules: Record<string, string>; cases: Case[] };

function env(i: Case["in"]): EnvState {
  return {
    env: "dev",
    live_sha: i.live_sha,
    live_at: null,
    converged: null,
    pending_commits: i.pending_commits,
    pending_compare_url: null,
    gate: { waiting: i.waiting, since: null, run_url: null },
    drift: { open: i.drift_open, since: null, issue_url: null, resources: 0 },
    health: { last_result: i.last_result, stop_the_line: i.stop_the_line },
  };
}

describe("precedence fixture (shared with Python)", () => {
  it("matches the exported precedence table", () => {
    expect(PRECEDENCE.map((r) => r.status)).toEqual(fixture.order);
    expect(Object.fromEntries(PRECEDENCE.map((r) => [r.status, r.when]))).toEqual(fixture.rules);
    expect(fixture.fallback).toBe("unknown");
  });

  it("covers every adjacent and non-adjacent pair", () => {
    const names = fixture.cases.map((c) => c.name);
    for (let i = 0; i < fixture.order.length - 1; i++)
      for (let j = i + 1; j < fixture.order.length; j++)
        expect(names).toContain(`${fixture.order[i]} beats ${fixture.order[j]}`);
  });

  it.each(fixture.cases.map((c) => [c.name, c] as const))("%s", (_n, c) => {
    expect(cellStatus(env(c.in))).toBe(c.expect);
  });
});

describe("cellStatus null safety", () => {
  it("never throws on null or missing nested fields", () => {
    const sparse = { env: "x", live_sha: null, pending_commits: null } as unknown as EnvState;
    expect(cellStatus(sparse)).toBe("unknown");
    expect(cellStatus({} as EnvState)).toBe("unknown");
  });
});
