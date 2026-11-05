"use server";

import { randomUUID } from "node:crypto";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { rigThreeDRequest, type RigThreeDOperation } from "@/lib/tank";
import { administrator } from "./server";

export async function changeThreeD(form: FormData) {
  let notice = "failed";
  const jobId = String(form.get("jobId") ?? "");
  try {
    const identity = await administrator();
    const operation = String(form.get("operation"));
    if (!["submit", "approve", "mounts", "reset"].includes(operation)) throw new Error("Invalid action");
    const text = (key: string) => String(form.get(key) ?? "").trim();
    let payload: Record<string, unknown>;
    if (operation === "submit") {
      const anchors: Record<string, unknown> = {};
      for (const line of text("anchors").split(/\r?\n/).filter(Boolean)) {
        const [name, ...values] = line.split(",").map(value => value.trim());
        if (!name || values.length !== 7 || name in anchors || values.some(value => value === "" || !Number.isFinite(Number(value)))) throw new Error("Invalid point");
        anchors[name] = { position_mm: values.slice(0, 3).map(Number), quaternion: values.slice(3).map(Number) };
      }
      payload = {
        id: text("jobId") || randomUUID(), tenant_id: identity.tenantId, product_id: text("product"),
        source: text("source"), inputs: text("files").split(/\r?\n/).map(name => name.trim()).filter(Boolean)
          .map(name => `${identity.tenantId}/uploads/${name}`),
        dimensions_mm: ["width", "height", "depth"].map(key => Number(text(key))),
        anchors,
        cost_cap_usd: Number(text("limit")), timeout_seconds: 900,
      };
    } else if (operation === "approve") {
      payload = { id: text("id"), review: {
        scale: form.get("scale") === "on", appearance: form.get("appearance") === "on",
        attachment_points: form.get("attachment_points") === "on",
      } };
    } else if (operation === "mounts") {
      payload = Object.fromEntries(["part", "centre", "centre_anchor", "part_anchor"].map(key => [key, text(key)]));
    } else { payload = { id: text("id") }; }
    await rigThreeDRequest(identity.tenantId, operation as RigThreeDOperation, payload, identity.userId);
    notice = operation;
    revalidatePath("/rig/admin/three-d");
  } catch { /* No exception bodies or submitted data are shown or logged. */ }
  // Keep the same submission id after an uncertain response so retry cannot
  // create a second conversion. The server rejects a changed input for that id.
  const retry = notice === "failed" && /^[0-9a-f-]{36}$/.test(jobId) ? `&job=${jobId}` : "";
  redirect(`/rig/admin/three-d?notice=${notice}${retry}`);
}
