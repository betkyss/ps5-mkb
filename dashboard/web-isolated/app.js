'use strict';
const $ = id => document.getElementById(id);
let boot, store, draft, dirty=false, busy=false, lastStatus=null, received=0, failed=false;
let settingsDraft,settingsDirty=false;
let releaseBusy=false,staleStore=false,refreshFlight=null,refreshQueued=false,pollTimer=null;
let intent=0,requestSequence=0,acceptedSequence=0,sampleSequence=-1,serviceInstance=null;
let remoteBoot=null;
let bootstrapSequence=0;
let restoredSourceRevision=null;
let liveTimer=null,liveFlight=null,liveEdit=0,liveEpoch=0,liveState='',liveFailed=false,liveNeeded=false,liveWaiting=false;
const runtimeEdits=new Map();
const runtimeInputs={enabled:'recoil-enabled',vertical:'recoil-vertical',rapidFireEnabled:'rapid-fire-enabled'};

const sampleAge=()=>lastStatus ? (lastStatus.sampleAgeMs||0)+(performance.now()-received) : Infinity;
let assignment=null, pendingSource=null, listening=false, assignmentContext=null, suppressRecordContextMenu=false;
const clone = value => structuredClone(value);
let idSequence=0;
function uniqueProfileId(){
 const words=new Uint32Array(4);
 if(globalThis.crypto?.getRandomValues){crypto.getRandomValues(words);return 'p-'+Array.from(words,n=>n.toString(16).padStart(8,'0')).join('');}
 return 'p-'+Date.now().toString(36)+'-'+(++idSequence).toString(36)+'-'+Math.random().toString(36).slice(2,12);
}
// Audio has its own readback and request lane; profile ACKs never update drafts.
let audioView=null,audioReceived=0,audioFlight=null,audioDraft=null,audioDraftIdentity=null,audioEdit=0,audioTimer=null,audioError='',audioQueuedAction=null,audioAttemptedEdit=-1;
function sameAudioOutput(a,b){return a&&b&&['processId','sessionId','sessionGeneration','uid','deviceGeneration'].every(k=>a[k]===b[k]);}
function acceptAudio(status,fromAck=false){
 const next=status?.audio;if(status?.headsetAudioControlVersion!==1||!next){if(!fromAck){audioView=null;renderAudio();}return;}
 const same=sameAudioOutput(audioView,next);
 if(fromAck&&!same)return;
 if(same&&next.audioRevision<audioView.audioRevision)return;
 if(!same){clearTimeout(audioTimer);audioTimer=null;audioFlight=null;audioDraft=null;audioDraftIdentity=null;audioQueuedAction=null;audioError='';++audioEdit;}
 audioView=clone(next);audioReceived=performance.now();
 renderAudio();
 if(!audioTimer&&!audioFlight&&!audioQueuedAction&&audioDraft!==null&&audioEdit>audioAttemptedEdit&&audioFresh())queueAudioVolume();
}
function audioFresh(){return audioView&&audioView.stale!==true&&Number.isFinite(audioView.sampleAgeMs)&&audioView.sampleAgeMs+performance.now()-audioReceived<1000;}
function renderAudio(){
 if(!$('audio-volume'))return;
 const fresh=audioFresh(),a=audioView;
 $('audio-volume').disabled=!fresh||!a.supported;
 const pendingMute=audioQueuedAction?.action||audioFlight?.action;
 const muting=pendingMute&&'setOutputMuted' in pendingMute;
 $('audio-mute').disabled=!fresh||!a.supported||muting;
 if(audioDraft===null&&fresh&&Number.isFinite(a.osScalar))$('audio-volume').value=String(Math.round(a.osScalar*100));
 $('audio-level').textContent=audioDraft!==null?`${audioDraft}% · ожидает подтверждения`:fresh&&Number.isFinite(a.osScalar)?`${Math.round(a.osScalar*100)}%`:'Не подтверждено';
 $('audio-mute').textContent=muting?(pendingMute.setOutputMuted?'Выключаем звук…':'Включаем звук…'):a?.effectiveMuted?'Включить звук':'Выключить звук';
 $('audio-state').textContent=!a?'Управление аудио недоступно. Нужна новая сборка клиента.':!fresh?'Нет свежего подтверждения аудио':`${a.outputName||'Устройство не найдено'}${a.appMuted?' · звук приложения выключен':''}`;
 const errors={
  'headset-audio-unavailable':'Управление громкостью гарнитуры недоступно.',
  'volume-operation-pending':'Предыдущее изменение громкости ещё не завершено.',
  'volume-readback-pending':'Ожидаем подтверждение громкости устройства.',
  'invalid-volume':'Громкость должна быть от 0 до 100%.',
  'invalid-audio-command':'Некорректная команда управления звуком.',
  'unknown-audio-field':'Команда содержит неизвестное поле.',
  'device-missing':'Выбранный USB DualSense не найден.',
  'ambiguous-device':'Найдено несколько одинаковых устройств. Выбор не подтверждён.',
  'device-changed':'Устройство изменилось. Проверьте подключение и повторите действие.',
  'unsupported-volume-control':'Устройство не поддерживает управление общей громкостью.',
  'hardware-mute-read-only':'Аппаратное выключение звука нельзя снять программно.',
  'coreaudio-write-failed':'CoreAudio не подтвердил изменение громкости.',
  'volume-readback-unconfirmed':'Новое значение громкости не подтверждено устройством.',
  'audio-revision-changed':'Состояние аудио изменилось. Проверьте новое значение и повторите действие.'
 };
 const problem=audioError||a?.error||'';
 $('audio-error').textContent=errors[problem]||problem;
}
async function sendAudio(action,identity=audioView){
 if(!sameAudioOutput(audioView,identity))return;
 if(Object.keys(action).length!==1||!Object.keys(action).every(k=>['setVolume','setOutputMuted'].includes(k)))return;
 if('setOutputMuted' in action&&(audioQueuedAction||audioFlight?.action&&'setOutputMuted' in audioFlight.action))return;
 if(audioFlight){
  if('setOutputMuted' in action){audioQueuedAction={action,identity};renderAudio();}
  return;
 }
 if(!audioFresh()) {audioError='Сначала дождитесь свежего состояния';renderAudio();return;}
 const ticket=audioEdit,operationId=uniqueProfileId();
 const body={operationId,...Object.fromEntries(['processId','sessionId','sessionGeneration','audioRevision'].map(k=>[k,audioView[k]])),...action};
 const flight={action,identity};audioFlight=flight;
 if('setVolume' in action)audioAttemptedEdit=ticket;
 audioError='';renderAudio();
 try{
  const result=await request('audio',body);
  const a=result.status?.audio;
  if(!sameAudioOutput(audioView,identity))return;
  if(a&&!sameAudioOutput(a,identity)){await refresh();return;}
  if(a?.operationId===operationId&&result.accepted===true){
   if('setVolume' in action&&ticket===audioEdit)audioDraft=null;
  }else {audioError=result.error||'Действие не подтверждено';if('setVolume' in action&&ticket===audioEdit)audioDraft=null;}
  acceptAudio(result.status,true);
 }catch(e){if(!sameAudioOutput(audioView,identity))return;audioError=e.message;if('setVolume' in action&&ticket===audioEdit)audioDraft=null;await refresh();}
 finally{
  if(audioFlight!==flight)return;
  audioFlight=null;
  renderAudio();
  if(audioQueuedAction){const queued=audioQueuedAction;audioQueuedAction=null;sendAudio(queued.action,queued.identity);}
  else if(audioDraft!==null&&audioEdit>audioAttemptedEdit&&audioFresh())queueAudioVolume();
 }
}
function queueAudioVolume(flush=false){
 clearTimeout(audioTimer);
 const send=()=>{audioTimer=null;if(audioDraft!==null&&!audioFlight)sendAudio({setVolume:audioDraft},audioDraftIdentity);};
 if(flush)send();else audioTimer=setTimeout(send,75);
}
if($('audio-volume')){
 $('audio-volume').oninput=e=>{audioDraft=Number(e.target.value);audioDraftIdentity=audioView;++audioEdit;renderAudio();queueAudioVolume();};
 $('audio-volume').onchange=()=>queueAudioVolume(true);
 $('audio-volume').onpointerup=()=>queueAudioVolume(true);
 $('audio-mute').onclick=()=>{audioDraft=null;++audioEdit;sendAudio({setOutputMuted:!audioView?.effectiveMuted});};
}

const groups=[
 ['Движение',[['W','↑','Вперёд','Левый стик'],['A','←','Влево','Левый стик'],['S','↓','Назад','Левый стик'],['D','→','Вправо','Левый стик'],['Cross','×','Прыжок','Cross'],['L3','L3','Бег','Нажатие стика'],['Moon','○','Присесть','Circle · удержание'],['DodgeRoll','○○','Перекат','Автоматически два нажатия приседа по 64 мс с отпусканием между ними']]],
 ['Оружие и взаимодействие',[['R2','R2','Огонь / бросок / разместить','По контексту игры'],['L2','L2','Прицелиться','Удерживать для ADS'],['Box','□','Взаимодействие / перезарядка','Square · E и R являются aliases'],['HoldLeft','←','Удержать ←','Одно нажатие удерживает D-pad Left 600 мс'],['HoldRight','→','Удержать →','Одно нажатие удерживает D-pad Right 600 мс для проверки ближнего боя'],['Pyramid','△','Сменить / взять оружие','Triangle · удерживать, чтобы убрать; 1/2 не слоты'],['R1','R1','Отметка / ping','Нажатие'],['R3','R3','Активировать навык','Нажатие правого стика, когда навык открыт']]],
 ['Предметы и радиальное меню',[['L1','L1','Предметы / selector','Выбор и лечение зависят от контекста игры'],['LookUp','R↑','Выбор вверх','Явное направление правого стика'],['LookLeft','R←','Выбор влево','IJKL для консольных меню'],['LookDown','R↓','Выбор вниз','Диагонали нормализованы'],['LookRight','R→','Выбор вправо','Мышь также управляет правым стиком']]],
 ['Автотап',[['RapidFireToggle','↻','Включить / выключить автотап','Повторять огонь при удержании кнопки; для полуавтоматического оружия']]],
 ['Компенсация отдачи',[['RecoilToggle','↕','Включить / выключить','Только прицеливание + огонь; текущий выбор отражается в галочке'],['RecoilDecrease','−','Уменьшить силу','Шаг 0,5% хода стика'],['RecoilIncrease','+','Увеличить силу','Шаг 0,5%; выбор сохраняется автоматически']]],
 ['Инвентарь и система',[['Touchpad','▭','Инвентарь / карта','Tab · нажатие inventory, удержание map'],['Up','↑','Фонарик','D-pad Up · проверено пользователем'],['Down','↓','Ближайший голосовой чат','Вкл. / выкл. при режиме «Нажать для переключения»'],['Left','←','Реплики','Короткое нажатие D-pad Left · проверено пользователем'],['Right','→','Смена плеча камеры','Вид из-за левого или правого плеча'],['Options','Options','Меню','Options'],['Share','••','Создать / Share','Системная кнопка'],['PS','PS','PlayStation','Системная кнопка']]]
];
const labels={MetaLeft:'L Cmd',MetaRight:'R Cmd',CapsLock:'Caps Lock',Minus:'−',Equal:'=',BracketLeft:'[',BracketRight:']',Backslash:'\\',Semicolon:';',Quote:"'",Slash:'/',KeyA:'A',KeyS:'S',KeyD:'D',KeyF:'F',MouseLeft:'ЛКМ',MouseRight:'ПКМ',MouseMiddle:'Колёсико',MouseBack:'Боковая задняя',MouseForward:'Боковая передняя',Space:'Space',Tab:'Tab',ShiftLeft:'L Shift',ShiftRight:'R Shift',Enter:'Enter',Comma:',',Period:'.',ArrowUp:'↑',ArrowDown:'↓',ArrowLeft:'←',ArrowRight:'→',Escape:'Esc',Backquote:'`',Backspace:'⌫'};
const keyLabel=code=>labels[code]||code.replace(/^Key|^Digit/,'');
const sourceLabel=source=>{const pair=Object.entries(boot.codes).find(([,v])=>v===Number(source));return pair?keyLabel(pair[0]):`macOS ${source}`;};
function node(tag,text,className){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;}
function error(message){$('error').textContent=message||'';$('error').hidden=!message;if(message)$('notice').hidden=true;}
function notice(message){$('notice').textContent=message;$('notice').hidden=false;error('');}
const controllerNames={HoldLeft:'Удержать ←',HoldRight:'Удержать →',DodgeRoll:'Перекат',RapidFireToggle:'Автотап вкл. / выкл.',RecoilToggle:'Компенсация вкл. / выкл.',RecoilDecrease:'Компенсация слабее',RecoilIncrease:'Компенсация сильнее',W:'Левый стик вверх',A:'Левый стик влево',S:'Левый стик вниз',D:'Левый стик вправо',Cross:'Крест',Box:'Квадрат',Pyramid:'Треугольник',Moon:'Круг',L3:'L3 · нажатие левого стика',R3:'R3 · нажатие правого стика',L1:'L1',R1:'R1',L2:'L2',R2:'R2',Up:'D-pad вверх',Down:'D-pad вниз',Left:'D-pad влево',Right:'D-pad вправо',Options:'Options · меню',Share:'Create · создание',PS:'PS · система',Touchpad:'Тачпад',LookUp:'Правый стик вверх',LookDown:'Правый стик вниз',LookLeft:'Правый стик влево',LookRight:'Правый стик вправо'};
const actionLabel=action=>controllerNames[action]||action;
const shortActions={HoldLeft:'Удержать ←',HoldRight:'Удержать →',DodgeRoll:'Перекат',RapidFireToggle:'Автотап',RecoilToggle:'Отдача',RecoilDecrease:'Слабее',RecoilIncrease:'Сильнее',W:'Вперёд',A:'Влево',S:'Назад',D:'Вправо',Cross:'Прыжок',Moon:'Присед',L3:'Бег',R3:'Навык',R2:'Огонь',L2:'Прицел',Box:'Взаим.',Pyramid:'Оружие',R1:'Метка',L1:'Предм.',LookUp:'Выбор↑',LookDown:'Выбор↓',LookLeft:'Выбор←',LookRight:'Выбор→',Touchpad:'Инвентарь',Up:'D-pad↑',Down:'Голос',Left:'D-pad←',Right:'Плечо',Options:'Меню',Share:'Create',PS:'PS'};
const shortAction=action=>shortActions[action]||actionLabel(action);
const actionMeaning=action=>groups.flatMap(([,actions])=>actions).find(([value])=>value===action)?.[2]||actionLabel(action);
const choiceCaption=action=>({Box:'Взять / перезарядить',Pyramid:'Сменить оружие',Down:'Голос рядом',Right:'Смена плеча'}[action]||actionMeaning(action));
function controllerGlyph(action){
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');
 svg.setAttribute('viewBox','0 0 40 40');svg.setAttribute('class','controller-glyph');svg.setAttribute('role','img');svg.setAttribute('aria-label',actionLabel(action));svg.dataset.action=action;
 const title=document.createElementNS(ns,'title');title.textContent=actionLabel(action);svg.append(title);
 function shape(tag,attributes){const el=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attributes))el.setAttribute(key,value);svg.append(el);return el;}
 function text(value,x=20,y=24,size=13){shape('text',{x,y,'text-anchor':'middle','font-size':size}).textContent=value;}
 if(action==='HoldLeft'||action==='HoldRight'){shape('circle',{cx:20,cy:20,r:14});text(action==='HoldLeft'?'←':'→',20,26,24);}
 else if(action==='DodgeRoll'){shape('circle',{cx:13,cy:20,r:9});shape('circle',{cx:27,cy:20,r:9});}
 else if(action==='RapidFireToggle'){shape('circle',{cx:20,cy:20,r:14});text('↻',20,26,24);}
 else if(action.startsWith('Recoil')){shape('circle',{cx:20,cy:20,r:14});text(action==='RecoilToggle'?'↕':action==='RecoilDecrease'?'−':'+',20,25,20);}
 else if(action==='Cross')shape('path',{d:'M11 11 29 29 M29 11 11 29','stroke-width':2.6});
 else if(action==='Box')shape('rect',{x:10,y:10,width:20,height:20,'stroke-width':2.4});
 else if(action==='Pyramid')shape('path',{d:'M20 7 34 31H6Z','stroke-width':2.3});
 else if(action==='Moon')shape('circle',{cx:20,cy:20,r:12,'stroke-width':2.4});
 else if(['W','A','S','D','LookUp','LookDown','LookLeft','LookRight'].includes(action)){
  const left=['W','A','S','D'].includes(action),direction={W:0,A:270,S:180,D:90,LookUp:0,LookLeft:270,LookDown:180,LookRight:90}[action];
  shape('circle',{cx:20,cy:20,r:11});shape('circle',{cx:20,cy:20,r:6,'stroke-opacity':.4});
  shape('path',{d:'M20 2 16 7H24Z',transform:`rotate(${direction} 20 20)`,fill:'currentColor',stroke:'none'});
  text(left?'L':'R',20,23.5,10);
 }else if(['Up','Down','Left','Right'].includes(action)){
  const direction={Up:0,Down:180,Left:270,Right:90}[action];
  shape('path',{d:'M14 4H26V14H36V26H26V36H14V26H4V14H14Z','stroke-opacity':.5});
  shape('path',{d:'M15 5H25V15H15Z',transform:`rotate(${direction} 20 20)`,fill:'currentColor','fill-opacity':.16,stroke:'none'});
  shape('path',{d:'M20 7 16 12H24Z',transform:`rotate(${direction} 20 20)`,fill:'currentColor',stroke:'none'});
  shape('circle',{cx:20,cy:20,r:1.5,fill:'currentColor',stroke:'none'});
 }else if(action==='L3'||action==='R3'){
  shape('circle',{cx:20,cy:20,r:15});text(action,20,21.5,13);
  shape('path',{d:'M16 26 20 30 24 26','stroke-width':2});
 }else if(['L1','R1'].includes(action)){
  shape('path',{d:'M5 16Q5 9 12 8H28Q35 9 35 16V27H5Z'});text(action,20,23,14);
 }else if(['L2','R2'].includes(action)){
  shape('path',{d:'M8 6H30Q35 6 33 13L28 31Q27 34 23 34H12Q7 34 7 29V11Q7 6 8 6Z'});text(action,20,24,14);
 }else if(action==='Touchpad'){
  shape('rect',{x:4,y:9,width:32,height:22,rx:4});
  for(const x of [11,20,29])for(const y of [16,24])shape('circle',{cx:x,cy:y,r:1,fill:'currentColor',stroke:'none'});
 }else if(action==='Options'){
  shape('rect',{x:9,y:5,width:22,height:30,rx:6});shape('path',{d:'M15 14H25M15 20H25M15 26H25'});
 }else if(action==='Share'){
  shape('path',{d:'M20 5V11M8 10 12 14M32 10 28 14'});shape('rect',{x:15,y:18,width:10,height:17,rx:4});
 }else if(action==='PS'){
  shape('circle',{cx:20,cy:20,r:15});text('PS',20,25,14);
 }else{shape('rect',{x:5,y:5,width:30,height:30,rx:7});text(action);}
 return svg;
}
function navigationGlyph(kind){
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 40 40');svg.setAttribute('class','nav-glyph');svg.setAttribute('aria-hidden','true');
 const paths={bindings:'M5 10H35V30H5ZM10 16H11M16 16H17M22 16H23M28 16H29M10 22H11M16 22H17M22 22H23M28 22H29M14 26H26',aim:'M20 3V10M20 30V37M3 20H10M30 20H37M32 20A12 12 0 1 1 8 20 12 12 0 1 1 32 20M23 20A3 3 0 1 1 17 20 3 3 0 1 1 23 20',profiles:'M20 5 36 13 20 21 4 13ZM4 20 20 28 36 20M4 27 20 35 36 27',game:'M5 10H14M22 10H35M5 20H24M32 20H35M5 30H8M16 30H35M14 6H22V14H14ZM24 16H32V24H24ZM8 26H16V34H8Z',help:'M35 20A15 15 0 1 1 5 20 15 15 0 1 1 35 20M15 15Q15 10 20 10Q27 10 25 16L20 21V24M20 29H20.1',brand:'M12 11H28Q33 11 35 18L38 30Q39 36 33 34L26 29H14L7 34Q1 36 2 30L5 18Q7 11 12 11ZM9 18V26M5 22H13M28 19H28.1M32 24H32.1'};
 const p=document.createElementNS(ns,'path');p.setAttribute('d',paths[kind]||paths.help);svg.append(p);return svg;
}
function renderNavigationIcons(){
 for(const button of document.querySelectorAll('.nav[data-tab]'))button.prepend(navigationGlyph(button.dataset.tab));
 const mark=navigationGlyph('brand');
 mark.querySelector('path').setAttribute('d','M5 7H24V14H12V27H24V34H5ZM21 20H36M29 13L36 20 29 27');
 document.querySelector('.brandmark')?.append(mark);
}
renderNavigationIcons();
const navigation=document.querySelector('.navigation');
function syncNavigation(){if(navigation)navigation.open=window.innerWidth>700;const keyboard=document.querySelector('.keyboard-disclosure');if(keyboard)keyboard.open=window.innerWidth>700;}
syncNavigation();
window.addEventListener('resize',syncNavigation);
const controlLabels={arm:['Включить клавиатуру и мышь','Включаем управление…'],release:['Перейти на геймпад','Переходим на геймпад…'],launch:['Подключиться к PS5','Подключаемся к PS5…'],apply:['Применить на Mini','Применяем…'],save:['Сохранить','Сохраняем…'],recover:['Восстановить управление','Восстанавливаем…']};
const pendingRequests=new Set();
let statusGeneration=0;
const changesDevice=command=>['arm','release','launch','apply','recover','select','reset','create','import','fork-drafts'].includes(command);
function beginPending(command){
 if(command==='release')for(const request of pendingRequests)request.cancelled=true;
 const request={command,cancelled:false};pendingRequests.add(request);return request;
}
function invalidateStatus(){
 ++statusGeneration;lastStatus=null;received=0;renderUnknown('Проверяем состояние после команды…');
}
function renderCompactControls(){
 const s=lastStatus, pending=Array.from(pendingRequests);
 const age=typeof sampleAge==='function'?sampleAge():s?Date.now()-received:Infinity;
 const stale=!s||failed||age>=(typeof sampleAge==='function'?1000:10000);
 const active=s&&['active','preparing','releasing','error','unknown'].includes(s.capture);
 let command;
 if(pending.length||s?.busy)command='release';
 else if(s?.recoveryRequired&&$('recover'))command='recover';
 else if(stale)command='release';
 else if(s?.processAlive===false)command='launch';
 else if(active)command='release';
 else command='arm';
 for(const id of ['arm','release','launch','recover']){
  const button=$(id);if(!button)continue;
  button.hidden=id!==command;button.classList.toggle('primary',id===command);
 }
 const baselineUnknown=s?.adapter==='baseline'&&s.processAlive===true&&s.capture==='unknown'&&!pending.length&&!s.busy;
 const fallback=$('baseline-controls');
 fallback.hidden=!baselineUnknown;
 const armParent=baselineUnknown?fallback:$('main-controls');
 if($('arm').parentElement!==armParent)armParent.append($('arm'));
 if(baselineUnknown){$('arm').hidden=false;$('arm').classList.remove('primary');}
 const hint=$('control-hint');
 if(pending.length){
  $('connection-title').textContent=pending.some(p=>p.command==='release')?'Переходим на геймпад…':'Выполняем команду…';
  hint.textContent=command==='release'&&!$('release').disabled?'Можно отменить ожидание и перейти на геймпад.':'Дождитесь завершения команды.';
 }else if(command==='recover'){
  $('connection-title').textContent='Нужно восстановить управление';
  hint.textContent='Предыдущая команда не завершилась. Проверьте состояние одной кнопкой.';
 }else if(stale){
  $('connection-title').textContent='Проверяем подключение…';
  hint.textContent=$('release').disabled?'Состояние пока неизвестно. Проверьте подключение в подробностях.':'Состояние пока неизвестно. Выключение ввода остаётся доступным.';
 }else if(command==='launch'){
  $('connection-title').textContent='PS5 не подключена';
  hint.textContent='Подключитесь, чтобы играть с клавиатурой и мышью.';
 }else if(s.capture==='active'){
  $('connection-title').textContent='Клавиатура и мышь включены';
  hint.textContent='Esc тоже возвращает управление геймпаду.';
 }else if(active){
  $('connection-title').textContent=s.capture==='preparing'?'Подготавливаем управление…':s.capture==='releasing'?'Переходим на геймпад…':'Управление требует проверки';
  hint.textContent='Перейдите на геймпад, чтобы выключить захват.';
 }else{
  $('connection-title').textContent=s.connected===true?'Клавиатура и мышь выключены':'Нет соединения с PS5';
  hint.textContent=s.connected===true?'Включите, когда будете готовы играть.':'Подробности подключения доступны ниже.';
 }
}
function renderPending(){
 for(const [id,labels] of Object.entries(controlLabels)){
  const button=$(id);if(!button)continue;
  const request=Array.from(pendingRequests).find(request=>request.command===id);
  button.textContent=request?(request.cancelled?'Ожидаем отменённую команду…':labels[1]):labels[0];button.setAttribute('aria-busy',String(Boolean(request)));
 }
 $('control-progress').hidden=!pendingRequests.size;
 $('control-progress').textContent=Array.from(pendingRequests,request=>request.cancelled?
  `«${controlLabels[request.command]?.[0]||request.command}» отменена. Ожидаем завершения исходного запроса.`:
  (controlLabels[request.command]?.[1]||'Сохраняем изменения…')+' Ожидаем ответ службы.').join(' ');
 renderCompactControls();
}
function renderPendingDevice(){
 const requests=Array.from(pendingRequests);
 if(requests.some(request=>changesDevice(request.command))){
  $('capture-state').textContent='Неизвестен, ожидаем ответ';
  $('connection-title').textContent='Состояние после команды ещё не подтверждено';
 }
 if(requests.some(request=>request.command==='launch'))$('ps5-state').textContent='Подключение ещё не подтверждено';
}
async function request(path,body){
 const began=performance.now();
 const options={cache:'no-store',signal:AbortSignal.timeout(body?8500:1600)};
 if(body){options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':boot.csrf};options.body=JSON.stringify(body);}
 const r=await fetch('/api/'+path,options);let value;
 try{value=await r.json();}catch{throw Error('Служба вернула неполный ответ');}
 if(!r.ok){const e=Error(value.error||`Ошибка ${r.status}`);e.csrf=e.message==='CSRF token не совпадает';throw e;}
 if(path==='audio'&&value.status?.audio&&Number.isFinite(value.status.audio.sampleAgeMs))value.status.audio.sampleAgeMs+=Math.max(0,performance.now()-began);
 if(path==='status'){
  const transit=Math.max(0,performance.now()-began);
  value.sampleAgeMs=(value.sampleAgeMs||0)+transit;
  if(value.audio&&Number.isFinite(value.audio.sampleAgeMs))value.audio.sampleAgeMs+=transit;
  if(value.recoil&&Number.isFinite(value.recoil.sampleAgeMs)&&value.recoil.sampleAgeMs>=0)value.recoil.sampleAgeMs+=transit;
  for(const key of ['stateFeedbackAttemptAgeMs','stateFeedbackSuccessAgeMs'])if(Number.isFinite(value[key])&&value[key]>=0)value[key]+=transit;
 }
 return value;
}
const nativeLiveConnected=()=>lastStatus?.adapter==='native'&&lastStatus.connected===true&&!lastStatus.nativeRejected;
const liveAvailable=()=>nativeLiveConnected()&&!failed&&sampleAge()<1000&&['active','off'].includes(lastStatus.capture);
function buttons(){
 const remoteBlocked=failed||sampleAge()>=1000||staleStore||lastStatus?.busy||lastStatus?.recoveryRequired;
 $('save').disabled=busy||releaseBusy||Boolean(liveFlight)||staleStore||lastStatus?.recoveryRequired||!dirty||!validLiveProfile(draft);
 $('apply').disabled=busy||dirty||remoteBlocked||!lastStatus?.canApply;
 $('arm').disabled=busy||releaseBusy||staleStore||sampleAge()>=1000||failed||lastStatus?.recoveryRequired||(lastStatus?.nativeRejected&&lastStatus?.adapter!=='baseline')||!lastStatus?.processAlive||lastStatus.capture==='active'||lastStatus.busy;
 $('release').disabled=releaseBusy;
 if($('recover'))$('recover').disabled=busy||releaseBusy||!lastStatus?.recoveryRequired;
 $('launch').disabled=busy||remoteBlocked||!lastStatus||lastStatus?.nativeRejected||lastStatus?.recoveryRequired||lastStatus?.processAlive;
 $('profile-select').disabled=busy||remoteBlocked;
 for(const input of document.querySelectorAll('.tab input,.tab select,.tab textarea')){
  if(busy&&!input.hasAttribute('data-busy-disabled')){input.dataset.busyDisabled=String(input.disabled);input.disabled=true;}
  else if(!busy&&input.hasAttribute('data-busy-disabled')){input.disabled=input.dataset.busyDisabled==='true';delete input.dataset.busyDisabled;}
 }
 $('new-binding').disabled=busy||!editorReady();
 for(const button of document.querySelectorAll('.assign,.bind button,.keycap'))button.disabled=busy||(button.classList.contains('keycap')&&boot.reserved.includes(Number(button.dataset.source)));
 renderPending();
 for(const id of ['reset','baseline','duplicate','import'])$(id).disabled=busy||remoteBlocked;
 $('settings-save').disabled=busy||remoteBlocked||!settingsDirty;
 $('save').hidden=nativeLiveConnected();$('save').textContent='Сохранить без применения';$('apply').hidden=true;
 $('draft-state').textContent=liveState|| (staleStore?'Сохранённая версия изменилась, черновики сохранены':dirty?'Есть черновик':'Сохранён');
 $('revision').textContent=`Версия ${store?.revision||'…'}`;
 $('draft-conflict').hidden=!staleStore;
 $('resolve-drafts').disabled=!staleStore||busy||releaseBusy;
 $('resolve-drafts').textContent=Object.keys(remoteBoot?.store.profiles||{}).length>=20?'Сверить с существующим профилем':'Сохранить оба черновика отдельной копией';
 $('restore-drafts').disabled=busy||releaseBusy;
}
function validRadialMenu(m){
 return isObject(m)&&['enabled','gain','open_actions'].every(k=>Object.hasOwn(m,k))&&Object.keys(m).every(k=>['enabled','gain','open_actions','open_delay_ms'].includes(k))&&
  (!Object.hasOwn(m,'open_delay_ms')||(Number.isInteger(m.open_delay_ms)&&m.open_delay_ms>=0&&m.open_delay_ms<=1500))&&typeof m.enabled==='boolean'&&
  typeof m.gain==='number'&&Number.isFinite(m.gain)&&m.gain>=.0001&&m.gain<=1&&
  Array.isArray(m.open_actions)&&m.open_actions.length<=3&&(!m.enabled||m.open_actions.length>0)&&
  m.open_actions.every(a=>['L1','R1','Left'].includes(a))&&new Set(m.open_actions).size===m.open_actions.length;
}
function validLiveProfile(p){
 if(Object.hasOwn(p,'radial_menu')&&!validRadialMenu(p.radial_menu))return false;
 const num=(x,lo,hi)=>typeof x==='number'&&Number.isFinite(x)&&x>=lo&&x<=hi;
 if(!p.name?.trim()||p.name.length>120||!num(p.dpi,0,1000000)||!num(Object.hasOwn(p,'radial_deadzone')?p.radial_deadzone:0,0,.99))return false;
 for(const name of ['hip','ads']){
  const m=p[name];if(!num(m.gain,.000001,1)||!num(m.smoothing_ms,0,1000)||!num(m.cm360,0,10000))return false;
  if(Boolean(m.x.length)!==Boolean(m.y.length)||m.x.length&&(p.dpi<=0||m.cm360<=0))return false;
  for(const axis of [m.x,m.y])if(axis.length){
   if(axis.length<2)return false;
   for(let i=0;i<axis.length;i++)if(!num(axis[i][0],0,1000000)||!num(axis[i][1],0,1)||
    (!i?axis[i][0]!==0:axis[i][0]<=axis[i-1][0]||axis[i][1]<=axis[i-1][1]))return false;
  }
 }
 const r=p.recoil;
 if(r&&(!num(r.vertical,0,.25)||r.enabled!==undefined&&typeof r.enabled!=='boolean'||
  r.mode!==undefined&&!['constant','pulse'].includes(r.mode)||r.pulse_peak!==undefined&&!num(r.pulse_peak,.001,.25)||
  r.period_ms!==undefined&&(!num(r.period_ms,32,496)||r.period_ms%8)))return false;
 const f=p.rapid_fire;
 return !f||typeof f.enabled==='boolean'&&num(f.interval_ms,64,1000)&&num(f.press_ms,16,496)&&
  f.interval_ms%8===0&&f.press_ms%8===0&&f.press_ms<f.interval_ms;
}
function cancelLive(){clearTimeout(liveTimer);liveTimer=null;++liveEpoch;liveState='';liveFailed=false;liveNeeded=false;liveWaiting=false;}
function queueLive(){
 clearTimeout(liveTimer);
 if(!validLiveProfile(draft)){liveState='Ожидается допустимое значение';buttons();return;}
 if(staleStore||busy||releaseBusy||liveFailed||lastStatus?.recoveryRequired)return;
 liveTimer=setTimeout(()=>{liveTimer=null;void sendLive();},250);
}
function changed(runtimeField){
 ++liveEdit;liveFailed=false;liveNeeded=true;
 if(typeof runtimeField==='string')runtimeEdits.set(runtimeField,liveEdit);
 dirty=JSON.stringify(draft)!==JSON.stringify(store.profiles[store.selected]);
 liveState='Ожидается применение';queueLive();buttons();
}
function syncRuntime(){
 const s=lastStatus,r=s?.recoil;
 if(liveFlight||r?.profileRevision!==s?.workerRevision||s?.capture!=='active'||s.connected!==true||r?.fresh!==true||failed||sampleAge()>=1000)return;
 let changedRuntime=false;
 for(const [key,value] of [['enabled',r.enabled],['vertical',r.strengthPercent/100],['rapidFireEnabled',r.rapidFireEnabled]]){
  const input=$(runtimeInputs[key]);
  if(runtimeEdits.has(key)||document.activeElement===input||
   (key==='vertical'?!Number.isFinite(value):typeof value!=='boolean'))continue;
  const section=key==='rapidFireEnabled'?'rapid_fire':'recoil',field=key==='rapidFireEnabled'?'enabled':key;
  const defaults=section==='recoil'?{vertical:0}:{enabled:false,interval_ms:120,press_ms:40};
  draft[section]={...defaults,...draft[section]};
  if(draft[section][field]===value)continue;
  draft[section][field]=value;changedRuntime=true;
  if(input){if(key==='vertical')input.value=value*100;else input.checked=value;}
 }
 if(changedRuntime){++liveEdit;dirty=true;if(!liveFailed){liveNeeded=true;liveState='Runtime изменён, сохраняется';queueLive();}buttons();}
}
async function sendLive(){
 if(liveFlight||busy||releaseBusy||staleStore||liveFailed||(!dirty&&!liveNeeded)||!validLiveProfile(draft))return;
 if(!liveAvailable()){
  liveWaiting=true;liveState='Нет подключения';buttons();return;
 }
 const edit=liveEdit,epoch=liveEpoch,ticket=intent,instance=serviceInstance;
 const previousCapture=lastStatus.capture;
 const profile=clone(draft),fields=Array.from(runtimeEdits.keys());
 liveWaiting=false;liveState='Применяется';
 // Polls begun before this operation cannot publish over its ACK.
 ++statusGeneration;liveFlight={edit,epoch,status:lastStatus};buttons();
 let acknowledged=false;
 try{
  const result=await request('live',{revision:store.revision,profile,runtimeFields:fields});
  if(epoch!==liveEpoch||ticket!==intent||instance!==serviceInstance)return;
  if(!result.store||result.status?.capture!==previousCapture||result.status?.connected!==true)
   throw Error('Native не подтвердил применение');
  ++bootstrapSequence;store=result.store;remoteBoot={...remoteBoot,store};
  for(const [key,version] of runtimeEdits)if(version<=edit)runtimeEdits.delete(key);
  const saved=store.profiles[store.selected];
  const option=Array.from($('profile-select').options).find(option=>option.value===store.selected);
  if(option)option.textContent=saved.name;
  for(const key of ['enabled','vertical','rapidFireEnabled'])if(!runtimeEdits.has(key)){
   const section=key==='rapidFireEnabled'?'rapid_fire':'recoil',field=key==='rapidFireEnabled'?'enabled':key;
   if(saved[section]&&document.activeElement!==$(runtimeInputs[key])){
    draft[section]={...draft[section],[field]:saved[section][field]};
    const input=$(runtimeInputs[key]);if(input){if(key==='vertical')input.value=saved[section][field]*100;else input.checked=saved[section][field];}
   }
  }
  dirty=JSON.stringify(draft)!==JSON.stringify(saved);
  liveNeeded=edit!==liveEdit;
  liveState=edit===liveEdit?'Применено':'Ожидается применение';acknowledged=true;
  lastStatus={...lastStatus,...result.status,savedRevision:store.revision,selectedId:store.selected};received=performance.now();
  ++statusGeneration;
 }catch(e){if(epoch===liveEpoch&&ticket===intent){liveFailed=true;liveState='Ошибка';error(e.message+' Черновик сохранён. Повтор после новой правки или явного восстановления.');}}
 finally{
  liveFlight=null;buttons();
  if(acknowledged&&epoch===liveEpoch&&ticket===intent&&liveEdit>edit)queueLive();
  void refresh();
 }
}
function acceptBootstrap(next){
 const changedInstance=serviceInstance!==null&&next.serviceInstance!==serviceInstance;
 if(assignment&&(changedInstance||next.store.revision!==store?.revision||next.store.selected!==store?.selected))endAssign();
 boot={...next,csrf:next.csrf};remoteBoot=next;
 if(!store){store=next.store;draft=clone(store.profiles[store.selected]);settingsDraft=clone(store.gameSettings);render();return;}
 if(changedInstance||next.store.revision!==store.revision||next.store.selected!==store.selected){
  if(dirty||settingsDirty){const wasStale=staleStore;staleStore=true;if(!wasStale)notice('Сохранённая версия изменилась. Сохраните оба черновика отдельной копией или экспортируйте их вместе.');}
  else{store=next.store;draft=clone(store.profiles[store.selected]);settingsDraft=clone(store.gameSettings);staleStore=false;render();}
 }
}
async function renewSession(expectedInstance){
 const sequence=++bootstrapSequence,ticket=intent,instance=serviceInstance;
 const next=await request('bootstrap');
 if(sequence!==bootstrapSequence||ticket!==intent||serviceInstance!==instance)return false;
 if(expectedInstance!==undefined&&next.serviceInstance!==expectedInstance)return false;
 if(next.serviceInstance===serviceInstance&&store&&next.store.revision<store.revision)return false;
 acceptBootstrap(next);serviceInstance=next.serviceInstance;buttons();return true;
}
async function mutate(command,extra={},message){
 const release=command==='release';
 if(release ? releaseBusy : (busy||releaseBusy||liveFlight))return false;
 const preserveProfile=command==='settings'&&liveNeeded&&!liveFailed;
 if(preserveProfile){clearTimeout(liveTimer);liveTimer=null;}else cancelLive();
 const ticket=++intent;
 if(release)releaseBusy=true;else busy=true;
 const pending=beginPending(command);
 buttons();if(lastStatus)renderStatus();error('');$('notice').hidden=true;
 try{
  const result=await request(command,{revision:store?.revision,...extra});
  if(ticket!==intent)return false;
  if(result.store){
   if(assignment)endAssign();
   ++bootstrapSequence;
   remoteBoot={...remoteBoot,store:result.store};
   if(command==='recover'&&(dirty||settingsDirty)){
    staleStore=true; // Keep the displayed source ID and both drafts together.
   }else{
    restoredSourceRevision=null;
    store=result.store;staleStore=false;
    if(['settings','fork-drafts','recover'].includes(command)){settingsDraft=clone(store.gameSettings);settingsDirty=false;}
    if(['save','create','select','reset','import','fork-drafts','recover'].includes(command)){draft=clone(store.profiles[store.selected]);dirty=false;runtimeEdits.clear();}
   }
   render();
  }
  if(command==='save')liveState='Сохранено без применения';
  if(command==='recover'){
   const s=result.status;
   const released=s?.releaseConfirmed===true&&s.releaseProof==='native-cleanup'&&s.capture==='off'&&s.cleanupFinished===true&&
    typeof s.processId==='string'&&typeof s.sessionId==='string'&&
    (!lastStatus?.processId||s.processId===lastStatus.processId)&&(!lastStatus?.sessionId||s.sessionId===lastStatus.sessionId);
   message=released?'Состояние сверено с клиентом. Захват выключен.':'Хранилище восстановлено. Выключение ввода не подтверждено.';
  }
  if(['create','select','reset','import','fork-drafts'].includes(command)){
   ++liveEdit;liveNeeded=true;liveState='Ожидается применение';
   for(const key of Object.keys(runtimeInputs))runtimeEdits.set(key,liveEdit);
   queueLive();
  }
  if(changesDevice(command))invalidateStatus();
  if(message)notice(result.status?.adapter==='baseline'?'Helper подтвердил команду. Свежий статус захвата baseline недоступен.':message);
  return true;
 }catch(e){
  if(ticket===intent){
   error(e.message);
   if(e.csrf){try{await renewSession();if(ticket===intent)error('Сессия службы обновлена. Черновики сохранены. Команда не повторялась.');}catch(renew){if(ticket===intent)error(renew.message);}}
  }
  return false;
 }finally{
  if(release)releaseBusy=false;else busy=false;
  pendingRequests.delete(pending);
  if(pending.cancelled)invalidateStatus();
  if(lastStatus&&!failed)renderStatus();
  buttons();void refresh();if(liveNeeded)queueLive();
 }
}
function keepDraft(){if(!dirty)return true;return confirm('Есть несохранённый черновик. Отменить его изменения?');}
function render(){
 if(assignment)guardAssignment();
 const select=$('profile-select');select.replaceChildren();
 for(const [id,p] of Object.entries(store.profiles)){const option=node('option',p.name);option.value=id;option.selected=id===store.selected;select.append(option);}
 $('profile-name').value=draft.name;$('deadzone').value=draft.radial_deadzone||0;$('dpi').value=draft.dpi;
 renderBindings();renderAim();renderCalibration();renderSettings();buttons();
}
function renderBindings(){
 const container=$('action-groups');container.replaceChildren();
 groups.forEach(([title,actions],index)=>{
  const group=node('section',undefined,'group'),heading=node('div',undefined,'group-heading');heading.append(node('h3',title));group.append(heading);
  const cards=node('div',undefined,'actions');
  for(const [action,,name,note] of actions){
   if(!boot.actions.includes(action))continue;
   const card=node('article',undefined,'action-card');card.dataset.action=action;
   const badge=node('div',undefined,'controller-label');badge.append(controllerGlyph(action));card.append(badge);
   const content=node('div',undefined,'action-content');content.append(node('div',name,'action-name'),node('div',actionLabel(action),'controller-name'),node('div',note,'action-note'));
   const binds=node('div',undefined,'bindings');
   for(const [source,value] of Object.entries(draft.binds))if(value===action){
    const cap=node('div',undefined,'bind');cap.dataset.source=source;cap.append(controllerGlyph(action),node('span',sourceLabel(source)));
    const remove=node('button','×');remove.title=`Убрать ${sourceLabel(source)}`;remove.setAttribute('aria-label',remove.title);
    remove.onclick=()=>{delete draft.binds[source];changed();renderBindings();renderAim();};cap.append(remove);binds.append(cap);
   }
   const assignButton=node('button','+ Назначить','assign');assignButton.setAttribute('aria-label',`Назначить: ${actionLabel(action)}`);assignButton.onclick=()=>beginAssign(action);binds.append(assignButton);content.append(binds);card.append(content);cards.append(card);
  }group.append(cards);container.append(group);
 });
 const keyboard=$('keyboard');keyboard.replaceChildren();
 const rows=['Escape Digit1 Digit2 Digit3 Digit4 Digit5 Digit6 Digit7 Digit8 Digit9 Digit0 Minus Equal Backspace','Tab KeyQ KeyW KeyE KeyR KeyT KeyY KeyU KeyI KeyO KeyP BracketLeft BracketRight Backslash','CapsLock KeyA KeyS KeyD KeyF KeyG KeyH KeyJ KeyK KeyL Semicolon Quote Enter','ShiftLeft KeyZ KeyX KeyC KeyV KeyB KeyN KeyM Comma Period Slash ShiftRight','Backquote MetaLeft Space MetaRight ArrowLeft ArrowUp ArrowDown ArrowRight','MouseLeft MouseMiddle MouseRight MouseBack MouseForward'];
 for(const row of rows){const line=node('div',undefined,'keyrow');for(const code of row.split(' ')){
  const source=boot.codes[code],action=draft.binds[source],reserved=boot.reserved.includes(source);
  const cap=node('button',undefined,'keycap'+(action?' bound':'')+(['Tab','ShiftLeft','ShiftRight','Enter','Backspace'].includes(code)?' wide':'')+(code==='Space'?' space':''));
  cap.dataset.source=source;cap.append(node('span',keyLabel(code),'key-name'));if(action)cap.append(controllerGlyph(action),node('span',shortAction(action),'key-action'));
  cap.title=`${keyLabel(code)}: ${reserved?'зарезервировано':action?`${actionMeaning(action)} · ${actionLabel(action)}`:'не назначено'}`;cap.setAttribute('aria-label',cap.title);cap.disabled=reserved;
  cap.onclick=()=>beginAssign(action||'Cross',undefined,source);line.append(cap);
 }keyboard.append(line);}
 buttons();
}
function editorReady(){return Boolean(boot?.codes&&boot.actions?.length&&store?.profiles?.[store.selected]&&draft);}
function currentAssignment(){
 return assignmentContext&&editorReady()&&assignmentContext.id===store.selected&&assignmentContext.revision===store.revision&&
  assignmentContext.service===boot.serviceInstance&&assignmentContext.draft===draft&&
  (pendingSource===null||assignmentContext.binds[pendingSource]===draft.binds[pendingSource]);
}
function guardAssignment(){
 if(currentAssignment())return true;
 if(assignment){endAssign();notice('Профиль или назначение изменились. Откройте редактор заново.');}
 return false;
}
function beginAssign(action,_title,source){
 if(busy||!editorReady())return;
 assignmentContext={id:store.selected,revision:store.revision,service:boot.serviceInstance,draft,binds:{...draft.binds}};
 suppressRecordContextMenu=false;
 assignment=boot.actions.includes(action)?action:boot.actions[0];pendingSource=source===undefined?null:Number(source);listening=false;
 const picker=$('assign-source');picker.replaceChildren(node('option','Выберите клавишу или кнопку мыши'));
 picker.firstChild.value='';
 for(const [code,value] of Object.entries(boot.codes))if(!boot.reserved.includes(value)){
  const option=node('option',keyLabel(code));option.value=value;picker.append(option);
 }
 picker.value=pendingSource===null?'':String(pendingSource);
 const actions=$('assign-actions');actions.replaceChildren();
 for(const value of boot.actions){
  const choice=node('button',undefined,'action-choice');choice.type='button';choice.dataset.action=value;
  choice.append(controllerGlyph(value),node('span',choiceCaption(value),'choice-action'),node('span',actionLabel(value),'choice-control'));choice.title=`${actionMeaning(value)} · ${actionLabel(value)}`;choice.setAttribute('aria-label',choice.title);choice.onclick=()=>{assignment=value;stopListening();updateAssignment();};actions.append(choice);
 }
 $('assign-dialog').showModal();updateAssignment();picker.focus();
}
function stopListening(){listening=false;$('record-source').textContent='Записать нажатие';$('record-source').setAttribute('aria-pressed','false');}
function updateAssignment(){
 if(!guardAssignment())return;
 for(const choice of $('assign-actions').children)choice.setAttribute('aria-pressed',String(choice.dataset.action===assignment));
 $('assign-preview').replaceChildren(controllerGlyph(assignment),node('span',`${actionMeaning(assignment)} · ${actionLabel(assignment)}`));
 const old=pendingSource===null?null:draft.binds[pendingSource],conflict=old&&old!==assignment;
 const aliases=Object.entries(draft.binds).filter(([source,value])=>value===assignment&&Number(source)!==pendingSource).map(([source])=>sourceLabel(source));
 $('assign-message').textContent=pendingSource===null?'Выберите физическую клавишу.':conflict?`${sourceLabel(pendingSource)}: сейчас ${actionLabel(old)}. Новое назначение: ${actionLabel(assignment)}.`:`${sourceLabel(pendingSource)} → ${actionLabel(assignment)}`;
 $('assign-aliases').textContent=aliases.length?`Эту кнопку уже нажимают: ${aliases.join(', ')}. Можно назначить ещё один источник.`:'Этой кнопке пока не назначены другие источники.';
 const invalid=pendingSource===57&&assignment!=='DodgeRoll';
 if(invalid)$('assign-message').textContent='Caps Lock поддерживает только Перекат.';
 $('replace-binding').hidden=!conflict;$('replace-binding').disabled=invalid;$('commit-binding').hidden=Boolean(conflict);$('commit-binding').disabled=pendingSource===null||invalid;
}
function endAssign(){stopListening();assignment=null;assignmentContext=null;pendingSource=null;suppressRecordContextMenu=false;$('assign-dialog').close();}
function assign(source){
 if(!guardAssignment())return;
 if(source===undefined||!Object.values(boot.codes).includes(Number(source))){$('assign-message').textContent='Эта клавиша или кнопка не поддерживается. Выберите другую.';return;}
 source=Number(source);
 if(boot.reserved.includes(source)){$('assign-message').textContent='Клавиша зарезервирована. Выберите другую.';return;}
 pendingSource=source;$('assign-source').value=String(source);stopListening();updateAssignment();
}
function commitSource(source){
 if(!guardAssignment()||busy||!assignment||source===null||!Object.values(boot.codes).includes(Number(source))||boot.reserved.includes(Number(source)))return;
 if(Number(source)===57&&assignment!=='DodgeRoll')return;
 draft.binds[source]=assignment;endAssign();changed();renderBindings();renderAim();notice('Назначение применяется автоматически.');
}
$('new-binding').onclick=()=>beginAssign('Cross');
$('assign-source').onchange=e=>{stopListening();pendingSource=e.target.value===''?null:Number(e.target.value);updateAssignment();};
$('record-source').onclick=()=>{
 if(listening){stopListening();updateAssignment();return;}
 listening=true;$('record-source').textContent='Остановить запись';$('record-source').setAttribute('aria-pressed','true');
 $('assign-message').textContent='Нажмите клавишу или кнопку мыши в области записи. Esc отменяет назначение.';$('record-target').focus();
};
document.addEventListener('keydown',e=>{
 if(!assignment){if(e.code==='Escape'){cancelLive();void mutate('release',{},'Ввод выключен.');}return;}
 if(e.code==='Escape'){e.preventDefault();e.stopPropagation();endAssign();return;}
 if(!listening||e.target.closest?.('input,select,textarea,button,a,summary,[role="button"],[contenteditable="true"]'))return;
 if(e.code==='Tab'&&e.target!==$('record-target'))return;
 e.preventDefault();e.stopPropagation();if(!e.repeat)assign(boot.codes[e.code]);
},true);
$('record-target').addEventListener('pointerdown',e=>{
 if(!assignment||!listening)return;e.preventDefault();e.stopPropagation();suppressRecordContextMenu=e.button===2;assign(e.button===0?boot.codes.MouseLeft:e.button===2?boot.codes.MouseRight:e.button===1?boot.codes.MouseMiddle:e.button===3?boot.codes.MouseBack:e.button===4?boot.codes.MouseForward:undefined);
});
$('record-target').addEventListener('auxclick',e=>{if(assignment){e.preventDefault();e.stopPropagation();}});
$('record-target').addEventListener('contextmenu',e=>{if(listening||suppressRecordContextMenu){e.preventDefault();e.stopPropagation();}suppressRecordContextMenu=false;});
$('assign-dialog').addEventListener('cancel',e=>{e.preventDefault();endAssign();});
$('assign-dialog').addEventListener('close',()=>{stopListening();assignment=null;assignmentContext=null;pendingSource=null;suppressRecordContextMenu=false;});
window.addEventListener('blur',()=>{if(assignment)endAssign();});
$('replace-binding').onclick=()=>commitSource(pendingSource);$('commit-binding').onclick=()=>commitSource(pendingSource);$('cancel-assign').onclick=endAssign;
function renderAim(){
 $('aim-modes').replaceChildren();
 for(const mode of ['hip','ads']){const panel=node('section',undefined,'panel');panel.append(node('p',mode.toUpperCase(),'mode-title'),node('h3',mode==='hip'?'Без прицела':'В прицеле, L2'));
  for(const [key,title,min,max,step,description] of [
   ['gain','Чувствительность, gain',.000001,1,.001,'От 0.000001 до 1. Больше значение, больше отклонение стика при том же движении.'],
   ['smoothing_ms','Сглаживание, мс',0,1000,1,'От 0 до 1000 мс. 0 отключает фильтр. Большие значения задерживают отклик.'],
   ['cm360','cm/360',0,10000,1,'Не участвует без измеренной LUT. Импортированная LUT использует DPI и cm/360.']]){
   const measured=Boolean(draft[mode].x.length);
   const label=node('label',title),input=node('input');input.id=mode+'-'+key;input.type='number';input.min=min;input.max=max;input.step=step;input.value=draft[mode][key];input.disabled=(key==='cm360'&&!measured)||(key==='gain'&&measured);
   input.oninput=()=>{draft[mode][key]=input.value===''?null:Number(input.value);changed();};label.append(input);panel.append(label,node('p',key==='gain'&&measured?'При измеренной LUT gain не участвует. Чувствительность задаётся cm/360.':description));
  }panel.append(node('p',draft[mode].x.length?`Импортирована LUT, ${draft[mode].x.length} точек X. Таблица сохраняется при экспорте.`:'LUT не измерена. Используется baseline gain.'));$('aim-modes').append(panel);
 }
 const menuPanel=node('section',undefined,'panel');
 menuPanel.append(node('h3','Круговые меню'));
 const menuDefaults={enabled:false,gain:.015,open_delay_ms:350,open_actions:['L1','R1','Left']};
 const updateMenu=patch=>{draft.radial_menu={...menuDefaults,...draft.radial_menu,...patch};changed();};
 const menuLabel=node('label','Сохранять выбор мышью'),menuToggle=node('input');
 menuToggle.id='radial-menu-enabled';menuToggle.type='checkbox';menuToggle.checked=draft.radial_menu?.enabled===true;
 menuToggle.onchange=()=>updateMenu({enabled:menuToggle.checked});menuLabel.append(menuToggle);menuPanel.append(menuLabel);
 const gainLabel=node('label','Чувствительность выбора'),gainInput=node('input');
 gainInput.id='radial-menu-gain';gainInput.type='number';gainInput.min=.0001;gainInput.max=1;gainInput.step=.001;
 gainInput.value=draft.radial_menu?.gain??.015;
 gainInput.oninput=()=>updateMenu({gain:gainInput.value===''?null:Number(gainInput.value)});
 gainLabel.append(gainInput);menuPanel.append(gainLabel);
 const delayLabel=node('label','Задержка открытия колеса, мс'),delayInput=node('input');
 delayInput.id='radial-menu-open-delay';delayInput.type='number';delayInput.min=0;delayInput.max=1500;delayInput.step=1;
 delayInput.value=draft.radial_menu?.open_delay_ms??350;
 delayInput.oninput=()=>updateMenu({open_delay_ms:delayInput.value===''?null:Number(delayInput.value)});
 delayLabel.append(delayInput);menuPanel.append(delayLabel,node('p','Больше значение — дольше защита от движения камеры. 0 — выбор сразу.'));

 for(const [action,title] of [['L1','Предметы'],['R1','Метки'],['Left','Реплики']]){
  const bound=Object.entries(draft.binds).filter(([,a])=>a===action).map(([s])=>sourceLabel(s)).join(', ')||'не назначено';
  const label=node('label',`${title}: ${bound}`),input=node('input');input.type='checkbox';input.id=`radial-menu-${action}`;
  input.checked=(draft.radial_menu?.open_actions??menuDefaults.open_actions).includes(action);
  input.onchange=()=>{const actions=new Set(draft.radial_menu?.open_actions??menuDefaults.open_actions);if(input.checked)actions.add(action);else actions.delete(action);updateMenu({open_actions:[...actions]});};
  label.append(input);menuPanel.append(label);
 }
 menuPanel.append(node('p','Удерживайте назначенную клавишу, чтобы открыть меню. Мышь перемещает выбор от текущего положения. После закрытия новое меню начинается из центра.'));
 $('aim-modes').append(menuPanel);
 const recoilPanel=node('section',undefined,'panel');
 recoilPanel.append(node('h3','Компенсация отдачи'));
 const recoilToggleLabel=node('label','Компенсация включена'),recoilToggle=node('input');
 recoilToggle.type='checkbox';recoilToggle.id='recoil-enabled';recoilToggle.checked=draft.recoil?.enabled===true;
 recoilToggle.onchange=()=>{draft.recoil={vertical:draft.recoil?.vertical??0,...draft.recoil,enabled:recoilToggle.checked};changed('enabled');};
 recoilToggleLabel.append(recoilToggle);recoilPanel.append(recoilToggleLabel);
 const pulseMode=draft.recoil?.mode==='pulse';
 const modeLabel=node('label','Режим компенсации'),modeSelect=node('select');modeSelect.id='recoil-mode';
 for(const [value,title] of [['constant','Постоянная'],['pulse','Импульсная для слабой компенсации']]){const option=node('option',title);option.value=value;option.selected=(draft.recoil?.mode||'constant')===value;modeSelect.append(option);}
 modeSelect.onchange=()=>{draft.recoil={vertical:draft.recoil?.vertical??0,...draft.recoil,mode:modeSelect.value};changed();renderAim();};modeLabel.append(modeSelect);recoilPanel.append(modeLabel);
 const recoilLabel=node('label',pulseMode?'Целевая средняя сила сигнала, %':'Сила вниз, % хода стика'),recoilInput=node('input');
 recoilInput.id='recoil-vertical';recoilInput.type='number';recoilInput.min=0;recoilInput.max=25;recoilInput.step=.1;recoilInput.value=draft.recoil?.vertical===null?'':(draft.recoil?.vertical||0)*100;
 recoilInput.oninput=()=>{draft.recoil={...draft.recoil,vertical:recoilInput.value===''?null:Number(recoilInput.value)/100};changed('vertical');};
 const recoilBindings=action=>Object.entries(draft.binds).filter(([,value])=>value===action).map(([source])=>sourceLabel(source)).join(', ')||'не назначено';
 recoilLabel.append(recoilInput);recoilPanel.append(recoilLabel,node('p',`Пробная настройка. Работает при одновременном прицеливании и огне. Текущий выбор применяется и сохраняется автоматически. Переключение: ${recoilBindings('RecoilToggle')}. Уменьшить: ${recoilBindings('RecoilDecrease')}. Увеличить: ${recoilBindings('RecoilIncrease')}. Поле показывает текущую силу, включая изменения клавишами.`));
 if(pulseMode){
  for(const [key,id,title,value,min,max,step,scale] of [['pulse_peak','recoil-peak','Сила импульса, %',(draft.recoil?.pulse_peak??.11)*100,.1,25,.1,100],['period_ms','recoil-period','Период импульсов, мс',draft.recoil?.period_ms??120,32,496,8,1]]){
   const label=node('label',title),input=node('input');input.id=id;input.type='number';input.min=min;input.max=max;input.step=step;input.value=draft.recoil?.[key]===null?'':value;
   input.oninput=()=>{draft.recoil={...draft.recoil,[key]:input.value===''?null:Number(input.value)/scale};changed();};label.append(input);recoilPanel.append(label);
  }
  recoilPanel.append(node('p','Если целевая сила равна силе импульса или выше, вывод становится постоянным. При меньшей цели импульсы чередуются с паузами. Цель 9% при импульсе 11% задаёт долю включения около 82%. Фактическая сила и плавность зависят от игры; это пробная настройка. Радиальная компенсация deadzone не меняется.'));
 }
 $('aim-modes').append(recoilPanel);
 const rapidPanel=node('section',undefined,'panel');rapidPanel.append(node('h3','Автотап огня'));
 const rapidDefaults={enabled:false,interval_ms:120,press_ms:40};
 const rapidLabel=node('label','Автотап включён'),rapidToggle=node('input');
 rapidToggle.type='checkbox';rapidToggle.id='rapid-fire-enabled';rapidToggle.checked=draft.rapid_fire?.enabled===true;
 rapidToggle.onchange=()=>{draft.rapid_fire={...rapidDefaults,...draft.rapid_fire,enabled:rapidToggle.checked};changed('rapidFireEnabled');};
 rapidLabel.append(rapidToggle);rapidPanel.append(rapidLabel);
 for(const [key,id,title,min,max] of [['interval_ms','rapid-fire-interval','Интервал между нажатиями, мс',64,1000],['press_ms','rapid-fire-press','Длительность нажатия, мс',16,496]]){
  const label=node('label',title),input=node('input');input.id=id;input.type='number';input.min=min;input.max=max;input.step=8;input.value=draft.rapid_fire?.[key]===null?'':draft.rapid_fire?.[key]??rapidDefaults[key];
  input.oninput=()=>{draft.rapid_fire={...rapidDefaults,...draft.rapid_fire,[key]:input.value===''?null:Number(input.value)};changed();};label.append(input);rapidPanel.append(label);
 }
 rapidPanel.append(node('p',`При удержании огня повторяет нажатия, в том числе без прицеливания. На автоматическом оружии выключайте: автотап прерывает непрерывный огонь. Игра ограничивает темп стрельбы. Оба значения должны быть кратны 8 мс. Длительность нажатия должна быть меньше интервала. Временное переключение: ${recoilBindings('RapidFireToggle')}. Клавишу можно назначить в разделе привязок. Применение профиля возвращает состояние галочки.`));
 $('aim-modes').append(rapidPanel);
 $('dpi-note').textContent=draft.hip.x.length||draft.ads.x.length?'Импортированная LUT использует DPI. Редактор сохраняет все её точки.':'Без измеренной LUT DPI и cm/360 не участвуют в расчёте.';
}
function renderCalibration(){
 const container=$('calibration-fields');container.replaceChildren();
 const fields=[['game_version','Версия игры',128],['fov','Поле зрения, FOV',128],['optic','Оптика',128],['date','Дата измерения',64],['settings','Условия и настройки измерения',2048],['notes','Заметки',2048]];
 for(const [key,title,max] of fields){
  const label=node('label',title),input=node(max>128?'textarea':'input');input.id='calibration-'+key;input.maxLength=max;input.value=draft.calibration?.[key]||'';
  input.oninput=()=>{draft.calibration={...draft.calibration};if(input.value)draft.calibration[key]=input.value;else delete draft.calibration[key];if(!Object.keys(draft.calibration).length)delete draft.calibration;changed();};
  label.append(input);container.append(label);
 }
}
const settingNames={response:'Кривая отклика',lookBoost:'Hip: Boost H',lookBoostVertical:'Hip: Boost V',ramp:'Hip: Ramp, с',zoomBoostHorizontal:'ADS: Boost H',zoomBoostVertical:'ADS: Boost V',zoomRamp:'ADS: Ramp, с',lookDeadzone:'Мёртвая зона обзора',moveDeadzone:'Мёртвая зона движения',horizontal:'Чувствительность H',vertical:'Чувствительность V',zoom:'Zoom, %',scoped:'Scoped, %',aimAssist:'Помощь прицеливания'};
function completeSettings(){return Object.keys(boot.recommended).every(key=>settingsDraft.actual[key]!==undefined&&settingsDraft.actual[key]!==null);}
function settingsChanged(){settingsDirty=JSON.stringify(settingsDraft)!==JSON.stringify(store.gameSettings);$('settings-date').textContent=settingsDraft.confirmedAt?`Подтверждение в черновике: ${settingsDraft.confirmedAt}`:'Изменённые значения не подтверждены на PS5.';buttons();}
$('settings-confirm').onchange=()=>{if($('settings-confirm').checked&&!completeSettings()){$('settings-confirm').checked=false;error('Сначала запишите все значения с экрана PS5. Пустые поля ещё не сверены.');return;}settingsDraft.confirmedAt=$('settings-confirm').checked?new Date().toISOString():'';settingsChanged();};
function renderSettings(){
 $('settings-fields').replaceChildren();$('settings-confirm').checked=Boolean(settingsDraft.confirmedAt)&&completeSettings();
 $('settings-date').textContent=!completeSettings()?'Checklist дополнен отдельными Boost и Ramp. Пустые поля ещё не сверены; прежние значения сохранены.':settingsDraft.confirmedAt?`Вы подтвердили checklist: ${settingsDraft.confirmedAt}`:'Фактические значения не подтверждены. Ниже заполнен последний известный baseline.';
 for(const [key,recommended] of Object.entries(boot.recommended)){
  const row=node('div',undefined,'setting-row');row.append(node('span',settingNames[key]),node('span',typeof recommended==='boolean'?(recommended?'Вкл':'Выкл'):String(recommended)));
  const input=node(key==='response'||key==='aimAssist'?'select':'input');input.dataset.setting=key;input.setAttribute('aria-label',settingNames[key]);
  if(key==='response'||key==='aimAssist')for(const value of (key==='response'?['Linear','Exponential','Другое']:['false','true'])){const option=node('option',value==='false'?'Выкл':value==='true'?'Вкл':value);option.value=value;input.append(option);}
  else{input.type='number';input.min=0;input.max=10000;input.step='any';input.placeholder='Не сверено';}
  input.value=settingsDraft.actual[key]===undefined||settingsDraft.actual[key]===null?'':String(settingsDraft.actual[key]);
  input.oninput=()=>{settingsDraft.actual[key]=key==='response'?input.value:key==='aimAssist'?input.value==='true':input.value===''?null:Number(input.value);settingsDraft.confirmedAt='';$('settings-confirm').checked=false;settingsChanged();};
  row.append(input);$('settings-fields').append(row);
 }
}
function refresh(){
 if(refreshFlight){refreshQueued=true;return refreshFlight;}
 clearTimeout(pollTimer);
 const ticket=intent,sequence=++requestSequence,generation=statusGeneration;
 refreshFlight=(async()=>{
  try{
   const result=await request('status');
   if(ticket!==intent||generation!==statusGeneration||sequence<acceptedSequence)return;
   if(serviceInstance!==null&&serviceInstance!==result.serviceInstance){
    sampleSequence=-1;if(!await renewSession(result.serviceInstance))return;
    if(ticket!==intent||generation!==statusGeneration)return;
   }
   serviceInstance=result.serviceInstance;
   if(result.sampleSequence<sampleSequence)return;
   acceptedSequence=sequence;sampleSequence=result.sampleSequence??sampleSequence;
   const prior=liveFlight?.status||lastStatus;
   const fresh=!result.stale&&result.sampleAgeMs<1000;
   const sessionLost=prior&&['processId','sessionId','sessionGeneration'].some(key=>prior[key]!=null&&result[key]!=null&&prior[key]!==result[key]);
   if(fresh&&prior&&(sessionLost||prior.connected===true&&result.connected===false&&!liveWaiting||
      prior.processAlive===true&&result.processAlive===false&&!liveWaiting||
      prior.capture==='active'&&['off','releasing'].includes(result.capture))){
    const uncertain=liveFailed;cancelLive();liveFailed=uncertain;
    if(uncertain)liveState='Ошибка';else if(result.connected!==true)liveState='Нет подключения';
   }
   lastStatus=result;received=performance.now();failed=Boolean(result.stale);acceptAudio(result);syncRuntime();renderStatus();
   if(liveWaiting&&liveNeeded&&!liveFailed&&liveAvailable())queueLive();
   if(!liveFlight&&(result.savedRevision!==store.revision||result.selectedId!==store.selected)){
    await renewSession(result.serviceInstance);
   }
  }catch(e){if(ticket===intent&&generation===statusGeneration&&sequence>=acceptedSequence){failed=true;error(e.message);renderUnknown('Нет связи');}}
  finally{refreshFlight=null;buttons();const again=refreshQueued;refreshQueued=false;pollTimer=setTimeout(refresh,again?0:300);}
 })();return refreshFlight;
}
function renderUnknown(label){
 renderAudio();
 $('rapid-fire-runtime').textContent='Автотап: состояние неизвестно';
 $('recoil-runtime').textContent='Компенсация: состояние неизвестно';
 $('feedback-state').textContent='Локальная отправка: неизвестно';
 $('service-state').textContent=label;$('capture-state').textContent='Неизвестен';$('ps5-state').textContent='Неизвестен';
 $('applied-state').textContent='Не подтверждён';$('connection-title').textContent=label;$('live-dot').classList.add('error');
}
function renderRecoil(){
 const s=lastStatus,r=s?.recoil,age=r&&Number.isFinite(r.sampleAgeMs)?r.sampleAgeMs+Math.max(0,performance.now()-received):Infinity;
 const rapid=$('rapid-fire-runtime');
 rapid.textContent=!failed&&s?.connected===true&&sampleAge()<1000&&s.capture==='active'&&r?.fresh===true&&age<1000&&typeof r.rapidFireEnabled==='boolean'?`Автотап ${r.rapidFireEnabled?'включён':'выключен'}`:'Автотап: состояние неизвестно';
 const target=$('recoil-runtime');
 if(!s||failed||sampleAge()>=1000||s.connected!==true){target.textContent='Компенсация: состояние неизвестно';return;}
 if(s.capture==='off'){target.textContent='Компенсация: ввод выключен';return;}
 if(s.capture!=='active'||r?.fresh!==true||age>=1000||typeof r.enabled!=='boolean'||!Number.isFinite(r.strengthPercent)){
  target.textContent='Компенсация: состояние неизвестно';return;
 }
 if(!r.enabled){target.textContent=`Компенсация выключена · сила ${r.strengthPercent.toFixed(1)}%`;return;}
 const phase=r.applying?'передаёт поправку':!r.aiming?'ожидает прицеливания':!r.firing?'ожидает стрельбы':r.pulseMode&&r.pulsePause?'пауза импульса':'поправка приостановлена';
 const output=Number.isFinite(r.outputY)?` · выход Y ${(r.outputY/32767*100).toFixed(1)}%`:'';
 target.textContent=`Компенсация включена · ${r.pulseMode?'средний сигнал':'сила'} ${r.strengthPercent.toFixed(1)}% · ${phase}${output}`;
}
function renderFeedback(){
 renderRecoil();
 const s=lastStatus;
 if(!s||failed||sampleAge()>=1000){$('feedback-state').textContent='Локальная отправка: неизвестно';return;}
 const sendAge=Number.isFinite(s.stateFeedbackAttemptAgeMs)&&s.stateFeedbackAttemptAgeMs>=0?s.stateFeedbackAttemptAgeMs+Math.max(0,performance.now()-received):NaN;
 const sendKnown=s.connected===true&&Number.isFinite(sendAge)&&sendAge>=0&&sendAge<1000;
 const sendState=sendKnown&&s.stateFeedbackHealth==='ok'?'успешно':sendKnown&&s.stateFeedbackHealth==='error'?'ошибка':'неизвестно';
 $('feedback-state').textContent=`Локальная отправка: ${sendState}${sendKnown?', '+Math.round(sendAge)+' мс назад':''}. Получение PS5 не подтверждается. Tick: ${s.ticks??'—'}, пропущено периодов: ${s.skipped??'—'}.`;

}
function renderStatus(){
 renderAudio();
 const s=lastStatus;
 if(!s||failed||sampleAge()>=1000){renderUnknown('Статус устарел');return;}
 $('service-state').textContent='На связи';
 renderFeedback();

 $('ps5-state').textContent=s.processAlive?(s.connected===true?'Клиент / PS5 подключена':s.connected===false?'Клиент / PS5 отключена':'Клиент / PS5 неизвестно'):'Клиент не подтверждён';
 const capture={off:'Выключен',preparing:'Подготовка…',active:'Включён',releasing:'Освобождается…',error:'Ошибка',unknown:'Неизвестен'};
 $('capture-state').textContent=capture[s.capture]||'Неизвестен';
 $('applied-state').textContent=s.busy?'Операция…':!failed&&!staleStore&&s.appliedKnown&&s.savedRevision===store.revision&&s.selectedId===store.selected&&s.savedDigest===s.appliedDigest?'Соответствует сохранённому':s.appliedKnown?'Применён другой профиль':'Не подтверждён';
 $('connection-title').textContent=s.busy?'Операция на Mac mini…':s.capture==='active'?'Управление на Mac mini включено':'Mac mini · пульт на связи';
 $('status-reason').textContent=s.reason||s.error||(s.canApply?'Настройки применяются автоматически. Esc возвращает управление геймпаду.':'Для применения профиля нужна новая dashboard-сборка клиента.');
 $('live-dot').classList.toggle('error',s.capture==='error');
 renderPendingDevice();
 renderPending();
 const detail=`Источник: ${s.adapter||'ожидание'}. PID: ${s.pid||'неизвестен'}. Разрешения: ${s.permission||'unknown'}. Устройство: ${s.device||'unknown'}. Возраст permission snapshot: ${s.permissionAgeMs>=0?Math.round(s.permissionAgeMs)+' мс':'неизвестен'}. Saved digest: ${s.savedDigest||'неизвестен'}. Applied digest: ${s.appliedDigest||'неизвестен'}. ${s.legacyEvidence?'Исторический журнал, не свежая телеметрия: '+s.legacyEvidence:''}`;
 const describe=s=>s?`VID ${s.vid}, PID ${s.pid}${s.location===undefined?'':', location '+s.location}`:'неизвестен';
 $('status-details').textContent=detail+` Запрошена мышь: ${describe(s.requestedSelector)}. Выбор подтверждён: ${s.selectionConfirmed?'да':'нет'}. Последний выбор: ${describe(s.selectedSelector)}, возраст ${s.selectionAgeMs>=0?Math.round(s.selectionAgeMs)+' мс':'неизвестен'}. Клавиатуры: ${s.keyboardScope||'Все клавиатуры Mini, подтверждение backend недоступно'}.`;
}
$('save').onclick=()=>mutate('save',{profile:draft},'Профиль сохранён на Mac mini без применения.');
$('apply').onclick=()=>mutate('apply',{},'Клиент подтвердил профиль. Захват выключен.');
$('arm').onclick=()=>mutate('arm',{},'Backend подтвердил включение ввода. Esc выключает захват.');
$('release').onclick=()=>mutate('release',{},'Backend подтвердил выключение захвата.');
$('launch').onclick=()=>mutate('launch',{},'Соединение с PS5 подтверждено.');
$('refresh').onclick=async()=>{await renewSession();await refresh();};
$('recover').onclick=()=>mutate('recover',{},'Состояние сверено с клиентом. Захват выключен.');
$('profile-select').onchange=async()=>{const id=$('profile-select').value;if(!keepDraft()||!await mutate('select',{id},'Профиль выбран. Ожидается автоматическое применение.'))$('profile-select').value=store.selected;};
$('profile-name').oninput=e=>{draft.name=e.target.value;changed();};
$('deadzone').oninput=e=>{draft.radial_deadzone=e.target.value===''?null:Number(e.target.value);changed();};
$('dpi').oninput=e=>{draft.dpi=e.target.value===''?null:Number(e.target.value);changed();};
$('duplicate').onclick=()=>{const name=prompt('Название нового профиля',draft.name+' · копия');if(name)mutate('create',{id:uniqueProfileId(),profile:{...draft,name}},'Копия сохранена. Захват выключен.');};
$('export').onclick=()=>{const blob=new Blob([JSON.stringify(draft,null,2)+'\n'],{type:'application/json'});const a=node('a');a.href=URL.createObjectURL(blob);a.download='arc-raiders-profile.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
$('import').onclick=()=>{$('import-file').click();};
$('import-file').onchange=async e=>{
 const file=e.target.files[0];e.target.value='';if(!file)return;
 if(busy||releaseBusy)return;
 if(file.size>50000){error('Файл превышает 50 КиБ');return;}
 if(!keepDraft())return;
 const ticket=++intent,sourceId=store.selected,revision=store.revision;
 const drafts=JSON.stringify([draft,settingsDraft]);
 try{
  const profileText=await file.text();
  if(ticket!==intent||busy||releaseBusy||store.selected!==sourceId||store.revision!==revision||staleStore||JSON.stringify([draft,settingsDraft])!==drafts){
   if(ticket===intent)error('Импорт отменён: профиль или операция изменились. Оба черновика сохранены.');return;
  }
  await mutate('import',{profileText},'Профиль проверен. Ожидается автоматическое применение.');
 }catch(e){if(ticket===intent)error(e.message);}
};
$('reset').onclick=()=>{if(keepDraft()&&confirm('Вернуть полный ARC preset? Захват будет выключен.'))mutate('reset',{kind:'preset'},'ARC preset восстановлен. Ожидается автоматическое применение.');};
$('baseline').onclick=()=>{if(keepDraft()&&confirm('Восстановить принятый baseline? Захват будет выключен.'))mutate('reset',{kind:'baseline'},'Принятый baseline восстановлен. Ожидается автоматическое применение.');};
$('settings-save').onclick=()=>mutate('settings',{gameSettings:clone(settingsDraft)},'Checklist сохранён. Настройки игры на PS5 не менялись.');
$('resolve-drafts').onclick=async()=>{
 if(busy||releaseBusy)return;
 const ticket=++intent;
 if(!await renewSession()||ticket!==intent||busy||releaseBusy)return;
 const sourceId=store.selected;
 if(Object.keys(remoteBoot.store.profiles).length>=20){
  const id=prompt('Лимит 20 профилей. Укажите ID существующего профиля для явного сохранения обоих черновиков:\n'+Object.entries(remoteBoot.store.profiles).map(([id,p])=>id+': '+p.name).join('\n'),sourceId);
  if(id===null)return;
  if(!Object.hasOwn(remoteBoot.store.profiles,id)){error('Профиль с таким ID не найден. Оба черновика сохранены.');return;}
  if(!confirm(`Заменить профиль ${id} (${remoteBoot.store.profiles[id].name}) черновиком ${sourceId} и сохранить checklist?`))return;
  await reconcileExisting(id);return;
 }
 if(!confirm(`Создать отдельную копию черновика ${sourceId} и сохранить вместе с checklist? Текущие профили останутся в списке.`))return;
 await mutate('fork-drafts',{revision:remoteBoot.store.revision,sourceId,id:uniqueProfileId(),profile:clone(draft),gameSettings:clone(settingsDraft)},'Оба черновика сохранены отдельной копией. Можно продолжать редактирование.');
};
async function reconcileExisting(id){
 if(busy||releaseBusy)return;
 const ticket=++intent,profile=clone(draft),settings=clone(settingsDraft);
 let saved=remoteBoot.store;
 busy=true;buttons();error('');
 try{
  for(const [command,extra] of [
   ...(saved.selected===id?[]:[['select',{id}]]),['save',{profile}],['settings',{gameSettings:settings}]]){
   if(ticket!==intent)return;
   const result=await request(command,{revision:saved.revision,...extra});
   if(ticket!==intent)return;
   if(!result.store||result.store.selected!==id)throw Error('Сохранённый профиль не подтверждён');
   saved=result.store;remoteBoot={...remoteBoot,store:saved};++bootstrapSequence;
  }
  store=saved;draft=clone(profile);settingsDraft=clone(settings);dirty=settingsDirty=staleStore=false;restoredSourceRevision=null;
  ++liveEdit;liveFailed=false;liveNeeded=true;liveWaiting=false;runtimeEdits.clear();
  for(const key of Object.keys(runtimeInputs))runtimeEdits.set(key,liveEdit);
  liveState='Ожидается применение';
  notice('Оба черновика сохранены в выбранный профиль.');render();
 }catch(e){if(ticket===intent)error(e.message+' Оба черновика сохранены; требуется повторная сверка.');}
 finally{
  if(ticket!==intent||dirty||settingsDirty){draft=profile;settingsDraft=settings;staleStore=true;}
  busy=false;buttons();void refresh();if(ticket===intent&&!staleStore&&liveNeeded)queueLive();
 }
}
$('export-drafts').onclick=()=>{
 const value={version:1,sourceId:store.selected,sourceRevision:restoredSourceRevision??store.revision,profile:clone(draft),gameSettings:clone(settingsDraft)};
 const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'}));
 const link=node('a');link.href=url;link.download='dashboard-drafts.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
const isObject=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
function validDraftBundle(b){
 const numeric=v=>v===null||typeof v==='number'&&Number.isFinite(v);
 const keys=(v,required,optional=[])=>isObject(v)&&required.every(k=>Object.hasOwn(v,k))&&Object.keys(v).every(k=>[...required,...optional].includes(k));
 if(!keys(b,['version','sourceId','sourceRevision','profile','gameSettings'])||b.version!==1||typeof b.sourceId!=='string'||!Number.isSafeInteger(b.sourceRevision)||b.sourceRevision<1)return false;
 const p=b.profile;
 if(!keys(p,['version','name','dpi','binds','hip','ads'],['radial_deadzone','calibration','recoil','rapid_fire','radial_menu'])||p.version!==1||typeof p.name!=='string'||!numeric(p.dpi)||!isObject(p.binds)||!Object.values(p.binds).every(v=>typeof v==='string')||('radial_deadzone' in p&&!numeric(p.radial_deadzone)))return false;
 for(const mode of [p.hip,p.ads]){
  if(!keys(mode,['gain','smoothing_ms','cm360','x','y'])||!['gain','smoothing_ms','cm360'].every(k=>numeric(mode[k])))return false;
  if(!['x','y'].every(k=>Array.isArray(mode[k])&&mode[k].every(row=>Array.isArray(row)&&row.length===2&&row.every(numeric))))return false;
 }
 if('calibration' in p&&(!keys(p.calibration,[],['game_version','fov','optic','date','settings','notes'])||!Object.values(p.calibration).every(v=>typeof v==='string')))return false;
 if('radial_menu' in p&&!validRadialMenu(p.radial_menu))return false;
 if('rapid_fire' in p){
  const r=p.rapid_fire;
  if(!keys(r,['enabled','interval_ms','press_ms'])||typeof r.enabled!=='boolean'||!Number.isInteger(r.interval_ms)||r.interval_ms<64||r.interval_ms>1000||r.interval_ms%8||!Number.isInteger(r.press_ms)||r.press_ms<16||r.press_ms>496||r.press_ms%8||r.press_ms>=r.interval_ms)return false;
 }
 if('recoil' in p&&(!keys(p.recoil,['vertical'],['enabled','mode','pulse_peak','period_ms'])||('enabled' in p.recoil&&typeof p.recoil.enabled!=='boolean')||typeof p.recoil.vertical!=='number'||!Number.isFinite(p.recoil.vertical)||p.recoil.vertical<0||p.recoil.vertical>.25))return false;
 if(p.recoil){
  if('mode' in p.recoil&&!['constant','pulse'].includes(p.recoil.mode))return false;
  if('pulse_peak' in p.recoil&&(!Number.isFinite(p.recoil.pulse_peak)||p.recoil.pulse_peak<.001||p.recoil.pulse_peak>.25))return false;
  if('period_ms' in p.recoil&&(!Number.isInteger(p.recoil.period_ms)||p.recoil.period_ms<32||p.recoil.period_ms>496||p.recoil.period_ms%8!==0))return false;
 }
 const s=b.gameSettings;
 return keys(s,['actual','confirmedAt'])&&typeof s.confirmedAt==='string'&&keys(s.actual,['response','aimAssist'],Object.keys(boot.recommended))&&
  typeof s.actual.response==='string'&&typeof s.actual.aimAssist==='boolean'&&Object.entries(s.actual).every(([k,v])=>['response','aimAssist'].includes(k)||numeric(v));
}
$('restore-drafts').onclick=()=>{if(!busy&&!releaseBusy)$('drafts-file').click();};
$('drafts-file').onchange=async event=>{
 const file=event.target.files[0];event.target.value='';if(!file)return;
 if(busy||releaseBusy){error('Дождитесь завершения операции. Оба черновика сохранены.');return;}
 const ticket=++intent,owns=()=>ticket===intent&&!busy&&!releaseBusy;
 try{
  if(file.size>131072)throw Error('Файл черновиков превышает 128 КиБ');
  const bundle=JSON.parse(await file.text());
  if(!owns())return;
  if(!validDraftBundle(bundle))throw Error('Неверный файл черновиков');
  if(!await renewSession()||!owns())return;
  if(!Object.hasOwn(remoteBoot.store.profiles,bundle.sourceId))throw Error('Исходный профиль отсутствует в этой службе');
  if((dirty||settingsDirty)&&!confirm('Заменить оба локальных черновика данными из файла?'))return;
  if(!owns())return;
  store={...remoteBoot.store,selected:bundle.sourceId};draft=clone(bundle.profile);settingsDraft=clone(bundle.gameSettings);
  restoredSourceRevision=bundle.sourceRevision;dirty=settingsDirty=staleStore=true;render();
  notice('Оба черновика восстановлены. Сохраните их отдельной копией, чтобы продолжить.');
 }catch(e){if(owns())error(e.message);}
};
for(const nav of document.querySelectorAll('[data-tab]'))nav.onclick=()=>{document.querySelectorAll('.tab').forEach(el=>el.hidden=el.id!=='tab-'+nav.dataset.tab);document.querySelectorAll('.nav').forEach(el=>{el.classList.toggle('active',el===nav);if(el===nav)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});$('heading').textContent=nav.querySelector('span').textContent;if(window.innerWidth<=700){document.querySelector('.navigation').open=false;$('heading').focus({preventScroll:true});}};
window.addEventListener('beforeunload',e=>{if(dirty||settingsDirty){e.preventDefault();e.returnValue='';}});
setInterval(()=>{
 renderFeedback();
 const ms=sampleAge();$('age').textContent=Number.isFinite(ms)?`${Math.round(ms)} мс назад`:'Ожидание sample';
 if(ms>=1000){failed=true;renderUnknown('Статус устарел');buttons();}
},200);
(async()=>{try{await renewSession();await refresh();}catch(e){error('Не удалось открыть dashboard: '+e.message);}})();
