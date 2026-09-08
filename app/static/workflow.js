/* Hash shortcuts must reveal forms nested in collapsed sections, including
   after fast page swaps. No record or game state is changed here. */
(function(){
  function revealSection(){
    if(!location.hash)return;
    let id;try{id=decodeURIComponent(location.hash.slice(1));}catch(_){return;}
    const target=document.getElementById(id);if(!target)return;
    for(let node=target;node;node=node.parentElement){if(node.tagName==='DETAILS')node.open=true;}
    requestAnimationFrame(()=>target.scrollIntoView({block:'start',behavior:'instant'}));
  }
  window.addEventListener('hashchange',revealSection);
  document.addEventListener('htmx:afterSettle',revealSection);
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href^="#"]');
    if(link&&link.getAttribute('href')===location.hash)revealSection();
  });
  revealSection();
})();
