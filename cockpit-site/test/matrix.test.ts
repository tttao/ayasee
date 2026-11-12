import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { mergeEnvOrder, renderMatrix } from "../src/matrix.ts";
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

const cells = () => [...root.querySelectorAll<HTMLButtonElement>("button.cell")];
const cell = (repo: string, env: string) =>
  root.querySelector<HTMLButtonElement>(`button.cell[data-repo="${repo}"][data-env="${env}"]`)!;

describe("renderMatrix", () => {
  it("renders repos x envs cells (repos with errors have none)", () => {
    renderMatrix(base, root, NOW);
    expect(cells()).toHaveLength(3 * 3);
    const heads = [...root.querySelectorAll("thead th")].map((h) => h.textContent);
    expect(heads).toEqual(expect.arrayContaining(["dev", "uat", "prod"]));
  });

  it("uses the R59 ordered merge of env names, with gaps for missing envs", () => {
    const s = clone();
    s.repos[0]!.envs = s.repos[0]!.envs.filter((e) => e.env !== "uat");
    s.repos[1]!.envs.push({ ...structuredClone(s.repos[1]!.envs[0]!), env: "sandbox" });
    renderMatrix(s, root, NOW);
    const heads = [...root.querySelectorAll("thead th.env")].map((h) => h.textContent);
    expect(heads).toEqual(["dev", "uat", "prod", "sandbox"]);
    expect(cell("acme/lakehouse", "uat")).toBeNull();
    const gap = root.querySelector('td[data-repo="acme/lakehouse"][data-env="uat"]')!;
    expect(gap.querySelector(".none-mark")?.textContent).toBe("—");
    expect(gap.querySelector(".none-mark")?.getAttribute("aria-hidden")).toBe("true");
    expect(gap.querySelector(".visually-hidden")?.textContent).toBe("Not configured");
  });

  it("omits the SHA of a cell with nothing live and shows a 7 character SHA otherwise", () => {
    renderMatrix(base, root, NOW);
    const unknown = cell("acme/new-repo", "dev");
    expect(unknown.querySelector(".sha")).toBeNull();
    expect(unknown.getAttribute("aria-label")).toContain("No live SHA");
    expect(unknown.querySelector(".pill")?.textContent).toContain("Unknown");
    expect(cell("acme/lakehouse", "dev").querySelector(".sha")?.textContent).toBe("a1b2c3d");
  });

  it("status pills carry a text label and an icon, never colour only", () => {
    renderMatrix(base, root, NOW);
    const expected: Record<string, string> = {
      "acme/lakehouse|dev": "Synced",
      "acme/lakehouse|uat": "Pending",
      "acme/lakehouse|prod": "Waiting",
      "acme/ml-platform|dev": "Drift",
      "acme/ml-platform|uat": "Failed",
      "acme/ml-platform|prod": "Held",
    };
    for (const [key, label] of Object.entries(expected)) {
      const [repo, env] = key.split("|") as [string, string];
      const pill = cell(repo, env).querySelector(".pill")!;
      expect(pill.querySelector(".pill-label")?.textContent).toBe(label);
      expect(pill.querySelector(`svg.pill-icon--${label.toLowerCase()}`)?.childElementCount).toBeGreaterThan(0);
      expect(pill.querySelector(".pill-icon")?.getAttribute("aria-hidden")).toBe("true");
      expect(pill.className).toContain(`pill--${label.toLowerCase()}`);
    }
  });

  it("shows the pending count when > 0 and a waiting-since age when waiting", () => {
    renderMatrix(base, root, NOW);
    expect(cell("acme/lakehouse", "uat").querySelector(".pending")?.textContent).toBe("3 commits");
    expect(cell("acme/lakehouse", "dev").querySelector(".pending")).toBeNull();
    const prod = cell("acme/lakehouse", "prod");
    expect(prod.querySelector(".pending")?.textContent).toBe("2 commits");
    expect(prod.querySelector(".waiting-since")?.textContent).toBe("2h at gate");
    expect(prod.getAttribute("aria-label")).toContain("2 commits pending. Waiting at a gate for 2 hours.");
    expect(cell("acme/lakehouse", "uat").querySelector(".waiting-since")).toBeNull();
  });

  it("renders findings counts per repo, and n/a when null", () => {
    renderMatrix(base, root, NOW);
    const row = (repo: string) => root.querySelector(`tr[data-repo="${repo}"] .findings`)!;
    expect(row("acme/lakehouse").textContent).toContain("critical 0");
    expect(row("acme/lakehouse").textContent).toContain("high 2");
    expect(row("acme/lakehouse").querySelector("a")?.getAttribute("href")).toMatch(/^https:\/\//);
    expect(row("acme/new-repo").textContent).toBe("n/a");
  });

  it("renders a repo error as a full-width row message", () => {
    renderMatrix(base, root, NOW);
    const td = root.querySelector('tr[data-repo="acme/broken"] td.repo-error')!;
    expect(td.textContent).toContain("ledger branch not readable (403)");
    expect(td.getAttribute("colspan")).toBe("4"); // 3 envs + findings
    expect(root.querySelectorAll('tr[data-repo="acme/broken"] button')).toHaveLength(0);
  });

  it("does not throw on null fields", () => {
    const s = clone();
    const e = s.repos[0]!.envs[0]!;
    e.pending_commits = null;
    e.gate.waiting = null;
    e.live_sha = null;
    s.repos[0]!.findings = { critical: null, high: null, url: null };
    expect(() => renderMatrix(s, root, NOW)).not.toThrow();
  });

  it("re-render replaces previous content", () => {
    renderMatrix(base, root, NOW);
    renderMatrix(base, root, NOW);
    expect(root.querySelectorAll("table")).toHaveLength(1);
  });

  it("is XSS-safe: hostile repo, env and error strings stay text", () => {
    const s = clone();
    const evil = "<img src=x onerror=alert(1)>";
    s.repos[0]!.repo = evil;
    s.repos[0]!.envs[0]!.env = evil;
    s.repos[3]!.error = "<script>alert(2)</script>";
    s.repos[0]!.findings.url = "javascript:alert(3)";
    renderMatrix(s, root, NOW);
    expect(root.querySelector("img")).toBeNull();
    expect(root.querySelector("script")).toBeNull();
    expect(root.textContent).toContain(evil);
    expect(root.textContent).toContain("<script>alert(2)</script>");
    expect(root.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(root.innerHTML).not.toContain("<img");
  });
});

describe("selection and keyboard", () => {
  it("emits cockpit:select with {repo, env} on click", () => {
    renderMatrix(base, root, NOW);
    const seen: unknown[] = [];
    root.addEventListener("cockpit:select", (ev) => seen.push((ev as CustomEvent).detail));
    cell("acme/ml-platform", "uat").click();
    expect(seen).toEqual([{ repo: "acme/ml-platform", env: "uat" }]);
  });

  it("uses a roving tabindex: exactly one cell is tabbable", () => {
    renderMatrix(base, root, NOW);
    expect(cells().filter((c) => c.tabIndex === 0)).toHaveLength(1);
    expect(cells()[0]!.tabIndex).toBe(0);
  });

  it("arrow keys move focus, skip error rows and clamp at the edges", () => {
    renderMatrix(base, root, NOW);
    // Triage order: the unreadable repo, then worst status first (held, waiting, unknown).
    expect([...root.querySelectorAll<HTMLElement>("tbody tr")].map((r) => r.dataset.repo)).toEqual([
      "acme/broken",
      "acme/ml-platform",
      "acme/lakehouse",
      "acme/new-repo",
    ]);
    const press = (el: HTMLElement, key: string) =>
      el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    const a = cell("acme/lakehouse", "dev");
    a.focus();
    press(a, "ArrowRight");
    expect(document.activeElement).toBe(cell("acme/lakehouse", "uat"));
    expect(cell("acme/lakehouse", "uat").tabIndex).toBe(0);
    expect(a.tabIndex).toBe(-1);
    press(document.activeElement as HTMLElement, "ArrowDown");
    expect(document.activeElement).toBe(cell("acme/new-repo", "uat"));
    press(document.activeElement as HTMLElement, "ArrowDown"); // last row: clamp
    expect(document.activeElement).toBe(cell("acme/new-repo", "uat"));
    press(document.activeElement as HTMLElement, "ArrowUp");
    expect(document.activeElement).toBe(cell("acme/lakehouse", "uat"));
    press(document.activeElement as HTMLElement, "ArrowUp");
    expect(document.activeElement).toBe(cell("acme/ml-platform", "uat"));
    press(document.activeElement as HTMLElement, "ArrowUp"); // only the error row above: clamp
    expect(document.activeElement).toBe(cell("acme/ml-platform", "uat"));
    press(document.activeElement as HTMLElement, "ArrowLeft");
    expect(document.activeElement).toBe(cell("acme/ml-platform", "dev"));
    press(document.activeElement as HTMLElement, "ArrowLeft");
    expect(document.activeElement).toBe(cell("acme/ml-platform", "dev"));
    press(document.activeElement as HTMLElement, "End");
    expect(document.activeElement).toBe(cell("acme/ml-platform", "prod"));
    press(document.activeElement as HTMLElement, "Home");
    expect(document.activeElement).toBe(cell("acme/ml-platform", "dev"));
  });
});

describe("mergeEnvOrder (R59)", () => {
  const fixture = JSON.parse(
    readFileSync(join(import.meta.dirname, "../../schema/fixtures/env-order.json"), "utf8"),
  ) as { cases: { name: string; in: string[][]; expect: string[] }[] };
  it.each(fixture.cases)("shared fixture: $name", (c) => {
    expect(mergeEnvOrder(c.in)).toEqual(c.expect);
  });
});

describe("waiting without a since timestamp", () => {
  it("reads just 'at gate'", () => {
    const s = clone();
    s.repos[0]!.envs[2]!.gate.since = null;
    renderMatrix(s, root, NOW);
    expect(cell("acme/lakehouse", "prod").querySelector(".waiting-since")?.textContent).toBe("at gate");
  });

  it("shows the labels of a repository as text tags under its name", () => {
    const s = clone();
    s.repos[0]!.labels = [
      { kind: "non-compliant", setting: "solo_mode" },
      { kind: "deviation", setting: "identity" },
    ];
    s.repos[1]!.labels = [{ kind: "deviation", setting: "<img src=x onerror=alert(1)>" }];
    renderMatrix(s, root, NOW);
    const tags = [...root.querySelectorAll<HTMLElement>(`tr[data-repo="${s.repos[0]!.repo}"] th .repo-label`)];
    expect(tags.map((t) => t.textContent)).toEqual(["solo mode · non-compliant", "secrets identity · deviation"]);
    expect(tags[0]!.classList.contains("repo-label--non-compliant")).toBe(true);
    expect(tags[1]!.title).toContain("D7");
    // an unknown setting is rendered as text, never as markup
    expect(root.querySelector("img")).toBeNull();
    // no labels field (unknown) and an empty list both render nothing
    expect(root.querySelectorAll(`tr[data-repo="${s.repos[2]!.repo}"] .repo-label`)).toHaveLength(0);
  });
});
