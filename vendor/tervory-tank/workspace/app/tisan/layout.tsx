import type { Metadata } from "next";
import "./tisan.css";

export const metadata: Metadata = { title: "Tisan · Tervory" };

export default function TisanLayout({ children }: { children: React.ReactNode }) {
  return children;
}
