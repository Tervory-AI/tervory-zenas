import type { Metadata } from "next";
import { inter, jost, mono } from "../fonts";
import "./prices.css";

export const metadata: Metadata = { title: "Prices · Tervory" };

export default function PricesLayout({ children }: { children: React.ReactNode }) {
  return <div className={`${jost.variable} ${inter.variable} ${mono.variable}`}>{children}</div>;
}
