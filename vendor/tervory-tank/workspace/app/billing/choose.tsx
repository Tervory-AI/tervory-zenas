"use client";

import { useFormStatus } from "react-dom";

export function ChooseButton() {
  const { pending } = useFormStatus();
  return (
    <button className="primary" type="submit" disabled={pending}>
      {pending ? "Opening Stripe…" : "Choose this plan"}
    </button>
  );
}
