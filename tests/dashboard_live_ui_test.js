(async()=>{
 const results=[];
 const assert=(v,label)=>{if(!v)throw Error(label);results.push(label);};
 const settle=async()=>{for(let i=0;i<10;i++)await new Promise(r=>setTimeout(r,0));};
 const input=(id,value)=>{const e=$(id);e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));};
 const wait=ms=>new Promise(r=>setTimeout(r,ms));
 try{
  await settle();
  Object.assign(testState.status,{capture:'active',adapter:'native',workerRevision:0,recoil:{fresh:true,enabled:false,rapidFireEnabled:false,strengthPercent:0,runtimeRevision:0,profileRevision:0,sampleAgeMs:0}});
  await refresh();await settle();
  const calls=[],replies=[];
  window.testMutation=async(path,body)=>{
   if(path!=='live')return null;
   calls.push(structuredClone(body));
   return await new Promise(resolve=>replies.push(()=>{
    const saved=structuredClone(testState.boot.store);saved.revision++;saved.profiles[saved.selected]=body.profile;
    testState.boot.store=saved;testState.status.savedRevision=saved.revision;
    testState.status.workerRevision++;
    const p=body.profile;
    Object.assign(testState.status.recoil,{enabled:p.recoil?.enabled??false,strengthPercent:(p.recoil?.vertical??0)*100,
     rapidFireEnabled:p.rapid_fire?.enabled??false,profileRevision:testState.status.workerRevision});
    resolve({ok:true,json:async()=>({store:structuredClone(saved),status:structuredClone(testState.status)})});
   }));
  };
  input('recoil-vertical','');await wait(300);
  assert(calls.length===0&&$('recoil-vertical').value==='','incomplete numeric input is retained and never sent');
  input('recoil-vertical','-1');await wait(300);assert(calls.length===0,'negative recoil never sent');
  input('recoil-vertical','2');input('recoil-vertical','3');await wait(300);
  assert(calls.length===1&&calls[0].profile.recoil.vertical===.03,'debounce coalesces edits');
  assert(!busy&&!$('recoil-vertical').disabled&&liveState==='Применяется','editing remains available during live ACK');
  input('recoil-vertical','4');await wait(300);
  assert(calls.length===1,'updates are serialized');
  replies.shift()();await settle();
  assert($('recoil-vertical').value==='4'&&draft.recoil.vertical===.04&&liveState!=='Применено','old ACK preserves newer draft');
  await wait(300);assert(calls.length===2&&calls[1].profile.recoil.vertical===.04,'newest queued draft follows first ACK');
  replies.shift()();await settle();assert(liveState==='Применено'&&!dirty,'only acknowledged latest edit is applied');
  assert($('save').hidden&&$('apply').hidden,'manual save and apply hidden');
  // Runtime changes hydrate fields and persist through the same serialized path.
  Object.assign(testState.status.recoil,{enabled:true,strengthPercent:7.5,rapidFireEnabled:true,runtimeRevision:1});
  await refresh();await settle();
  assert($('recoil-enabled').checked&&$('rapid-fire-enabled').checked&&$('recoil-vertical').value==='7.5','fresh hotkeys hydrate fields');
  input('hip-gain','0.02');await wait(300);
  assert(calls.length===3&&calls[2].runtimeFields.length===0&&calls[2].profile.recoil.vertical===.075,'neighbor edit carries runtime without taking ownership');
  replies.shift()();await settle();
  // Field focus prevents asynchronous runtime text replacement.
  $('recoil-vertical').focus();$('recoil-vertical').value='7.1';
  testState.status.recoil.strengthPercent=9;testState.status.recoil.runtimeRevision++;
  await refresh();await settle();assert($('recoil-vertical').value==='7.1','poll preserves focused numeric text');
  $('recoil-vertical').blur();cancelLive();
  input('recoil-vertical','5');await mutate('release',{},'released');await wait(300);
  assert(calls.length===3&&liveState!=='Применено','Release cancels debounced update');
  await refresh();await settle();cancelLive();
  input('recoil-vertical','6');const flight=sendLive();await settle();
  assert(calls.length===4,'in-flight cancellation fixture');
  await mutate('release',{},'released');replies.shift()();await flight;await settle();
  assert(liveState!=='Применено'&&$('recoil-vertical').value==='6','late ACK after Release cannot overwrite draft or report applied');
  cancelLive();store=clone(testState.boot.store);staleStore=false;testState.status.capture='off';await refresh();await settle();
  input('recoil-vertical','8');const off=sendLive();await settle();replies.shift()();await off;await settle();
  assert(calls.length===5&&lastStatus.capture==='off','off update never arms capture');
  cancelLive();testState.status.connected=false;await refresh();await settle();
  input('recoil-vertical','9');await sendLive();assert(calls.length===5&&liveState==='Нет подключения','disconnect retains draft without reconnect');
  testState.status.connected=true;testState.status.capture='off';await refresh();await settle();
  window.testMutation=async(path)=>path==='live'?{ok:false,json:async()=>({error:'uncertain result'})}:null;
  input('recoil-vertical','10');await sendLive();await settle();await wait(300);
  assert(calls.length===5&&liveFailed&&liveState==='Ошибка','uncertain result is not blindly retried');
  cancelLive();window.dashboardTestResult={ok:true,results};
 }catch(e){window.dashboardTestResult={ok:false,error:e.stack,results};}
})();
