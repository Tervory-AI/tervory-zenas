/** Labels for the Tisan page. Sample builds are not shown. No secrets. */

export type View = "overview" | "builds" | "design" | "tests" | "settings";
export type Filter = "all" | "Build" | "Page" | "Test";
export type Dialog = "none" | "build" | "plugins";
export type PluginTab = "keys" | "files" | "links" | "mcp";
export type Kind = "Build" | "Page" | "Test";

export type SearchRecord = {
  id: string;
  kind: Kind;
  title: string;
  detail: string;
  view: View;
};

export const PAGE_NAV: { id: View; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "builds", label: "Builds" },
  { id: "design", label: "Design" },
  { id: "tests", label: "Tests" },
  { id: "settings", label: "Settings" },
];

export const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "Build", label: "Builds" },
  { id: "Page", label: "Pages" },
  { id: "Test", label: "Tests" },
];

/** Records search can find. Empty until a real build exists. */
export const RECORDS: SearchRecord[] = [];

export function showsChat(view: View, query: string, filter: Filter): boolean {
  return view === "overview" && query.trim() === "" && filter === "all";
}

export function showsResults(view: View, query: string, filter: Filter): boolean {
  return view === "overview" && !showsChat(view, query, filter);
}

/** Which control is the single red button. Chat views use Send. The pop-up uses its own confirm. */
export function primaryAction(input: {
  view: View;
  query: string;
  filter: Filter;
  dialog: Dialog;
  pluginTab: PluginTab;
  mobile: boolean;
}): "send" | "start" | "start-confirm" | "save-key" | "choose-files" | "add-link" | "add-connection" {
  if (input.dialog === "build") return "start-confirm";
  if (input.dialog === "plugins") {
    if (input.mobile) return "save-key";
    if (input.pluginTab === "files") return "choose-files";
    if (input.pluginTab === "links") return "add-link";
    if (input.pluginTab === "mcp") return "add-connection";
    return "save-key";
  }
  if (showsChat(input.view, input.query, input.filter)) return "send";
  return "start";
}

export function searchRecords(query: string, filter: Filter): SearchRecord[] {
  const q = query.trim().toLowerCase();
  return RECORDS.filter((row) => {
    if (filter !== "all" && row.kind !== filter) return false;
    if (!q) return true;
    return `${row.kind} ${row.title} ${row.detail}`.toLowerCase().includes(q);
  });
}

export function groupMeta(kind: Kind, count: number): { heading: string; countLabel: string } {
  if (kind === "Build") return { heading: "Builds", countLabel: count === 1 ? "1 build" : `${count} builds` };
  if (kind === "Page") return { heading: "Pages", countLabel: count === 1 ? "1 page" : `${count} pages` };
  const runs = count === 1 ? "1 test run" : `${count} test runs`;
  return { heading: "Tests", countLabel: runs };
}

export function resultSummary(query: string, count: number): string {
  const noun = count === 1 ? "result" : "results";
  const q = query.trim();
  if (!q) return `${count} ${noun}`;
  return `${count} ${noun} for “${q}”`;
}

export type Citation = { title: string; detail: string; view: View };

export const OPENING_THREAD: { role: "user" | "assistant"; text: string; when?: string; sources?: Citation[] }[] = [];

export function replyTo(text: string): { text: string; when: string; sources: Citation[] } {
  const hits = searchRecords(text, "all").slice(0, 4);
  if (hits.length === 0) {
    return {
      text: "No builds yet. Nothing was sent, and no build was started.",
      when: "Answered just now",
      sources: [],
    };
  }
  return {
    text: "These are the closest matches. Nothing was sent, and no build was started.",
    when: "Answered just now",
    sources: hits.map((hit) => ({ title: hit.title, detail: hit.detail, view: hit.view })),
  };
}

export const RULES = [
  { title: "Saved page only", detail: "Tisan reads the locked Figma page saved in the repo. It does not call Figma live." },
  { title: "No public internet", detail: "Tisan has no route to the public internet while it builds." },
  { title: "Draft pull requests", detail: "Every build ends as a draft pull request for you to review. Tisan does not merge." },
  { title: "Deploy on a clean scan", detail: "Tisan deploys only after the tests pass and the scan comes back clean." },
];

export const PLUGIN_TABS: { id: PluginTab; label: string }[] = [
  { id: "keys", label: "API keys" },
  { id: "files", label: "Files" },
  { id: "links", label: "Source links" },
  { id: "mcp", label: "MCP connections" },
];
