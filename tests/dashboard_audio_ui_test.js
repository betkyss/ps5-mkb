(async()=>{
 const results=[];const assert=(v,label)=>{if(!v)throw Error(label);results.push(label);};
 const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setTimeout(r,0));};
 const wait=ms=>new Promise(r=>setTimeout(r,ms));
 try{
  await settle();assert($('audio-volume').disabled,'old binary shows unavailable controls');
  Object.assign(testState.status,{headsetAudioControlVersion:1,audio:{processId:'p',sessionId:'s',sessionGeneration:1,audioRevision:1,sampleAgeMs:0,supported:true,uid:'dualsense-1',deviceGeneration:1,outputName:'DualSense',osScalar:.35,effectiveMuted:false}});
  await refresh();await settle();assert($('audio-volume').value==='35'&&!$('audio-volume').disabled,'native OS readback initializes slider');
  const calls=[],replies=[];
  window.testMutation=async(path,body)=>{
   if(path!=='audio')return null;calls.push(structuredClone(body));
   const a=testState.status.audio;a.audioRevision++;a.operationId=body.operationId;
   if('setVolume' in body)a.osScalar=body.setVolume/100;
   if('setOutputMuted' in body)a.effectiveMuted=body.setOutputMuted;
   const snapshot=structuredClone(testState.status);
   return new Promise(resolve=>replies.push(sampleAgeMs=>{
    if(sampleAgeMs!==undefined)snapshot.audio.sampleAgeMs=sampleAgeMs;
    resolve({ok:true,json:async()=>({accepted:true,status:snapshot})});
   }));

  };
  const edit=v=>{$('audio-volume').value=String(v);$('audio-volume').dispatchEvent(new Event('input',{bubbles:true}));};
  edit(40);edit(41);await wait(110);assert(calls.length===1&&calls[0].setVolume===41,'75ms coalescing sends newest draft');
  edit(57);replies.shift()();await settle();assert($('audio-volume').value==='57','old ACK preserves newer draft');
  await wait(110);assert(calls.length===2&&calls[1].setVolume===57,'new draft follows ACK');replies.shift()();await settle();
  assert(!document.getElementById('audio-mic'),'microphone control is absent');
  const countBeforeUnknown=calls.length;
  await sendAudio({setMicrophoneEnabled:true});await sendAudio({setMicrophoneEnabled:false});
  assert(calls.length===countBeforeUnknown,'UI rejects removed microphone actions');
  edit(60);await wait(110);$('audio-mute').click();
  assert($('audio-mute').disabled&&$('audio-mute').textContent==='Выключаем звук…','queued mute shows pending target and disables repeat');
  $('audio-mute').click();
  replies.shift()();await settle();assert(calls.at(-1).setOutputMuted===true,'mute queued behind output write');
  assert($('audio-mute').disabled,'inflight mute stays disabled');
  const muteCalls=calls.length;$('audio-mute').click();assert(calls.length===muteCalls,'inflight mute does not repeat from stale status');
  replies.shift()();await settle();assert(!$('audio-mute').disabled,'confirmed mute enables the next explicit toggle');
  for(const field of ['uid','deviceGeneration'])for(const queued of ['mute','volume']) {
   edit(43);await wait(110);
   if(queued==='mute')$('audio-mute').click();else edit(44);
   const beforeReconnect=calls.length;
   const next=testState.status.audio;
   next[field]=field==='uid'?next.uid+'-replacement':next.deviceGeneration+1;
   next.audioRevision=1;next.osScalar=.19;
   await refresh();await settle();
   assert(audioDraft===null&&audioQueuedAction===null,field+' reconnect clears queued '+queued);
   const replacement=structuredClone(audioView);
   replies.shift()();await settle();await wait(110);
   assert(calls.length===beforeReconnect,field+' reconnect never sends old '+queued+' to replacement');
   assert(sameAudioOutput(audioView,replacement)&&audioView.osScalar===.19&&$('audio-volume').value==='19',field+' reconnect ignores old-device ACK');
  }
  for(const code of ['headset-audio-unavailable','volume-operation-pending','volume-readback-pending','invalid-volume','invalid-audio-command','unknown-audio-field']) {
   audioError=code;renderAudio();assert($('audio-error').textContent!==code&&/[А-Яа-я]/.test($('audio-error').textContent),'Russian output error: '+code);
  }
  audioError='';renderAudio();
  assert(!('revision' in calls[0])&&!('profile' in calls[0]),'audio protocol is independent of profile');
  busy=true;failed=true;renderAudio();assert(!$('audio-volume').disabled,'profile busy or stale input snapshot does not disable fresh audio');busy=false;failed=false;
  const count=calls.length;await refresh();await settle();assert(calls.length===count,'status polling does not write audio');
  testState.status.audio.osScalar=.23;testState.status.audio.audioRevision++;
  await refresh();await settle();assert($('audio-volume').value==='23','external volume changes update slider');
  // A newer, never-attempted draft resumes after stale ACK and a fresh GET.
  edit(64);await wait(110);edit(65);const delayedCount=calls.length;
  testClock+=1200;replies.shift()();await settle();await wait(110);
  assert(calls.length===delayedCount&&audioDraft===65,'stale ACK keeps newer draft unsent');
  await refresh();await settle();await wait(110);
  assert(calls.length===delayedCount+1&&calls.at(-1).setVolume===65,'fresh GET resumes never-attempted draft');
  replies.shift()();await settle();
  // Freshness can expire after input but before the debounce callback.
  edit(66);const debounceCount=calls.length;testClock+=1200;await wait(110);
  assert(calls.length===debounceCount&&audioDraft===66&&audioError,'expired debounce displays stale error without sending');
  await refresh();await settle();await wait(110);
  assert(calls.length===debounceCount+1&&calls.at(-1).setVolume===66,'fresh GET resumes expired debounce once');
  replies.shift()();await settle();
  // Rejected current edits finish with readback instead of eternal pending text.
  window.testMutation=async(path,body)=>{
   if(path!=='audio')return null;
   const status=structuredClone(testState.status);
   return {ok:true,json:async()=>({accepted:false,error:'audio-revision-changed',status})};
  };
  edit(71);await wait(110);await settle();
  assert(audioDraft===null&&!$('audio-level').textContent.includes('ожидает'),'rejected current draft leaves pending state');
  const rejectedCount=testRequests.filter(r=>r.path==='audio').length;await refresh();await wait(110);
  assert(testRequests.filter(r=>r.path==='audio').length===rejectedCount,'fresh GET never retries an already rejected volume');
  // Slow audio ACK age includes request round trip.
  window.testMutation=async(path,body)=>{
   if(path!=='audio')return null;
   const status=structuredClone(testState.status);status.audio.operationId=body.operationId;status.audio.audioRevision++;
   testClock+=1200;
   return {ok:true,json:async()=>({accepted:true,status})};
  };
  edit(72);await wait(110);await settle();assert(!audioFresh(),'audio ACK includes round trip age');
  const version=testState.status.headsetAudioControlVersion;delete testState.status.headsetAudioControlVersion;
  await refresh();await settle();assert(audioView===null&&$('audio-volume').disabled,'authoritative status without capability clears old audio');
  testState.status.headsetAudioControlVersion=version;await refresh();await settle();
  testClock+=1500;renderAudio();assert($('audio-volume').disabled&&$('audio-state').textContent.includes('свежего'),'stale snapshot is explicit');
  assert(!testRequests.some(r=>r.path==='live'),'audio edits never use profile live endpoint');
  window.dashboardTestResult={ok:true,results};
 }catch(e){window.dashboardTestResult={ok:false,error:e.stack,results};}
})();
