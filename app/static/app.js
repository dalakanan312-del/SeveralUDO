async function rollDie(notation,saveId){const target=document.getElementById('dice-result');target.textContent='Rolling…';const response=await fetch('/api/dice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({notation,save_id:saveId})});const data=await response.json();target.innerHTML=`<div class="notice"><strong>${data.total}</strong> · faces ${data.faces.join(', ')}<small>Verified audit ${data.audit_id}</small></div>`}

document.addEventListener('input',(event)=>{if(!event.target.matches('.automation-age-days'))return;const form=event.target.closest('.automation-review-form');const birth=form?.querySelector('.automation-birth-day');const detected=Number(form?.dataset.detectedGlobalDay);const age=Number(event.target.value);if(birth&&Number.isFinite(detected)&&Number.isFinite(age)&&event.target.value!=='')birth.value=String(Math.trunc(detected-age));});

function setMobileMenu(open){const sidebar=document.querySelector('.app-sidebar'),toggle=document.querySelector('.mobile-menu-toggle');if(!sidebar||!toggle)return;sidebar.classList.toggle('mobile-menu-open',open);toggle.setAttribute('aria-expanded',String(open));const icon=toggle.querySelector('.mobile-menu-icon');if(icon)icon.textContent=open?'×':'☰';}
function refreshActiveNavigation(){const current=location.pathname;let activeLabel='Tracker';document.querySelectorAll('aside nav a').forEach((link)=>{const active=new URL(link.href,location.href).pathname===current;link.classList.toggle('active',active);if(active)activeLabel=(link.querySelector('span')?.textContent||link.textContent).trim();});const label=document.querySelector('.mobile-menu-copy strong');if(label)label.textContent=activeLabel;}
document.addEventListener('click',(event)=>{if(event.target.closest('.mobile-menu-toggle')){const sidebar=document.querySelector('.app-sidebar');setMobileMenu(!sidebar?.classList.contains('mobile-menu-open'));return;}if(event.target.closest('.app-nav a'))setMobileMenu(false);});
document.addEventListener('keydown',(event)=>{if(event.key==='Escape')setMobileMenu(false);});
window.addEventListener('resize',()=>{if(innerWidth>800)setMobileMenu(false);});
document.addEventListener('htmx:afterSwap',()=>{refreshActiveNavigation();setMobileMenu(false);});
window.addEventListener('popstate',refreshActiveNavigation);

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
