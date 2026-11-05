import localFont from "next/font/local";

export const jost = localFont({
  src: "./Jost-300-latin.woff2",
  weight: "300",
  style: "normal",
  variable: "--font-jost",
  display: "swap",
});

export const inter = localFont({
  src: "./Inter-latin.woff2",
  weight: "100 900",
  style: "normal",
  variable: "--font-inter",
  display: "swap",
});

export const mono = localFont({
  src: [
    { path: "./JetBrainsMono-latin.woff2", weight: "400", style: "normal" },
    { path: "./JetBrainsMono-latin.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-mono",
  display: "swap",
});

export const onboardingBody = localFont({
  src: "./Inter-latin.woff2",
  weight: "100 900",
  style: "normal",
  variable: "--onb-body",
});

export const onboardingHeading = localFont({
  src: "./Jost-300-latin.woff2",
  weight: "300",
  style: "normal",
  variable: "--onb-heading",
});

export const onboardingMono = localFont({
  src: "./JetBrainsMono-latin.woff2",
  weight: "400",
  style: "normal",
  variable: "--onb-mono",
});
