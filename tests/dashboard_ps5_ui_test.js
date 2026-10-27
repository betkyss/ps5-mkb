'use strict';
(async()=>{
 const results=[],assert=(value,label)=>{if(!value)throw Error(label);results.push(label);};
 const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setTimeout(r,0));};
 const select=(id,value)=>{const el=$(id);el.value=String(value);el.dispatchEvent(new Event('change',{bubbles:true}));};
 const key=(code,target=document)=>{const e=new KeyboardEvent('keydown',{code,key:code, bubbles:true,cancelable:true});target.dispatchEvent(e);return e;};
 const pointer=(target,button=0)=>target.dispatchEvent(new MouseEvent('pointerdown',{button,bubbles:true,cancelable:true}));
 const choose=action=>document.querySelector('#assign-actions [data-action="'+action+'"]').click();
 const response=value=>({ok:true,json:async()=>structuredClone(value)});
 const poll=async()=>{await settle();await refresh();await settle();};
 try{
  assert($('new-binding').disabled,'new assignment disabled before bootstrap');
  beginAssign('Cross');assert(!$('assign-dialog').open,'assignment is safe before bootstrap');
  await poll();
  assert(!$('safety-dialog')&&!$('safe-check'),'control checkbox and confirmation dialog removed');
  assert($('settings-confirm'),'in-game verification checkbox retained');
  assert(document.querySelectorAll('.action-card').length===testState.boot.actions.length,'all contract actions rendered');
  const visibleControls=()=>Array.from(document.querySelectorAll('.controls button')).filter(button=>!button.hidden).map(button=>button.id);
  assert(!$('connection-details').open&&$('connection-details').contains($('feedback-state'))&&$('connection-details').contains($('refresh')),'diagnostics and manual refresh are collapsed');
  const previousAdapter=testState.status.adapter;
  testState.status.adapter='baseline';testState.status.processAlive=true;testState.status.capture='unknown';await poll();
  assert(!$('arm').hidden&&$('connection-details').contains($('arm'))&&!$('release').hidden,'legacy baseline retains manual enable in details and primary release');
  testState.status.adapter=previousAdapter;
  testState.status.processAlive=true;testState.status.capture='off';await poll();
  assert(visibleControls().join(',')==='arm'&&$('arm').parentElement===$('main-controls'),'native enable returns to main controls');
  testState.status.capture='active';await poll();
  assert(visibleControls().join(',')==='release','only gamepad switch is shown when input is active');
  testState.status.processAlive=false;testState.status.capture='unknown';await poll();
  assert(visibleControls().join(',')==='launch','only connect is shown when client is absent');
  if($('recover')){
   testState.status.recoveryRequired=true;await poll();
   assert(visibleControls().join(',')==='recover','recovery remains reachable with absent client and unknown capture');
   failed=true;renderPending();assert(visibleControls().join(',')==='recover','stale native state does not hide server recovery');failed=false;
   testState.status.recoveryRequired=false;
  }
  testState.status.processAlive=true;testState.status.capture='off';await poll();

  for(const action of testState.boot.actions){
   const glyph=document.querySelector('.action-card[data-action="'+action+'"] .controller-glyph');
   assert(glyph&&glyph.getAttribute('viewBox')==='0 0 40 40'&&glyph.getAttribute('aria-label')&&glyph.querySelector('title'),'accessible vector glyph: '+action);
  }
  assert(actionLabel('W').includes('Левый стик')&&actionLabel('LookUp').includes('Правый стик')&&actionLabel('Up').includes('D-pad'),'stick and D-pad directions remain distinct');
  for(const button of document.querySelectorAll('.nav[data-tab]'))assert(button.querySelector('svg[aria-hidden="true"]')&&button.querySelector('span').textContent,'navigation icon retains visible name');
  const designs=['W','LookUp','Up','L3','R3','L1','L2','R1','R2','Touchpad','Options','Share','PS'].map(action=>{
   const svg=controllerGlyph(action).cloneNode(true);svg.removeChild(svg.querySelector('title'));return svg.innerHTML;
  });
  assert(new Set(designs).size===designs.length,'sticks, D-pad, bumpers, triggers and system buttons have distinct symbols');
  const geometry=action=>{const svg=controllerGlyph(action);svg.querySelectorAll('text,title').forEach(e=>e.remove());return svg.innerHTML;};
  assert(geometry('W')!==geometry('Up')&&geometry('W')!==geometry('L3'),'stick movement, D-pad and stick click have different shapes');
  assert(geometry('L1')!==geometry('L2')&&geometry('R1')!==geometry('R2'),'bumpers and triggers differ without text');
  assert(new Set(['Up','Down','Left','Right'].map(geometry)).size===4,'each D-pad direction is visually distinct');

  for(const [source,action] of Object.entries(draft.binds)){
   const cap=document.querySelector('.keycap[data-source="'+source+'"]');
   assert(cap.querySelector('.key-action')?.textContent&&cap.getAttribute('aria-label').includes(actionMeaning(action)),'bound key has visible action and full accessible meaning: '+source);
  }
  assert(actionMeaning('Down').includes('голос')&&actionMeaning('Right').includes('плеча'),'current voice and shoulder bindings have clear names');

  for(const [source,action] of Object.entries(draft.binds)){
   const bind=document.querySelector('.bind[data-source="'+source+'"]');
   assert(bind?.querySelector('svg').dataset.action===action,'saved source displays controller action: '+source);
  }
  let finish;
  for(const command of ['arm','launch','release']){
   testState.status.capture='off';testState.status.processAlive=command!=='launch';await poll();
   window.testMutation=path=>path===command?new Promise(resolve=>{finish=resolve;}):null;
   const count=testRequests.length;$('notice').hidden=false;$('notice').textContent='old success';
   assert(!$(command).disabled,'fresh control enabled: '+command);
   $(command).click();await settle();
   assert(testRequests.length===count+1&&testRequests.at(-1).path===command,'one click sends one mock POST: '+command);
   assert($(command).disabled&&$(command).getAttribute('aria-busy')==='true'&&!$('control-progress').hidden,'pending is visible and disables duplicate: '+command);
   assert(visibleControls().join(',')==='release','pending command keeps only the gamepad action visible');
   assert($('notice').hidden,'previous success hidden during request: '+command);
   $(command).click();assert(testRequests.length===count+1,'second click cannot duplicate: '+command);
   if(command==='arm'){testState.status.capture='active';await poll();assert($('capture-state').textContent!=='Включён','poll cannot show active before arm response');}
   if(command==='launch'){
    testState.status.processAlive=true;testState.status.connected=true;await poll();
    assert(!$('ps5-state').textContent.includes('подключена')&&!$('control-progress').hidden,'connected poll cannot confirm pending launch');
   }
   finish(response({status:{capture:command==='arm'?'active':'off'}}));await settle();
   assert(!$('notice').hidden&&$(command).getAttribute('aria-busy')==='false','success only after response: '+command);
   assert(testRequests.at(-1).csrf==='test','existing CSRF header preserved: '+command);
   if(command==='launch')assert($('ps5-state').textContent.includes('подключена'),'fresh post-ACK poll confirms launched client');
  }
  for(const message of ['Отказ mock backend','Timeout mock backend']){
   testState.status.processAlive=false;testState.status.capture='off';await poll();
   window.testMutation=()=>message.startsWith('Timeout')?Promise.reject(Error(message)):Promise.resolve({ok:false,json:async()=>({error:message})});
   const before=testRequests.length;$('launch').click();await settle();
   assert(testRequests.length===before+1&&$('error').textContent===message&&$('notice').hidden,'failure visible with no retry or success: '+message);
  }
  window.testMutation=null;testState.status.processAlive=true;testState.status.capture='off';await poll();
  for(const condition of ['busy','recoveryRequired']){
   testState.status[condition]=true;await poll();const count=testRequests.length;$('arm').click();$('launch').click();
   assert($('arm').disabled&&$('launch').disabled&&testRequests.length===count,'remote guard preserved: '+condition);
   testState.status[condition]=false;
  }
  await poll();testFailure=true;await poll();
  assert($('arm').disabled&&$('launch').disabled,'offline guard preserved');testFailure=false;await poll();
  testClock+=11000;for(const timer of testIntervals.filter(t=>[200,1000].includes(t.ms)))timer.fn();
  assert($('arm').disabled&&$('launch').disabled,'stale guard preserved');await poll();
  for(const [code,originalAction] of [['KeyE','Box'],['KeyG',undefined]]){
   const source=boot.codes[code];document.querySelector('.keycap[data-source="'+source+'"]').click();
   assert(pendingSource===source&&assignment===(originalAction||'Cross'),'keycap opens matching source and action: '+code);
   choose('R1');$(originalAction?'replace-binding':'commit-binding').click();
   assert(draft.binds[source]==='R1'&&!$('assign-dialog').open,'keycap editor commits chosen action: '+code);
   if(originalAction)draft.binds[source]=originalAction;else delete draft.binds[source];changed();renderBindings();
  }
  const original=JSON.stringify(draft.binds),count=testRequests.length;
  $('new-binding').click();
  assert($('assign-dialog').open&&!listening,'editor opens without listening');
  const choices=Array.from($('assign-source').options).map(option=>option.value).filter(Boolean);
  assert(choices.length===Object.values(boot.codes).filter(value=>!boot.reserved.includes(value)).length,'source picker uses every allowed native mapping');
  assert(boot.reserved.every(value=>!choices.includes(String(value)))&&choices.includes('258')&&choices.includes('259')&&choices.includes('260')&&!choices.includes('261'),'middle and side mouse supported; reserved and extra sources absent');
  key('KeyG');pointer($('assign-message'));pointer($('assign-source'));
  assert(JSON.stringify(draft.binds)===original&&pendingSource===null,'typing and clicking outside listening do not assign');
  select('assign-source',boot.codes.KeyG);choose('R1');
  assert(JSON.stringify(draft.binds)===original&&testRequests.length===count,'source/action selection is local and waits for commit');
  assert($('assign-preview').querySelector('svg').dataset.action==='R1','chosen action has vector preview');
  $('commit-binding').click();
  assert(draft.binds[String(boot.codes.KeyG)]==='R1'&&dirty&&!$('assign-dialog').open,'explicit commit writes native source to local draft');
  assert(document.querySelector('.keycap[data-source="'+boot.codes.KeyG+'"] svg').dataset.action==='R1','keycap and card show same action');
  $('new-binding').click();select('assign-source',boot.codes.KeyE);choose('Cross');
  for(const choice of document.querySelectorAll('.action-choice'))assert(choice.getAttribute('aria-label').includes(actionMeaning(choice.dataset.action)),'action choice has full accessible meaning');

  assert(!$('replace-binding').hidden&&$('commit-binding').hidden&&$('assign-message').textContent.includes('Квадрат')&&$('assign-message').textContent.includes('Крест'),'conflict identifies previous and proposed actions');
  $('cancel-assign').click();assert(draft.binds[String(boot.codes.KeyE)]==='Box','cancel conflict preserves original binding');
  $('new-binding').click();select('assign-source',boot.codes.KeyE);choose('Cross');$('replace-binding').click();
  assert(draft.binds[String(boot.codes.KeyE)]==='Cross'&&draft.binds[String(boot.codes.KeyR)]==='Box','explicit replacement preserves other alias');
  $('new-binding').click();select('assign-source',boot.codes.KeyF);choose('Box');
  assert($('assign-aliases').textContent.includes('R'),'alias hint lists existing sources');$('commit-binding').click();
  assert(draft.binds[String(boot.codes.KeyF)]==='Box'&&draft.binds[String(boot.codes.KeyR)]==='Box','multiple sources for one button remain allowed');
  $('new-binding').click();choose('LookRight');$('record-source').click();
  assert(listening,'recording requires explicit action');
  key('KeyM');assert(listening&&$('assign-message').textContent.includes('зарезервирована'),'record rejects reserved key');
  key('F12');assert(listening&&$('assign-message').textContent.includes('не поддерживается'),'record rejects unsupported key');
  const typed=key('KeyQ',$('assign-source'));assert(!typed.defaultPrevented&&listening,'selector keyboard navigation is not intercepted');
  const textInput=key('KeyQ',$('profile-name'));assert(!textInput.defaultPrevented&&listening,'text input is not intercepted during recording');
  const actionChoice=$('assign-actions').querySelector('[data-action="Box"]');
  const actionKey=key('Enter',actionChoice);assert(!actionKey.defaultPrevented&&listening,'Enter on action button is not recorded');
  actionChoice.click();assert(assignment==='Box'&&pendingSource===null&&!listening,'keyboard activation can select an action without assigning Enter');
  choose('LookRight');$('record-source').click();
  const stopKey=key('Space',$('record-source'));assert(!stopKey.defaultPrevented&&listening,'Space on stop button is not recorded');
  $('record-source').click();assert(!listening&&pendingSource===null,'keyboard activation can stop recording without assigning Space');
  $('record-source').click();
  key('KeyT',$('record-target'));
  assert(!listening&&pendingSource===boot.codes.KeyT&&!Object.hasOwn(draft.binds,String(boot.codes.KeyT)),'record captures mapped source without auto commit');
  $('commit-binding').click();assert(draft.binds[String(boot.codes.KeyT)]==='LookRight','recorded source uses native mapping');
  const beforeEscape=JSON.stringify(draft.binds);$('new-binding').click();$('record-source').click();key('Escape');
  assert(!$('assign-dialog').open&&!listening&&JSON.stringify(draft.binds)===beforeEscape,'Escape cancels recording and editor');
  $('new-binding').click();$('record-source').click();pointer($('record-target'),1);
  assert(pendingSource===boot.codes.MouseMiddle&&!listening,'middle mouse records its source');
  choose('R3');select('assign-source',boot.codes.MouseMiddle);$('commit-binding').click();
  assert(draft.binds['258']==='R3','middle click R3 commits');
  for(const [button,code] of [[3,'MouseBack'],[4,'MouseForward']]) {
   $('new-binding').click();$('record-source').click();pointer($('record-target'),button);
   assert(pendingSource===boot.codes[code]&&!listening,'side mouse records its source');
   const aux=new MouseEvent('auxclick',{button,bubbles:true,cancelable:true});$('record-target').dispatchEvent(aux);
   assert(aux.defaultPrevented,'side button browser navigation suppressed');
   $('commit-binding').click();assert(draft.binds[String(boot.codes[code])]==='Cross','side binding committed');
  }
  $('new-binding').click();$('record-source').click();pointer($('record-target'),2);assert(pendingSource===boot.codes.MouseRight&&!listening,'right mouse records only in recording area');
  const contextMenu=new MouseEvent('contextmenu',{button:2,bubbles:true,cancelable:true});$('record-target').dispatchEvent(contextMenu);
  assert(contextMenu.defaultPrevented,'right-click menu is suppressed after pointerdown stops listening');$('cancel-assign').click();
  $('new-binding').click();$('record-source').click();key('Tab',$('record-target'));
  assert(pendingSource===boot.codes.Tab&&!listening,'Tab records its native source in the explicit recording area');$('cancel-assign').click();
  const saved=structuredClone(draft);$('save').click();await poll();
  assert(!dirty&&JSON.stringify(testState.boot.store.profiles.arc)===JSON.stringify(saved),'existing save API persists edited bindings');
  boot=null;store=null;draft=null;settingsDraft=null;
  if(typeof renewSession==='function')await renewSession();
  else{boot=await request('bootstrap');store=boot.store;draft=clone(store.profiles[store.selected]);settingsDraft=clone(store.gameSettings);render();}
  await poll();
  assert(JSON.stringify(draft)===JSON.stringify(saved)&&document.querySelector('.keycap[data-source="'+boot.codes.KeyT+'"] svg').dataset.action==='LookRight','bootstrap reload restores mappings and vector keycaps');
  const resetPosts=testRequests.length;let confirmations=0;window.confirm=()=>{confirmations++;return false;};$('reset').click();
  assert(confirmations===1&&testRequests.length===resetPosts,'reset keeps confirmation');
  assert(!testRequests.some(r=>r.path==='apply'),'editing and saving never auto-apply');

  // A clean bootstrap or a changed source cannot inherit an older editor's consent.
  $('new-binding').click();select('assign-source',boot.codes.KeyE);choose('R1');
  draft.binds[boot.codes.KeyE]='R2';$('replace-binding').click();
  assert(!$('assign-dialog').open&&draft.binds[boot.codes.KeyE]==='R2','changed source rejects stale editor commit');
  draft=clone(store.profiles[store.selected]);dirty=false;render();
  const refreshBoot=async()=>{
   if(typeof renewSession==='function')await renewSession();
   else{boot=await request('bootstrap');store=boot.store;draft=clone(store.profiles[store.selected]);settingsDraft=clone(store.gameSettings);render();}
  };
  for(const changedId of [false,true]){
   $('new-binding').click();select('assign-source',boot.codes.KeyE);choose('R1');
   const next=clone(testState.boot.store);next.revision++;
   if(changedId){next.selected='other';next.profiles.other=clone(next.profiles.arc);}
   next.profiles[next.selected].binds[boot.codes.KeyE]='R2';testState.boot.store=next;
   await refreshBoot();$('replace-binding').onclick();
   assert(!$('assign-dialog').open&&draft.binds[boot.codes.KeyE]==='R2','clean bootstrap closes editor before replacement: '+changedId);
  }
  $('new-binding').click();select('assign-source',boot.codes.KeyE);choose('Cross');
  await mutate('select',{id:'arc'});$('replace-binding').onclick();
  assert(!$('assign-dialog').open&&store.selected==='arc'&&draft.binds[boot.codes.KeyE]==='R2','profile selection rejects old editor commit');
  const readyBoot=boot;boot=undefined;beginAssign('Cross');assert(!$('assign-dialog').open,'failed bootstrap cannot open editor');boot=readyBoot;

  // Freshness and command progress are separate even when buttons rerender.
  testState.status.processAlive=true;testState.status.capture='off';await poll();
  let armDone;window.testMutation=path=>path==='arm'?new Promise(resolve=>{armDone=resolve;}):response({status:{capture:'off'}});
  const pendingArm=mutate('arm',{},'arm acknowledged');await settle();
  testFailure=true;await poll();buttons();
  assert($('capture-state').textContent.includes('Неизвестен')&&$('ps5-state').textContent.includes('Неизвестен')&&!$('control-progress').hidden,'offline remains unknown while command stays pending');
  testFailure=false;await poll();testClock+=11000;for(const timer of testIntervals.filter(t=>[200,1000].includes(t.ms)))timer.fn();buttons();
  assert($('capture-state').textContent.includes('Неизвестен')&&!$('control-progress').hidden,'expired sample remains unknown while command stays pending');

  // Both a pre-ACK sample and the displayed pre-command state are obsolete after ACK.
  const statusReplies=[];window.testStatus=()=>new Promise(resolve=>statusReplies.push(resolve));
  const oldPoll=refresh();await settle();armDone(response({status:{capture:'active'}}));await settle();
  assert(lastStatus===null&&!$('capture-state').textContent.includes('Включён'),'ACK invalidates displayed pre-command status');
  statusReplies.shift()(response({...testState.status,capture:'active'}));await oldPoll;await settle();
  assert(lastStatus===null,'pre-ACK poll cannot republish obsolete status');
  window.testStatus=null;
  for(const reply of statusReplies)reply(response({...testState.status,capture:'off'}));
  await pendingArm;await poll();assert($('capture-state').textContent==='Выключен','post-ACK fresh poll determines device state');

  let cancelledDone;window.testMutation=path=>path==='arm'?new Promise(resolve=>{cancelledDone=resolve;}):response({status:{capture:'off'}});
  const cancelledArm=mutate('arm',{},'must not appear');await settle();
  const beforeRelease=testRequests.length;await mutate('release',{},'release acknowledged');await settle();
  if(typeof renewSession==='function'){
   assert(testRequests.length===beforeRelease+1&&busy&&!releaseBusy,'priority Release can finish before arm request');
   testState.status.capture='active';await poll();
   assert($('arm').getAttribute('aria-busy')==='true'&&$('control-progress').textContent.includes('отменена')&&!$('capture-state').textContent.includes('Включён'),'remaining cancelled arm stays visible without late active');
  }else assert(testRequests.length===beforeRelease&&$('release').disabled,'legacy Release retains single-flight semantics');
  testState.status.capture='off';cancelledDone(response({status:{capture:'active'}}));await cancelledArm;await poll();
  assert(!pendingRequests.size&&$('control-progress').hidden&&$('capture-state').textContent==='Выключен','late response clears only its request and fresh state wins');
  if(typeof renewSession==='function')assert($('notice').textContent==='release acknowledged','late cancelled arm cannot replace Release acknowledgement');
  window.testMutation=null;
  window.dashboardTestResult={ok:true,results};
 }catch(e){window.dashboardTestResult={ok:false,error:e.stack,results};}
})();
