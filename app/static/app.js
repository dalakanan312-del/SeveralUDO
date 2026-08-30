async function rollDie(notation,saveId){const target=document.getElementById('dice-result');target.textContent='Rolling…';const response=await fetch('/api/dice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({notation,save_id:saveId})});const data=await response.json();target.innerHTML=`<div class="notice"><strong>${data.total}</strong> · faces ${data.faces.join(', ')}<small>Verified audit ${data.audit_id}</small></div>`}

document.addEventListener('input',(event)=>{if(!event.target.matches('.automation-age-days'))return;const form=event.target.closest('.automation-review-form');const birth=form?.querySelector('.automation-birth-day');const detected=Number(form?.dataset.detectedGlobalDay);const age=Number(event.target.value);if(birth&&Number.isFinite(detected)&&Number.isFinite(age)&&event.target.value!=='')birth.value=String(Math.trunc(detected-age));});

function setMobileMenu(open){const sidebar=document.querySelector('.app-sidebar'),toggle=document.querySelector('.mobile-menu-toggle');if(!sidebar||!toggle)return;sidebar.classList.toggle('mobile-menu-open',open);toggle.setAttribute('aria-expanded',String(open));const icon=toggle.querySelector('.mobile-menu-icon');if(icon)icon.textContent=open?'×':'☰';}
const NAVIGATION_STATE_KEY='decades-navigation-groups';
function savedNavigationGroups(){try{return JSON.parse(localStorage.getItem(NAVIGATION_STATE_KEY)||'{}');}catch(_error){return {};}}
function storeNavigationGroups(){const state={};document.querySelectorAll('.nav-group').forEach((group)=>{state[group.dataset.navGroup]=group.open;});try{localStorage.setItem(NAVIGATION_STATE_KEY,JSON.stringify(state));}catch(_error){}}
function filterNavigation(value){const query=String(value||'').trim().toLowerCase();let matches=0;const saved=savedNavigationGroups();document.querySelectorAll('.nav-group').forEach((group)=>{let groupMatches=0;group.querySelectorAll('[data-nav-search]').forEach((link)=>{const match=!query||link.dataset.navSearch.toLowerCase().includes(query);link.hidden=!match;if(match&&query){groupMatches+=1;matches+=1;}});group.hidden=Boolean(query&&!groupMatches);if(query&&groupMatches)group.open=true;else if(!query){group.open=Boolean(group.querySelector('.active')||(group.dataset.navGroup in saved?saved[group.dataset.navGroup]:group.open));}});const empty=document.querySelector('.nav-no-results');if(empty)empty.hidden=!query||matches>0;}
function initializeNavigation(){const nav=document.querySelector('.app-nav');if(!nav||nav.dataset.organized==='true')return;nav.dataset.organized='true';const saved=savedNavigationGroups();document.querySelectorAll('.nav-group').forEach((group)=>{group.open=Boolean(group.querySelector('.active')||(group.dataset.navGroup in saved?saved[group.dataset.navGroup]:group.open));group.addEventListener('toggle',()=>{if(!document.querySelector('#navigation-filter')?.value)storeNavigationGroups();});});const filter=document.querySelector('#navigation-filter');if(filter)filter.addEventListener('input',()=>filterNavigation(filter.value));}
function refreshActiveNavigation(){const current=location.pathname;let activeLabel='Overview';document.querySelectorAll('aside nav a').forEach((link)=>{const active=new URL(link.href,location.href).pathname===current;link.classList.toggle('active',active);if(active){link.setAttribute('aria-current','page');activeLabel=(link.querySelector('span')?.textContent||link.textContent).trim();const group=link.closest('.nav-group');if(group)group.open=true;}else link.removeAttribute('aria-current');});const label=document.querySelector('.mobile-menu-copy strong');if(label)label.textContent=activeLabel;}
document.addEventListener('click',(event)=>{if(event.target.closest('.mobile-menu-toggle')){const sidebar=document.querySelector('.app-sidebar');setMobileMenu(!sidebar?.classList.contains('mobile-menu-open'));return;}if(event.target.closest('.app-nav a'))setMobileMenu(false);});
document.addEventListener('keydown',(event)=>{if(event.key==='Escape')setMobileMenu(false);});
window.addEventListener('resize',()=>{if(innerWidth>800)setMobileMenu(false);});
document.addEventListener('htmx:afterSwap',()=>{refreshActiveNavigation();setMobileMenu(false);});
window.addEventListener('popstate',refreshActiveNavigation);
initializeNavigation();refreshActiveNavigation();

function showTrackerAlert(event){
  let tray=document.getElementById('tracker-alert-tray');
  if(!tray){tray=document.createElement('div');tray.id='tracker-alert-tray';tray.className='tracker-alert-tray';tray.setAttribute('aria-live','polite');document.body.appendChild(tray);}
  const card=document.createElement('section');card.className=`tracker-alert ${event.category||''}`;
  const copy=document.createElement('div');const title=document.createElement('strong');title.textContent=event.title;const body=document.createElement('p');body.textContent=event.body||'';copy.append(title,body);
  const actions=document.createElement('div');const open=document.createElement('a');open.className='button primary';open.href=event.url||'/p/automation';open.textContent='Review';const close=document.createElement('button');close.type='button';close.textContent='Not now';close.addEventListener('click',()=>card.remove());actions.append(open,close);card.append(copy,actions);tray.prepend(card);
  if(window.Notification&&Notification.permission==='granted'){try{const notice=new Notification(event.title,{body:event.body||'Open Decades Tracker to review.'});notice.onclick=()=>{window.focus();location.href=event.url||'/p/automation';};}catch(_error){}}
}

let trackerAlertPollActive=false;
async function pollTrackerAlerts(){
  const body=document.body;const feed=body.dataset.notificationFeed;const saveId=body.dataset.saveId;if(!feed||!saveId)return;
  if(document.hidden||trackerAlertPollActive)return;trackerAlertPollActive=true;
  const key=`decades-notification-cursor:${saveId}`;let cursor=localStorage.getItem(key);
  if(!cursor){cursor=body.dataset.notificationCursor||new Date().toISOString();localStorage.setItem(key,cursor);}
  try{const response=await fetch(`${feed}?after=${encodeURIComponent(cursor)}`,{headers:{Accept:'application/json'}});if(!response.ok)return;const data=await response.json();for(const item of data.events||[])showTrackerAlert(item);if(data.cursor)localStorage.setItem(key,data.cursor);}catch(_error){}finally{trackerAlertPollActive=false;}
}

document.addEventListener('click',async(event)=>{if(!event.target.closest('#enable-browser-notifications'))return;if(!window.Notification){alert('This browser does not support desktop notifications. Live in-app alerts will still work.');return;}const result=await Notification.requestPermission();event.target.textContent=result==='granted'?'Desktop notifications allowed':'Desktop notifications blocked';});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollTrackerAlerts();});
window.setInterval(pollTrackerAlerts,30000);window.setTimeout(pollTrackerAlerts,1800);

function themeRgb(value){const clean=String(value||'').replace('#','');return [0,2,4].map((index)=>parseInt(clean.slice(index,index+2),16));}
function themeHex(channels){return '#'+channels.map((channel)=>Math.max(0,Math.min(255,Math.round(channel))).toString(16).padStart(2,'0')).join('');}
function themeMix(first,second,amount){const a=themeRgb(first),b=themeRgb(second);return themeHex(a.map((channel,index)=>channel+(b[index]-channel)*amount));}
function themeLuminance(value){return themeRgb(value).map((channel)=>{const n=channel/255;return n<=.04045?n/12.92:Math.pow((n+.055)/1.055,2.4);}).reduce((total,channel,index)=>total+channel*[.2126,.7152,.0722][index],0);}
function themeContrast(first,second){const values=[themeLuminance(first),themeLuminance(second)].sort((a,b)=>b-a);return (values[0]+.05)/(values[1]+.05);}
function themeDarken(value,limit){let result=value;while(themeLuminance(result)>limit)result=themeMix(result,'#000000',.18);return result;}
function updateThemePreview(editor){
  const preview=editor.querySelector('#theme-preview');if(!preview)return;
  const value=(name)=>editor.elements[name]?.value;
  const accent=value('theme_accent'),rawBackground=value('theme_background'),rawSurface=value('theme_surface');let background=themeDarken(rawBackground,.055),surface=themeDarken(rawSurface,.085),ink=value('theme_text'),muted=value('theme_muted');
  const canvasAdjusted=background!==rawBackground||surface!==rawSurface;if(themeContrast(surface,ink)<4.5)ink=['#f8f5ee','#171512'].sort((a,b)=>themeContrast(surface,b)-themeContrast(surface,a))[0];if(themeContrast(surface,muted)<3)muted=themeMix(ink,surface,.38);
  const radius={square:'5px',soft:'15px',round:'24px'}[value('theme_corners')]||'15px';const size={small:'13.5px',standard:'14.5px',large:'16px'}[value('theme_text_scale')]||'14.5px';const font={classic:"Georgia, 'Times New Roman', serif",modern:"Inter, 'Segoe UI', sans-serif",bookish:"'Palatino Linotype', Palatino, Georgia, serif"}[value('theme_heading_style')]||'Georgia, serif';
  const colors={'--ink':ink,'--text':ink,'--muted':muted,'--paper':background,'--panel':surface,'--panel-raised':themeMix(surface,'#ffffff',.055),'--panel-soft':themeMix(background,surface,.52),'--line':themeMix(surface,ink,.17),'--gold':accent,'--gold-bright':themeMix(accent,'#ffffff',.32),'--gold-dim':themeMix(accent,background,.48),'--accent-rgb':themeRgb(accent).join(','),'--paper-rgb':themeRgb(background).join(','),'--panel-rgb':themeRgb(surface).join(','),'--radius':radius,'--theme-body-size':size,'--theme-heading-font':font};Object.entries(colors).forEach(([key,item])=>preview.style.setProperty(key,item));
  const score=editor.querySelector('#theme-contrast-score');if(score)score.textContent=`Text contrast ${themeContrast(surface,ink).toFixed(2)}:1${ink!==value('theme_text')?' · text adjusted':''}${canvasAdjusted?' · canvas darkened':''}`;
}
function initializeThemeEditor(){
  const editor=document.querySelector('#appearance-editor');if(!editor||editor.dataset.ready==='true')return;editor.dataset.ready='true';const custom=editor.querySelector('input[name="theme_preset"][value="custom"]');
  const selectCard=(input)=>{editor.querySelectorAll('.theme-preset-card').forEach((card)=>card.classList.toggle('selected',card.contains(input)));};
  editor.querySelectorAll('input[name="theme_preset"]').forEach((radio)=>radio.addEventListener('change',()=>{selectCard(radio);if(radio.value!=='custom'){['accent','background','surface','text','muted'].forEach((key)=>{editor.elements[`theme_${key}`].value=radio.dataset[key];});}updateThemePreview(editor);}));
  editor.querySelectorAll('input[type="color"]').forEach((input)=>input.addEventListener('input',()=>{if(custom){custom.checked=true;selectCard(custom);}updateThemePreview(editor);}));
  editor.querySelectorAll('select,input[name="theme_reduce_motion"]').forEach((input)=>input.addEventListener('change',()=>updateThemePreview(editor)));updateThemePreview(editor);
}
initializeThemeEditor();document.addEventListener('htmx:afterSwap',initializeThemeEditor);
