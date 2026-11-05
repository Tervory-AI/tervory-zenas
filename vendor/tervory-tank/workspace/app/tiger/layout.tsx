import type { Metadata } from "next";
import "./tiger.css";

export const metadata: Metadata = { title: "Tiger · Tervory" };

export default function TigerLayout({ children }: { children: React.ReactNode }) {
  return children;
}
