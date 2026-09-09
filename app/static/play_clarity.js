/* Consequences are supplied by a private server-side preview, never guessed here. */
(() => {
  if(typeof document==='undefined')return;
  const ctx=()=>document.querySelector('#ui-context');
  function batchKey(){const board=document.querySelector('[data-workboard]');if(!board||!ctx())return null;return 'decades:batch-scroll:'+ctx().dataset.user+':'+ctx().dataset.save+':'+(document.querySelector('select[name=household]')?.value||'all')+':'+board.dataset.window;}
  window.addEventListener('pagehide',()=>{const key=batchKey();if(key)try{sessionStorage.setItem(key,JSON.stringify({y:scrollY,day:document.body.dataset.currentGlobalDay}));}catch{}});
  const scrollKey=batchKey();if(scrollKey)try{const old=JSON.parse(sessionStorage.getItem(scrollKey)||'null');if(old&&old.day===document.body.dataset.currentGlobalDay)requestAnimationFrame(()=>scrollTo({top:old.y,behavior:'instant'}));}catch{}
  const headers=()=>({'X-Decades-Fragment':'1','X-UI-Save':ctx()?.dataset.save||'','X-Dynasty-Epoch':ctx()?.dataset.epoch||''});
  const report=message=>{let n=document.querySelector('#ui-status');if(!n){n=document.createElement('p');n.id='ui-status';n.setAttribute('role','status');document.querySelector('main')?.prepend(n);}n.textContent=message;};
  async function json(url,options){const r=await fetch(url,{cache:'no-store',...options});let data;try{data=await r.json();}catch{throw Error('The tracker could not return a preview. No changes were confirmed.');}if(!r.ok)throw Error(data.detail||'The save changed. Review a new preview.');return data;}
  function dialog(title){const d=document.createElement('dialog');d.className='u-confirm-dialog';d.setAttribute('aria-label',title);const h=document.createElement('h2');h.textContent=title;d.append(h);document.body.append(d);const opener=document.activeElement;d.addEventListener('close',()=>{d.remove();if(opener?.isConnected)opener.focus();});return d;}
  function paragraph(d,text){const p=document.createElement('p');p.textContent=text;d.append(p);}
  function showPreview(p,form){
    const d=dialog('Review before confirming');paragraph(d,p.label);
    paragraph(d,p.actual!=null?`Result ${p.actual} · ${p.outcome}`:`Moves ${p.counts.moved} pending rolls · adds ${p.counts.added} obligations · retires ${p.counts.retired}`);
    if(p.completed_unchanged)paragraph(d,'Completed results stay unchanged.');
    paragraph(d,'No proposed changes have been applied. Native die throws are retained, so reopening this preview does not reroll them.');
    const ul=document.createElement('ul');for(const effect of p.effects){const li=document.createElement('li');li.textContent=effect;ul.append(li);}d.append(ul);
    const confirm=document.createElement('button');confirm.type='button';confirm.className='primary';confirm.textContent='Confirm these changes';
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Keep unchanged';cancel.addEventListener('click',()=>d.close());d.append(confirm,cancel);
    confirm.addEventListener('click',async()=>{confirm.disabled=true;cancel.disabled=true;let committed=false;
      try{const result=await json('/api/previews/'+encodeURIComponent(p.token)+'/confirm',{method:'POST',headers:headers()});committed=true;d.close();
        if(result.kind==='roll')document.dispatchEvent(new CustomEvent('decades:roll-confirmed',{detail:{...result,form,scrollY:window.scrollY}}));
        else location.assign(result.return_to||'/p/rules');
      }catch(error){paragraph(d,(committed?'Changes saved; refresh the display. ':'Nothing confirmed. ')+error.message);confirm.disabled=false;cancel.disabled=false;}
    });d.showModal();cancel.focus();
  }
  window.addEventListener('submit',async e=>{
    const form=e.target;if(!(form instanceof HTMLFormElement))return;
    const path=new URL(form.action,location.href).pathname;
    if(!['/settings','/api/rule-packs'].includes(path)&&!/^\/api\/rolls\/[^/]+\/(roll|complete)$/.test(path))return;
    e.preventDefault();e.stopImmediatePropagation();if(form.dataset.previewBusy)return;
    form.dataset.previewBusy='true';const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
    try{const h=headers();if(form.dataset.version)h['X-Record-Version']=form.dataset.version;else delete h['X-Decades-Fragment'];
      // Older embedded roll forms use the same shared card after this release.
      if(!h['X-Decades-Fragment']&&path.startsWith('/api/rolls/'))h['X-Decades-Fragment']='preview';
      if(path==='/settings'||path==='/api/rule-packs')h['X-Decades-Fragment']='preview';
      const data=await json(path,{method:'POST',headers:h,body:new FormData(form)});showPreview(data.preview,form);
    }catch(error){report(error.message);}finally{delete form.dataset.previewBusy;buttons.forEach(b=>b.disabled=false);}
  },true);
  document.addEventListener('click',e=>{const b=e.target.closest('[data-date-evidence]');if(!b)return;const d=dialog(b.textContent);let evidence={};try{evidence=JSON.parse(b.dataset.dateEvidence);}catch{}for(const [key,value] of Object.entries(evidence))paragraph(d,key+': '+value);const close=document.createElement('button');close.type='button';close.textContent='Close';close.addEventListener('click',()=>d.close());d.append(close);d.showModal();});
})();
