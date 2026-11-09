import { notFound } from "next/navigation";
import { readTrigger } from "../actions";
import { CRM, Resource } from "../copy";
import CRMView from "../records";
import ConsoleView from "../view";
export const dynamic = "force-dynamic";
export default async function Page({
  params,
}: {
  params: Promise<{ segments?: string[] }>;
}) {
  const { segments: parts = ["crm", "leads"] } = await params;
  const [area, section, id] = parts;
  if (
    parts.length > 3 ||
    (id &&
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
        id,
      ))
  )
    notFound();
  const configured = () => process.env.TANK_API_KEY ? null : <p role="status">Missing setting: TANK_API_KEY</p>;
  if (area === "crm" && Object.hasOwn(CRM, section)) {
    const missing = configured(); if (missing) return missing;
    const resource = section as Resource;
    return (
      <CRMView
        resource={resource}
        detail={!!id}
        data={await readTrigger("crm", resource, id)}
      />
    );
  }
  if (
    (area === "marketing" &&
      ["channels", "posts", "composer", "calendar", "reviews"].includes(
        section,
      )) ||
    (area === "bot" && ["inbox", "settings"].includes(section))
  ) {
    const missing = configured(); if (missing) return missing;
    return (
      <ConsoleView
        area={area}
        section={section}
        id={id}
        data={await readTrigger(area, section, id)}
      />
    );
  }
  notFound();
}
