(async()=>{
 const results=[];
 const assert=(v,label)=>{if(!v)throw Error(label);results.push(label);};
 const settle=async()=>{for(let i=0;i<10;i++)await new Promise(r=>setTimeout(r,0));};
 const wait=ms=>new Promise(r=>setTimeout(r,ms));
 const input=(id,value)=>{const el=$(id);el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));};
 const status=()=>({...testState.status,adapter:'native',connected:true,processAlive:true,capture:'off',stale:false,
  sampleAgeMs:0,serviceInstance:'instance-1',workerRevision:0,processId:'p',sessionId:'s',sessionGeneration:1,
  savedRevision:testState.boot.store.revision,recoil:{fresh:true,enabled:false,rapidFireEnabled:false,strengthPercent:0,runtimeRevision:0,profileRevision:0,sampleAgeMs:0}});
 const reset=()=>{
  cancelLive();clearTimeout(pollTimer);runtimeEdits.clear();liveFlight=null;dirty=settingsDirty=staleStore=busy=releaseBusy=failed=false;
  store=clone(testState.boot.store);draft=clone(store.profiles[store.selected]);settingsDraft=clone(store.gameSettings);
  remoteBoot={...testState.boot,store:clone(store)};testState.status=status();lastStatus=clone(testState.status);received=performance.now();
  window.testMutation=null;render();
 };
 const liveAck=(body,view=testState.status)=>{
  const saved=clone(testState.boot.store);saved.revision++;const candidate=clone(body.profile);
  if(view.capture==='active'){candidate.recoil={enabled:false,vertical:0,...candidate.recoil};candidate.rapid_fire={enabled:false,interval_ms:120,press_ms:40,...candidate.rapid_fire};}
  saved.profiles[saved.selected]=candidate;
  testState.boot.store=saved;testState.status={...view,savedRevision:saved.revision,
   recoil:{...view.recoil,enabled:candidate.recoil?.enabled??false,strengthPercent:(candidate.recoil?.vertical??0)*100,rapidFireEnabled:candidate.rapid_fire?.enabled??false}};
  return {ok:true,json:async()=>({store:clone(saved),status:clone(testState.status)})};
 };
 try{
  await settle();reset();
  let finish;testState.status.capture='active';lastStatus=clone(testState.status);
  window.testMutation=(path,body)=>path==='live'?new Promise(r=>{finish=()=>r(liveAck(body,{...status(),capture:'active'}));}):null;
  input('recoil-vertical','2');const flight=sendLive();await settle();const epoch=liveEpoch;
  testState.status.capture='unknown';await refresh();await settle();
  assert(liveEpoch===epoch&&liveFlight&&liveState!=='Применено','unknown passive poll retains pending ACK without success');
  finish();await flight;await settle();assert(liveState==='Применено','valid ACK after unknown poll succeeds');

  reset();let calls=[];window.testMutation=(path,body)=>path==='live'?(calls.push(clone(body)),liveAck(body)):null;
  input('hip-gain','0.021');const expectedRevision=store.revision+1;
  settingsDraft.actual.horizontal=71;settingsDirty=true;
  await mutate('settings',{gameSettings:clone(settingsDraft)});await wait(300);await settle();
  assert(calls.length===1&&calls[0].revision===expectedRevision&&calls[0].profile.hip.gain===.021,'checklist save requeues unsent profile with returned revision');

  reset();calls=[];window.testMutation=(path,body)=>path==='live'?(calls.push(path),liveAck(body)):null;
  testState.status.connected=false;lastStatus=clone(testState.status);input('hip-gain','0.022');await sendLive();
  assert(calls.length===0&&liveWaiting,'disconnected update remains known-unsent');
  testState.status.connected=true;await refresh();await wait(300);await settle();
  assert(calls.length===1&&!testRequests.slice(-1).some(x=>['arm','launch'].includes(x.path)),'connection restoration sends unsent update without arm or reconnect');
  reset();calls=[];window.testMutation=(path,body)=>path==='live'?(calls.push(path),liveAck(body)):null;
  failed=true;input('hip-gain','0.023');await sendLive();assert(!calls.length,'stale state does not submit');
  await refresh();await wait(300);await settle();assert(calls.length===1,'fresh status resumes unsent stale-state update');

  reset();calls=[];window.testMutation=path=>path==='live'?(calls.push(path),Promise.reject(Error('uncertain'))):null;
  input('hip-gain','0.024');await sendLive();await settle();assert(liveFailed,'submitted failure marked uncertain');
  testState.status.capture='unknown';testState.status.connected=null;await refresh();
  testState.status={...status(),capture:'active',recoil:{...status().recoil,enabled:true,rapidFireEnabled:true,strengthPercent:6}};
  await refresh();await wait(300);await settle();
  assert(calls.length===1&&liveFailed&&liveState==='Ошибка','restoration never retries uncertain submitted result');
  assert($('recoil-enabled').checked&&$('rapid-fire-enabled').checked&&$('recoil-vertical').value==='6','fresh hotkeys hydrate safe fields while preserving failed state');

  reset();testState.status.adapter='baseline';testState.status.connected=false;lastStatus=clone(testState.status);
  input('profile-name','offline saved');buttons();const before=testRequests.length;
  assert(!$('save').hidden&&!$('save').disabled,'offline baseline retains store-only Save');
  await $('save').onclick();await settle();
  assert(testRequests.slice(before).every(x=>x.path==='save')&&liveState==='Сохранено без применения','offline save is distinct and does not apply');

  reset();calls=[];window.testMutation=(path,body)=>path==='live'?(calls.push(path),liveAck(body)):null;
  input('deadzone','');await wait(300);await settle();
  assert(!calls.length&&draft.radial_deadzone===null&&$('deadzone').value==='','empty explicit deadzone never reaches backend');

  reset();calls=[];window.testMutation=(path,body)=>path==='live'?(calls.push(path),liveAck(body)):null;
  input('recoil-vertical','');input('rapid-fire-interval','');
  $('recoil-mode').value='pulse';$('recoil-mode').dispatchEvent(new Event('change',{bubbles:true}));
  input('recoil-peak','');renderAim();await wait(300);await settle();
  assert(!calls.length&&$('recoil-vertical').value===''&&$('recoil-peak').value===''&&$('rapid-fire-interval').value==='',
   'adjacent mode and binding rerenders preserve incomplete numeric values');

  reset();window.testMutation=(path,body)=>path==='live'?liveAck(body):null;
  const name=$('profile-name');name.focus();input('profile-name','renamed live');await sendLive();await settle();
  assert($('profile-select').selectedOptions[0].textContent==='renamed live'&&$('profile-name')===name,'live rename updates option without rebuilding focused editor');

  reset();window.testMutation=(path,body)=>path==='live'?(calls.push(clone(body)),liveAck(body)):null;calls=[];
  for(let i=0;i<19;i++)testState.boot.store.profiles['extra-'+i]=clone(draft);
  remoteBoot={...testState.boot,store:clone(testState.boot.store)};staleStore=true;
  draft.name='resolved limit';settingsDraft.actual.horizontal=72;dirty=settingsDirty=true;
  await reconcileExisting(store.selected);await wait(300);await settle();
  assert(calls.length===1&&calls[0].profile.name==='resolved limit'&&calls[0].revision===store.revision-1,'20-profile reconciliation schedules accepted profile using confirmed revision');
  assert(!dirty&&!staleStore,'reconciliation does not restore obsolete edits after live ACK');
  cancelLive();window.dashboardTestResult={ok:true,results};
 }catch(e){window.dashboardTestResult={ok:false,error:e.stack,results};}
})();
