import { redirect } from "next/navigation";
import { triggerIdentity } from "@/lib/trigger";
import { activeTenant } from "@/lib/tenant";
import { isFounder } from "@/lib/admin";
import { commandRequest, commandTools, listRoster } from "@/lib/tank";
import { modelValue } from "./models";
import { presentThread } from "./presentation";
import type { Thread, ModelOption } from "./contract";
import { CommandWindow } from "./window";
export const dynamic = "force-dynamic";
export default async function ChatsPage() {
  const tenant = await triggerIdentity(await activeTenant());
  if (!tenant) redirect("/onboarding");
  const founder = isFounder(tenant.userId);
  let threads: Thread[] = []; let models: ModelOption[] = []; const errors: string[] = [];
  const [threadResult, modelResult, toolsResult] = await Promise.allSettled([commandRequest(tenant, "threads").then(response => response.json() as Promise<Thread[]>), founder ? listRoster() : Promise.resolve([]), founder ? Promise.resolve([]) : commandTools(tenant)]);
  if (modelResult.status === "fulfilled") models = modelResult.value.map((model, i) => ({ value: modelValue(model.name), label: founder ? model.name : `Option ${i + 1}`, inputPerM: model.inputPerM, outputPerM: model.outputPerM }));
  else errors.push("The available options could not be loaded. Auto is still selected.");
  if (threadResult.status === "fulfilled") threads = threadResult.value.map(thread => presentThread(thread));
  else errors.push("Your chats could not be loaded. Please try again.");
  if (toolsResult.status === "rejected") errors.push("Tool access could not be loaded. Chat is still available.");
  return <CommandWindow initialThreads={threads} initialTools={toolsResult.status === "fulfilled" ? toolsResult.value : []} models={models} founder={founder} account={tenant.orgName} initialError={errors.join(" ")} />;
}
