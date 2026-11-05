import type { Metadata } from "next";
import "./rig.css";

export const metadata: Metadata = { title: "Rig · Tervory" };

export default function RigLayout({ children }: { children: React.ReactNode }) {
  return children;
}
