(async()=>{
 const results=[],assert=(v,label)=>{if(!v)throw Error(label);results.push(label);};
 const settle=async()=>{for(let i=0;i<10;i++)await new Promise(r=>setTimeout(r,0));};
 const input=(id,value)=>{const e=document.querySelector(id);e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));};
 const response=value=>({ok:true,json:async()=>clone(value)});
 let original;
 async function reset(count=1){
  await settle();if(refreshFlight)await refreshFlight;clearTimeout(pollTimer);++intent;++bootstrapSequence;
  window.testMutation=null;window.testBootstrap=null;window.testPending=null;window.testFailure=false;
  const next=clone(original);for(let i=1;i<count;i++)next.profiles['p'+i]={...clone(next.profiles.arc),name:'Profile '+i};
  testState.boot.store=clone(next);store=clone(next);remoteBoot=clone(testState.boot);boot=clone(testState.boot);
  draft=clone(store.profiles.arc);settingsDraft=clone(store.gameSettings);dirty=settingsDirty=staleStore=busy=releaseBusy=false;
  lastStatus={...testState.status,savedRevision:next.revision,selectedId:'arc',capture:'active',processId:'process',sessionId:'session',recoveryRequired:false,stale:false};
  testState.status=clone(lastStatus);received=performance.now();failed=false;render();testRequests.length=0;error('');$('notice').hidden=true;
 }
 function remoteSelection(){const next=clone(testState.boot.store);next.revision++;next.selected='other';next.profiles.other={...clone(next.profiles.arc),name:'Recovered profile'};next.gameSettings.actual.horizontal=73;return next;}
 const bundle=()=>({version:1,sourceId:store.selected,sourceRevision:store.revision,profile:clone(draft),gameSettings:clone(settingsDraft)});
 const file=value=>({target:{files:[{size:5000,text:async()=>JSON.stringify(value)}],value:'bundle'}});
 try{
  await settle();original=clone(testState.boot.store);await reset();
  let next=remoteSelection();testState.boot.store=clone(next);window.testMutation=path=>path==='recover'?response({store:next}):null;
  await mutate('recover',{});
  assert(store.selected==='other'&&draft.name==='Recovered profile'&&settingsDraft.actual.horizontal===73,'clean recovery reconciles both drafts and selected profile');
  assert(!$('notice').textContent.includes('Захват выключен')&&$('notice').textContent.includes('не подтверждено'),'store-only recovery never claims input shutdown');
  await reset();input('#profile-name','Keep profile');input('[data-setting="horizontal"]','71');
  const kept=JSON.stringify([draft,settingsDraft]);next=remoteSelection();testState.boot.store=clone(next);
  window.testMutation=path=>path==='recover'?response({store:next,status:{processId:'process',sessionId:'session',releaseConfirmed:true,releaseProof:'native-cleanup',capture:'off',cleanupFinished:true}}):null;
  await mutate('recover',{});
  assert(store.selected==='arc'&&staleStore&&JSON.stringify([draft,settingsDraft])===kept&&remoteBoot.store.selected==='other','dirty recovery retains original profile ID and both drafts with conflict');
  assert($('notice').textContent.includes('Захват выключен'),'matching release acknowledgement permits shutdown message');
  await reset();input('#profile-name','Saved profile');input('[data-setting="horizontal"]','72');
  let finish;window.testMutation=(path,body)=>path==='save'?new Promise(r=>{finish=()=>{const saved=clone(testState.boot.store);saved.revision++;saved.profiles.arc=clone(body.profile);testState.boot.store=saved;r(response({store:saved}));};}):null;
  const saving=mutate('save',{profile:clone(draft)});await settle();let read=false;
  await $('drafts-file').onchange({target:{files:[{size:100,text:async()=>{read=true;return JSON.stringify(bundle());}}],value:'file'}});
  assert(!read&&busy,'restore cannot start during Save');finish();await saving;
  assert(draft.name==='Saved profile'&&settingsDraft.actual.horizontal===72&&settingsDirty,'rejected restore leaves both working drafts intact');
  await reset();input('#profile-name','Save supersedes restore');input('[data-setting="horizontal"]','74');
  let fileDone;const imported=bundle();imported.profile.name='Must not overwrite';
  const restoring=$('drafts-file').onchange({target:{files:[{size:100,text:()=>new Promise(r=>{fileDone=()=>r(JSON.stringify(imported));})}],value:'file'}});
  const saveDuringRead=mutate('save',{profile:clone(draft)});fileDone();await restoring;await saveDuringRead;
  assert(draft.name==='Save supersedes restore'&&settingsDraft.actual.horizontal===74,'Save supersedes delayed file read without replacing either draft');
  await reset();input('#profile-name','Save during bootstrap');let bootDone;
  window.testBootstrap=()=>new Promise(r=>{bootDone=()=>r(response(testState.boot));});
  const delayedRestore=$('drafts-file').onchange(file(bundle()));await settle();
  const bootstrapSave=mutate('save',{profile:clone(draft)});bootDone();await delayedRestore;await bootstrapSave;window.testBootstrap=null;
  assert(draft.name==='Save during bootstrap'&&!staleStore,'Save supersedes restore during bootstrap completion');
  await reset();const stable=JSON.stringify([draft,settingsDraft,store]);
  for(const mutateBad of [b=>b.gameSettings.actual=1,b=>b.profile.binds=[],b=>b.profile.hip=[],b=>b.profile.ads.y={},b=>b.profile.calibration=4,b=>b.gameSettings.actual.response={},b=>b.gameSettings.actual.horizontal='wrong']){
   const bad=bundle();mutateBad(bad);await $('drafts-file').onchange(file(bad));
   assert(JSON.stringify([draft,settingsDraft,store])===stable,'malformed bundle preserves working editor');
  }
  const incomplete=bundle();incomplete.profile.hip.gain=null;incomplete.profile.dpi=null;incomplete.gameSettings.actual.horizontal=null;
  await $('drafts-file').onchange(file(incomplete));
  assert(draft.hip.gain===null&&settingsDraft.actual.horizontal===null&&dirty&&settingsDirty,'legitimate incomplete numeric drafts remain editable');
  async function prepareFull(){
   await reset(20);input('#profile-name','Twenty-profile draft');input('[data-setting="horizontal"]','79');
   testState.boot.store.selected='p19';testState.boot.store.revision++;remoteBoot=clone(testState.boot);staleStore=true;buttons();
   window.prompt=()=> 'p5';window.confirm=()=>true;
  }
  await prepareFull();await $('resolve-drafts').onclick();
  assert(Object.keys(store.profiles).length===20&&store.selected==='p5'&&draft.name==='Twenty-profile draft'&&settingsDraft.actual.horizontal===79&&!staleStore,'20-profile conflict reconciles into confirmed existing profile');
  assert(testRequests.map(r=>r.path).join(',')==='select,save,settings','20-profile reconciliation uses existing revision-checked APIs');
  await prepareFull();const beforePartial=JSON.stringify([draft,settingsDraft]);
  window.testMutation=path=>path==='settings'?{ok:false,json:async()=>({error:'settings failure'})}:null;
  await $('resolve-drafts').onclick();
  assert(staleStore&&store.selected==='arc'&&JSON.stringify([draft,settingsDraft])===beforePartial,'partial reconciliation keeps both drafts and original identity');
  window.testMutation=null;await $('resolve-drafts').onclick();
  assert(!staleStore&&store.selected==='p5'&&settingsDraft.actual.horizontal===79&&Object.keys(store.profiles).length===20,'partial 20-profile reconciliation can be retried to completion');
  for(const change of ['selection','revision','draft','bootstrap']){
   await reset(2);input('#profile-name','Original import draft');input('[data-setting="horizontal"]','81');
   const importedProfile={...clone(draft),name:'Imported into old target'};let readDone;
   const pendingImport=$('import-file').onchange({target:{files:[{size:1000,text:()=>new Promise(r=>{readDone=()=>r(JSON.stringify(importedProfile));})}],value:'profile'}});
   if(change==='selection'){await mutate('select',{id:'p1'});input('#profile-name','New selected draft');}
   if(change==='revision')await mutate('save',{profile:clone(draft)});
   if(change==='draft')input('#profile-name','Edited while file loads');
   if(change==='bootstrap'){testState.boot.store.revision++;testState.boot.store.selected='p1';await renewSession();}
   const before=JSON.stringify([draft,settingsDraft]),selected=store.selected;
   readDone();await pendingImport;
   assert(!testRequests.some(r=>r.path==='import')&&JSON.stringify([draft,settingsDraft])===before&&store.selected===selected,'superseded ordinary import preserves drafts: '+change);
  }
  await reset();input('[data-setting="horizontal"]','82');
  const ordinary={...clone(draft),name:'Ordinary import'};
  await $('import-file').onchange({target:{files:[{size:1000,text:async()=>JSON.stringify(ordinary)}],value:'profile'}});
  assert(draft.name==='Ordinary import'&&settingsDraft.actual.horizontal===82&&settingsDirty,'unchanged ordinary import still works and preserves checklist draft');
  window.dashboardTestResult={ok:true,results};
 }catch(e){window.dashboardTestResult={ok:false,error:e.stack,results};}
})();
