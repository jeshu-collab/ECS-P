/* wiring + polling loops */
let terrKey='',camTimer=null;
if($('cam')) $('cam').addEventListener('error',()=>{const w=$('camWrap');if(w&&!w.querySelector('.camera-error')){const m=document.createElement('div');m.className='camera-error';m.style.cssText='padding:14px;color:#f0c7a6;background:#211b16;font:13px/1.5 monospace';m.textContent='Camera stream unavailable. On Raspberry Pi, check camera connection and run without --no-camera; in --sim mode this build now starts the camera automatically.';w.appendChild(m)}});
if($('cam')) $('cam').addEventListener('load',()=>{const m=$('camWrap')?.querySelector('.camera-error');if(m)m.remove()});
$('goal').innerHTML=GOALS.map(g=>`<option>${g}</option>`).join('');
$('goal').onchange=()=>{goal=$('goal').value;why()};$('bWhy').onclick=()=>{goal=$('goal').value;why()};
if($('tSch'))$('tSch').onclick=()=>setView('sch');if($('tCam'))$('tCam').onclick=()=>setView('cam');
function setView(v){view=v;if($('tSch'))$('tSch').classList.toggle('on',v=='sch');if($('tCam'))$('tCam').classList.toggle('on',v=='cam');
  $('schWrap').style.display=v=='sch'?'':'none';$('camWrap').style.display=v=='cam'?'':'none';
  clearInterval(camTimer);
  if(v=='cam'){
    if(USE_MOCK){const draw=()=>{if(S){camFrame();$('cam').src=camCv.toDataURL('image/jpeg',.8)}};draw();camTimer=setInterval(draw,400)}
    else $('cam').src=(CFG.baseUrl||'')+'/stream.mjpg?'+Date.now();
  }else $('cam').removeAttribute('src');
}
$('bVer').onclick=async()=>{$('evres').textContent=JSON.stringify(await api('/api/verify'),null,1)};
$('bTam').onclick=async()=>{const r=await api('/api/tamper_demo');$('evres').textContent=`Edited record #${r.edited_record} in a COPY of the log.\nBefore edit: ${JSON.stringify(r.before)}\nAfter edit: ${JSON.stringify(r.after)}\n→ chain verification ${(r.after&&!r.after.ok?'DETECTED the tampering at record '+r.after.first_bad_id:'did not detect (log too short?)')}`};
$('bAud').onclick=async()=>{$('evres').textContent='Random audit: '+JSON.stringify(await api('/api/audit'),null,1)};
document.querySelectorAll('[data-cal]').forEach(b=>b.onclick=async()=>{const [s,p]=b.dataset.cal.split(',');const r=await api('/api/calibrate',{sensor:s,point:p});if(r.error)alert(r.error)});
$('csvLink').onclick=async e=>{ if(!USE_MOCK)return; e.preventDefault();
  const r=await api('/api/export.csv'),a=document.createElement('a');a.href=URL.createObjectURL(new Blob([r.csv],{type:'text/csv'}));a.download='aquamesh-evidence.csv';a.click()};
if($('csvLink')&&CFG.baseUrl)$('csvLink').href=CFG.baseUrl+'/api/export.csv';

async function poll(){
  const s=await api('/api/snapshot');if(!s||!s.zones){return}
  S=s;const m=$('mode');m.textContent=s.mode+(s.mode!='LIVE'?' ×'+s.speed:'');m.className='badge '+s.mode.toLowerCase().slice(0,4);
  $('clock').textContent=hms(s.t)+'  ·  up '+Math.round(s.uptime)+'s';
  zones();hyp();alerts();fires();health();ablate();
  const tk=JSON.stringify(S.terrain)+JSON.stringify(S.cal.rain)+JSON.stringify(S.cal.soil);
  if(tk!==terrKey&&!document.activeElement.closest('#terrain')){terrKey=tk;terrain()}else if(tk===terrKey)calNow();
  if(!goal){goal=S.zones.land.goal;$('goal').value=GOALS.includes(goal)?goal:GOALS[0];why()}
  experiments();
}
async function polh(){const h=await api('/api/history?n=900');if(h&&!h.error){H=h;drawCharts()}}
async function slow(){if(S){evlog();why()}}

(async()=>{
  await detectBackend();
  if($('cam')&&!USE_MOCK)$('cam').src=(CFG.baseUrl||'')+'/stream.mjpg?'+Date.now();
  if(USE_MOCK)MockBackend.start({speed:CFG.mockSpeed||4,scenario:CFG.mockStartScenario||'heavy_rain'});
  poll();polh();setInterval(poll,1000);setInterval(polh,2000);setInterval(slow,3000);
  setInterval(()=>{if(S&&view=='sch')schematic()},250);
})();
