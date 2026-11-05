"""Run the real Market Lab components against fixture feeds in local Postgres.

Creates an isolated Next app under /tmp. Production authentication is unchanged.
Requires DATABASE_URL for a loopback *_test database and installed Workspace deps.
"""
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT / "workspace"
TENANT = "00000000-0000-0000-0000-000000000905"


def prepare(folder: Path) -> None:
    def write(name: str, text: str) -> None:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (folder / "node_modules").symlink_to(WORKSPACE / "node_modules", target_is_directory=True)
    (folder / "public").symlink_to(WORKSPACE / "public", target_is_directory=True)
    write("package.json", json.dumps({"private": True, "dependencies": json.loads((WORKSPACE / "package.json").read_text())["dependencies"]}))
    write("tsconfig.json", json.dumps({"compilerOptions": {"baseUrl": ".", "target": "ES2022", "module": "esnext", "moduleResolution": "bundler", "jsx": "preserve", "strict": True, "esModuleInterop": True, "skipLibCheck": True, "paths": {"@/*": [str(WORKSPACE / "*")]}}}))
    write("next.config.mjs", "export default {experimental:{externalDir:true},devIndicators:false,webpack(config){config.resolve.alias['@']=" + json.dumps(str(WORKSPACE)) + ";return config;}};")
    write("app/layout.tsx", f'''import {{jost, inter, mono}} from {json.dumps(str(WORKSPACE / 'app/fonts'))};
import {json.dumps(str(WORKSPACE / 'app/market-lab/market-lab.css'))};
export default function Layout({{children}}:{{children:React.ReactNode}}){{return <html lang="en" className={{`${{jost.variable}} ${{inter.variable}} ${{mono.variable}}`}}><body style={{{{margin:0}}}}><div style={{{{position:"fixed",right:8,bottom:8,zIndex:10000,padding:"8px 12px",background:"#001840",color:"white",font:"14px Arial"}}}}>local, fixture page</div>{{children}}</body></html>}}''')
    write("app/market-lab/page.tsx", f'''import {{MarketLabView}} from {json.dumps(str(WORKSPACE / 'app/market-lab/view'))};
import {{loadMarketLab}} from {json.dumps(str(WORKSPACE / 'app/market-lab/load'))};
import {{emptyFlags}} from {json.dumps(str(WORKSPACE / 'app/market-lab/catalog'))};
import {{offPanel}} from {json.dumps(str(WORKSPACE / 'app/market-lab/plugins'))};
import {{marketLabRequest}} from "@/lib/tank";
import {{revalidatePath}} from "next/cache";
export const dynamic="force-dynamic";
async function refresh(){{"use server";await marketLabRequest("{TENANT}","refresh");revalidatePath("/market-lab");}}
export default async function Page(){{return <MarketLabView model={{await loadMarketLab("{TENANT}")}} plugins={{offPanel("Plugins are outside this local fixture proof.")}} envKeys={{emptyFlags()}} refresh={{refresh}} workspaceName="Local fixture workspace" quarantine={{null}}/>;}}''')
    write("app/market-lab/ask/route.ts", f'''import {{NextResponse}} from "next/server";
import {{marketLabRequest}} from "@/lib/tank";
export async function POST(request:Request){{const body=await request.json();if(Object.keys(body).some(key=>key!=="question")||typeof body.question!=="string"||!body.question.trim()||body.question.length>2000)return NextResponse.json({{error:"A question is required"}},{{status:400}});return NextResponse.json(await marketLabRequest("{TENANT}","ask",{{question:body.question}}));}}''')
    write("app/market-lab/sources/[id]/page.tsx", '''import {StoredSourcePage} from "@/app/market-lab/source-page";
export const dynamic="force-dynamic";
export {sourceMetadata as metadata} from "@/app/market-lab/source-page";
export default async function Page({params,searchParams}:{params:Promise<{id:string}>,searchParams:Promise<{row?:string;page?:string;kind?:string}>}){const {id}=await params;const {row,page,kind}=await searchParams;return <StoredSourcePage tenantId="''' + TENANT + '''" id={id} row={row} page={page} kind={kind}/>;}''')
    write("app/market-lab/sources/[id]/not-found.tsx", 'export {default} from "@/app/market-lab/source-not-found";')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3046)
    parser.add_argument("--api-port", type=int, default=8046)
    parser.add_argument("--history", action="store_true", help="Use saved port-volume input with 67 yearly observations for pagination proof")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "agents/market_lab"))
    sys.path.insert(0, str(ROOT / "tank"))
    from fixture_proof.server import services
    with services(args.api_port, history=args.history) as env, tempfile.TemporaryDirectory(prefix="market-lab-fixture-") as raw:
        folder = Path(raw)
        prepare(folder)
        web = subprocess.Popen(["node", str(WORKSPACE / "node_modules/next/dist/bin/next"), "dev", "--hostname", "127.0.0.1", "--port", str(args.port)], cwd=folder, env=env)
        try:
            print("Local fixture page started through real Tank and room routes. Refresh proves queueing only; no worker or public feeds run. Ctrl-C stops all services.", flush=True)
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
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        main()
    except KeyboardInterrupt:
        pass
