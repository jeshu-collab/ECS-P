/* shared helpers, global state, backend selection */
const $=id=>document.getElementById(id);
const CFG=window.AQUAMESH_CONFIG||{};
const TOK=new URLSearchParams(location.search).get('token')||CFG.token||'';
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const hms=t=>new Date(t*1000).toLocaleTimeString([], {hour12:false});
const LV=['NORMAL','WATCH','WARNING','CRITICAL'];
const GOALS=['land_warning','land_critical','drain_warning','drain_critical','river_warning','river_surge_suspected','drain_blockage_suspected',
 'drain_overload_suspected','drain_backflow_risk','compound_event','land_inundation_risk_rising','runoff_potential_high'];
let S=null,H={},goal=null,view='sch',expLoaded=false,SC=null,USE_MOCK=false;

/* GET when b===undefined, otherwise POST JSON. Same contract as the original dashboard. */
async function api(p,b){
  if(USE_MOCK){await 0;return MockBackend.handle(p,b)}
  const o=b===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Token':TOK},body:JSON.stringify(b)};
  try{return await(await fetch((CFG.baseUrl||'')+p,o)).json()}catch(e){return {error:String(e)}}
}
async function detectBackend(){
  const mode=CFG.backend||'auto';
  if(mode==='mock'){USE_MOCK=true;return}
  if(mode==='live'){USE_MOCK=false;return}
  try{
    const ac=new AbortController(),t=setTimeout(()=>ac.abort(),2500);
    const r=await fetch((CFG.baseUrl||'')+'/api/snapshot',{signal:ac.signal});clearTimeout(t);
    const j=await r.json();USE_MOCK=!(j&&j.zones);
  }catch(e){USE_MOCK=true}
}
