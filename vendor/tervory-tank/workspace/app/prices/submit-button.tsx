"use client";

import { useFormStatus } from "react-dom";

export function SubmitButton({ label, pending, tone }: { label: string; pending: string; tone: "primary" | "ghost" }) {
  const { pending: busy } = useFormStatus();
  return (
    <button type="submit" className={tone} disabled={busy}>
      {busy ? pending : label}
    </button>
  );
}
