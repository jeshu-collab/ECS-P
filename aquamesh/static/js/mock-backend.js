/* In-browser simulator that answers the same /api/* endpoints as the real AquaMesh gateway.
   Replace by pointing config.js at a real server – the UI code does not change. */
const MockBackend=(()=>{
const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const rn=s=>(Math.random()-.5)*2*s;
const LVN=['NORMAL','WATCH','WARNING','CRITICAL'];
const DEF={rain:{dry:4095,wet:1200},soil:{dry:3300,wet:1400}};

/* ---------- scenarios (sim-truth targets) ---------- */
const SCEN=[
 {id:'normal',title:'Baseline: dry day',expect:'All zones NORMAL, no rules fire.',t:{rain:.02,soil:.25,cover:.2,rflow:.3,dlvl:.12,dflow:.6,land:0}},
 {id:'heavy_rain',title:'Heavy rain on wet soil',expect:'Runoff potential high; drain overload suspected (not blockage); land starts to pond.',t:{rain:.9,soil:.8,cover:.45,rflow:.6,dlvl:.85,dflow:.8,land:.12}},
 {id:'blockage',title:'Drain blockage',expect:'Drain high, flow low, no rain → blockage beats overload in the abductive comparison.',t:{rain:.03,soil:.3,cover:.2,rflow:.3,dlvl:.9,dflow:.05,land:.1}},
 {id:'surge',title:'River surge',expect:'River cover & flow high → surge suspected; land only floods if terrain allows overbank.',t:{rain:.35,soil:.6,cover:.85,rflow:.85,dlvl:.5,dflow:.5,land:.18}},
 {id:'compound',title:'Compound event',expect:'Rain + surge + drain overload together → compound_event, CRITICAL zones.',t:{rain:.95,soil:.9,cover:.9,rflow:.9,dlvl:.92,dflow:.4,land:.3}},
 {id:'cam_fault',title:'Camera offline',expect:'River & land go UNKNOWN, confidence drops, rules abstain instead of guessing.',cam:true,t:{rain:.7,soil:.6,cover:.5,rflow:.6,dlvl:.7,dflow:.7,land:.05}},
 {id:'node_loss',title:'Drain node unplugged',expect:'Load cell & MPU OFFLINE, link loss rises, drain zone UNKNOWN.',node:true,t:{rain:.6,soil:.5,cover:.4,rflow:.5,dlvl:.6,dflow:.5,land:.04}},
 {id:'replay',title:'Packet replay / CRC errors',expect:'Replays & CRC errors counted; MPU flagged SUSPECT and weighted down.',replay:true,t:{rain:.5,soil:.5,cover:.35,rflow:.4,dlvl:.5,dflow:.5,land:.03}}
];

/* ---------- state ---------- */
let simT=Date.now()/1000-1000, t0=Date.now()/1000, speed=1, scen=SCEN[0], lastRec=0;
const X={rain:.02,soil:.25,cover:.2,rflow:.3,dlvl:.12,dflow:.6,land:0,dist:1,mq:300,hx:12000};
const ablate=new Set();
const cal=JSON.parse(JSON.stringify(DEF));
const terr={elev:{river:5,drain:3,land:0},tol:.5,connected:{'river>drain':true,'river>land':true,'drain>land':true}};
const Hh={};let alerts=[],timeline=[],prevFacts=new Set(),alSeq=1;
const nodes=['river-node','drain-node','land-node'].map(n=>({node:n,received:0,lost:0,crc_bad:0,replays:0,dups:0,reboots:0,rssi:-62}));
let ev=[],audits={runs:0,failed:0},timer=null;

/* ---------- hash-chained evidence log ---------- */
function h53(str){let h1=0xdeadbeef,h2=0x41c6ce57;for(let i=0,c;i<str.length;i++){c=str.charCodeAt(i);h1=Math.imul(h1^c,2654435761);h2=Math.imul(h2^c,1597334677)}
 h1=Math.imul(h1^(h1>>>16),2246822507)^Math.imul(h2^(h2>>>13),3266489909);h2=Math.imul(h2^(h2>>>16),2246822507)^Math.imul(h1^(h1>>>13),3266489909);
 return (4294967296*(2097151&h2)+(h1>>>0)).toString(16).padStart(14,'0').slice(0,12)}
const recHash=r=>h53(r.prev+'|'+r.id+'|'+r.ts+'|'+r.kind+'|'+JSON.stringify(r.payload));
function addEv(kind,payload){const prev=ev.length?ev[ev.length-1].hash:'GENESIS';
 const r={id:(ev.length?ev[ev.length-1].id:0)+1,ts:simT,kind,payload,prev};r.hash=recHash(r);ev.push(r);if(ev.length>3000)ev.shift()}
function verify(arr){for(let i=0;i<arr.length;i++){const r=arr[i];
  if(recHash(r)!==r.hash)return {ok:false,first_bad_id:r.id,reason:'hash mismatch'};
  if(i>0&&r.prev!==arr[i-1].hash)return {ok:false,first_bad_id:r.id,reason:'broken link'}}
 return {ok:true,records:arr.length,head:arr.length?arr[arr.length-1].hash:null}}

/* ---------- knowledge base ---------- */
const LEAF={ // name:[text, sources, value-key, test]
 rain_heavy:['Rainfall is heavy (rain > 0.60)',['rain'],'rain',v=>v>.6],
 rain_light:['Rainfall is light (rain < 0.30)',['rain'],'rain',v=>v<.3],
 soil_saturated:['Soil is saturated (soil > 0.70)',['soil'],'soil',v=>v>.7],
 river_cover_high:['River coverage high (> 0.55)',['camera'],'cover',v=>v>.55],
 river_flow_fast:['River flow fast (> 0.60)',['camera'],'rflow',v=>v>.6],
 drain_level_high:['Drain level high (> 0.65)',['hx'],'dlvl',v=>v>.65],
 drain_level_critical:['Drain level critical (> 0.85)',['hx'],'dlvl',v=>v>.85],
 drain_flow_low:['Drain outflow low (< 0.30)',['mpu'],'dflow',v=>v<.3],
 land_standing_water:['Standing water on land (> 3%)',['camera'],'land',v=>v>.03],
 land_level_warning:['Standing water > 8%',['camera'],'land',v=>v>.08],
 land_level_critical:['Standing water > 20%',['camera'],'land',v=>v>.2],
 land_wet_rising:['Standing water rising over last minute',['camera'],'rise',v=>v>.004],
 edge_river_drain_open:['Terrain: river → drain path open',['terrain'],'er_d',v=>v],
};
const RULES=[
 ['R1','runoff_potential_high',['rain_heavy','soil_saturated'],'Heavy rain on saturated soil gives high runoff.'],
 ['R2','river_surge_suspected',['river_cover_high','river_flow_fast'],'Wide and fast river → surge.'],
 ['R3','drain_blockage_suspected',['drain_level_high','drain_flow_low','rain_light'],'Full drain, little outflow and no rain to explain it → obstruction.'],
 ['R4','drain_overload_suspected',['drain_level_high','rain_heavy'],'Full drain during heavy rain → capacity overload.'],
 ['R5','drain_backflow_risk',['river_cover_high','drain_level_high','edge_river_drain_open'],'High river can push water back up a connected drain.'],
 ['R6','land_inundation_risk_rising',['land_standing_water','land_wet_rising'],'Ponding that is still growing.'],
 ['R7','compound_event',['river_surge_suspected','drain_level_high','land_standing_water'],'River, drain and land stressed at once.'],
 ['R8','river_warning',['river_cover_high'],'River zone at WARNING.'],
 ['R9','drain_warning',['drain_level_high'],'Drain zone at WARNING.'],
 ['R10','drain_critical',['drain_level_critical'],'Drain zone at CRITICAL.'],
 ['R11','land_warning',['land_level_warning'],'Land zone at WARNING.'],
 ['R12','land_critical',['land_level_critical','land_standing_water'],'Land zone at CRITICAL.']];
const RMAP=Object.fromEntries(RULES.map(r=>[r[1],r]));
const ALERTMAP={river_surge_suspected:['WARNING','River surge suspected'],drain_blockage_suspected:['WARNING','Drain blockage suspected'],
 drain_overload_suspected:['WARNING','Drain overload suspected'],drain_backflow_risk:['CRITICAL','Drain backflow risk'],
 land_inundation_risk_rising:['WARNING','Land inundation risk rising'],compound_event:['CRITICAL','Compound flood event']};
const HYPW={ // evidence -> weights per hypothesis
 rain:{p_rain:4.5,p_surge:1,p_block:-2.5,p_inund:1},soil:{p_rain:1.5,p_surge:.5,p_inund:1.2},
 river_cover:{p_surge:4,p_inund:1.2},river_flow:{p_surge:2.5},dist:{p_surge:1.2},
 drain_level:{p_block:3.5,p_inund:1},drain_flow:{p_block:-3.5},land_still:{p_inund:5}};
const BIAS={p_rain:-1.2,p_surge:-2.2,p_block:-1.5,p_inund:-1.5};
const HLAB={p_rain:'Heavy rainfall',p_surge:'River surge',p_block:'Drain blockage',p_inund:'Land inundation'};

/* ---------- simulation ---------- */
function cur(){return scen}
function openEdges(){const e=terr.elev,t=terr.tol,c=terr.connected;
 return {edge_river_drain_open:c['river>drain']&&e.river+t>=e.drain,edge_river_land_open:c['river>land']&&e.river+t>=e.land,edge_drain_land_open:c['drain>land']&&e.drain+t>=e.land}}
function step(dt){
 const T=scen.t,o=openEdges(),k=tau=>1-Math.exp(-dt/tau),n=s=>rn(s*Math.sqrt(dt));
 let landT=T.land;if(!o.edge_river_land_open&&!o.edge_drain_land_open)landT*=.1;
 else if(X.cover>.55&&!o.edge_river_land_open&&!(X.dlvl>.65&&o.edge_drain_land_open))landT*=.15;
 const ease=(key,tg,tau,ns)=>{X[key]=clamp(X[key]+(tg-X[key])*k(tau)+n(ns))};
 ease('rain',T.rain,20,.004);ease('soil',T.soil,90,.002);ease('cover',T.cover,40,.004);ease('rflow',T.rflow,30,.006);
 ease('dlvl',T.dlvl,45,.004);ease('dflow',T.dflow,30,.006);ease('land',landT,70,.0012);
 X.dist=.8+X.rflow*2.2+rn(.05);X.mq=280+X.rain*40+rn(4);X.hx=12000+X.dlvl*88000+rn(150);
 simT+=dt;
}
function sig(){
 const ab=ablate,A={camera:!ab.has('camera')&&!scen.cam,rain:!ab.has('rain'),soil:!ab.has('soil'),hx:!ab.has('hx')&&!scen.node,mpu:!ab.has('mpu')&&!scen.node,gas:!ab.has('gas')};
 const rawRain=A.rain?Math.round(DEF.rain.dry-X.rain*(DEF.rain.dry-DEF.rain.wet)):null;
 const rawSoil=A.soil?Math.round(DEF.soil.dry-X.soil*(DEF.soil.dry-DEF.soil.wet)):null;
 const cl=(raw,c)=>raw==null?null:clamp((c.dry-raw)/(c.dry-c.wet||1));
 const arr=Hh.land_still||[],ago=arr.length>30?arr[arr.length-30][1]:null;
 const land=A.camera?X.land:null;
 return {A,raw:{rain:rawRain,soil:rawSoil},rain:cl(rawRain,cal.rain),soil:cl(rawSoil,cal.soil),
  cover:A.camera?X.cover:null,rflow:A.camera?X.rflow:null,land,rise:land==null||ago==null?(land==null?null:0):land-ago,
  dlvl:A.hx?X.dlvl:null,dflow:A.mpu?X.dflow:null,dist:A.mpu?X.dist:null,mq:A.gas?X.mq:null,hx:A.hx?X.hx:null}
}
function derive(){
 const s=sig(),o=openEdges(),memo={};
 const ctx={...s,er_d:o.edge_river_drain_open};
 const srcState=src=>src==='terrain'?'OK':!s.A[src]?(ablate.has(src)?'DISABLED':'OFFLINE'):(src==='mpu'&&scen.replay?'SUSPECT':'OK');
 function status(n){if(memo[n])return memo[n];let st;
  if(LEAF[n]){const [,,key,fn]=LEAF[n],v=ctx[key];st=v==null?'UNKNOWN':(fn(v)?'TRUE':'FALSE')}
  else{const ps=RMAP[n][2].map(status);st=ps.every(x=>x==='TRUE')?'TRUE':ps.includes('FALSE')?'FALSE':'UNKNOWN'}
  return memo[n]=st}
 const names=[...Object.keys(LEAF),...RULES.map(r=>r[1])];names.forEach(status);
 const facts=names.filter(n=>memo[n]==='TRUE').map(n=>({name:n}));
 if(o.edge_river_land_open)facts.push({name:'edge_river_land_open'});
 const firings=RULES.filter(r=>memo[r[1]]==='TRUE'&&!['R8','R9','R10','R11','R12'].includes(r[0])||(['R8','R9','R10','R11','R12'].includes(r[0])&&memo[r[1]]==='TRUE'))
   .map(r=>({rule:r[0],premises:r[2],then:r[1],doc:r[3]}));
 /* zones */
 const lvl=(v,th)=>v==null?-1:th.filter(t=>v>=t).length;
 const zl={river:lvl(s.cover,[.4,.55,.8]),drain:lvl(s.dlvl,[.4,.65,.85]),land:lvl(s.land,[.03,.08,.2])};
 /* probabilities */
 const lg={...BIAS},contrib={};
 const ev={rain:s.rain,soil:s.soil,river_cover:s.cover,river_flow:s.rflow,dist:s.dist==null?null:clamp(s.dist/3),drain_level:s.dlvl,drain_flow:s.dflow,land_still:s.land==null?null:clamp(s.land/.3)};
 const penal=scen.replay?.6:1;
 for(const [e,w] of Object.entries(HYPW)){const v=ev[e];if(v==null)continue;contrib[e]={};
  for(const [h,wt] of Object.entries(w)){const c=wt*(v-.4)*1.6*(e==='dist'?penal:1);lg[h]+=c;contrib[e][h]=Math.round(c*100)/100}}
 const P={};for(const h in lg)P[h]=1/(1+Math.exp(-lg[h]));
 const hyp=Object.keys(HLAB).map(h=>({id:h,label:HLAB[h],p:P[h]}));
 /* abduction */
 const rain=s.rain??.3,dfl=s.dflow??.5,dl=s.dlvl??0,cov=s.cover??.2;
 const ab=[['Rain overload (capacity exceeded)',.05+rain*dfl*2],['Blockage / obstruction',.05+(1-dfl)*(1-rain)*dl*2],
  ['River backflow',.05+cov*dl*(o.edge_river_drain_open?1.5:.15)],['Sensor artefact / noise',.05+(scen.replay?.5:0)+(s.dlvl==null?.3:0)]];
 const tot=ab.reduce((a,b)=>a+b[1],0),abduction=ab.map(a=>({label:a[0],p:a[1]/tot}));
 /* health */
 const health={};
 for(const k of ['camera','rain','soil','hx','mpu','gas']){const st=srcState(k),reasons=[];
  if(st==='OFFLINE')reasons.push(k==='camera'?'no frames for >10 s':'node not reporting');
  if(st==='SUSPECT')reasons.push('replayed / CRC-failed packets rejected');
  if(st==='DISABLED')reasons.push('disabled by operator (ablation)');
  if(k==='gas')reasons.push('context only');
  health[k]={state:st,rel:st==='OK'?.97:st==='SUSPECT'?.6:null,reasons}}
 /* zones */
 const srcs={river:[['camera',.6],['mpu',.25],['rain',.15]],drain:[['hx',.6],['mpu',.25],['rain',.15]],land:[['camera',.5],['soil',.3],['rain',.2]]};
 const relOf=k=>health[k].state==='OK'?1:health[k].state==='SUSPECT'?.6:0;
 const goalOf={river:'river_warning',drain:'drain_warning',land:'land_warning'};
 const probOf={river:[P.p_surge,'P(river surge)'],drain:[Math.max(P.p_block,clamp(P.p_rain*dl*1.2)),'P(drain stress)'],land:[P.p_inund,'P(inundation)']};
 const reasonOf={
  river:()=>s.cover==null?'Camera unavailable – river coverage and flow cannot be assessed; rules abstain.':`Coverage ${(s.cover*100).toFixed(0)}%, flow ${(s.rflow*100).toFixed(0)}%${memo.river_surge_suspected==='TRUE'?' → surge suspected (R2).':'.'}${s.rain!=null?` Rain ${(s.rain*100).toFixed(0)}%.`:''}`,
  drain:()=>s.dlvl==null?'Load-cell node offline – drain level unknown; rules abstain.':`Level ${(s.dlvl*100).toFixed(0)}%, outflow ${s.dflow==null?'n/a':(s.dflow*100).toFixed(0)+'%'}. ${memo.drain_blockage_suspected==='TRUE'?'Blockage suspected (R3).':memo.drain_overload_suspected==='TRUE'?'Overload suspected during heavy rain (R4).':'Within capacity.'}${memo.drain_backflow_risk==='TRUE'?' Backflow risk (R5).':''}`,
  land:()=>s.land==null?'Camera unavailable – standing water cannot be measured; rules abstain.':`Standing water ${(s.land*100).toFixed(1)}%${memo.land_wet_rising==='TRUE'?' and rising (R6)':''}. Soil ${s.soil==null?'n/a':(s.soil*100).toFixed(0)+'% wet'}.${!o.edge_river_land_open&&!o.edge_drain_land_open?' Terrain blocks inflow paths.':''}`};
 const zones={};
 for(const z of ['river','drain','land']){const i=zl[z],avl=srcs[z].filter(a=>relOf(a[0])>0).length;
  const cov=srcs[z].reduce((a,[k,w])=>a+w*relOf(k),0),ind=avl>=3?1:avl===2?.85:avl===1?.6:0,conf=cov*ind;
  const [pr,pl]=probOf[z],exp=[.1,.3,.6,.85][Math.max(0,i)],d=pr-exp;
  zones[z]={level:i<0?'UNKNOWN':LVN[i],status:i<0?'NO_DATA':conf<.7?'DEGRADED':'OK',conf_label:conf>=.7?'high':conf>=.4?'moderate':'low',
   reason:reasonOf[z](),confidence:conf,prob:pr,prob_label:pl,goal:goalOf[z],
   agree:i<0?'n/a (rules abstain)':Math.abs(d)<.3?'agree':d>0?'probability higher':'rules higher'}}
 return {s,o,memo,facts,firings,zones,zl,hyp,P,abduction,contrib,health,names}
}

/* ---------- record: history, events, alerts, evidence ---------- */
function push(k,v){(Hh[k]=Hh[k]||[]).push([simT,v==null?null:Math.round(v*10000)/10000]);while(Hh[k][0][0]<simT-900)Hh[k].shift()}
function tl(ev_,text,rule){timeline.unshift({t:simT,ev:ev_,text,rule});timeline.length=Math.min(timeline.length,60)}
function record(){
 const D=derive(),s=D.s;
 push('rain',s.rain);push('soil',s.soil);push('river_cover',s.cover);push('river_flow',s.rflow);push('drain_level',s.dlvl);push('drain_flow',s.dflow);
 push('land_still',s.land);push('p_rain',D.P.p_rain);push('p_surge',D.P.p_surge);push('p_block',D.P.p_block);push('p_inund',D.P.p_inund);
 push('sev_river',Math.max(0,D.zl.river));push('sev_drain',Math.max(0,D.zl.drain));push('sev_land',Math.max(0,D.zl.land));
 push('dist',s.dist);push('mq',s.mq);push('hx',s.hx);
 /* fact transitions */
 const now=new Set(D.facts.map(f=>f.name));
 for(const n of now)if(!prevFacts.has(n)&&RMAP[n]&&!['R8','R9','R10','R11','R12'].includes(RMAP[n][0])){
   tl('ON',`${n} asserted`,RMAP[n][0]);addEv('rule_on',{fact:n,rule:RMAP[n][0]});
   if(ALERTMAP[n]){const [l,t]=ALERTMAP[n];alerts.unshift({id:alSeq++,key:n,level:l,title:t,detail:RMAP[n][3]+' Premises: '+RMAP[n][2].join(', ')+'.',t:simT,t_clear:null,active:true});}}
 for(const n of prevFacts)if(!now.has(n)&&RMAP[n]&&!['R8','R9','R10','R11','R12'].includes(RMAP[n][0])){
   tl('OFF',`${n} retracted`,RMAP[n][0]);addEv('rule_off',{fact:n});const a=alerts.find(x=>x.key===n&&x.active);if(a){a.active=false;a.t_clear=simT}}
 prevFacts=now;
 /* zone alerts + health alerts */
 for(const z of ['river','drain','land']){const key='zone_'+z,a=alerts.find(x=>x.key===key&&x.active),L=D.zones[z].level;
  if((L==='WARNING'||L==='CRITICAL')){ if(!a){alerts.unshift({id:alSeq++,key,level:L,title:`${z} zone ${L}`,detail:D.zones[z].reason,t:simT,t_clear:null,active:true});addEv('alert',{zone:z,level:L})}
   else if(a.level!==L){a.level=L;a.title=`${z} zone ${L}`;a.detail=D.zones[z].reason}}
  else if(a){a.active=false;a.t_clear=simT}}
 for(const [k,h] of Object.entries(D.health)){const key='health_'+k,a=alerts.find(x=>x.key===key&&x.active),bad=h.state==='OFFLINE'||h.state==='SUSPECT';
  if(bad&&!a){alerts.unshift({id:alSeq++,key,level:'INFO',title:`Sensor ${k} ${h.state}`,detail:h.reasons.join('; '),t:simT,t_clear:null,active:true});addEv('sensor_health',{source:k,state:h.state})}
  else if(!bad&&a){a.active=false;a.t_clear=simT}}
 alerts.length=Math.min(alerts.length,50);
 /* link counters */
 nodes.forEach((n,i)=>{const down=i===1&&scen.node;if(down){n.lost++}else{n.received++;if(Math.random()<.01)n.lost++}
  if(i===1&&scen.replay){n.replays+=Math.random()<.5?1:0;n.dups+=Math.random()<.3?1:0;n.crc_bad+=Math.random()<.35?1:0}
  n.rssi=down?null:Math.round(-60-i*4+rn(3))});
 if(simT-lastRec>=10||!ev.length){lastRec=simT;addEv('sensor_snapshot',{rain:s.rain&&+s.rain.toFixed(2),cover:s.cover&&+s.cover.toFixed(2),drain:s.dlvl&&+s.dlvl.toFixed(2),land:s.land&&+s.land.toFixed(3),zones:Object.values(D.zones).map(z=>z.level[0]).join('')})}
}
function advance(total){let left=total;while(left>0){const dt=Math.min(2,left);step(dt);record();left-=dt}}
function setScenario(id,quiet){const s=SCEN.find(x=>x.id===id);if(!s)return false;scen=s;if(!quiet){tl('SCENARIO','scenario → '+s.title);addEv('scenario',{id})}return true}

/* ---------- API router ---------- */
function snapshot(){
 const D=derive(),now=Date.now()/1000;
 return {mode:'SIM',speed,t:simT,uptime:now-t0,zones:D.zones,hyp:D.hyp,abduction:D.abduction,contributions:D.contrib,alerts,firings:D.firings,timeline,
  health:D.health,links:nodes.map(n=>({...n,loss_pct:(n.lost/Math.max(1,n.received+n.lost)*100).toFixed(1)})),
  gateway:{connected:true,port:'simulated://gateway'},facts:D.facts,
  terrain:{elev:{...terr.elev},tol:terr.tol,connected:{...terr.connected},open:D.o},
  cal:JSON.parse(JSON.stringify(cal)),raw:{rain:D.s.raw.rain,soil:D.s.raw.soil},ablate:[...ablate],
  evidence:{records:ev.length,head:ev.length?ev[ev.length-1].hash:'-',audits:{...audits},outbox:ev.length%7}}
}
function tree(n,D){
 if(RMAP[n]){const r=RMAP[n],ch=r[2].map(c=>tree(c,D));
  return {text:n,status:D.memo[n],detail:r[3],rule:r[0],children:ch,missing:ch.flatMap(c=>c.status==='UNKNOWN'?(c.missing||[]):[]),sources:[]}}
 const [text,srcs,key]=LEAF[n],v=D.s[key]??(key==='er_d'?D.o.edge_river_drain_open:null);
 return {text,status:D.memo[n],detail:v==null?null:(typeof v==='boolean'?String(v):(+v).toFixed(3)),children:[],sources:srcs,
  missing:D.memo[n]==='UNKNOWN'?srcs.map(s=>s+' unavailable'):[]}
}
const HYPOF={drain_blockage_suspected:'p_block',river_surge_suspected:'p_surge',runoff_potential_high:'p_rain',land_inundation_risk_rising:'p_inund',
 land_warning:'p_inund',land_critical:'p_inund',drain_warning:'p_block',drain_critical:'p_block',river_warning:'p_surge',drain_overload_suspected:'p_rain',drain_backflow_risk:'p_surge',compound_event:'p_inund'};
function why(g){
 if(!RMAP[g]&&!LEAF[g])return {error:'unknown goal '+g};
 const D=derive(),t=tree(g,D),chain=[];
 (function walk(n){n.children.forEach(walk);if(n.rule&&n.status==='TRUE')chain.push(n.rule)})(t);
 const lines=[];(function w(n,d){lines.push('  '.repeat(d)+({TRUE:'✔',FALSE:'✘',UNKNOWN:'?'}[n.status])+' '+n.text+(n.rule?' ['+n.rule+']':''));n.children.forEach(c=>w(c,d+1))})(t,0);
 const verdict=t.status==='TRUE'?'holds':t.status==='FALSE'?'does not hold':'cannot be decided (missing evidence)';
 const out={text:`${g} ${verdict}.\n`+lines.join('\n'),chain,tree:t};
 const h=HYPOF[g];if(h){out.bayes={label:HLAB[h],p:D.P[h],contributions:Object.fromEntries(Object.entries(D.contrib).filter(([e,c])=>c[h]!=null&&Math.abs(c[h])>.1).map(([e,c])=>[e,c[h]]))}}
 return out}
const csv=()=>'id,ts,kind,hash,payload\n'+ev.map(r=>[r.id,r.ts,r.kind,r.hash,'"'+JSON.stringify(r.payload).replace(/"/g,'""')+'"'].join(',')).join('\n');

function handle(path,body){
 const u=new URL(path,'http://x'),p=u.pathname,q=u.searchParams;
 switch(p){
 case '/api/snapshot':return snapshot();
 case '/api/history':{const o={};for(const k in Hh)o[k]=Hh[k].slice();return o}
 case '/api/why':return why(q.get('goal'));
 case '/api/evidence':return ev.slice(-(+q.get('n')||14)).reverse();
 case '/api/verify':return verify(ev);
 case '/api/audit':{audits.runs++;const k=Math.floor(Math.random()*ev.length),r=ev[k];const ok=r&&recHash(r)===r.hash&&(k===0||r.prev===ev[k-1].hash);if(!ok)audits.failed++;return {ok,sampled_record:r&&r.id}}
 case '/api/tamper_demo':{if(ev.length<4)return {edited_record:null,before:verify(ev),after:null};
  const c=JSON.parse(JSON.stringify(ev)),i=Math.floor(c.length/2);c[i].payload={tampered:true};return {edited_record:c[i].id,before:verify(ev),after:verify(c)}}
 case '/api/export.csv':return {csv:csv()};
 case '/api/scenarios':return {scenarios:SCEN.map(({id,title,expect})=>({id,title,expect})),current:scen.id,speed};
 case '/api/scenario':if(body){if(body.speed)speed=clamp(+body.speed,1,60);if(body.name){if(!setScenario(body.name))return {error:'unknown scenario'}}}return {ok:true,current:scen.id,speed};
 case '/api/ablate':if(body){body.off?ablate.add(body.source):ablate.delete(body.source);addEv('ablation',{...body})}return {ok:true,ablate:[...ablate]};
 case '/api/terrain':if(body){if(body.elev)Object.assign(terr.elev,body.elev);if(body.connected)Object.assign(terr.connected,body.connected);addEv('terrain',{elev:{...terr.elev},connected:{...terr.connected}})}return {ok:true};
 case '/api/calibrate':{const raw=sig().raw[body.sensor];if(raw==null)return {error:'no current '+body.sensor+' reading to calibrate with'};
  const c=cal[body.sensor];if(!c)return {error:'unknown sensor'};const old=c[body.point];c[body.point]=raw;
  if(c.dry===c.wet){c[body.point]=old;return {error:'dry and wet points must differ – set the other point first'}}
  addEv('calibration',{sensor:body.sensor,point:body.point,raw});return {ok:true}}
 }
 return {error:'not found: '+p};
}

/* ---------- lifecycle ---------- */
function start(opts={}){
 if(timer)return;speed=opts.speed||speed;
 setScenario('normal',true);advance(700);setScenario(opts.scenario||'heavy_rain');advance(120);
 let last=Date.now();
 timer=setInterval(()=>{const n=Date.now();advance((n-last)/1000*speed);last=n},500);
}
function stop(){clearInterval(timer);timer=null}
return {start,stop,handle,advance,csv,SCEN};
})();
if(typeof module!=='undefined')module.exports=MockBackend;
