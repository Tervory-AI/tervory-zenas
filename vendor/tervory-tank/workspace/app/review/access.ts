import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { founderDenial, isListedFounder, reviewGate, type FounderDenial } from "./founder";

export type Reviewer = {
  userId: string;
  name: string;
  admissionNo: string;
  jurisdictions: Array<"us" | "za">;
  founder: boolean;
  role: string;
};

/** SigninDirectory role `org:reviewer`, plus a jurisdiction tag and an admission number. Founders open the same page. */
export async function requireReviewer(): Promise<Reviewer> {
  const { userId, orgRole, tenantId } = await auth();
  const role = (orgRole ?? "").replace(/^org:/, "");
  const founder = isListedFounder(userId, process.env["TERVORY_ADMIN_USER_IDS"]);
  if (!userId || (!founder && role !== "reviewer")) notFound();
  const qualifications = JSON.parse(process.env.TRIGGER_SIGNIN_REVIEWERS ?? "{}") as Record<string, Record<string, { jurisdiction?: unknown; admission_no?: unknown }>>;
  const qualification = tenantId ? qualifications[tenantId]?.[userId] : undefined;
  const gate = reviewGate({
    userId,
    orgRole,
    adminIds: process.env["TERVORY_ADMIN_USER_IDS"],
    jurisdiction: qualification?.jurisdiction,
    admissionNo: qualification?.admission_no,
  });
  if (!gate) notFound();
  const name = gate.founder ? "Founder" : "Reviewer";
  return { userId, name, admissionNo: gate.admissionNo, jurisdictions: gate.jurisdictions, founder: gate.founder, role };
}

/** Skala and Tank. Same SigninDirectory user list as the admin page (`TERVORY_ADMIN_USER_IDS`). */
export async function rejectUnlessFounder(): Promise<FounderDenial | null> {
  const { userId } = await auth();
  return founderDenial(userId, process.env["TERVORY_ADMIN_USER_IDS"]);
}
