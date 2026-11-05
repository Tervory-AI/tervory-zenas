// Demonstration content only. Never merge examples into tenant scan results.
export const checks = [
  { title: "Secrets", copy: "Find exposed credentials. Report the location, never the value." },
  { title: "Dependencies", copy: "Compare package versions with advisory data included with Radix." },
  { title: "Unsafe code", copy: "Look for risky patterns in the code supplied for review." },
] as const;

export const exampleFindings = [
  {
    id: "example-secret",
    severity: "Critical",
    title: "Exposed credential",
    file: "config/settings.py",
    line: 12,
    rule: "Example secret rule",
    fix: "Remove the credential, rotate it, and review the repository history.",
    detail: "Keep credentials in the server environment. Check that old credentials no longer work. The report records the location, never the secret value.",
  },
  {
    id: "example-dependency",
    severity: "High",
    title: "Risky dependency",
    file: "requirements.txt",
    line: 8,
    rule: "Example dependency advisory",
    fix: "Move to a patched package version and check compatibility.",
    detail: "Review the advisory and the package release notes, update the dependency, and run the project tests before checking again.",
  },
  {
    id: "example-static",
    severity: "Medium",
    title: "Unsafe SQL query",
    file: "app/search.py",
    line: 24,
    rule: "Example SQL rule",
    fix: "Use a parameterised query instead of joining user input into SQL.",
    detail: "Pass user values separately from the query text. Add a test for unexpected input, then run a fresh code check.",
  },
] as const;

export function exampleReport(): string {
  return [
    "# Radix — EXAMPLE report",
    "",
    "EXAMPLE ONLY. These findings are made up. This is not a scan of your code.",
    "This report does not approve a release.",
    "",
    ...exampleFindings.flatMap((finding) => [
      "## " + finding.severity + " — " + finding.title + " (Example)",
      finding.file + ":" + finding.line,
      "Rule: " + finding.rule,
      finding.fix,
      finding.detail,
      "",
    ]),
  ].join("\n");
}
