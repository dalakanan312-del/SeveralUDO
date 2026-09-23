/* An in-app reminder only. This module never changes a Sim or confirms a death. */
(function(){
  'use strict';
  const scope = data => JSON.stringify([data.user_id,data.save_id,String(data.epoch||''),data.branch_id||'',data.global_day]);
  const itemKey = item => JSON.stringify([item.id,item.global_day,item.cause,item.source_roll_id||'']);
  const matches = (data, context) => !!context && data.user_id===context.user && data.save_id===context.save && String(data.epoch||'')===String(context.epoch||'');
  const unseen = (data, seen) => !data.suppressed && data.items.some(item=>!seen.includes(itemKey(item)));
  const summary = data => [data.today_count ? `${data.today_count} ${data.today_count===1?'death':'deaths'} due today` : '', data.overdue_count ? `${data.overdue_count} overdue` : ''].filter(Boolean).join(' · ');
  if(typeof module!=='undefined')module.exports={scope,itemKey,matches,unseen,summary};
  if(typeof document==='undefined')return;

  let current=null, modal=null, editing=false, owner=null;
  const memory=new Map();
  const banner=()=>document.querySelector('[data-death-reminders]');
  const key=data=>'decades-death-reminders:'+scope(data);
  function seen(data){
    if(memory.has(key(data)))return memory.get(key(data));
    try{const value=JSON.parse(sessionStorage.getItem(key(data))||'[]');return Array.isArray(value)?value:[];}catch{return [];}
  }
  function remember(data){
    const value=[...new Set([...seen(data),...data.items.map(itemKey)])];
    memory.set(key(data),value);
    try{sessionStorage.setItem(key(data),JSON.stringify(value));}catch{/* Session-only fallback when storage is blocked. */}
  }
  function close(){if(modal){const old=modal;modal=null;old.close();old.remove();}}
  function element(tag,text,className){const node=document.createElement(tag);if(text)node.textContent=text;if(className)node.className=className;return node;}
  function fill(dialog){
    const list=dialog.querySelector('[data-death-list]');list.replaceChildren();
    dialog.querySelector('h2').textContent=current.today_count?'Deaths due today':'Overdue deaths to review';
    dialog.querySelector('[data-death-context]').textContent=`${current.save_name}${current.branch_name?' → '+current.branch_name:''} · Global Day ${current.global_day}`;
    dialog.querySelector('[data-death-totals]').textContent=summary(current);
    for(const item of current.items){
      const row=element('li');const name=element('a',item.name);name.href=item.profile_url;
      const date=element('small',`${item.global_day===current.global_day?'Due today':'Overdue'} · GD ${item.global_day}`);
      row.append(name,date,element('p',item.cause));
      const review=element('a','Review / confirm','button');review.href=item.review_url;
      row.append(review);list.append(row);
    }
    const extra=dialog.querySelector('[data-death-extra]');
    const remaining=current.today_count+current.overdue_count-current.items.length;
    extra.hidden=remaining<=0;extra.textContent=`${remaining} more scheduled deaths are listed in the death review.`;
    remember(current);
  }
  function show(manual=false){
    const host=banner();
    if(!current||document.hidden||!matches(current,host?.dataset)||current.suppressed||!current.items.length||modal)return;
    if(document.querySelector('dialog[open], [aria-modal="true"]:not([hidden])'))return;
    if(!manual&&(editing||document.querySelector('[data-preview-busy]')||!unseen(current,seen(current))))return;
    const opener=document.activeElement;
    const dialog=element('dialog',null,'death-reminder-dialog');modal=dialog;
    dialog.setAttribute('aria-labelledby','death-reminder-title');dialog.setAttribute('aria-describedby','death-reminder-help');
    const heading=element('h2');heading.id='death-reminder-title';
    const context=element('p',null,'eyebrow');context.dataset.deathContext='';
    const totals=element('strong');totals.dataset.deathTotals='';
    const help=element('p','These Sims have an unconfirmed scheduled death. Carry it out in your game when ready, then review and confirm the details in the tracker. Nothing is marked dead by this reminder.');help.id='death-reminder-help';
    const list=element('ul',null,'death-reminder-list');list.dataset.deathList='';
    const extra=element('p');extra.dataset.deathExtra='';
    const actions=element('div',null,'death-reminder-actions');
    const later=element('button','Remind me later');later.type='button';later.autofocus=true;later.addEventListener('click',close);
    const all=element('a','Open death review','button primary');all.href=current.review_url;
    actions.append(later,all);dialog.append(context,heading,totals,help,list,extra,actions,
      element('small','Closing this popup leaves every scheduled death unchanged. Use “Review deaths” to reopen it.'));
    dialog.addEventListener('close',()=>{if(modal===dialog)modal=null;dialog.remove();if(opener?.isConnected)opener.focus();});
    // These links use the existing review page, never a direct confirmation POST.
    dialog.addEventListener('click',event=>{if(event.target.closest('a'))close();});
    document.body.append(dialog);fill(dialog);dialog.showModal();
  }
  function update(data){
    const host=banner();if(!matches(data,host?.dataset))return;
    const oldScope=current&&scope(current),changed=JSON.stringify(current)!==JSON.stringify(data);current=data;owner=host;
    if(oldScope!==scope(data)||data.suppressed||!data.items.length)close();
    host.hidden=!!data.suppressed||!data.items.length;
    host.querySelector('[data-death-summary]').textContent=summary(data);
    if(modal){if(changed)fill(modal);}else show();
  }
  document.addEventListener('decades:live-status',event=>{editing=!!event.detail.editing;if(event.detail.death_reminders)update(event.detail.death_reminders);});
  document.addEventListener('click',event=>{if(event.target.closest('[data-open-death-reminders]'))show(true);});
  document.addEventListener('htmx:afterSwap',()=>{
    if(owner&&owner!==banner()){close();current=null;owner=null;}
    document.dispatchEvent(new CustomEvent('decades:request-status'));
  });
  document.addEventListener('decades:roll-confirmed',()=>document.dispatchEvent(new CustomEvent('decades:request-status')));
  // Retry only the presentation after a different dialog closes; no extra network polling.
  document.addEventListener('close',()=>setTimeout(()=>show(),0),true);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)document.dispatchEvent(new CustomEvent('decades:request-status'));});
})();
