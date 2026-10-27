'use strict';
window.testIntervals=[];window.testRequests=[];window.testFailure=false;window.testMutation=null;
window.setInterval=(fn,ms)=>{testIntervals.push({fn,ms});return testIntervals.length;};
window.confirm=()=>true;window.prompt=()=>null;
const NativeDate=Date;
window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[testClock]));}static now(){return testClock;}};
window.fetch=async(url,options={})=>{
 const path=String(url).replace('/api/','');
 if(path==='bootstrap')return window.testBootstrap?window.testBootstrap():{ok:true,json:async()=>structuredClone(testState.boot)};
 if(path==='status'){
  if(window.testStatus)return window.testStatus();
  if(testFailure)throw Error('offline fixture');
  return {ok:true,json:async()=>structuredClone(testState.status)};
 }
 if(options.method!=='POST')throw Error('Expected POST');
 const body=JSON.parse(options.body);testRequests.push({path,body,csrf:options.headers['X-CSRF-Token']});
 if(testMutation){const result=await testMutation(path,body);if(result)return result;}
 if(!['save','settings','select'].includes(path))throw Error('Unhandled mock command: '+path);
 const store=structuredClone(testState.boot.store);store.revision++;
 if(path==='save')store.profiles[store.selected]=structuredClone(body.profile);
 else if(path==='settings')store.gameSettings=structuredClone(body.gameSettings);
 else store.selected=body.id;
 testState.boot.store=store;testState.status.savedRevision=store.revision;testState.status.sampleSequence++;
 return {ok:true,json:async()=>({store:structuredClone(store)})};
};
