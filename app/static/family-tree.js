/* Family graph algorithms are independent of the DOM and exercised in Node tests. */
(function(global){
  'use strict';
  const key=(a,b)=>JSON.stringify([a,b].sort());
  function index(graph){
    const nodes=new Map(graph.nodes.map(n=>[n.id,n])),parents=new Map(),children=new Map(),partners=new Map(),adj=new Map();
    for(const id of nodes.keys()){parents.set(id,[]);children.set(id,[]);partners.set(id,[]);adj.set(id,[])}
    for(const e of graph.edges){if(!nodes.has(e.from)||!nodes.has(e.to))continue;
      adj.get(e.from).push({id:e.to,edge:e});adj.get(e.to).push({id:e.from,edge:e});
      if(e.type==='parent'){parents.get(e.to).push(e.from);children.get(e.from).push(e.to)}
      else{partners.get(e.from).push(e.to);partners.get(e.to).push(e.from)}
    }
    const order=new Map(graph.nodes.map((n,i)=>[n.id,i]));
    const sorted=items=>Array.from(new Set(items)).sort((a,b)=>order.get(a)-order.get(b));
    return {nodes,parents,children,partners,adj,sorted};
  }
  function ancestry(id,g){const found=new Map([[id,0]]),queue=[id];for(let i=0;i<queue.length;i++)for(const p of g.parents.get(queue[i])||[])if(!found.has(p)){found.set(p,found.get(queue[i])+1);queue.push(p)}return found}
  function kinship(a,b,g){
    if(a===b)return 'Selected Sim';
    const adoptive=(g.adj.get(a)||[]).find(x=>x.id===b&&x.edge.type==='parent'&&x.edge.adoptive);
    if(adoptive)return adoptive.edge.from===b?'Adoptive parent':'Adopted child';
    const partner=(g.adj.get(a)||[]).find(x=>x.id===b&&x.edge.type==='partner');if(partner)return partner.edge.role;
    const aa=ancestry(a,g),bb=ancestry(b,g),generation=(n,word)=>n===1?word:n===2?'Grand'+word.toLowerCase():n===3?'Great-grand'+word.toLowerCase():`${n-2}× great-grand${word.toLowerCase()}`;
    if(aa.has(b))return generation(aa.get(b),'Parent');if(bb.has(a))return generation(bb.get(a),'Child');
    const shared=[...aa.keys()].filter(x=>bb.has(x)).sort((x,y)=>(aa.get(x)+bb.get(x))-(aa.get(y)+bb.get(y))||Math.max(aa.get(x),bb.get(x))-Math.max(aa.get(y),bb.get(y))||x.localeCompare(y));
    if(shared.length){const up=aa.get(shared[0]),down=bb.get(shared[0]);
      if(up===1&&down===1){const ap=g.parents.get(a),bp=g.parents.get(b),same=ap.filter(x=>bp.includes(x)).length;return same===1&&ap.length>=2&&bp.length>=2?'Half-sibling':'Sibling'}
      if(down===1)return up===2?'Aunt / uncle':`${up-2}× great-aunt / uncle`;
      if(up===1)return down===2?'Niece / nephew':`${down-2}× great-niece / nephew`;
      const degree=Math.min(up,down)-1,removed=Math.abs(up-down);return `${degree===1?'First':degree===2?'Second':degree===3?'Third':degree+'th'} cousin${removed?' · '+removed+'× removed':''}`;
    }
    if((g.children.get(a)||[]).some(c=>(g.parents.get(c)||[]).includes(b)))return 'Co-parent';
    return 'Connected through family';
  }
  function pathBetween(a,b,g){const seen=new Map([[a,null]]),q=[a];for(let i=0;i<q.length&&!seen.has(b);i++)for(const next of g.adj.get(q[i])||[])if(!seen.has(next.id)){seen.set(next.id,{id:q[i],edge:next.edge});q.push(next.id)}if(!seen.has(b))return null;const ids=[b],edges=[];while(ids[0]!==a){const prev=seen.get(ids[0]);edges.unshift(prev.edge);ids.unshift(prev.id)}return {ids,edges,label:kinship(a,b,g)}}
  function visible(graph,g,state){
    const allowed=id=>state.scope!=='current'||!g.nodes.get(id)?.frozen;
    const set=new Set(),add=id=>{if(g.nodes.has(id)&&allowed(id))set.add(id)};add(state.focus);
    if(!set.size)return {ids:[],total:0};
    if(state.mode==='direct'){
      for(const p of g.parents.get(state.focus)){add(p);for(const sibling of g.children.get(p))add(sibling)}
      for(const p of g.partners.get(state.focus))add(p);
      for(const c of g.children.get(state.focus)){add(c);for(const p of g.parents.get(c))add(p)}
    }else{
      const q=[[state.focus,0]],visited=new Set([state.focus]);
      for(let i=0;i<q.length;i++){const [id,d]=q[i];if(d>=state.depth)continue;
        const next=state.mode==='ancestors'?g.parents.get(id):state.mode==='descendants'?g.children.get(id):g.adj.get(id).map(x=>x.id);
        for(const n of g.sorted(next))if(!visited.has(n)&&allowed(n)){visited.add(n);add(n);q.push([n,d+1])}
      }
      // Include the other recorded parent for every displayed child, without inventing a marriage.
      if(state.mode!=='ancestors')for(const id of [...set])if(g.parents.get(id).some(p=>set.has(p)))for(const p of g.parents.get(id))add(p);
    }
    for(const id of state.expanded||[])if(set.has(id))for(const n of g.adj.get(id)||[])add(n.id);
    // Fold descendants, but never remove the focus or one of its ancestors.
    const protectedIds=ancestry(state.focus,g),pruned=new Set();for(const id of state.collapsed||[]){const q=[id],seen=new Set([id]);for(let i=0;i<q.length;i++)for(const c of g.children.get(q[i])||[])if(!protectedIds.has(c)&&!seen.has(c)){seen.add(c);pruned.add(c);q.push(c)}}
    for(const id of pruned)set.delete(id);
    const total=set.size,ordered=g.sorted(set),limit=graph.limit||180;
    if(ordered.length>limit){const near=new Map([[state.focus,0]]),q=[state.focus];for(let i=0;i<q.length;i++)for(const n of g.adj.get(q[i])||[])if(set.has(n.id)&&!near.has(n.id)){near.set(n.id,near.get(q[i])+1);q.push(n.id)}ordered.sort((a,b)=>(near.get(a)??Infinity)-(near.get(b)??Infinity)||g.nodes.get(a).name.localeCompare(g.nodes.get(b).name));}
    return {ids:g.sorted(ordered.slice(0,limit)),total};
  }
  function layout(graph,g,ids){
    const chosen=new Set(ids),edges=graph.edges.filter(e=>chosen.has(e.from)&&chosen.has(e.to)),components=new Map(ids.map(id=>[id,id]));
    const root=id=>components.get(id);
    function ranks(){const groups=new Set(components.values()),out=new Map([...groups].map(id=>[id,new Set()])),degree=new Map([...groups].map(id=>[id,0])),rank=new Map([...groups].map(id=>[id,0]));let bad=false;
      for(const e of edges)if(e.type==='parent'){const a=root(e.from),b=root(e.to);if(a===b){bad=true;continue}if(!out.get(a).has(b)){out.get(a).add(b);degree.set(b,degree.get(b)+1)}}
      const q=[...groups].filter(id=>!degree.get(id));for(let i=0;i<q.length;i++)for(const c of out.get(q[i])){rank.set(c,Math.max(rank.get(c),rank.get(q[i])+1));degree.set(c,degree.get(c)-1);if(!degree.get(c))q.push(c)}
      return {rank,ok:!bad&&q.length===groups.size};
    }
    const initial=ranks();
    // Co-parents share a layout row, without inventing a romantic relationship.
    const aligned=edges.filter(e=>e.type==='partner').map(e=>[e.from,e.to]);
    for(const id of ids){const ps=g.parents.get(id).filter(p=>chosen.has(p));for(let i=1;i<ps.length;i++)aligned.push([ps[0],ps[i]])}
    for(const pair of aligned){const a=root(pair[0]),b=root(pair[1]);if(a===b)continue;const old=new Map(components);for(const [id,c] of components)if(c===b)components.set(id,a);if(!ranks().ok){components.clear();for(const [id,c] of old)components.set(id,c)}}
    const ranked=ranks(),units=new Map();for(const id of ids){const c=root(id);if(!units.has(c))units.set(c,[]);units.get(c).push(id)}
    const rows=new Map();for(const [c,members] of units){const r=ranked.rank.get(c)||0;if(!rows.has(r))rows.set(r,[]);rows.get(r).push({id:c,members:g.sorted(members)})}
    const columns=new Map();for(const r of [...rows.keys()].sort((a,b)=>a-b)){
      const list=rows.get(r);list.sort((a,b)=>{const anchor=unit=>{const values=unit.members.flatMap(id=>g.parents.get(id)).filter(id=>columns.has(id)).map(id=>columns.get(id));return values.length?values.reduce((x,y)=>x+y,0)/values.length:Infinity};return anchor(a)-anchor(b)||ids.indexOf(a.members[0])-ids.indexOf(b.members[0])});
      let cursor=0;for(const u of list){for(const id of u.members){columns.set(id,cursor);cursor+=248}cursor+=40}
    }
    const family=new Map();for(const e of edges.filter(e=>e.type==='parent')){const ps=g.sorted(g.parents.get(e.to).filter(p=>chosen.has(p))),k=JSON.stringify(ps);if(!family.has(k))family.set(k,{parents:ps,children:[],partner:null});if(!family.get(k).children.includes(e.to))family.get(k).children.push(e.to)}
    for(const e of edges.filter(e=>e.type==='partner')){const k=key(e.from,e.to);let unit=[...family.values()].find(u=>u.parents.length===2&&key(...u.parents)===k);if(!unit){unit={parents:[e.from,e.to],children:[],partner:e};family.set('partner:'+k,unit)}else unit.partner=e}
    const width=Math.max(760,...[...rows.values()].map(list=>list.reduce((sum,u)=>sum+u.members.length*248+40,0)+40));
    const rowY=new Map(),positions=new Map();let y=44;
    for(const r of [...rows.keys()].sort((a,b)=>a-b)){rowY.set(r,y);const list=rows.get(r),rowWidth=list.reduce((sum,u)=>sum+u.members.length*248+40,0)-40;let x=(width-rowWidth)/2;
      for(const u of list){for(const id of u.members){positions.set(id,{id,x,y,width:224,height:142,rank:r});x+=248}x+=40}
      const count=[...family.values()].filter(u=>Math.max(...u.parents.map(p=>ranked.rank.get(root(p))||0))===r).length;y+=142+Math.max(100,count*15+48);
    }
    const connections=[],lanes=new Map();for(const unit of family.values()){
      const ps=unit.parents.map(p=>positions.get(p)),cs=g.sorted(unit.children).map(c=>positions.get(c));const r=Math.max(...ps.map(p=>p.rank)),lane=lanes.get(r)||0;lanes.set(r,lane+1);
      const hubY=Math.max(...ps.map(p=>p.y+p.height))+24+lane*15,hubX=ps.reduce((sum,p)=>sum+p.x+p.width/2,0)/ps.length;
      const path=ps.map(p=>`M ${p.x+p.width/2} ${p.y+p.height} V ${hubY} H ${hubX}`).join(' ');
      connections.push({parents:unit.parents,children:[],ids:unit.parents,path,x:hubX,y:hubY,
        style:unit.partner?(unit.partner.active?'partner':'former'):'parent',
        adoptive:false,
        label:unit.partner?unit.partner.role:unit.parents.length===1?'One visible parent':'Co-parents'});
      for(const c of cs)connections.push({parents:unit.parents,children:[c.id],ids:[...unit.parents,c.id],
        path:`M ${hubX} ${hubY} H ${c.x+c.width/2} V ${c.y}`,x:hubX,y:hubY,style:'parent',
        adoptive:edges.some(e=>e.type==='parent'&&e.adoptive&&e.to===c.id&&unit.parents.includes(e.from)),label:''});
    }
    return {nodes:[...positions.values()],connections,width,height:Math.max(380,y),warning:!initial.ok?'Conflicting ancestry detected. Review parent records; no saved relationships were changed.':''};
  }
  function exportSvg(graph,drawing,options={}){
    const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
    const text=(x,y,value,size=13)=>'<text x="'+x+'" y="'+y+'" font-size="'+size+'">'+escape(value)+'</text>';
    const clip=(value,max=30)=>String(value||'').length>max?String(value).slice(0,max-1)+'…':String(value||'');
    const nodes=new Map(graph.nodes.map(n=>[n.id,n]));
    let svg='<svg xmlns="http://www.w3.org/2000/svg" width="'+drawing.width+'" height="'+drawing.height+'" viewBox="0 0 '+drawing.width+' '+drawing.height+'" role="img"><title>'+escape(options.title||'Family Tree')+'</title><rect width="100%" height="100%" fill="white"/><g font-family="Arial, sans-serif" fill="#161616">';
    for(const c of drawing.connections)svg+='<path d="'+c.path+'" fill="none" stroke="#72501e" stroke-width="2"'+(c.adoptive?' stroke-dasharray="2 5"':c.style==='former'?' stroke-dasharray="6 5"':'')+'/>'+text(c.x+4,c.y-5,c.label,12);
    for(const p of drawing.nodes){const n=nodes.get(p.id);svg+='<g><title>'+escape(n.name+' · '+n.birth+' · '+n.status)+'</title><rect x="'+p.x+'" y="'+p.y+'" width="'+p.width+'" height="'+p.height+'" rx="9" fill="'+(n.frozen?'#f0ede7':'#fff')+'" stroke="#72501e"/>';
      svg+=text(p.x+12,p.y+25,clip(n.name,25),16)+text(p.x+12,p.y+49,clip(n.status));
      if(options.dates!==false)svg+=text(p.x+12,p.y+73,clip('Born: '+n.birth,29))+text(p.x+12,p.y+95,n.death?clip('Died / scheduled: '+n.death,29):'');
      if(n.branch)svg+=text(p.x+12,p.y+122,clip(n.branch+' · '+n.branchStatus,29),12);svg+='</g>';
    }
    return svg+'</g></svg>';
  }
  const api={index,ancestry,kinship,pathBetween,visible,layout,exportSvg};if(typeof module!=='undefined'&&module.exports){module.exports=api;return}global.DecadesFamilyTree=api;
  function mount(){
    const root=document.getElementById('family-explorer');if(!root||root.dataset.ready)return;root.dataset.ready='1';
    const graph=JSON.parse(root.querySelector('[data-family-graph]').textContent),g=index(graph),initial=JSON.parse(root.querySelector('[data-family-options]').textContent);
    let state={...initial,expanded:[],collapsed:[],selected:initial.focus,zoom:1},trail=[],drawing,connection=null;
    const $=s=>root.querySelector(s),stage=$('.ft-stage'),viewport=$('.ft-viewport'),extent=$('.ft-extent'),list=$('.ft-list'),details=$('.ft-details'),message=$('.ft-message'),controller=new AbortController();
    const on=(target,type,fn,opts={})=>target.addEventListener(type,fn,{...opts,signal:controller.signal});
    function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
    function button(text,action,id,cls=''){const b=el('button',cls,text);b.type='button';b.dataset.action=action;if(id)b.dataset.id=id;return b}
    function push(){trail.push({...state,expanded:[...state.expanded],collapsed:[...state.collapsed],scrollX:viewport.scrollLeft,scrollY:viewport.scrollTop});if(trail.length>30)trail.shift()}
    function url(){const u=new URL(location.href);for(const k of ['focus','mode','depth','scope','view'])u.searchParams.set(k,state[k]);u.searchParams.set('photos',state.photos?'1':'0');u.searchParams.set('dates',state.dates?'1':'0');history.replaceState(history.state,'',u)}
    function zoom(value,center=true){state.zoom=Math.max(.25,Math.min(1.6,value));stage.style.transform=`scale(${state.zoom})`;extent.style.width=drawing.width*state.zoom+'px';extent.style.height=drawing.height*state.zoom+'px';$('[data-zoom-label]').textContent=Math.round(state.zoom*100)+'%';if(center)centerOn(state.selected)}
    function centerOn(id){const p=drawing.nodes.find(n=>n.id===id);if(!p)return;viewport.scrollLeft=Math.max(0,(p.x+112)*state.zoom-viewport.clientWidth/2);viewport.scrollTop=Math.max(0,(p.y+71)*state.zoom-viewport.clientHeight/2)}
    function portrait(n){const box=el('span','ft-photo'+(n.dead?' is-dead':''));if(state.photos&&n.portrait){const img=el('img');img.src=n.portrait;img.alt='';img.loading='lazy';box.append(img)}else box.textContent=n.name.slice(0,1);return box}
    function render(){
      root.classList.toggle('hide-photos',!state.photos);
      const vis=visible(graph,g,state);drawing=layout(graph,g,vis.ids);stage.replaceChildren();list.replaceChildren();
      stage.style.width=drawing.width+'px';stage.style.height=drawing.height+'px';
      const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.classList.add('ft-lines');svg.setAttribute('width',drawing.width);svg.setAttribute('height',drawing.height);
      for(const c of drawing.connections){const path=document.createElementNS(ns,'path');path.setAttribute('d',c.path);path.setAttribute('class','ft-line '+c.style+(c.adoptive?' adoptive':''));path.dataset.ids=JSON.stringify(c.ids);const title=document.createElementNS(ns,'title');title.textContent=c.label;path.append(title);svg.append(path);
        const label=document.createElementNS(ns,'text');label.setAttribute('x',c.x);label.setAttribute('y',c.y-5);label.setAttribute('text-anchor','middle');label.textContent=c.label;svg.append(label)}
      stage.append(svg);
      for(const p of drawing.nodes){const n=g.nodes.get(p.id),card=el('article','ft-person'+(n.id===state.focus?' is-focus':'')+(n.dead?' is-dead':''));card.dataset.id=n.id;card.style.left=p.x+'px';card.style.top=p.y+'px';
        const choose=button('','select',n.id,'ft-select');choose.setAttribute('aria-label',n.name+', '+n.status+', show details');choose.append(portrait(n));const copy=el('span','ft-copy');copy.append(el('small','ft-role',kinship(state.focus,n.id,g)),el('strong','',n.name),el('span','ft-status',n.status));if(state.dates)copy.append(el('small','ft-dates',n.birth+(n.death?' – '+n.death:'')));if(n.branch)copy.append(el('small','ft-branch',n.branch+' · '+n.branchStatus));choose.append(copy);card.append(choose);
        const actions=el('div','ft-node-actions');actions.append(button('Focus','focus',n.id),button(state.expanded.includes(n.id)?'Less family':'More family','expand',n.id));if(g.children.get(n.id).length)actions.append(button(state.collapsed.includes(n.id)?'Show children':'Hide children','collapse',n.id));card.append(actions);stage.append(card);
        const row=el('li','ft-list-person');row.append(button(n.name,'select',n.id),el('span','',kinship(state.focus,n.id,g)+' · '+n.status),button('Center branch','focus',n.id));list.append(row);
      }
      for(const k of ['mode','depth','scope','view'])$(`[name=${k}]`).value=state[k];$('[name=photos]').checked=state.photos;$('[name=dates]').checked=state.dates;$('[data-previous]').disabled=!trail.length;
      viewport.hidden=state.view==='list';list.hidden=state.view!=='list';
      message.textContent=[`${vis.ids.length} of ${vis.total} people in this view`,vis.total>vis.ids.length?'View limited for speed. Focus a smaller branch to see the rest.':'',drawing.warning,...graph.warnings].filter(Boolean).join(' · ');
      zoom(state.zoom,false);showDetails(state.selected);highlight();url();
    }
    function showDetails(id){const n=g.nodes.get(id);if(!n)return;state.selected=id;details.replaceChildren();const head=el('div','ft-detail-heading');head.append(portrait(n),el('h2','',n.name));details.append(head,el('p','',n.status));
      const dl=el('dl');for(const [label,value] of [['Born',n.birth],['Died / scheduled',n.death],['Birth surname',n.birthSurname],['Married surname',n.marriedSurname],['Household',n.household],['Branch',n.branch?n.branch+' · '+n.branchStatus:''],['Preserved date',n.frozen?'GD '+n.frozenDay:'']])if(value){dl.append(el('dt','',label),el('dd','',value))}details.append(dl);
      const profile=el('a','button',n.frozen?'Open preserved profile':'Open full profile');profile.href=n.profile;details.append(profile,button('Center this branch','focus',id,'primary'));
      if(n.missingParents.length)details.append(el('p','ft-warning','Recorded parent not shown: '+n.missingParents.join(', ')+'. They may be hidden, archived, or missing.'));
      for(const [title,ids] of [['Parents',g.parents.get(id)],['Partners',g.partners.get(id)],['Children',g.children.get(id)]]){const section=el('section');section.append(el('h3','',title+' · '+ids.length));for(const other of g.sorted(ids))section.append(button(g.nodes.get(other).name,'select',other,'ft-relative'));if(!ids.length)section.append(el('small','','None recorded'));details.append(section)}
      const histories=g.adj.get(id).filter(x=>x.edge.type==='partner');for(const h of histories){const d=el('details');d.append(el('summary','','Relationship history · '+g.nodes.get(h.id).name));for(const r of h.edge.history||[])d.append(el('p','',`${r.type} · ${r.status}${r.start!==null&&r.start!==undefined?' · from GD '+r.start:''}${r.end!==null&&r.end!==undefined?' to GD '+r.end:''}`));details.append(d)}
      highlight();
    }
    function highlight(){const ids=new Set(connection?connection.ids:[state.selected,...(g.adj.get(state.selected)||[]).map(x=>x.id)]);for(const card of stage.querySelectorAll('.ft-person')){card.classList.toggle('is-selected',card.dataset.id===state.selected);card.classList.toggle('is-dimmed',!!connection&&!ids.has(card.dataset.id))}for(const line of stage.querySelectorAll('.ft-line')){const related=JSON.parse(line.dataset.ids);line.classList.toggle('is-highlighted',connection?related.filter(x=>ids.has(x)).length>=2:related.includes(state.selected));line.classList.toggle('is-dimmed',!!connection&&related.filter(x=>ids.has(x)).length<2)}}
    on(root,'click',event=>{const b=event.target.closest('[data-action]');if(!b)return;const id=b.dataset.id;
      if(b.dataset.action==='select'){showDetails(id);centerOn(id);if(window.innerWidth<900)details.scrollIntoView({block:'nearest'})}
      if(b.dataset.action==='focus'){push();state.focus=id;state.selected=id;state.expanded=[];state.collapsed=[];if(state.scope==='current'&&g.nodes.get(id).frozen)state.scope='dynasty';connection=null;search.value='';results.replaceChildren();render();centerOn(id)}
      if(b.dataset.action==='expand'||b.dataset.action==='collapse'){push();const k=b.dataset.action==='expand'?'expanded':'collapsed';state[k]=state[k].includes(id)?state[k].filter(x=>x!==id):[...state[k],id];render()}
      if(b.dataset.action==='zoom-in')zoom(state.zoom+.15);if(b.dataset.action==='zoom-out')zoom(state.zoom-.15);
      if(b.dataset.action==='fit'){zoom(Math.min(1,(viewport.clientWidth-24)/drawing.width,(viewport.clientHeight-24)/drawing.height),false);viewport.scrollLeft=0;viewport.scrollTop=0}
      if(b.dataset.action==='center')centerOn(state.focus);
      if(b.dataset.action==='previous'&&trail.length){state=trail.pop();connection=null;render();viewport.scrollLeft=state.scrollX;viewport.scrollTop=state.scrollY}
      if(b.dataset.action==='print')window.print();
      if(b.dataset.action==='export'){const content=exportSvg(graph,drawing,{dates:state.dates,title:'Family Tree'}),href=URL.createObjectURL(new Blob([content],{type:'image/svg+xml;charset=utf-8'})),link=el('a');link.href=href;link.download='family-tree.svg';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(href),1000)}
      if(b.dataset.action==='trace'){
        const a=$('[name=trace_from]').value,c=$('[name=trace_to]').value;connection=pathBetween(a,c,g);const output=$('[data-trace-output]');output.replaceChildren();
        if(!connection){output.textContent='No recorded family connection.';highlight();return}
        output.append(el('strong','',g.nodes.get(c).name+' · '+connection.label+' of '+g.nodes.get(a).name));
        for(let i=0;i<connection.ids.length;i++){if(i)output.append(el('span','',' → '));output.append(button(g.nodes.get(connection.ids[i]).name,'select',connection.ids[i]))}
        if(connection.edges.some(e=>e.adoptive))output.append(el('p','','This path includes recorded adoptive parentage.'));
        const visibleIds=new Set(drawing.nodes.map(n=>n.id));if(connection.ids.some(n=>!visibleIds.has(n)))output.append(el('p','','Some relatives on this path are outside the current view. Select their name, then center their branch to explore.'));highlight();
      }
      if(b.dataset.action==='clear-trace'){connection=null;$('[data-trace-output]').replaceChildren();highlight()}
    });
    on(root,'change',event=>{const name=event.target.name;if(['mode','depth','scope','view','photos','dates'].includes(name)){push();state[name]=name==='depth'?Math.max(1,Math.min(8,Number(event.target.value)||3)):['photos','dates'].includes(name)?event.target.checked:event.target.value;state.expanded=[];state.collapsed=[];if(state.scope==='current'&&g.nodes.get(state.focus)?.frozen)state.focus=graph.nodes.find(n=>!n.frozen)?.id||state.focus;state.selected=state.focus;connection=null;render();centerOn(state.focus)}});
    const search=$('[name=search]'),results=$('[data-search-results]');on(search,'input',()=>{results.replaceChildren();const q=search.value.trim().toLocaleLowerCase();if(!q)return;for(const n of graph.nodes.filter(n=>(n.name+' '+n.birthSurname+' '+n.number).toLocaleLowerCase().includes(q)).slice(0,30))results.append(button(n.name+(n.birth?' · '+n.birth:''),'focus',n.id));if(!results.childElementCount)results.textContent='No matching Sim.'});
    let drag=null;on(viewport,'pointerdown',e=>{if(e.pointerType==='mouse'&&e.button===0&&!e.target.closest('button,a')){drag={x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture(e.pointerId)}});on(viewport,'pointermove',e=>{if(drag){viewport.scrollLeft=drag.left+drag.x-e.clientX;viewport.scrollTop=drag.top+drag.y-e.clientY}});on(viewport,'pointerup',()=>{drag=null});on(viewport,'pointercancel',()=>{drag=null});
    on(document,'htmx:beforeCleanupElement',e=>{if(e.detail.elt===root||e.detail.elt.contains(root))controller.abort()});
    on(window,'beforeprint',()=>{$('.ft-print').innerHTML=exportSvg(graph,drawing,{dates:state.dates,title:'Family Tree'})});
    render();requestAnimationFrame(()=>centerOn(state.focus));
  }
  mount();
})(typeof window!=='undefined'?window:globalThis);
