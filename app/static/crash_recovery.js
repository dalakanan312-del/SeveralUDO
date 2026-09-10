/* The existing status poll carries recovery state; no extra network polling. */
document.addEventListener('decades:clock',event=>{
  const status=event.detail;
  if(!status)return;
  const banner=document.querySelector('[data-recovery-banner]');
  if(banner)banner.hidden=!status.recovery_required;
});
