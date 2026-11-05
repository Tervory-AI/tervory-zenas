"use server";

import { revalidatePath } from "next/cache";

/** Reads the page again. It does not start, retry or re-run any batch. */
export async function refreshBase(): Promise<void> {
  revalidatePath("/base");
}
