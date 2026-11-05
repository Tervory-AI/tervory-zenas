import { VAULT_LINE } from "./records";

export type AskMessage = { role: "user" | "assistant"; content: string };

const GROUND =
  `You are Tank. Answer about this account only. ${VAULT_LINE} Do not reveal a key, a secret, a token, or another squad's vault. If asked for a key, say the keys stay in tank.vault and this page does not show them.`;

const KEY_LIKE = /\b(?:sk|pk|rk|ak|api)[_-][A-Za-z0-9_-]{8,}\b/gi;

export function hideKeys(text: string): string {
  return text.replace(KEY_LIKE, "[hidden]");
}

/** Attach Tank's standing rule to the latest question. Keys in the question are hidden first. */
export function groundAsk(messages: AskMessage[]): AskMessage[] {
  const safe = messages
    .filter((message) => (message.role === "user" || message.role === "assistant") && typeof message.content === "string")
    .slice(-12)
    .map((message) => ({ role: message.role, content: hideKeys(message.content) }));
  return safe.map((message, index) => {
    if (index !== safe.length - 1 || message.role !== "user") return message;
    return { role: "user", content: `${message.content}\n\n${GROUND}` };
  });
}
