import { inter, jost, mono } from "../fonts";
import "./matters.css";

export default function MattersLayout({ children }: { children: React.ReactNode }) {
  return <div className={`zenas-page ${jost.variable} ${inter.variable} ${mono.variable}`}>{children}</div>;
}
