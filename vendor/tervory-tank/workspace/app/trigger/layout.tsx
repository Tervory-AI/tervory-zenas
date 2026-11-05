import TriggerShell from "./shell";
import "./trigger.css";
export default function Layout({ children }: { children: React.ReactNode }) {
  return <TriggerShell>{children}</TriggerShell>;
}
