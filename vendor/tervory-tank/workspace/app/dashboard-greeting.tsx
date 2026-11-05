"use client";

import { useEffect, useState } from "react";

export function localGreeting(hour: number): string {
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

/** Only the browser knows the client's time zone. Keep the server render neutral. */
export function DashboardGreeting({ firstName }: { firstName?: string | null }) {
  const [greeting, setGreeting] = useState<string | null>(null);
  useEffect(() => {
    const update = () => setGreeting(localGreeting(new Date().getHours()));
    update();
    const timer = window.setInterval(update, 60000);
    return () => window.clearInterval(timer);
  }, []);
  return <h1>{greeting}{greeting && firstName ? ", " : ""}{firstName}</h1>;
}
