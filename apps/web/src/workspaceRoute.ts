export function readWorkspaceRoute(hash: string) {
  const [page, query = ""] = hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query);
  return {
    page: page || "overview",
    projectId: params.get("project") || "",
    runId: params.get("run") || "",
  };
}

export function workspaceHash(page: string, projectId: string, runId = "") {
  const params = new URLSearchParams();
  if (projectId) params.set("project", projectId);
  if (runId) params.set("run", runId);
  const query = params.toString();
  return `#${page}${query ? `?${query}` : ""}`;
}
