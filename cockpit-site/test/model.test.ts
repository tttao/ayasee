import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";

it("src/model.ts is up to date with schema/snapshot.v1.json (run `npm run gen-types`)", () => {
  const pkg = JSON.parse(readFileSync(join(import.meta.dirname, "../package.json"), "utf8")) as {
    scripts: Record<string, string>;
  };
  const args = pkg.scripts["gen-types"]!.split(/\s+/).slice(1);
  const dir = mkdtempSync(join(tmpdir(), "model-"));
  try {
    const out = join(dir, "model.ts");
    const fixed = args.map((a) => (a === "src/model.ts" ? out : a));
    const root = join(import.meta.dirname, "..");
    execFileSync(join(root, "node_modules/.bin/json2ts"), fixed, { cwd: root });
    expect(readFileSync(join(root, "src/model.ts"), "utf8")).toBe(readFileSync(out, "utf8"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
