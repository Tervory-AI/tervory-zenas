// Tank's own labels. No key, no secret, and no other squad's vault.

export const VAULT_LINE =
  "Tank keeps the platform keys. No agent can read another agent's vault or the platform keys.";

export type TankRecord = {
  id: string;
  title: string;
  detail: string;
  href?: string;
};

export const TANK_RECORDS: readonly TankRecord[] = [
  {
    id: "ledger",
    title: "Ledger",
    detail: "Calls and cost for this account.",
    href: "/ledger",
  },
  {
    id: "tasks",
    title: "Tasks",
    detail: "Work queued for this account.",
    href: "/tasks",
  },
  {
    id: "data",
    title: "Data",
    detail: "Records this account keeps.",
    href: "/data",
  },
  {
    id: "plugins",
    title: "Plugins",
    detail: "Tank keeps the platform keys. This page does not show them.",
  },
  {
    id: "vault",
    title: "tank.vault",
    detail: "Always on. Holds the platform keys. Squads cannot read it.",
  },
];

export function searchTank(query: string, records: readonly TankRecord[] = TANK_RECORDS): TankRecord[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  return records.filter((record) => `${record.title} ${record.detail}`.toLowerCase().includes(needle));
}
