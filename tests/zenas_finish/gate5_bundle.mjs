// Bundle real components with the existing Next webpack and TypeScript toolchain.
import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
import path from 'node:path';
const root=process.cwd(),output=process.argv[2];
const require=createRequire(path.join(root,'workspace/package.json'));
const packaged=require('next/dist/compiled/webpack/webpack');packaged.init();
const actions=['addZenasConnection','addZenasLink','removeZenasConnection','removeZenasFile','removeZenasLink','removeZenasSecret','saveZenasKey','uploadZenasFiles'];
let entry=`
    import React, {useState} from 'react'; import {createRoot} from 'react-dom/client';
    import Signup from './workspace/app/sign-up/[[...sign-up]]/page';
    import {ZenasView} from './workspace/app/zenas/view';
    import {MatterBoard} from './workspace/app/matters/board';
    import {ReviewDesk} from './workspace/app/review/desk';
    import {FeedSwitch} from './workspace/app/zenas/feeds/board';
    import {MootCourt} from './workspace/app/zenas/moot-court';
    import {MootScreen} from './workspace/app/zenas/moot-screen';
    import {WatchPanel} from './workspace/app/moot/WatchPanel';
    import {DeadlinesPanel} from './workspace/app/zenas/deadlines-panel';
    import {offPanel} from './workspace/app/zenas/plugins';
    import {emptyConvert} from './workspace/app/api/zenas/feeds/public';
    import './workspace/app/zenas/zenas.css'; import './workspace/app/zenas/feeds/feeds.css';
    import './workspace/app/matters/matters.css'; import './workspace/app/review/review.css';
    const root=createRoot(document.getElementById('root'));
    window.renderScreen=(mode,props={})=>{
      const screens={signup:<Signup/>,view:<ZenasView plugins={offPanel('Sign in to use Plugins.')} workspaceName={null}/>,
        matters:<main className="zenas-page"><MatterBoard initial={[]} note="" {...props}/></main>,
        review:<main className="zenas-page"><ReviewDesk items={[]} note="" {...props}/></main>,
        feeds:<main className="zf"><FeedSwitch initialRegion="us-east" initialSources={[]} initialScores={[]} initialReview="off" initialConvert={emptyConvert('us-east')} note="" {...props}/></main>,
        moot:<main className="zn" style={{display:'block',overflow:'auto',padding:16}}><MootCourt/></main>,
        "moot-screen":<MootScreen {...props}/>,"moot-public":<WatchPanel {...props}/>,
        deadlines:<main className="zn"><DeadlinesPanel/></main>};
      root.render(screens[mode]);
    }; window.renderScreen(window.screenMode??'view',window.screenProps??{});
`;
entry=entry.replaceAll("from './workspace/", "from '"+root+"/workspace/").replaceAll("import './workspace/", "import '"+root+"/workspace/");
writeFileSync(path.join(output,'entry.tsx'),entry);
const fake={
 'next/link':'import React from "react";export default function Link({children,...props}){return <a {...props}>{children}</a>}',
 'next/navigation':'export const useRouter=()=>({refresh:()=>window.dispatchEvent(new Event("refresh"))});export const usePathname=()=>"/zenas";',
 'next-auth/react':'export const useSession=()=>({data:null,status:"unauthenticated"});export const signIn=async(...args)=>{window.signinRequest=args};export const signOut=async()=>{};export const SessionProvider=({children})=>children;',
 'actions':actions.map(name=>'export async function '+name+'(){return {ok:false,message:"Sign in to change Plugins."}}').join(';'),
};
const alias={'@':path.join(root,'workspace')};
for(const [name,code] of Object.entries(fake)){
 const file=path.join(output,name.replaceAll('/','-')+'.tsx');writeFileSync(file,code);
 alias[name==='actions'?path.join(root,'workspace/app/zenas/plugin-actions'):name]=file;
}
const config={mode:'production',entry:path.join(output,'entry.tsx'),output:{path:output,filename:'client.js'},
 resolve:{alias,extensions:['.ts','.tsx','.js'],modules:[path.join(root,'workspace/node_modules'),'node_modules']},
 module:{rules:[{test:/\.tsx?$/,use:[{loader:path.join(root,'tests/zenas_finish/gate5_loader.cjs')}]},{test:/\.css$/,type:'asset/source'}]},
 optimization:{minimize:false},performance:{hints:false}};
await new Promise((resolve,reject)=>packaged.webpack(config,(error,stats)=>{
 if(error||stats.hasErrors())reject(error??new Error(stats.toString({all:false,errors:true})));else resolve();
}));
const fs=require('node:fs');writeFileSync(path.join(output,'client.css'),['globals.css','zenas/zenas.css','zenas/feeds/feeds.css','matters/matters.css','review/review.css'].map(p=>fs.readFileSync(path.join(root,'workspace/app',p),'utf8')).join('\n'));
