import { createHash } from "node:crypto";
export function modelValue(name: string): string { return createHash("sha256").update(name).digest("hex"); }
