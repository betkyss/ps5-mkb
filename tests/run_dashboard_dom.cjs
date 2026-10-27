'use strict';
const fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('./dashboard-ui/node_modules/jsdom');
const root=path.resolve(__dirname,'..');
const preset=JSON.parse(fs.readFileSync(path.join(root,'tests/profile-fixture.json'),'utf8'));
const fixture=JSON.parse(require('node:child_process').execFileSync('python3',['-c',
 `import json;from dashboard.schema import CODES,RESERVED,ACTIONS;from dashboard.store import RECOMMENDED;print(json.dumps(dict(codes=CODES,reserved=sorted(RESERVED),actions=ACTIONS,recommended=RECOMMENDED)))`],{cwd:root}));
fixture.csrf='test';fixture.serviceInstance='instance-1';fixture.store={revision:1,selected:'arc',profiles:{arc:preset},applied:null,gameSettings:{actual:fixture.recommended,confirmedAt:''}};
async function run(directory,test,preFile='dashboard_isolated_ui_pre.js'){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'dashboard',directory,'index.html'),'utf8'),{url:'http://localhost:9876',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;w.isSecureContext=false;w.structuredClone=structuredClone;w.AbortSignal=AbortSignal;w.testClock=100000;
 Object.defineProperty(w.performance,'now',{value:()=>w.testClock});
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
 w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
 w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};
 w.testState={boot:structuredClone(fixture),status:{serviceInstance:'instance-1',sampleSequence:1,sampleAgeMs:0,savedRevision:1,selectedId:'arc',processAlive:true,connected:true,capture:'off',canApply:true,appliedKnown:false}};
 let production=fs.readFileSync(path.join(root,'dashboard',directory,'app.js'),'utf8');
 if(process.argv.includes('--mutation'))production=production.replace("$('release').disabled=releaseBusy;","$('release').disabled=busy||releaseBusy||lastStatus?.busy;");
 const pre=fs.readFileSync(path.join(root,'tests',preFile),'utf8').replace('__FIXTURE__',JSON.stringify(w.testState));
 try{
  w.eval(pre+'\n'+production+'\n'+fs.readFileSync(path.join(root,'tests',test),'utf8'));
  const end=Date.now()+15000;
  while(!w.dashboardTestResult&&Date.now()<end)await new Promise(r=>setTimeout(r,5));
  const result=w.dashboardTestResult;
  if(!result?.ok)throw Error(JSON.stringify(result||{error:'DOM runner timeout'}));
  console.log('PASS '+directory+': '+result.results.length+' DOM assertions');
 }finally{w.close();}
}
(async()=>{
 const specific=process.argv.find(arg=>arg.startsWith('--test='));
 if(specific){await run('web-isolated',specific.slice(7));return;}
 const mutation=process.argv.includes('--mutation'),isolatedOnly=process.argv.includes('--only-isolated');

 await run('web-isolated','dashboard_isolated_ui_test.js');
 if(!mutation){
  await run('web-isolated','dashboard_review_ui_test.js');
  await run('web-isolated','dashboard_live_ui_test.js');
  await run('web-isolated','dashboard_live_review_ui_test.js');
  await run('web-isolated','dashboard_audio_ui_test.js');

  await run('web-isolated','dashboard_ps5_ui_test.js','dashboard_ps5_ui_pre.js');
  await run('web-isolated','dashboard_design_ui_test.js');
 }
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
