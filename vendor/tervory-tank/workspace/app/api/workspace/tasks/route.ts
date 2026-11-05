import { NextResponse } from "next/server";
import { currentTenant } from "@/lib/tenant";
import { CommandRequestError, workspaceRequest } from "@/lib/tank";

export const runtime = "nodejs";

export async function GET() {
  try {
    const identity = await currentTenant();
    return NextResponse.json(await workspaceRequest(identity, "/tasks"));
  } catch (error) {
    return NextResponse.json({error: "The results could not be read."},
      {status: error instanceof CommandRequestError ? error.status : 502});
  }
}
