export const checks = [
  { title: "Secrets", copy: "Find exposed credentials. Report the location, never the value." },
  { title: "Dependencies", copy: "Compare package versions with advisory data included with Radix." },
  { title: "Unsafe code", copy: "Look for risky patterns in the code supplied for review." },
] as const;
