"""Local browser proof: real components/services; only account identity is supplied locally."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "agents/market_lab"), str(ROOT / "agents/_chassis")]
from fixture_proof.run import prepare, WORKSPACE
from warning_proof.services import services
from warning_proof.seed import OWNER, ONE, RESOLVED


def page_app(folder: Path) -> None:
    prepare(folder)

    def write(name: str, content: str) -> None:
        file = folder / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)

    write(
        "tenant.ts",
        'import {cookies} from "next/headers";\nexport async function localTenant(){const selection=(await cookies()).get("proof_case")?.value;return '
        + json.dumps({"one": ONE, "resolved": RESOLVED})
        + '[selection as "one"|"resolved"] ?? '
        + json.dumps(OWNER)
        + ";}",
    )
    write(
        "app/layout.tsx",
        """import {jost,inter,mono} from "@/app/fonts";
import "@/app/market-lab/market-lab.css";
export const metadata={title:"Market Lab · Tervory"};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="en" className={`${jost.variable} ${inter.variable} ${mono.variable}`}><body style={{margin:0}}>{children}</body></html>}""",
    )
    write(
        "app/market-lab/page.tsx",
        """import {MarketLabView} from "@/app/market-lab/view";
import {loadMarketLab} from "@/app/market-lab/load";
import {emptyFlags} from "@/app/market-lab/catalog";
import {offPanel} from "@/app/market-lab/plugins";
import {WarningSection} from "@/app/market-lab/warnings-server";
import {localTenant} from "../../tenant";
export const dynamic="force-dynamic";
async function refresh(){"use server";}
export default async function Page(){const tenant=await localTenant();return <MarketLabView model={await loadMarketLab(tenant)} plugins={offPanel("No plugins connected.")} envKeys={emptyFlags()} refresh={refresh} workspaceName="AmmoSync" quarantine={null} warnings={<WarningSection tenantId={tenant}/>}/>;}""",
    )
    write(
        "app/market-lab/sources/[id]/page.tsx",
        """import {StoredSourcePage} from "@/app/market-lab/source-page";
import {localTenant} from "../../../../tenant";
export const dynamic="force-dynamic";
export default async function Page({params,searchParams}:{params:Promise<{id:string}>,searchParams:Promise<{row?:string;page?:string;kind?:string}>}){const {id}=await params;const {row,page,kind}=await searchParams;return <StoredSourcePage tenantId={await localTenant()} id={id} row={row} page={page} kind={kind}/>;}""",
    )
    # The real client control and Tank transport are used. This local-only wrapper
    # supplies the account identity; it cannot change production authentication.
    write(
        "app/market-lab/warnings/feedback/route.ts",
        """import {NextResponse} from "next/server";
import {marketWarningRequest} from "@/lib/tank";
import {localTenant} from "../../../../tenant";
export async function POST(request:Request){const body=await request.json();return NextResponse.json(await marketWarningRequest(await localTenant(),"feedback",{...body,actor:"local-owner"}));}""",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3047)
    args = parser.parse_args()
    with (
        services() as env,
        tempfile.TemporaryDirectory(prefix="market-warning-page-") as raw,
    ):
        folder = Path(raw)
        page_app(folder)
        web = subprocess.Popen(
            [
                "node",
                str(WORKSPACE / "node_modules/next/dist/bin/next"),
                "dev",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
            cwd=folder,
            env=env,
        )
        try:
            web.wait()
        finally:
            if web.poll() is None:
                web.terminate()
                try:
                    web.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    web.kill()
                    web.wait()


if __name__ == "__main__":

    def stop(*_args):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        main()
    except KeyboardInterrupt:
        pass
