/**
 * Plain-English names for the Market Lab feeds.
 * Ids and key names follow agents/market_lab/feeds (ML-1). Values are never stored here.
 *
 * Official Market Lab mark, used as-is. Light wordmark for the dark panel.
 */
export const MARKET_LAB_LOGO_SRC = "/brand/market-lab-on-dark.png";

export const WORKSPACE_HREF = "/";
export const HELP_HREF = "https://tervory.com";

export type Cadence = "daily" | "weekly" | "monthly" | "annual";
export type FeedKind = "copy" | "linked" | "fred";
export type LinkGroupId = "carrier" | "state" | "season" | "ports";

export type CatalogItem = {
  id: string;
  title: string;
  summary: string;
  sourceLabel: string;
  cadence: Cadence;
  keyName: string;
  kind: FeedKind;
  href?: string;
  linkLabel?: string;
  linkNote?: string;
  group?: LinkGroupId;
  short?: string;
  fredSeries?: string;
};

export const CADENCE_LABEL: Record<Cadence, string> = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
  annual: "Yearly",
};

export const COMMODITIES: { code: string; label: string }[] = [
  { code: "PCOPPUSDM", label: "Copper" },
  { code: "PZINCUSDM", label: "Zinc" },
  { code: "PLEADUSDM", label: "Lead" },
  { code: "PCOTTINDUSDM", label: "Cotton" },
];

export const LINK_GROUPS: { id: LinkGroupId; title: string; cadence: string; lead: string }[] = [
  { id: "carrier", title: "Carrier rules", cadence: "Checked weekly", lead: "What each carrier allows you to ship, and where." },
  { id: "state", title: "State pages", cadence: "Checked weekly", lead: "California guidance and the federal directory of state laws." },
  { id: "season", title: "Hunting seasons", cadence: "Checked weekly", lead: "Season dates by state, so you can plan stock ahead." },
  { id: "ports", title: "Other ports", cadence: "Linked monthly, not copied", lead: "" },
];

/** Wide cards, in this order, when the feed is on. */
export const FEATURED_IDS = ["eia_diesel", "federal_register"] as const;

export const CATALOG: CatalogItem[] = [
  {
    id: "sec_edgar",
    title: "SEC EDGAR",
    summary: "",
    sourceLabel: "SEC EDGAR",
    cadence: "daily",
    keyName: "",
    kind: "copy",
  },
  {
    id: "listing_counts",
    title: "Monthly licence listings",
    summary: "How many licence holders there are, by place and licence type. Counts only, no names.",
    sourceLabel: "Bureau of Alcohol, Tobacco, Firearms and Explosives",
    cadence: "monthly",
    keyName: "",
    kind: "copy",
  },
  {
    id: "check_counts",
    title: "Background-check counts",
    summary: "Monthly background checks by state and type. These are checks, not sales.",
    sourceLabel: "Federal Bureau of Investigation",
    cadence: "monthly",
    keyName: "",
    kind: "copy",
  },
  {
    id: "census_population",
    title: "Census population",
    summary: "Population by state and county, from the American Community Survey.",
    sourceLabel: "Census",
    cadence: "annual",
    keyName: "CENSUS_API_KEY",
    kind: "copy",
  },
  {
    id: "legiscan",
    title: "State bill tracking",
    summary: "Status changes on state bills about firearms and ammunition, from LegiScan.",
    sourceLabel: "LegiScan",
    cadence: "weekly",
    keyName: "LEGISCAN_API_KEY",
    kind: "copy",
  },
  {
    id: "federal_register",
    title: "Federal Register notices",
    summary: "New federal notices about firearms and shipping hazardous materials.",
    sourceLabel: "Federal Register",
    cadence: "weekly",
    keyName: "",
    kind: "copy",
  },
  {
    id: "ecfr",
    title: "Federal rules for shipping hazardous materials",
    summary: "Rules for identifying, packing, labelling and transporting hazardous materials.",
    sourceLabel: "Electronic Code of Federal Regulations",
    cadence: "monthly",
    keyName: "",
    kind: "copy",
  },
  {
    id: "census_trade",
    title: "Census trade",
    summary: "Imports and exports of cartridges, powders and primers, by partner country and port.",
    sourceLabel: "Census",
    cadence: "monthly",
    keyName: "CENSUS_API_KEY",
    kind: "copy",
  },
  {
    id: "eia_diesel",
    title: "U.S. diesel prices",
    summary: "Average U.S. price of diesel at the pump, each week. A guide to freight fuel costs.",
    sourceLabel: "U.S. Energy Information Administration",
    cadence: "weekly",
    keyName: "EIA_API_KEY",
    kind: "copy",
  },
  {
    id: "world_bank_commodities",
    title: "World commodity prices",
    summary: "Monthly world prices for copper, zinc, lead and cotton.",
    sourceLabel: "World Bank",
    cadence: "monthly",
    keyName: "",
    kind: "copy",
  },
  {
    id: "noaa_alerts",
    title: "Severe-weather alerts",
    summary: "Active National Weather Service alerts that could slow shipping.",
    sourceLabel: "National Weather Service",
    cadence: "daily",
    keyName: "",
    kind: "copy",
  },
  {
    id: "noaa_climate",
    title: "Monthly climate",
    summary: "Monthly temperature and precipitation by state.",
    sourceLabel: "National Oceanic and Atmospheric Administration",
    cadence: "monthly",
    keyName: "NOAA_TOKEN",
    kind: "copy",
  },
  {
    id: "port_los_angeles",
    title: "Port of Los Angeles container volume",
    summary: "Containers through the Port of Los Angeles each month, measured in 20-foot container units.",
    sourceLabel: "Port of LA",
    cadence: "monthly",
    keyName: "",
    kind: "copy",
  },
  {
    id: "bts_freight",
    title: "U.S. freight volumes — stored figures",
    summary: "Freight volume compared with the year 2000. A reading of 110 means 10% more freight than in 2000.",
    sourceLabel: "Bureau of Transportation Statistics",
    cadence: "monthly",
    keyName: "",
    kind: "copy",
  },
  {
    id: "licence_holders",
    title: "Paid hunting licence holders by state",
    summary: "Paid hunting licence holders in each state, once a year.",
    sourceLabel: "U.S. Fish and Wildlife Service",
    cadence: "annual",
    keyName: "",
    kind: "copy",
  },
  {
    id: "port_long_beach",
    title: "Port of Long Beach container statistics",
    summary: "Linked, not copied.",
    sourceLabel: "Port of Long Beach",
    cadence: "monthly",
    keyName: "",
    kind: "linked",
    group: "ports",
    href: "https://polb.com/business/port-statistics/",
    linkNote: "Opens polb.com",
  },
  {
    id: "port_nynj",
    title: "Port of New York and New Jersey loaded containers",
    summary: "",
    sourceLabel: "Port of New York and New Jersey",
    cadence: "annual",
    keyName: "",
    kind: "copy",
    href: "https://data.ny.gov/Transportation/Loaded-Containers-Monthly-Imports-and-Exports-Thro/629s-5a55",
    linkNote: "Opens data.ny.gov",
  },
  {
    id: "fred_tsifrght",
    title: "Freight volumes — Federal Reserve Bank of St. Louis",
    summary: "Open the publisher’s website to read these figures. They are not used in saved-feed answers.",
    sourceLabel: "FRED",
    cadence: "monthly",
    keyName: "FRED_API_KEY",
    kind: "fred",
    short: "Freight index",
    fredSeries: "TSIFRGHT",
    href: "https://fred.stlouisfed.org/series/TSIFRGHT",
  },
  {
    id: "fred_tsifrghtc",
    title: "Monthly change in freight volume",
    summary: "Open the publisher’s website to read these figures. They are not used in saved-feed answers.",
    sourceLabel: "FRED",
    cadence: "monthly",
    keyName: "FRED_API_KEY",
    kind: "fred",
    short: "Monthly change",
    fredSeries: "TSIFRGHTC",
    href: "https://fred.stlouisfed.org/series/TSIFRGHTC",
  },
  {
    id: "fred_pcoppusdm",
    title: "Copper price — Federal Reserve Bank of St. Louis",
    summary: "Open the publisher’s website to read these figures. They are not used in saved-feed answers.",
    sourceLabel: "FRED",
    cadence: "monthly",
    keyName: "FRED_API_KEY",
    kind: "fred",
    short: "Copper",
    fredSeries: "PCOPPUSDM",
    href: "https://fred.stlouisfed.org/series/PCOPPUSDM",
  },
  {
    id: "fred_pzincusdm",
    title: "Zinc price — Federal Reserve Bank of St. Louis",
    summary: "Open the publisher’s website to read these figures. They are not used in saved-feed answers.",
    sourceLabel: "FRED",
    cadence: "monthly",
    keyName: "FRED_API_KEY",
    kind: "fred",
    short: "Zinc",
    fredSeries: "PZINCUSDM",
    href: "https://fred.stlouisfed.org/series/PZINCUSDM",
  },
  {
    id: "fred_pleadusdm",
    title: "Lead price — Federal Reserve Bank of St. Louis",
    summary: "Open the publisher’s website to read these figures. They are not used in saved-feed answers.",
    sourceLabel: "FRED",
    cadence: "monthly",
    keyName: "FRED_API_KEY",
    kind: "fred",
    short: "Lead",
    fredSeries: "PLEADUSDM",
    href: "https://fred.stlouisfed.org/series/PLEADUSDM",
  },
  {
    id: "fred_pcottindusdm",
    title: "Cotton price — Federal Reserve Bank of St. Louis",
    summary: "Open the publisher’s website to read these figures. They are not used in saved-feed answers.",
    sourceLabel: "FRED",
    cadence: "monthly",
    keyName: "FRED_API_KEY",
    kind: "fred",
    short: "Cotton",
    fredSeries: "PCOTTINDUSDM",
    href: "https://fred.stlouisfed.org/series/PCOTTINDUSDM",
  },
  {
    id: "fred_gasdesw",
    title: "Diesel prices — Federal Reserve Bank of St. Louis",
    summary: "Open the publisher’s website to read these figures. They are not used in saved-feed answers.",
    sourceLabel: "FRED",
    cadence: "monthly",
    keyName: "FRED_API_KEY",
    kind: "fred",
    short: "Diesel",
    fredSeries: "GASDESW",
    href: "https://fred.stlouisfed.org/series/GASDESW",
  },
  {
    id: "ups_hazmat",
    title: "UPS hazardous materials",
    summary: "Linked, not copied.",
    sourceLabel: "UPS",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "carrier",
    href: "https://www.ups.com/us/en/support/shipping-support/shipping-special-care-regulated-items/hazardous-materials.page",
  },
  {
    id: "fedex_hazmat",
    title: "FedEx hazardous materials",
    summary: "Linked, not copied.",
    sourceLabel: "FedEx",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "carrier",
    href: "https://www.fedex.com/en-us/shipping/hazardous-materials.html",
  },
  {
    id: "usps_pub52",
    title: "U.S. Postal Service rules for hazardous and restricted mail",
    summary: "Linked, not copied.",
    sourceLabel: "USPS",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "carrier",
    href: "https://pe.usps.com/text/pub52/welcome.htm",
  },
  {
    id: "ups_firearms",
    title: "UPS firearms shipping rules",
    summary: "Linked, not copied.",
    sourceLabel: "UPS",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "carrier",
    href: "https://www.ups.com/us/en/support/shipping-support/shipping-special-care-regulated-items/prohibited-items/firearms.page",
  },
  {
    id: "ca_oag_firearms",
    title: "California Attorney General, firearms",
    summary: "Linked, not copied.",
    sourceLabel: "California Attorney General",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "state",
    href: "https://oag.ca.gov/firearms",
  },
  {
    id: "atf_state_ordinances",
    title: "State and local firearms laws",
    summary: "Linked, not copied.",
    sourceLabel: "Bureau of Alcohol, Tobacco, Firearms and Explosives",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "state",
    href: "https://www.atf.gov/firearms/state-laws-and-published-ordinances",
  },
  {
    id: "season_ca",
    title: "California hunting seasons",
    summary: "Linked, not copied.",
    sourceLabel: "California",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "season",
    linkLabel: "California",
    href: "https://wildlife.ca.gov/Hunting",
  },
  {
    id: "season_tx",
    title: "Texas hunting seasons",
    summary: "Linked, not copied.",
    sourceLabel: "Texas",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "season",
    linkLabel: "Texas",
    href: "https://tpwd.texas.gov/huntwild/hunt/",
  },
  {
    id: "season_fl",
    title: "Florida hunting seasons",
    summary: "Linked, not copied.",
    sourceLabel: "Florida",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "season",
    linkLabel: "Florida",
    href: "https://myfwc.com/hunting/",
  },
  {
    id: "season_pa",
    title: "Pennsylvania hunting seasons",
    summary: "Linked, not copied.",
    sourceLabel: "Pennsylvania",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "season",
    linkLabel: "Pennsylvania",
    href: "https://www.pgc.pa.gov/",
  },
  {
    id: "season_ny",
    title: "New York hunting seasons",
    summary: "Linked, not copied.",
    sourceLabel: "New York",
    cadence: "weekly",
    keyName: "",
    kind: "linked",
    group: "season",
    linkLabel: "New York",
    href: "https://www.dec.ny.gov/outdoor/hunting.html",
  },
];

export const KEY_NAMES = ["CENSUS_API_KEY", "LEGISCAN_API_KEY", "EIA_API_KEY", "NOAA_TOKEN", "FRED_API_KEY"] as const;
export type KeyName = (typeof KEY_NAMES)[number];

export type KeyFlags = Record<KeyName, boolean>;

export function emptyFlags(): KeyFlags {
  return {
    CENSUS_API_KEY: false,
    LEGISCAN_API_KEY: false,
    EIA_API_KEY: false,
    NOAA_TOKEN: false,
    FRED_API_KEY: false,
  };
}
