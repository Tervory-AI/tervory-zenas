/** Approved copy from the Rig agent frames. */

export const ACCOUNT_NAV = [
  { href: "https://app.tervory.com/", label: "Dashboard" },
  { href: "/matters", label: "Projects" },
  { href: "/chats", label: "Chats" },
] as const;

export const ACCOUNT_BOTTOM = [
  { href: "/settings", label: "Settings" },
  { href: "https://tervory.com", label: "Support" },
] as const;

export const TABS = [
  { id: "search", label: "Search" },
  { id: "overview", label: "Overview" },
  { id: "catalogues", label: "Catalogues" },
  { id: "fits", label: "Fits to review" },
  { id: "skins", label: "Skins" },
  { id: "progress", label: "Progress" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export type Part = {
  id: string;
  type: string;
  name: string;
  price: number;
  market: number | null;
};

export type Pistol = {
  id: string;
  name: string;
  price: number;
  market: number | null;
};

export type WaitingFit = {
  id: string;
  name: string;
  fits: string;
  why: string;
};

export const BUILT: { title: string; text: string }[] = [];
export const PLAN: { code: string; title: string; text: string; state: string; on: boolean }[] = [];
export const CHANGES: { id: string; title: string; when: string }[] = [];
export const SKIN_STEPS = [
  "Same search and fits as the main Rig.",
  "The dealer's own logo, colours and prices.",
  "Tervory's name and logo never show on the dealer's page.",
  "Built only after the main product is done.",
] as const;
