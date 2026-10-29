const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
let route, build;
test.before(() => {
  build = fs.mkdtempSync(path.join(os.tmpdir(), "creator-review-route-"));
  execFileSync(process.execPath, [path.resolve("apps/web/node_modules/typescript/lib/tsc.js"),
    "apps/web/src/workspaceRoute.ts", "--target", "ES2022", "--module", "commonjs", "--outDir", build]);
  route = require(path.join(build, "workspaceRoute.js"));
});
test.after(() => fs.rmSync(build, { recursive: true, force: true }));

test("a copied review URL restores both the project and exact review", async () => {
  const { readWorkspaceRoute, workspaceHash } = route;
  assert.deepEqual(readWorkspaceRoute(workspaceHash("review", "project-A", "run-B")), {
    page: "review", projectId: "project-A", runId: "run-B",
  });
});

test("changing project removes an unrelated review and legacy page links still work", async () => {
  const { readWorkspaceRoute, workspaceHash } = route;
  assert.deepEqual(readWorkspaceRoute(workspaceHash("review", "project-C")), {
    page: "review", projectId: "project-C", runId: "",
  });
  assert.equal(readWorkspaceRoute("#rules").page, "rules");
  assert.equal(readWorkspaceRoute("").page, "overview");
});
