/* animated SVG schematic + simulated camera frame */
const lastH=k=>{const a=H[k];return a&&a.length?(a[a.length-1][1]||0):0};
function schematic(){
  const f=new Set(S.facts.map(x=>x.name));
  const rain=lastH('rain'),cov=lastH('river_cover'),lvl=lastH('drain_level'),still=lastH('land_still'),rf=lastH('river_flow');
  const eo=S.terrain.open,col=z=>({NORMAL:'#2ecc71',WATCH:'#f1c40f',WARNING:'#f39c12',CRITICAL:'#e74c3c',UNKNOWN:'#9b59b6'})[S.zones[z].level];
  const edge=(on,act)=>on?(act?'#e74c3c':'#5a7a99'):'#3a3f46';
  const bf=f.has('drain_backflow_risk'),ov=f.has('drain_blockage_suspected')||f.has('drain_overload_suspected')||f.has('drain_level_high'),lr=f.has('land_inundation_risk_rising');
  let s='';
  s+=`<ellipse cx="90" cy="26" rx="50" ry="16" fill="#31405a"/><ellipse cx="125" cy="22" rx="34" ry="14" fill="#3a4b69"/>`;
  for(let i=0;i<8;i++)s+=`<line x1="${60+i*13}" y1="44" x2="${54+i*13}" y2="${44+10+rain*28}" stroke="#3b9eff" stroke-width="2" opacity="${rain>.05?.35+rain*.6:.12}"/>`;
  s+=`<text x="170" y="30">rain ${(rain*100).toFixed(0)}%</text>`;
  s+=`<rect x="20" y="85" width="520" height="42" rx="8" fill="#10151b" stroke="${col('river')}" stroke-width="2.5"/>`;
  s+=`<rect x="22" y="87" width="${516*Math.min(1,cov)}" height="38" rx="6" fill="#1e6fd0" opacity=".85"/>`;
  for(let i=0;i<5;i++)s+=`<path d="M${30+i*100+((Date.now()/(200-rf*120))%100)} 106 q10 -8 20 0 t20 0" stroke="#bfe0ff" fill="none" opacity="${.15+rf*.6}"/>`;
  s+=`<text x="300" y="80">RIVER  cover ${(cov*100).toFixed(0)}%</text>`;
  s+=`<rect x="262" y="127" width="36" height="92" fill="#10151b" stroke="${col('drain')}" stroke-width="2.5"/>`;
  s+=`<rect x="264" y="${217-88*lvl}" width="32" height="${88*lvl}" fill="#e08a1e" opacity=".85"/>`;
  if(f.has('drain_blockage_suspected'))s+=`<g stroke="#e74c3c" stroke-width="4"><line x1="262" y1="168" x2="298" y2="190"/><line x1="298" y1="168" x2="262" y2="190"/></g>`;
  s+=`<text x="306" y="150">DRAIN ${(lvl*100).toFixed(0)}% full</text>`;
  s+=`<rect x="20" y="228" width="520" height="62" rx="8" fill="#16210f" stroke="${col('land')}" stroke-width="2.5"/>`;
  const np=Math.min(8,Math.round(still*30));for(let i=0;i<np;i++)s+=`<ellipse cx="${60+i*60}" cy="${262+(i%2)*8}" rx="${14+still*50}" ry="${7+still*16}" fill="#2a7bd6" opacity=".7"/>`;
  s+=`<text x="28" y="246">LAND  wet ${(still*100).toFixed(1)}%</text>`;
  const arrow=(x1,y1,x2,y2,c,dash,l)=>`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${c}" stroke-width="3" ${dash?'stroke-dasharray="5 4"':''} marker-end="url(#ar)"/><text x="${(x1+x2)/2+6}" y="${(y1+y2)/2}">${l}</text>`;
  s+=`<defs><marker id="ar" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" fill="context-stroke"/></marker></defs>`;
  s+=arrow(280,112,280,136,edge(eo.edge_river_drain_open,bf),!eo.edge_river_drain_open,'');
  s+=arrow(150,127,150,228,edge(eo.edge_river_land_open,f.has('river_surge_suspected')&&f.has('soil_saturated')),!eo.edge_river_land_open,'overbank');
  s+=arrow(280,219,280,228,edge(eo.edge_drain_land_open,ov&&lr),!eo.edge_drain_land_open,'');
  s+=`<text x="300" y="224" fill="#8a96a6" style="font-size:10px">dashed = path closed by terrain · red = active</text>`;
  $('sch').innerHTML=s;
}
/* Camera tab in simulator mode: paints a top-down diorama frame to a canvas and feeds the <img>. */
const camCv=document.createElement('canvas');camCv.width=640;camCv.height=360;
function camFrame(){
  const x=camCv.getContext('2d'),W=640,Hh=360;
  const cov=lastH('river_cover'),lvl=lastH('drain_level'),still=lastH('land_still'),rf=lastH('river_flow');
  const off=S&&S.health.camera.state!=='OK';
  x.fillStyle='#1c2a16';x.fillRect(0,0,W,Hh);
  if(off){x.fillStyle='#000';x.fillRect(0,0,W,Hh);x.fillStyle='#e74c3c';x.font='20px system-ui';x.fillText('NO SIGNAL – camera '+S.health.camera.state,170,185);return}
  x.fillStyle='#8a7a5a';x.fillRect(0,110,W,110);                       // river bed
  x.fillStyle='#2a74c8';x.fillRect(0,110+110*(1-cov)/2,W,110*cov);      // water
  x.strokeStyle='rgba(255,255,255,.35)';const ph=(Date.now()/(60-rf*40))%60;
  for(let i=-1;i<12;i++){x.beginPath();x.moveTo(i*60+ph,165);x.quadraticCurveTo(i*60+ph+15,155,i*60+ph+30,165);x.stroke()}
  x.fillStyle='#444';x.fillRect(300,220,40,70);x.fillStyle='#d9892b';x.fillRect(302,288-66*lvl,36,66*lvl);   // drain
  x.fillStyle='#2e3d22';x.fillRect(0,290,W,70);                                                        // land
  x.fillStyle='rgba(60,140,230,.75)';for(let i=0;i<Math.min(8,Math.round(still*30));i++){x.beginPath();x.ellipse(60+i*75,320+(i%2)*14,18+still*70,8+still*26,0,0,7);x.fill()}
  x.fillStyle='rgba(0,0,0,.55)';x.fillRect(0,0,W,22);x.fillStyle='#fff';x.font='12px monospace';x.fillText('SIMULATED CAMERA  '+new Date().toLocaleTimeString(),8,15);
}
