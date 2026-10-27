(async()=>{
 const results=[];
 const check=(ok,label)=>{if(!ok)throw Error(label);results.push(label);};
 try{
  await new Promise(resolve=>setTimeout(resolve,30));
  const menu=document.querySelector('.navigation');
  const keyboard=document.querySelector('.keyboard-disclosure');
  Object.defineProperty(window,'innerWidth',{value:1440,writable:true,configurable:true});
  window.dispatchEvent(new Event('resize'));
  check(menu.open,'desktop navigation opens');
  check(keyboard.open,'desktop keyboard opens');
  document.querySelector('[data-tab="aim"]').click();
  check(menu.open,'desktop navigation remains open after selection');
  check(document.querySelector('[data-tab="aim"]').getAttribute('aria-current')==='page','selected section announced');
  window.innerWidth=390;window.dispatchEvent(new Event('resize'));
  check(!menu.open,'mobile navigation starts collapsed');
  check(!keyboard.open,'mobile keyboard starts collapsed');
  menu.open=true;
  document.querySelector('[data-tab="profiles"]').click();
  check(!menu.open,'mobile selection closes menu');
  check(document.activeElement.id==='heading','focus moves outside closed menu');
  check(document.querySelectorAll('[aria-current="page"]').length===1,'only current section announced');
  check(!document.querySelector('#tab-profiles').hidden,'selected section visible');
  window.innerWidth=1440;window.dispatchEvent(new Event('resize'));
  check(menu.open,'navigation restored after enlarging viewport');
  window.dashboardTestResult={ok:true,results};
 }catch(error){window.dashboardTestResult={ok:false,error:error.stack,results};}
})();
