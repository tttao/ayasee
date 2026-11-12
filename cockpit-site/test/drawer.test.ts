import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { openDrawer, repoBaseUrl } from "../src/drawer.ts";
import { renderMatrix } from "../src/matrix.ts";
import type { Snapshot } from "../src/status.ts";
import { STATUS_META } from "../src/status.ts";

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
    // A drift cell leads with the Drift section, so pick the Live section's time explicitly.
    const live = [...d.querySelectorAll("section")].find((x) => x.querySelector("h3")?.textContent === "Live")!;
    const t = live.querySelector("time")!;
    expect(t.getAttribute("datetime")).toBe("2026-11-06T08:00:00Z");
    expect(t.textContent).toBe("2026-11-06T08:00:00Z");
    expect(t.parentElement!.textContent).toBe("1h ago 2026-11-06T08:00:00Z"); // relative age first
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
    for (const a of anchors) {
      expect(a.rel).toBe("noopener noreferrer");
      expect(a.target).toBe("_blank");
      expect(a.querySelector(".sr-only")?.textContent).toContain("(opens in a new tab)");
    }
  });

  it("says which link is missing after a count: '2 commits (compare link unavailable)'", () => {
    const d = open(clone(), "acme/lakehouse", "prod"); // 2 pending, no compare URL
    const dd = [...d.querySelectorAll("dt")].find((x) => x.textContent === "Pending commits")!.nextElementSibling!;
    expect(dd.textContent).toBe("2 commits (compare link unavailable)");
    const s = clone();
    s.repos[0]!.envs[2]!.pending_commits = 1;
    s.repos[0]!.envs[2]!.gate.run_url = "javascript:alert(1)";
    const d1 = open(s, "acme/lakehouse", "prod");
    expect(d1.textContent).toContain("1 commit (compare link unavailable)");
    expect(d1.textContent).toContain("run link unavailable");
    expect(d1.textContent).not.toMatch(/\d link unavailable/);
  });

  it("renders a link that fails sanitizing as plain text 'link unavailable'", () => {
    const s = clone();
    const e = s.repos[1]!.envs[0]!;
    e.drift.issue_url = "javascript:alert(1)";
    s.repos[1]!.findings.url = "http://insecure.example/x";
    const d = open(s, "acme/ml-platform", e.env);
    expect(d.querySelectorAll("a").length).toBe(0);
    expect(d.textContent).toContain("issue link unavailable");
    expect(d.textContent).toContain("code scanning link unavailable");
    expect(d.innerHTML).not.toContain("javascript:");
  });

  it("says so when the environment is not configured (a repo-error row has no cells)", () => {
    const d = open(clone(), "acme/broken", "dev");
    expect(d.textContent).toContain("dev is not configured for acme/broken.");
    expect(d.hasAttribute("aria-describedby")).toBe(false);
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

describe("drawer lead: why this status and where to go", () => {
  const why = (d: HTMLElement) => d.querySelector(".drawer-why")!.textContent;
  const firstLink = (d: HTMLElement) => d.querySelector("a");

  it("puts the pill and the reason under the heading and describes the dialog with the reason", () => {
    const d = open(clone(), "acme/lakehouse", "prod");
    const lead = d.querySelector(".drawer-head")!.nextElementSibling!;
    expect(lead.className).toBe("drawer-lead");
    expect(lead.querySelector(".pill--waiting")).not.toBeNull();
    expect(d.getAttribute("aria-labelledby")).toBe("drawer-title");
    expect(d.getAttribute("aria-describedby")).toBe(d.querySelector(".drawer-why")!.id);
    expect(d.getAttribute("aria-describedby")).not.toBe(d.getAttribute("aria-labelledby"));
  });

  it("waiting: gate age, and the gate run is the first link", () => {
    const s = clone();
    s.repos[0]!.envs[2]!.gate.since = "2026-11-01T09:00:00Z";
    const d = open(s, "acme/lakehouse", "prod");
    expect(why(d)).toBe("Waiting at the approval gate for 5d.");
    expect(firstLink(d)!.textContent).toContain("Open the run waiting for approval");
    expect(firstLink(d)!.href).toBe("https://github.com/acme/lakehouse/actions/runs/1");
    expect(d.querySelector(".drawer-section h3")!.textContent).toBe("Gate");
  });

  it("drift: age and resource count, and the drift issue is the first link", () => {
    const d = open(clone(), "acme/ml-platform", "dev");
    expect(why(d)).toBe("Drift issue open for 1d on 2 resources.");
    expect(firstLink(d)!.textContent).toContain("Open the drift issue");
    expect(firstLink(d)!.href).toBe("https://github.com/acme/ml-platform/issues/7");
    expect([...d.querySelectorAll(".drawer-section h3")].map((h) => h.textContent)).toEqual([
      "Drift",
      "Live",
      "Gate",
      "Health",
      "Findings (repository)",
      "Promotion",
    ]);
  });

  it("pending: commit count, and the compare view is the first link", () => {
    const d = open(clone(), "acme/lakehouse", "uat");
    expect(why(d)).toBe("3 commits are not yet applied here.");
    expect(firstLink(d)!.textContent).toContain("Compare the pending commits");
    expect(firstLink(d)!.href).toBe("https://github.com/acme/lakehouse/compare/a1b2c3d...f00dfee");
    expect(d.querySelector(".drawer-section h3")!.textContent).toBe("Live");
  });

  it("held and failed: says the snapshot has no link, with the repo host taken from another link of that repo", () => {
    const held = open(clone(), "acme/ml-platform", "prod");
    expect(why(held)).toBe(STATUS_META.held.help);
    expect(held.querySelector(".drawer-next")!.textContent).toContain("The snapshot carries no link for this.");
    expect(firstLink(held)!.textContent).toContain("Open stop-the-line issues for acme/ml-platform");
    expect(firstLink(held)!.href).toBe(
      "https://github.com/acme/ml-platform/issues?q=is%3Aissue%20is%3Aopen%20label%3Aayasee%3Astop-the-line",
    );
    expect(held.querySelector(".drawer-section h3")!.textContent).toBe("Health");

    const failed = open(clone(), "acme/ml-platform", "uat");
    expect(why(failed)).toBe(STATUS_META.failed.help);
    expect(firstLink(failed)!.textContent).toContain("Actions for acme/ml-platform");
    expect(firstLink(failed)!.href).toBe("https://github.com/acme/ml-platform/actions");
  });

  it("held and failed: words only when no host is known, and never a guessed github.com", () => {
    const s = clone();
    s.repos[1]!.envs[0]!.drift.issue_url = null;
    for (const env of ["uat", "prod"]) {
      const d = open(s, "acme/ml-platform", env);
      expect(d.querySelector(".drawer-lead a")).toBeNull();
      expect(d.querySelector(".drawer-next")!.textContent).toMatch(/carries no link for this\. Find the/);
    }
  });

  it("derives the repo URL only from a link of that same repo, keeping a GHES host", () => {
    const s = clone();
    const r = s.repos[1]!;
    r.findings.url = "https://ghes.example.test/acme/ml-platform/security/code-scanning";
    expect(repoBaseUrl(r)).toBe("https://ghes.example.test/acme/ml-platform");
    r.findings.url = null;
    r.envs[0]!.drift.issue_url = "https://github.com/other/repo/issues/7";
    expect(repoBaseUrl(r)).toBeNull();
    r.envs[0]!.drift.issue_url = "http://github.com/acme/ml-platform/issues/7";
    expect(repoBaseUrl(r)).toBeNull();
    r.envs[0]!.drift.issue_url = "https://github.com/acme/ml-platform/issues/7";
    r.repo = "acme/ml platform";
    expect(repoBaseUrl(r)).toBeNull();
    r.repo = "../..";
    expect(repoBaseUrl(r)).toBeNull();
  });

  it("synced and unknown: the legend sentence and no next step", () => {
    const d = open(clone(), "acme/lakehouse", "dev");
    expect(why(d)).toBe(STATUS_META.synced.help);
    expect(d.querySelector(".drawer-next")).toBeNull();
    const u = open(clone(), "acme/new-repo", "dev");
    expect(why(u)).toBe(STATUS_META.unknown.help);
    expect([...u.querySelectorAll(".drawer-section h3")].slice(0, 4).map((h) => h.textContent)).toEqual([
      "Live",
      "Gate",
      "Drift",
      "Health",
    ]);
  });

  it("waiting without a run link says so and gives the next step in words", () => {
    const s = clone();
    s.repos[0]!.envs[2]!.gate.run_url = null;
    s.repos[0]!.envs[2]!.gate.since = null;
    const d = open(s, "acme/lakehouse", "prod");
    expect(why(d)).toBe("Waiting at the approval gate.");
    expect(d.querySelector(".drawer-next")!.textContent).toBe(
      "The snapshot carries no link for this. Find the waiting run in the repository's Actions tab.",
    );
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

  it("closes on a backdrop click (target is the dialog itself) and returns focus", () => {
    const d = open(clone(), "acme/lakehouse", "dev");
    d.querySelector<HTMLElement>(".drawer-body")!.click(); // inside the panel: stays open
    d.querySelector<HTMLElement>("h2")!.click();
    expect(document.querySelector("dialog.drawer")).toBe(d);
    d.click();
    expect(document.querySelector("dialog.drawer")).toBeNull();
    expect(document.activeElement).toBe(cell("acme/lakehouse", "dev"));
  });

  it("does not close when a drag that started inside the panel ends on the backdrop", () => {
    const d = open(clone(), "acme/lakehouse", "dev");
    d.querySelector("h2")!.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    d.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
    expect(document.querySelector("dialog.drawer")).toBe(d);
    d.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    d.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
    expect(document.querySelector("dialog.drawer")).toBeNull();
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

  it("names the first and last configured environment and shows the median and promotion count", async () => {
    const d = open(clone(), "acme/lakehouse", "dev", {
      loadHistory: async () => hist("2026-11-05T00:00:00Z", "2026-11-05T05:00:00Z"),
    });
    await vi.waitFor(() =>
      expect(d.querySelector(".lead-time")?.textContent).toBe(
        "Median dev → prod lead time: 5 h (1 promotion in the 2 snapshots of the published history window).",
      ),
    );
  });

  it("uses the repo's own first and last environment names, not dev and prod", async () => {
    const s = clone();
    const names = ["sandbox", "staging", "live"];
    s.repos[0]!.envs.forEach((e, i) => (e.env = names[i]!));
    const h = hist("2026-11-05T00:00:00Z", "2026-11-05T03:00:00Z");
    h[0]!.repos[0]!.envs[0]!.env = "sandbox";
    h[1]!.repos[0]!.envs[0]!.env = "live";
    const d = open(s, "acme/lakehouse", "sandbox", { loadHistory: async () => h });
    await vi.waitFor(() =>
      expect(d.querySelector(".lead-time")?.textContent).toContain("Median sandbox → live lead time: 3 h (1 promotion"),
    );
  });

  it("says n/a with the true reason, and why history is unavailable", async () => {
    const d = open(clone(), "acme/lakehouse", "dev", { loadHistory: async () => [] });
    await vi.waitFor(() =>
      expect(d.querySelector(".lead-time")?.textContent).toBe(
        "Median dev → prod lead time: n/a. The published history window holds no snapshots yet.",
      ),
    );
    const one = clone(); // prod runs an older SHA, so nothing went dev → prod in the window
    one.repos[0]!.envs.find((e) => e.env === "prod")!.live_sha = "0000000older";
    const d1 = open(clone(), "acme/lakehouse", "dev", { loadHistory: async () => [one] });
    await vi.waitFor(() =>
      expect(d1.querySelector(".lead-time")?.textContent).toBe(
        "Median dev → prod lead time: n/a. No commit was first live in dev and then in prod within the 1 snapshot of the published history window.",
      ),
    );
    const d2 = open(clone(), "acme/lakehouse", "dev", { loadHistory: () => Promise.reject(new Error("x")) });
    await vi.waitFor(() => {
      const t = d2.querySelector(".lead-time")!.textContent!;
      expect(t).toContain("Median dev → prod lead time: history unavailable.");
      expect(t).toContain("history/index.json");
      expect(t).toContain("pages renderer");
    });
    const d3 = open(clone(), "acme/lakehouse", "dev");
    expect(d3.querySelector(".lead-time")!.textContent).toContain("history unavailable.");
  });

  it("says n/a when the repository has fewer than two environments", () => {
    const s = clone();
    s.repos[0]!.envs = s.repos[0]!.envs.slice(0, 1);
    const d = open(s, "acme/lakehouse", "dev", { loadHistory: async () => [] });
    expect(d.querySelector(".lead-time")!.textContent).toContain("Lead time: n/a.");
  });
});
