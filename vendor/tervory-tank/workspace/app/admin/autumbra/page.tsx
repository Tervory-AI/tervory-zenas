import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { isFounder } from "@/lib/admin";
import { ghostPanel } from "@/lib/tank";
import { look, resolve, shieldAction } from "./actions";
import { AutumbraConsole } from "./panel";
import { shieldBoard, type ShieldBoard } from "./shields-data";
import { ShieldsTile } from "./tile";
import { isUsableGhostPanel } from "./watch-data";

export const dynamic = "force-dynamic";

export default async function AutumbraPage() {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) notFound();
  const board: ShieldBoard | null = await shieldBoard(userId).catch(() => null);
  const tile = <ShieldsTile board={board} act={shieldAction} />;
  const response = await ghostPanel(userId).catch(() => null);
  const data = isUsableGhostPanel(response) ? response : null;
  return <AutumbraConsole data={data} look={look} resolve={resolve} tile={tile} />;
}
