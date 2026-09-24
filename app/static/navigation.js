/* Navigation never confirms rolls, changes records, or submits a form. */
(function(){
  'use strict';
  function photoURL(value,base){
    try{const url=new URL(value,base);if(url.origin!==new URL(base).origin||!/^\/(portraits|photos)\/[^/]+\/[^/]+$/.test(url.pathname))return null;
      url.pathname=url.pathname.replace(/^\/photos\//,'/portraits/');url.searchParams.delete('download');return url;
    }catch{return null;}
  }
  function internalReferrer(value,base){
    try{const url=new URL(value);return url.origin===new URL(base).origin&&(/^\/(p|sims|households|relationships|pregnancies|photos)\//.test(url.pathname)||url.pathname==='/');}catch{return false;}
  }
  if(typeof module!=='undefined')module.exports={photoURL,internalReferrer};
  if(typeof document==='undefined')return;
  const marker='decadesCanGoBack';
  const originalPush=history.pushState.bind(history),originalReplace=history.replaceState.bind(history);
  const withMarker=(state,back)=>({...((state&&typeof state==='object')?state:{}),[marker]:back});
  // Keep our entry marker when HTMX replaces its own state. This prevents Back
  // from returning the native app to its initial loading screen or an external login.
  history.replaceState=function(state,title,url){return originalReplace(withMarker(state,state?.[marker]??history.state?.[marker]??internalReferrer(document.referrer,location.href)),title,url);};
  history.pushState=function(state,title,url){return originalPush(withMarker(state,true),title,url);};
  history.replaceState(history.state,'',location.href);
  let viewer=null;
  function closePhoto(){if(viewer){const old=viewer;viewer=null;old.close();}}
  function back(fallback='/p/today'){
    if(viewer){closePhoto();return true;}
    const dialog=document.querySelector('dialog[open]')||document.querySelector('[role="dialog"][aria-modal="true"]:not([hidden])');
    if(dialog){
      const close=dialog.querySelector('[data-close-dialog]');
      if(close){close.click();return true;}
      const event=new Event('cancel',{cancelable:true});if(dialog.dispatchEvent(event)&&typeof dialog.close==='function')dialog.close();return true;
    }
    if(history.length>1&&history.state?.[marker])history.back();else location.assign(fallback);
    return true;
  }
  window.decadesNavigateBack=back;
  const node=(tag,text,className)=>{const result=document.createElement(tag);if(text)result.textContent=text;if(className)result.className=className;return result;};
  function openPhoto(link,url){
    if(viewer)return;
    const opener=document.activeElement,position=window.scrollY,originalURL=location.href;
    const dialog=node('dialog',null,'photo-viewer');viewer=dialog;dialog.setAttribute('aria-label','Photo viewer');
    const toolbar=node('header',null,'photo-viewer-toolbar');
    const close=node('button','✕ Close photo','primary');close.type='button';close.dataset.closeDialog='';close.autofocus=true;close.addEventListener('click',closePhoto);
    const title=node('strong',link.querySelector('img')?.alt||link.dataset.photoLabel||'Photo');
    const zoom=node('button','Actual size');zoom.type='button';zoom.setAttribute('aria-pressed','false');
    zoom.addEventListener('click',()=>{const large=dialog.classList.toggle('photo-zoomed');zoom.setAttribute('aria-pressed',String(large));zoom.textContent=large?'Fit to screen':'Actual size';});
    const download=node('a','Download','button');const downloadURL=new URL(url);downloadURL.searchParams.set('download','1');download.href=downloadURL.href;download.download='';
    toolbar.append(close,title,zoom,download);
    const viewport=node('div',null,'photo-viewer-image');const photo=node('img');photo.alt=title.textContent;
    const status=node('p','Loading photo…','photo-viewer-status');status.setAttribute('role','status');
    photo.addEventListener('load',()=>{status.textContent='Press Escape or Close photo to return.';});
    photo.addEventListener('error',()=>{photo.hidden=true;status.textContent='The photo could not be loaded. Close photo still works.';});
    viewport.append(photo);dialog.append(toolbar,viewport,status);document.body.append(dialog);
    dialog.addEventListener('click',event=>{const r=dialog.getBoundingClientRect();if(event.target===dialog&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom))closePhoto();});
    dialog.addEventListener('close',()=>{if(viewer===dialog)viewer=null;dialog.remove();document.body.classList.remove('photo-viewer-open');if(location.href===originalURL){if(opener?.isConnected)opener.focus({preventScroll:true});window.scrollTo({top:position,behavior:'instant'});}});
    document.body.classList.add('photo-viewer-open');dialog.showModal();close.focus();photo.src=url.href;
  }
  document.addEventListener('click',event=>{
    const link=event.target.closest('a');if(!link||event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    if(link.hasAttribute('data-tracker-back')){event.preventDefault();event.stopImmediatePropagation();back(link.href);return;}
    if(link.hasAttribute('download'))return;
    const url=photoURL(link.href,location.href);if(!url)return;
    event.preventDefault();event.stopImmediatePropagation();openPhoto(link,url);
  },true);
  function init(){
    document.querySelectorAll('a[href]').forEach(link=>{
      if(link.hasAttribute('download'))return;const url=photoURL(link.href,location.href);if(!url)return;
      // A normal link remains usable with JavaScript disabled or modifier-clicks.
      const destination=new URL(url);destination.pathname=destination.pathname.replace(/^\/portraits\//,'/photos/');
      link.href=destination.href;link.removeAttribute('target');link.setAttribute('hx-boost','false');
    });
    const photo=document.querySelector('[data-photo-page-image]');
    if(photo){const error=()=>{document.querySelector('[data-photo-page-error]').hidden=false;};photo.addEventListener('error',error);if(photo.complete&&!photo.naturalWidth)error();}
  }
  document.addEventListener('htmx:beforeSwap',()=>{if(viewer)closePhoto();});
  document.addEventListener('htmx:afterSettle',init);document.addEventListener('htmx:historyRestore',init);
  document.addEventListener('keydown',event=>{
    if(event.altKey&&event.key==='ArrowLeft'){event.preventDefault();back();}
    else if(event.key==='Escape'&&!event.defaultPrevented&&!document.querySelector('dialog[open]')&&document.querySelector('.photo-page')){event.preventDefault();back(document.querySelector('.photo-page [data-tracker-back]').href);}
  });
  init();
})();
