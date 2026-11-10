import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { openDrawer } from "../src/drawer.ts";
import { renderMatrix } from "../src/matrix.ts";
import type { Snapshot } from "../src/status.ts";

const NOW = new Date("2026-11-06T09:30:00Z");
const base = JSON.parse(readFileSync(join(import.meta.dirname, "./fixtures/snapshot.json"), "utf8")) as Snapshot;
const clone = (): Snapshot => structuredClone(base);

let root: HTMLElement;
beforeEach(() => {
  document.body.replaceChildren();
  root = document.createElement("div");
  document.body.append(root);
});

const cell = (repo: string, env: string) =>
  root.querySelector<HTMLButtonElement>(`button.cell[data-repo="${repo}"][data-env="${env}"]`)!;
const key = (target: Element, k: string, shiftKey = false) =>
  target.dispatchEvent(new KeyboardEvent("keydown", { key: k, shiftKey, bubbles: true, cancelable: true }));

function open(s: Snapshot, repo: string, env: string, opts = {}) {
  renderMatrix(s, root, NOW);
  cell(repo, env)?.focus();
  return openDrawer(s, repo, env, { now: NOW, ...opts })!;
}

describe("drawer content", () => {
  it("shows the full live SHA in monospace, live_at, gate, drift, health and findings", () => {
    const s = clone();
    const d = open(s, "acme/ml-platform", "dev");
    expect(d.querySelector("code.mono")?.textContent).toBe("a1b2c3d4e5f6");
    const text = d.textContent!;
    expect(text).toContain("2026-11-06T08:00:00Z");
    expect(text).toContain("open"); // drift
    expect(text).toMatch(/Resources\s*2/);
    expect(text).toMatch(/Critical\s*1/);
    expect(text).toContain("Last result");
    const hrefs = [...d.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("https://github.com/acme/ml-platform/issues/7");
  });

  it("links the pending compare and the code-scanning page with rel noopener noreferrer", () => {
    const s = clone();
    const e = s.repos[0]!.envs.find((x) => x.pending_commits)!;
    e.pending_compare_url = "https://github.com/acme/lakehouse/compare/a...b";
    const d = open(s, "acme/lakehouse", e.env);
    const anchors = [...d.querySelectorAll("a")];
    expect(anchors.map((a) => a.href)).toEqual(
      expect.arrayContaining([
        "https://github.com/acme/lakehouse/compare/a...b",
        "https://github.com/acme/lakehouse/security/code-scanning",
      ]),
    );
    for (const a of anchors) expect(a.rel).toBe("noopener noreferrer");
  });

  it("renders a link that fails sanitizing as plain text 'link unavailable'", () => {
    const s = clone();
    const e = s.repos[1]!.envs[0]!;
    e.drift.issue_url = "javascript:alert(1)";
    s.repos[1]!.findings.url = "http://insecure.example/x";
    const d = open(s, "acme/ml-platform", e.env);
    expect(d.querySelectorAll("a").length).toBe(0);
    expect(d.textContent).toContain("link unavailable");
    expect(d.innerHTML).not.toContain("javascript:");
  });

  it("shows the repo error", () => {
    const s = clone();
    const d = open(s, "acme/broken", "dev");
    expect(d.textContent).toContain("ledger branch not readable (403)");
  });

  it("returns null for an unknown repo", () => {
    expect(openDrawer(clone(), "nope/x", "dev")).toBeNull();
  });

  it("treats names as text: an XSS-ish repo and env name creates no elements", () => {
    const s = clone();
    const evil = '<img src=x onerror="window.pwned=1">';
    s.repos[0]!.repo = `acme/${evil}`;
    s.repos[0]!.envs[0]!.env = evil;
    s.repos[0]!.error = '<script>window.pwned=1</script>';
    renderMatrix(s, root, NOW);
    const d = openDrawer(s, `acme/${evil}`, evil, { now: NOW })!;
    expect(d.querySelector("img, script")).toBeNull();
    expect(d.textContent).toContain(evil);
  });
});

describe("drawer behaviour", () => {
  it("has no form, no method attribute, no input and only a Close button", () => {
    const d = open(clone(), "acme/lakehouse", "prod");
    expect(d.querySelector("form")).toBeNull();
    expect(d.querySelector("[method]")).toBeNull();
    expect(d.querySelector("input, select, textarea")).toBeNull();
    expect([...d.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Close"]);
  });

  it("never calls fetch", () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    open(clone(), "acme/lakehouse", "prod");
    expect(f).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("moves focus into the dialog, closes on Escape and returns focus to the cell", () => {
    const d = open(clone(), "acme/lakehouse", "prod");
    expect(d.contains(document.activeElement)).toBe(true);
    key(d, "Escape");
    expect(document.querySelector("dialog.drawer")).toBeNull();
    expect(document.activeElement).toBe(cell("acme/lakehouse", "prod"));
  });

  it("closes with the Close button and returns focus", () => {
    const d = open(clone(), "acme/lakehouse", "dev");
    d.querySelector<HTMLButtonElement>("button.drawer-close")!.click();
    expect(document.querySelector("dialog.drawer")).toBeNull();
    expect(document.activeElement).toBe(cell("acme/lakehouse", "dev"));
  });

  it("traps Tab and Shift+Tab inside the dialog", () => {
    const s = clone();
    const d = open(s, "acme/ml-platform", "dev");
    const items = [...d.querySelectorAll<HTMLElement>("a[href], button")];
    const first = items[0]!;
    const last = items[items.length - 1]!;
    last.focus();
    key(d, "Tab");
    expect(document.activeElement).toBe(first);
    first.focus();
    key(d, "Tab", true);
    expect(document.activeElement).toBe(last);
  });

  it("opens from the cockpit:select event wiring and replaces an earlier drawer", () => {
    const s = clone();
    renderMatrix(s, root, NOW);
    root.addEventListener("cockpit:select", (ev) => {
      const { repo, env } = (ev as CustomEvent<{ repo: string; env: string }>).detail;
      openDrawer(s, repo, env, { now: NOW });
    });
    cell("acme/lakehouse", "dev").click();
    cell("acme/lakehouse", "prod").click();
    expect(document.querySelectorAll("dialog.drawer").length).toBe(1);
  });
});

describe("drawer lead time", () => {
  const hist = (devAt: string, prodAt: string): Snapshot[] => {
    const mk = (env: string, at: string): Snapshot => {
      const s = clone();
      const r = s.repos[0]!;
      r.envs = r.envs.filter((e) => e.env === env).map((e) => ({ ...e, live_sha: "f".repeat(40), live_at: at }));
      s.generated_at = at;
      return s;
    };
    return [mk("dev", devAt), mk("prod", prodAt)];
  };

  it("shows the median lead time and promotion count", async () => {
    const d = open(clone(), "acme/lakehouse", "dev", {
      loadHistory: async () => hist("2026-11-05T00:00:00Z", "2026-11-05T05:00:00Z"),
    });
    await vi.waitFor(() =>
      expect(d.querySelector(".lead-time")?.textContent).toBe("Median dev→prod lead time: 5 h (1 promotions)"),
    );
  });

  it("says n/a with no promotions and 'history unavailable' on failure", async () => {
    const d = open(clone(), "acme/lakehouse", "dev", { loadHistory: async () => [] });
    await vi.waitFor(() => expect(d.querySelector(".lead-time")?.textContent).toContain("n/a"));
    const d2 = open(clone(), "acme/lakehouse", "dev", { loadHistory: () => Promise.reject(new Error("x")) });
    await vi.waitFor(() => expect(d2.querySelector(".lead-time")?.textContent).toContain("history unavailable"));
  });
});
