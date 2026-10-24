/* Displays real raw packet fields and camera estimates; never substitutes simulated values in LIVE mode. */
(function(){
 const $=id=>document.getElementById(id);
 const fmt=(v,suffix='')=>v===undefined||v===null?'—':String(v)+suffix;
 async function refresh(){
  try{
   const r=await fetch((window.AQUAMESH_CONFIG?.baseUrl||'')+'/api/snapshot',{cache:'no-store'});
   const s=await r.json(); if(!s||!s.zones)return;
   const raw=s.raw||{}, vis=s.vision||{};
   const links=s.links||[];
   const linkByNode=Object.fromEntries(links.map(x=>[x.node,x]));
   const l=linkByNode.FLOOD01||{},g=linkByNode.GAS01||{};
   const field=(label,key,suf='')=>`<div>${label}: <b>${fmt(raw[key],suf)}</b></div>`;
   $('landTelemetry').innerHTML=
    `<div>Packet: <b>${fmt(l.last_pkt)}</b> · link: <b>${fmt(l.received, ' received')}</b></div>`+
    field('Rain raw ADC','rain')+field('Soil raw ADC','soil')+
    `<div>Pressure / HX710B raw: <b>${fmt(raw.hx)}</b></div>`+
    `<div>Node status: <b>${esc(s.health?.flood_node?.state||'UNKNOWN')}</b></div>`;
   $('gasTelemetry').innerHTML=
    `<div>Packet: <b>${fmt(g.last_pkt)}</b> · link: <b>${fmt(g.received, ' received')}</b></div>`+
    field('MQ gas raw ADC','mq')+`<div>MPU6050 disturbance (DIST): <b>${fmt(raw.dist)}</b></div>`+
    `<div>Node status: <b>${esc(s.health?.gas_node?.state||'UNKNOWN')}</b></div>`;
   const zone=(n)=>{const v=vis[n]||{};return `${n}: <b>${esc(v.state||'UNLABELED')}</b> · ${n==='river'?'flow '+fmt(v.flow):n==='drain'?'level '+fmt(v.level):'wet '+fmt(v.still)}`};
   $('camTelemetry').innerHTML=`<div>Vision: <b>${esc(vis.status||'NO CAMERA DATA')}</b></div><div>${zone('river')}</div><div>${zone('drain')}</div><div>${zone('land')}</div><div class="dim">Camera metrics require labeled regions and a learned dry baseline.</div>`;
  }catch(e){ }
 }
 function esc(v){return String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
 refresh();setInterval(refresh,1000);
})();