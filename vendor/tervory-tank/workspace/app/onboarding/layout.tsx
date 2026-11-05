import type { Metadata } from "next";
import { onboardingBody, onboardingHeading, onboardingMono } from "../fonts";
import "./onboarding.css";

export const metadata: Metadata = {
  title: "Get started · Tervory",
  description: "Create an organization, add a CSV, and start your first run.",
};

export default function OnboardingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={`${onboardingBody.variable} ${onboardingHeading.variable} ${onboardingMono.variable} onboard`} style={{ minHeight: "100vh" }}>
      {children}
    </div>
  );
}
