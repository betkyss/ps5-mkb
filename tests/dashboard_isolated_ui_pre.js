'use strict';
window.testIntervals=[];window.testRequests=[];window.testFailure=false;window.testPending=null;
window.setInterval=(fn,ms)=>{testIntervals.push({fn,ms});return testIntervals.length;};
window.prompt=()=> 'HTTP copy';window.confirm=()=>true;
window.fetch=async(url,options={})=>{
 const path=String(url).replace('/api/','');
 if(path==='status'){
  if(testPending)return testPending;
  if(testFailure)throw Error('offline fixture');
  return {ok:true,json:async()=>structuredClone(testState.status)};
 }
 if(path==='bootstrap'&&window.testBootstrap)return window.testBootstrap();
 if(path==='bootstrap')return {ok:true,json:async()=>structuredClone(testState.boot)};
 const body=JSON.parse(options.body);testRequests.push({path,body});
 if(window.testMutation){const result=await window.testMutation(path,body);if(result)return result;}
 if(path==='release')return {ok:true,json:async()=>({status:{capture:'off',cleanupFinished:true}})};
 if(!['settings','save','import','select','create','fork-drafts'].includes(path))throw Error('Forbidden live command: '+path);
 if(body.revision!==testState.boot.store.revision)return {ok:false,json:async()=>({error:'revision conflict'})};
 if(['create','fork-drafts'].includes(path)&&Object.keys(testState.boot.store.profiles).length>=20)return {ok:false,json:async()=>({error:'profile limit'})};
 const saved=structuredClone(testState.boot.store);saved.revision++;
 if(path==='settings')saved.gameSettings=body.gameSettings;
 if(path==='save')saved.profiles[saved.selected]=body.profile;
 if(path==='import')saved.profiles[saved.selected]=JSON.parse(body.profileText);
 if(path==='select')saved.selected=body.id;
 if(path==='fork-drafts'){saved.selected=body.id;saved.profiles[body.id]=body.profile;saved.gameSettings=body.gameSettings;}
 if(path==='create'){saved.selected=body.id;saved.profiles[body.id]=body.profile;}
 testState.boot.store=saved;testState.status.savedRevision=saved.revision;testState.status.selectedId=saved.selected;testState.status.sampleSequence++;
 return {ok:true,json:async()=>({store:structuredClone(saved)})};
};
