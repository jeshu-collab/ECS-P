(() => {
const $=id=>document.getElementById(id);
const CONFIG={liveEndpoint:"/api/twin/state",telemetryEndpoint:"",tickMs:1800,lat:16.9891,lon:82.2475,wxRefreshMs:600000,satelliteEndpoint:""};
const LIVE=true; // Telemetry comes only from the gateway API; no synthetic sensor mode.
const names={"RAIN-01":"Rain node","DRAIN-01":"Drainage node","AREA-01":"Low-lying area","RIVER-01":"River node","PI-EDGE":"Pi gateway","ALERT-01":"Speaker node"};
const base={
 "RAIN-01":{kind:"rain",elevation:14,rain:18.4,temp:28.1,humidity:82,battery:96,rssi:-61,loss:1.2},
 "DRAIN-01":{kind:"drain",elevation:11,level:8.1,flow:2.4,blockage:12,temp:29.2,battery:91,rssi:-66,loss:2},
 "AREA-01":{kind:"land",elevation:7,depth:1.8,soil:62,overflow:false,temp:30,battery:88,rssi:-72,loss:3.4},
 "RIVER-01":{kind:"river",elevation:4,level:12.4,rise:.3,turbidity:18,temp:27.8,battery:94,rssi:-58,loss:.8},
 "PI-EDGE":{kind:"edge",elevation:0,temp:42.3,battery:100,rssi:0,loss:0},
 "ALERT-01":{kind:"alert",elevation:0,temp:31.2,battery:100,rssi:-63,loss:1}
};
const baseLive=JSON.parse(JSON.stringify(base));Object.entries(baseLive).forEach(([id,n])=>{["rain","level","flow","depth","soil","rise","turbidity","blockage","humidity","temp","battery","rssi","loss"].forEach(k=>{if(k in n)n[k]=0});n.ok=false;n.inst=!(id==="DRAIN-01"||id==="RIVER-01");n.hw=null});
if(LIVE){names["RAIN-01"]="Rain node · FLOOD01";names["AREA-01"]="Soil + gas node · GAS01";names["DRAIN-01"]="Drainage node · not installed";names["RIVER-01"]="River node · not installed";names["PI-EDGE"]="Pi gateway";names["ALERT-01"]="Buzzer alarm"}
let state={nodes:JSON.parse(JSON.stringify(LIVE?baseLive:base)),selected:LIVE?"RAIN-01":"DRAIN-01",rain:LIVE?0:18,blockage:LIVE?0:12,river:LIVE?0:12.5,overflow:false,fault:false,nodeFailure:false,internetLoss:false,risk:LIVE?0:18,history:[],events:[],storm:false,view:"iso",lastRisk:LIVE?0:18,snap:null,snapAt:0,gasAlert:false,rainAlert:false};
const KA=LIVE?"soil":"river",KB=LIVE?"rain":"drain",MA=LIVE?100:35,MB=LIVE?100:25,LA=LIVE?"Soil":"River",LB=LIVE?"Rain":"Drain",UA=LIVE?"%":"cm",UB=LIVE?"mm/h":"cm",RS=LIVE?60000/CONFIG.tickMs:1;
function rnd(n){return (Math.random()-.5)*n}
function clamp(n,a,b){return Math.max(a,Math.min(b,n))}
function stamp(){return new Date().toLocaleTimeString("en-IN",{hour12:false})}
function log(title,detail,severity="info"){if(LIVE)return;state.events.unshift({title,detail,severity,time:stamp()});state.events=state.events.slice(0,12);renderAlerts()}
function toast(title,detail="Simulation state recalculated."){$("toast").querySelector("b").textContent=title;$("toast").querySelector("small").textContent=detail;$("toast").classList.add("show");clearTimeout(toast.timer);toast.timer=setTimeout(()=>$("toast").classList.remove("show"),2400)}
function derive(){
 if(LIVE)return deriveLive();
 const n=state.nodes;
 n["RAIN-01"].rain=clamp(state.rain+rnd(.9),0,120);
 n["DRAIN-01"].blockage=state.blockage;
 const runoff=state.rain*.075;
 n["DRAIN-01"].level=clamp(3+runoff+state.blockage*.095+rnd(.45),0,35);
 n["DRAIN-01"].flow=clamp(5.8*(1-state.blockage/115)+runoff*.08+rnd(.15),0,8);
 n["AREA-01"].depth=clamp(.2+state.rain*.045+state.blockage*.035+(state.overflow?9:0)+Math.max(0,state.river-25)*.12+rnd(.25),0,40);
 n["AREA-01"].soil=clamp(34+state.rain*.52+rnd(2),10,100);n["AREA-01"].overflow=state.overflow||n["AREA-01"].depth>7;
 n["RIVER-01"].level=clamp(state.river+state.rain*.018+rnd(.18),0,55);
 n["RIVER-01"].rise=clamp(.05+state.rain*.009+state.blockage*.002+rnd(.08),0,2.5);
 Object.values(n).forEach(x=>{if(x.kind!=="edge"&&x.kind!=="alert"){x.temp=clamp(x.temp+rnd(.16),20,48);x.battery=clamp(x.battery-rnd(.025),60,100);x.rssi=clamp(x.rssi+rnd(2),-96,-40);x.loss=clamp(x.loss+rnd(.2),0,25)}});
 if(state.fault)n["DRAIN-01"].level=1.2;
 const r=clamp(Math.round(state.rain*.24+state.blockage*.31+n["AREA-01"].depth*2.6+Math.max(0,n["RIVER-01"].level-18)*1.2+(state.overflow?23:0)+(state.fault?9:0)),0,100);
 state.lastRisk=state.risk;state.risk=r;
 state.history.push({river:n["RIVER-01"].level,drain:n["DRAIN-01"].level,rain:state.rain,risk:r});if(state.history.length>31)state.history.shift();infer();
}
function riskBand(){return state.risk>=75?"CRITICAL":state.risk>=50?"HIGH":state.risk>=28?"WATCH":"LOW"}
function online(id){if(LIVE)return !!state.nodes[id].ok;return !(state.nodeFailure&&id==="DRAIN-01")}
function svgEl(tag,attrs={},text){let e=document.createElementNS("http://www.w3.org/2000/svg",tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text!==undefined)e.textContent=text;return e}
const IW=1537,IH=1023,cv=$("overlay"),cx=cv.getContext("2d");
let sc=1,last=0,drops=[],hits=[];
const POS={"RAIN-01":{x:1372,y:292,c:"#d99a37",n:"RAIN",l:"up"},"DRAIN-01":{x:688,y:612,c:"#bd6845",n:"DRAIN",l:"up"},"AREA-01":{x:1222,y:548,c:"#70945d",n:"LOW AREA",l:"down"},"RIVER-01":{x:300,y:330,c:"#438c7b",n:"RIVER",l:"down"},"PI-EDGE":{x:1372,y:548,c:"#647a5b",n:"PI EDGE",l:"down"},"ALERT-01":{x:166,y:398,c:"#c9a24a",n:"SPEAKER",l:"down"}};
const RIVER=[[420,150],[385,215],[330,290],[270,370],[215,450],[180,540]],PIPE=[[480,568],[545,572],[830,632],[1040,688],[1190,735],[1228,770],[1228,830]],CHAIN=[["RAIN-01","DRAIN-01"],["DRAIN-01","AREA-01"],["AREA-01","RIVER-01"]];
function fit(){const w=$("twinStage").clientWidth,h=$("twinStage").clientHeight;if(!w||!h)return;const k=Math.min(w/IW,h/IH),d=window.devicePixelRatio||1;$("frame").style.width=IW*k+"px";$("frame").style.height=IH*k+"px";cv.width=Math.round(IW*k*d);cv.height=Math.round(IH*k*d);sc=k*d;if(typeof draw==="function")draw(performance.now())}
new ResizeObserver(fit).observe($("twinStage"));
function val(id){if(LIVE)return valLive(id);const n=state.nodes[id];if(id==="DRAIN-01"&&state.nodeFailure)return"OFFLINE";return id==="RAIN-01"?n.rain.toFixed(0)+" mm/h":id==="DRAIN-01"?n.level.toFixed(1)+" cm":id==="AREA-01"?n.depth.toFixed(1)+" cm":id==="RIVER-01"?n.level.toFixed(1)+" cm":id==="PI-EDGE"?(state.internetLoss?"LOCAL ONLY":"EDGE OK"):"RISK "+state.risk}
function rr(x,y,w,h,r){cx.beginPath();cx.moveTo(x+r,y);cx.arcTo(x+w,y,x+w,y+h,r);cx.arcTo(x+w,y+h,x,y+h,r);cx.arcTo(x,y+h,x,y,r);cx.arcTo(x,y,x+w,y,r);cx.closePath()}
function flow(p,col,sp,t,w){cx.save();cx.lineCap="round";cx.lineJoin="round";cx.strokeStyle=col;cx.lineWidth=w;cx.shadowColor=col;cx.shadowBlur=14;cx.setLineDash([1,w*4.5]);cx.lineDashOffset=-t*sp;cx.beginPath();p.forEach((q,i)=>i?cx.lineTo(q[0],q[1]):cx.moveTo(q[0],q[1]));cx.stroke();cx.restore()}
function frame(now){requestAnimationFrame(frame);draw(now)}
function draw(now){const dt=Math.min(.05,(now-last)/1000||.016),t=now/1000;last=now;
 cx.setTransform(sc,0,0,sc,0,0);cx.clearRect(0,0,IW,IH);const kk=sc/(window.devicePixelRatio||1),mini=kk<.4,B=mini?Math.max(1,.45/kk):1;
 const N=state.nodes,rainI=N["RAIN-01"].rain/120;
 // rain streaks scale with rainfall
 const want=Math.round(rainI*170);while(drops.length<want)drops.push({x:Math.random()*IW,y:Math.random()*560,v:700+Math.random()*500});drops.length=Math.min(drops.length,want);
 cx.strokeStyle="rgba(214,238,255,.55)";cx.lineWidth=2*B;cx.beginPath();drops.forEach(d=>{d.y+=d.v*dt;d.x+=d.v*.22*dt;if(d.y>560){d.y=-20;d.x=Math.random()*IW}cx.moveTo(d.x,d.y);cx.lineTo(d.x+4,d.y+16)});cx.stroke();
 // pond inundation
 const dp=N["AREA-01"].depth;if(dp>1.5){cx.fillStyle="rgba(60,130,112,"+Math.min(.55,dp/30)+")";cx.beginPath();cx.ellipse(1215,540,105,46,.08,0,7);cx.fill();if(dp>4){const k=(t*.6)%1;cx.strokeStyle="rgba(255,255,255,"+.7*(1-k)+")";cx.lineWidth=3;cx.beginPath();cx.ellipse(1215,540,40+k*70,16+k*28,.08,0,7);cx.stroke()}}
 // water flow dots
 flow(RIVER,"#d6f1ff",28+N["RIVER-01"].rise*40,t,6*B);
 flow(PIPE,state.blockage>55?"#ffb36b":"#e3f6ff",14+N["DRAIN-01"].flow*12,t,6*B);
 if(state.blockage>55){cx.strokeStyle="rgba(189,100,61,.85)";cx.lineWidth=4;cx.setLineDash([8,6]);cx.beginPath();cx.ellipse(688,605,62,48,0,0,7);cx.stroke();cx.setLineDash([])}
 // mesh chain: elevation hand-off Rain > Drain > Land > River
 CHAIN.forEach(([a,b],i)=>{const A=POS[a],B=POS[b],cxp=(A.x+B.x)/2,cyp=Math.min(A.y,B.y)-(i===2?210:110),broken=LIVE?(!online(a)||!online(b)):(state.nodeFailure&&(a==="DRAIN-01"||b==="DRAIN-01"));
  cx.save();const ghost=LIVE&&(state.nodes[a].inst===false||state.nodes[b].inst===false);cx.strokeStyle=ghost?"rgba(255,255,255,.2)":broken?"rgba(192,90,70,.8)":"rgba(255,255,255,.55)";cx.lineWidth=2.5*B;cx.setLineDash([9*B,10*B]);cx.lineDashOffset=-t*18;cx.beginPath();cx.moveTo(A.x,A.y);cx.quadraticCurveTo(cxp,cyp,B.x,B.y);cx.stroke();cx.restore();
  if(!broken&&!ghost){const s=(t*.25+i*.33)%1,u=1-s,px=u*u*A.x+2*u*s*cxp+s*s*B.x,py=u*u*A.y+2*u*s*cyp+s*s*B.y;cx.fillStyle="#f0c779";cx.shadowColor="#f0c779";cx.shadowBlur=14;cx.beginPath();cx.arc(px,py,6*B,0,7);cx.fill();cx.shadowBlur=0}});
 // nodes
 hits=[];
 for(const id in POS){const P=POS[id],sel=state.selected===id,bad=LIVE?(state.nodes[id].inst!==false&&!online(id)):(state.nodeFailure&&id==="DRAIN-01"),col=bad?"#c05a46":(LIVE&&state.nodes[id].inst===false)?"#8d9488":P.c,pr=1+.14*Math.sin(t*3+P.x);
  cx.fillStyle=col+"40";cx.beginPath();cx.arc(P.x,P.y,(sel?40:29)*pr*B,0,7);cx.fill();
  cx.fillStyle=col;cx.strokeStyle="#fffefa";cx.lineWidth=4*B;cx.beginPath();cx.arc(P.x,P.y,(sel?19:14)*B,0,7);cx.fill();cx.stroke();
  cx.fillStyle="#fffefa";cx.beginPath();cx.arc(P.x,P.y,4.5*B,0,7);cx.fill();
  if(sel){cx.save();cx.strokeStyle=col;cx.lineWidth=3;cx.setLineDash([10,8]);cx.lineDashOffset=-t*22;cx.beginPath();cx.arc(P.x,P.y,50*B,0,7);cx.stroke();cx.restore()}
  if(state.risk>50&&(id==="AREA-01"||id==="DRAIN-01")){cx.save();cx.strokeStyle="#c05a46";cx.lineWidth=3;cx.setLineDash([5,6]);cx.beginPath();cx.arc(P.x,P.y,36*B,0,7);cx.stroke();cx.restore()}
  if(!mini){const v=val(id);cx.textAlign="center";cx.font="700 19px 'DM Sans',sans-serif";const w1=cx.measureText(P.n).width;cx.font="500 18px 'DM Mono',monospace";const w2=cx.measureText(v).width;
  const w=Math.max(w1,w2)+28,h=56,x=P.x-w/2,y=P.l==="up"?P.y-h-34:P.y+34;
  cx.fillStyle="rgba(32,45,36,.93)";rr(x,y,w,h,10);cx.fill();cx.fillStyle=col;cx.fillRect(x+10,y,w-20,4);
  cx.fillStyle="#e9eee5";cx.font="700 19px 'DM Sans',sans-serif";cx.fillText(P.n,P.x,y+26);cx.fillStyle=bad?"#f2a08f":"#f0c779";cx.font="500 18px 'DM Mono',monospace";cx.fillText(v,P.x,y+47)}
  hits.push([id,P.x,P.y])}
}
cv.addEventListener("click",e=>{const r=cv.getBoundingClientRect(),k=IW/r.width,x=(e.clientX-r.left)*k,y=(e.clientY-r.top)*k,h=hits.find(q=>Math.hypot(q[1]-x,q[2]-y)<50);if(cur!==1)go(1);else if(h){selectNode(h[0]);go(2)}});
cv.addEventListener("mousemove",e=>{const r=cv.getBoundingClientRect(),k=IW/r.width,x=(e.clientX-r.left)*k,y=(e.clientY-r.top)*k;cv.style.cursor=cur!==1||hits.some(q=>Math.hypot(q[1]-x,q[2]-y)<50)?"pointer":"default"});
function renderTwin(){
 if(LIVE)return renderTwinLive();
 $("mapAlertText").textContent=state.risk>=75?"Critical inundation risk · act now":state.risk>=50?"Drain capacity stressed · monitor land node":state.risk>=28?"Water accumulation increasing":"No active hazard detected";
 $("mapAlertDot").className=state.risk>=75?"crit":state.risk>=28?"warn":"";
}
function selectNode(id){state.selected=id;renderInspector();renderTwin();$("nodeTabs").querySelectorAll("button").forEach(b=>b.classList.toggle("selected",b.dataset.node===id))}
function renderInspector(){
 if(LIVE)return renderInspectorLive();
 const id=state.selected,n=state.nodes[id];$("inspectorTitle").textContent=names[id];$("inspectorStatus").textContent=online(id)?"● ONLINE":"● DEGRADED";$("inspectorStatus").className="status-tag "+(online(id)?"online":"offline");
 let primary,label,unit,glyph,rows=[];
 if(n.kind==="rain"){primary=n.rain.toFixed(1);label="RAINFALL INTENSITY";unit="mm/h";glyph="⌁";rows=[["Air temperature",n.temp.toFixed(1)+" °C"],["Relative humidity",n.humidity+" %"],["Elevation",n.elevation+" m"],["Battery",n.battery.toFixed(0)+" %"],["Link RSSI",n.rssi.toFixed(0)+" dBm"],["Packet loss",n.loss.toFixed(1)+" %"]]}
 if(n.kind==="drain"){primary=n.level.toFixed(1);label="WATER LEVEL";unit="cm";glyph="≈";rows=[["Flow rate",n.flow.toFixed(1)+" L/min"],["Blockage estimate",n.blockage.toFixed(0)+" %"],["Elevation",n.elevation+" m"],["Battery",n.battery.toFixed(0)+" %"],["Link RSSI",n.rssi.toFixed(0)+" dBm"],["Packet loss",n.loss.toFixed(1)+" %"]]}
 if(n.kind==="land"){primary=n.depth.toFixed(1);label="SURFACE WATER DEPTH";unit="cm";glyph="⌁";rows=[["Soil moisture",n.soil.toFixed(0)+" %"],["Overflow trigger",n.overflow?"ACTIVE":"CLEAR"],["Elevation",n.elevation+" m"],["Battery",n.battery.toFixed(0)+" %"],["Link RSSI",n.rssi.toFixed(0)+" dBm"],["Packet loss",n.loss.toFixed(1)+" %"]]}
 if(n.kind==="river"){primary=n.level.toFixed(1);label="RIVER WATER LEVEL";unit="cm";glyph="≈";rows=[["Rate of rise",n.rise.toFixed(2)+" cm/min"],["Turbidity",n.turbidity+" NTU"],["Elevation",n.elevation+" m"],["Battery",n.battery.toFixed(0)+" %"],["Link RSSI",n.rssi.toFixed(0)+" dBm"],["Packet loss",n.loss.toFixed(1)+" %"]]}
 if(n.kind==="edge"){primary=n.temp.toFixed(1);label="PROCESSOR TEMPERATURE";unit="°C";glyph="⌘";rows=[["Role","Gateway + edge AI"],["Local inference","ACTIVE"],["Internet",state.internetLoss?"DISCONNECTED":"AVAILABLE"],["Packet buffer",state.internetLoss?"12 packets":"0 packets"],["Uptime","04h 32m"],["Storage","32% used"]]}
 if(n.kind==="alert"){primary=state.risk;label="ALERT RISK SCORE";unit="/100";glyph="!";rows=[["Alert level",riskBand()],["Output",state.risk>=75?"Continuous alarm":state.risk>=50?"Repeated warning tone":"Standby"],["Temperature",n.temp.toFixed(1)+" °C"],["Battery",n.battery+" %"],["Speaker","READY"],["Internet",state.internetLoss?"OFFLINE":"AVAILABLE"]]}
 $("primaryLabel").textContent=label;$("primaryValue").textContent=primary;$("primaryUnit").textContent=unit;$("primaryGlyph").textContent=glyph;$("nodeTemp").textContent=n.temp.toFixed(1)+"°C";$("tempState").textContent=n.temp>40?"● ELEVATED":"● NOMINAL";$("tempState").style.color=n.temp>40?"#b55b3d":"#5e8a5c";
 $("sensorList").innerHTML=rows.map(([a,b])=>`<div class="sensor-row"><span>${a}</span><b>${b}</b></div>`).join("");
}
function renderRoute(){if(LIVE)return renderRouteLive();let ids=["RAIN-01","DRAIN-01","AREA-01","RIVER-01"];let symbols=["☂","⌁","⌂","≈"];let html="";ids.forEach((id,i)=>{html+=`<div class="route-node ${online(id)?"":"failed"}"><i>${online(id)?symbols[i]:"×"}</i><span>${id.split("-")[0]}</span></div>`;if(i<ids.length-1)html+=`<div class="route-link ${state.nodeFailure&&i===0?"broken":""}"></div>`});$("routeDiagram").innerHTML=html;$("routeCount").textContent=state.nodeFailure?"REROUTED":"3 HOPS";$("routeMessage").textContent=state.nodeFailure?"Alternate route active":"Auto-route enabled"}
function renderEvents(){$("eventList").innerHTML=state.events.slice(0,9).map(e=>`<div class="ev ${e.severity}"><time>${e.time}</time><div><b>${e.title}</b><small>${e.detail}</small></div></div>`).join("")}
function renderAlerts(){renderEvents();if(LIVE)return renderAlertsLive();let alerts=[];if(state.risk>=75)alerts.push(["CRITICAL","Surface flooding likely","Water accumulation is high. Trigger the local alert endpoint.","critical"]);else if(state.risk>=50)alerts.push(["WARNING","Drainage capacity stressed","High runoff or blockage may overwhelm the drain.","warn"]);else if(state.risk>=28)alerts.push(["WATCH","Flood risk rising","Monitor land depth and river rate-of-rise.","warn"]);if(state.overflow)alerts.push(["LAND NODE","Surface overflow detected","Water-contact trigger is active in AREA-01.","critical"]);if(state.fault)alerts.push(["SENSOR FAULT","Drain level disagreement","Injected reading conflicts with expected flow and rainfall.","warn"]);if(state.nodeFailure)alerts.push(["MESH DEGRADED","Drain node unavailable","Route recovery simulated; local edge processing remains active.","warn"]);if(state.internetLoss)alerts.push(["OFFLINE MODE","Internet link unavailable","Gateway buffers data and continues local risk estimates.","info"]);if(!alerts.length)alerts.push(["SYSTEM NORMAL","No active flood alert","All simulated measurements remain within the watch threshold.","info"]);$("alertCount").textContent=alerts.length+" ACTIVE";$("alertList").innerHTML=alerts.slice(0,4).map(a=>`<div class="alert-item"><span class="alert-symbol ${a[3]}">${a[3]==="critical"?"!":a[3]==="warn"?"△":"✓"}</span><div class="alert-copy"><b>${a[1]}</b><p>${a[2]}</p></div><span class="alert-time">${stamp()}</span></div>`).join("")}
function chartPath(key,max){let hist=state.history;if(hist.length<2)return "";return hist.map((v,i)=>`${i?"L":"M"}${20+i*(700/(Math.max(1,hist.length-1)))} ${155-(v[key]/max)*130}`).join(" ")}
function renderChart(){let s=$("trendChart");s.innerHTML="";for(let i=0;i<5;i++){let y=15+i*35;s.append(svgEl("line",{x1:20,y1:y,x2:720,y2:y,stroke:"#ebe9df","stroke-width":1,"stroke-dasharray":"3 4"}));s.append(svgEl("text",{x:0,y:y+3,fill:"#a2a596","font-size":8,"font-family":"DM Mono"},String(LIVE?100-i*25:30-i*7.5)))}let p1=chartPath(KA,MA),p2=chartPath(KB,MB);s.append(svgEl("path",{d:p1,fill:"none",stroke:"#438c7b","stroke-width":2.5,"stroke-linejoin":"round","stroke-linecap":"round"}));s.append(svgEl("path",{d:p2,fill:"none",stroke:"#d38a43","stroke-width":2,"stroke-linejoin":"round","stroke-linecap":"round"}));if(state.history.length){let h=state.history[state.history.length-1];s.append(svgEl("circle",{cx:720,cy:155-h[KA]/MA*130,r:4,fill:"#438c7b",stroke:"#fff","stroke-width":2}))}}
function spark(id,key){let vals=state.history.slice(-16).map(v=>v[key]);if(vals.length<2)vals=[1,2,3,4,5];let mn=Math.min(...vals),mx=Math.max(...vals)+.01;let pts=vals.map((v,i)=>`${i/(vals.length-1)*130},${17-(v-mn)/(mx-mn)*14}`).join(" ");$(id).innerHTML=`<svg viewBox="0 0 130 20" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${key==="river"||key==="soil"?"#438c7b":"#d38a43"}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`}
function render(){
 $("riskValue").textContent=state.risk;$("riskMeter").style.width=state.risk+"%";$("riskBadge").textContent=riskBand();$("riskBadge").style.background=state.risk>=75?"#f6ddd5":state.risk>=50?"#fae8cc":state.risk>=28?"#f7edcf":"#e0ead8";$("riskBadge").style.color=state.risk>=75?"#ad4e3b":state.risk>=50?"#a66b21":"#49734a";$("riskNote").textContent=state.risk>=75?"Critical: immediate response":state.risk>=50?"Drain capacity under pressure":state.risk>=28?"Conditions require attention":"Conditions stable";$("riskTrend").textContent=(state.risk>state.lastRisk?"↗ +":"↘ ")+Math.abs(state.risk-state.lastRisk)+" pts";
 let onlineCount=Object.keys(state.nodes).filter(online).length;$("networkValue").textContent=Math.round(onlineCount/6*100);$("networkMeter").style.width=(onlineCount/6*100)+"%";$("onlineCount").textContent=onlineCount+" of 6 endpoints online";$("rainValue").textContent=state.nodes["RAIN-01"].rain.toFixed(1);$("riverValue").textContent=state.nodes["RIVER-01"].level.toFixed(1);$("rainTrend").textContent=state.rain>65?"▲ Heavy rainfall":"● Simulated";$("riverTrend").textContent="↗ +"+state.nodes["RIVER-01"].rise.toFixed(2)+" cm/min";
 $("modeLabel").textContent=state.internetLoss?"LOCAL EDGE MODE":"NO SENSOR DATA";$("connectionLabel").textContent=state.internetLoss?"EDGE MODE ACTIVE":"WAITING FOR HARDWARE";$("connectionDetail").textContent=state.internetLoss?"Internet unavailable · buffered":"No sensor packets received";$("hardwareStatus").textContent="NOT CONNECTED";$("footerTime").textContent=state.internetLoss?"EDGE BUFFERING":"WAITING FOR DATA";
 renderTwin();renderInspector();renderRoute();renderAlerts();renderChart();renderInfer();renderStats();renderSky();spark("rainSpark",KB);spark("riverSpark",KA);
 if(LIVE)renderLive();
}
function setToggle(id,on){$(id).classList.toggle("on",on)}
function applyControls(){if(LIVE)return;state.rain=+$("rainControl").value;state.blockage=+$("blockControl").value;state.river=+$("riverControl").value;$("rainControlVal").textContent=state.rain+" mm/h";$("blockControlVal").textContent=state.blockage+"%";$("riverControlVal").textContent=state.river.toFixed(1)+" cm";derive();render()}
["rainControl","blockControl","riverControl"].forEach(id=>$(id).addEventListener("input",()=>{applyControls()}));
function toggle(key,id,label){if(LIVE)return;state[key]=!state[key];setToggle(id,state[key]);derive();render();log(label,state[key]?"Scenario enabled":"Scenario cleared",state[key]?"warn":"info");toast(label,state[key]?"Enabled":"Disabled")}
$("overflowToggle").onclick=()=>toggle("overflow","overflowToggle","Land overflow");
$("sensorFaultToggle").onclick=()=>toggle("fault","sensorFaultToggle","Sensor fault");
$("nodeFailureToggle").onclick=()=>toggle("nodeFailure","nodeFailureToggle","Node failure");
$("internetToggle").onclick=()=>toggle("internetLoss","internetToggle","Internet loss");
$("nodeTabs").querySelectorAll("button").forEach(b=>b.onclick=()=>selectNode(b.dataset.node));
$("resetBtn").onclick=()=>{if(LIVE){state.history=[];toast("Trend cleared","Live readings continue.");render();return}state= {nodes:JSON.parse(JSON.stringify(base)),selected:"DRAIN-01",rain:18,blockage:12,river:12.5,overflow:false,fault:false,nodeFailure:false,internetLoss:false,risk:18,history:[],events:[],storm:false,view:state.view,lastRisk:18};$("rainControl").value=18;$("blockControl").value=12;$("riverControl").value=12.5;["overflowToggle","sensorFaultToggle","nodeFailureToggle","internetToggle"].forEach(id=>setToggle(id,false));for(let i=0;i<20;i++){derive()}render();log("Scenario reset","Baseline environment restored");toast("Baseline restored","All scenario inputs returned to default.")};
$("runStormBtn").onclick=()=>{if(LIVE||state.storm)return;state.storm=true;$("runStormBtn").classList.add("running");$("runStormBtn").textContent="◌ Storm sequence running";log("Storm sequence started","Rainfall ramps up over 30 seconds.","warn");let start=Date.now(),timer=setInterval(()=>{let t=(Date.now()-start)/30000;if(t>=1){clearInterval(timer);state.storm=false;$("runStormBtn").classList.remove("running");$("runStormBtn").textContent="▶ Run 30-sec storm sequence";state.rain=100;state.blockage=Math.max(state.blockage,45);$("rainControl").value=state.rain;$("blockControl").value=state.blockage;applyControls();log("Storm sequence complete","Peak rainfall scenario reached.","critical");return}state.rain=18+82*t;state.blockage=12+33*t;$("rainControl").value=state.rain;$("blockControl").value=state.blockage;applyControls()},900)};
$("exportBtn").onclick=()=>{if(LIVE)return exportLive();let rows=[["timestamp","node_id","type","elevation_m","primary_reading","temperature_c","battery_pct","rssi_dbm","packet_loss_pct"]];Object.entries(state.nodes).forEach(([id,n])=>rows.push([new Date().toISOString(),id,n.kind,n.elevation,n.rain??n.level??n.depth??n.temp,n.temp,n.battery,n.rssi,n.loss]));let csv=rows.map(r=>r.map(x=>`"${String(x??"").replaceAll('"','""')}"`).join(",")).join("\n");let a=document.createElement("a");a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));a.download="aquamesh-telemetry.csv";a.click();URL.revokeObjectURL(a.href);toast("Telemetry exported","CSV saved with current synthetic readings.")};
const VIEWS=[["Flood monitoring overview","Live gateway state, field-node telemetry and system health"],["Live environment","Elevation-aware flow across the miniature district"],["Sensor network","Every node, its readings and the mesh route"],["Scenario lab","Change inputs and watch the twin respond"],["Hydrological trend","River and drain levels over the last minute"],["Alerts and events","What the system is telling operators"],["Inference engine","Calculated indicators, each with a number and a threshold"],["Sky and archive","Weather, satellite-derived signals and recorded storm patterns"],["Reasoning engine","Forward chaining over evidence, backward-chaining proof for every alert, counterfactual replay"]];
let cur=0;
if(LIVE){VIEWS[3]=["Live inputs","Real sensor values from the ESP32 nodes driving the twin"];VIEWS[4]=["Sensor trend","Soil moisture and rainfall over the last 3 minutes"];document.querySelectorAll(".key span")[3].textContent="Inputs"}
function go(i){i=(i+VIEWS.length)%VIEWS.length;cur=i;$("deck").classList.toggle("full",i===1);document.querySelectorAll(".view").forEach((v,k)=>{v.classList.toggle("active",k===i);v.classList.toggle("before",k<i)});document.querySelectorAll(".key").forEach((b,k)=>b.classList.toggle("active",k===i));document.querySelectorAll("#pager i").forEach((d,k)=>d.classList.toggle("on",k===i));$("viewTitle").textContent=VIEWS[i][0];$("viewSub").textContent=VIEWS[i][1];if(i===1)setTimeout(fit,0)}
document.querySelectorAll(".key").forEach((b,k)=>b.onclick=()=>go(k));
addEventListener("keydown",e=>{if(e.ctrlKey||e.metaKey||e.altKey)return;const inp=e.target.tagName==="INPUT",m=/^(?:Numpad|Digit)([1-9])$/.exec(e.code);
 if(m){go(+m[1]-1);e.preventDefault()}
 else if(e.code==="NumpadAdd"||(e.code==="ArrowRight"&&!inp)){go(cur+1);e.preventDefault()}
 else if(e.code==="NumpadSubtract"||(e.code==="ArrowLeft"&&!inp)){go(cur-1);e.preventDefault()}
 else if(e.code==="Numpad0"||e.code==="Digit0")$("resetBtn").click();
 else if(e.code==="NumpadEnter")$("runStormBtn").click();
 else if(e.code==="NumpadDecimal")$("exportBtn").click()});

/* ---------- weather, satellite-derived signals and recorded archive ---------- */
const ARCH=window.AQUA_ARCHIVE||{hourly:[],events:[],cur:{}},EV=ARCH.events||[];
let wx={mode:"archive",series:[],now:0,cur:{},fc3:0,cloud:0,cape:0,soil:0,at:null,sat:null};
const avg=a=>a.length?a.reduce((x,y)=>x+y,0)/a.length:0;
function wxDerive(){const s=wx.series[wx.now]||{};wx.cloud=s.cloud||0;wx.cape=s.cape||0;wx.soil=s.soil||0}
function useArchive(){wx.mode="archive";wx.series=ARCH.hourly.map(r=>({rain:r[0],cloud:r[1],cape:r[2],soil:r[3]}));wx.now=wx.series.length-1;wx.cur=ARCH.cur||{};const y=wx.series.length-24;wx.fc3=avg(wx.series.slice(y+1,y+4).map(s=>s.rain));wxDerive()}
async function fetchWx(){
 wx.tried=true;const u="https://api.open-meteo.com/v1/forecast?latitude="+CONFIG.lat+"&longitude="+CONFIG.lon+"&current=temperature_2m,relative_humidity_2m,precipitation,cloud_cover,pressure_msl,wind_speed_10m&hourly=precipitation,cloud_cover,cape,soil_moisture_0_to_1cm&past_days=3&forecast_days=2&timezone=auto";
 try{const ac=new AbortController();setTimeout(()=>ac.abort(),7000);const r=await fetch(u,{signal:ac.signal});if(!r.ok)throw Error(r.status);const d=await r.json(),h=d.hourly,c=d.current;
  const S=h.time.map((t,i)=>({rain:h.precipitation[i]||0,cloud:h.cloud_cover[i]||0,cape:h.cape[i]||0,soil:Math.min(100,(h.soil_moisture_0_to_1cm[i]||0)/.45*100)}));
  let k=h.time.findIndex(t=>t.slice(0,13)===c.time.slice(0,13));if(k<0)k=Math.min(71,S.length-1);
  wx.mode="live";wx.series=S;wx.now=k;wx.at=Date.now();wx.cur={temp:c.temperature_2m,hum:c.relative_humidity_2m,rain:c.precipitation,cloud:c.cloud_cover,pres:c.pressure_msl,wind:c.wind_speed_10m};wx.fc3=avg(S.slice(k+1,k+4).map(s=>s.rain));wxDerive()}
 catch(e){if(wx.mode!=="live")LIVE?useOffline():useArchive()}
 if(CONFIG.satelliteEndpoint){try{wx.sat=await(await fetch(CONFIG.satelliteEndpoint)).json()}catch(e){wx.sat=null}}
 $("wxLabel").textContent=wx.mode==="live"?"WX · LIVE":wx.mode==="offline"?"WX · OFFLINE":"WX · ARCHIVE";$("wxPill").classList.toggle("off",wx.mode!=="live");
 infer();renderInfer();renderSky(true)}
function useOffline(){wx.mode="offline";wx.series=[];wx.now=0;wx.cur={};wx.fc3=0;wxDerive()}
LIVE?useOffline():useArchive();
function rainClass(r){return r<.5?"Dry":r<2.5?"Light":r<7.5?"Moderate":r<35?"Heavy":r<65?"Very heavy":"Extreme"}
/* ---------- inference: every output is a number with a unit ---------- */
function infer(){
 const N=state.nodes,I=state.inf=state.inf||{pr:state.rain,pk:state.risk,tr:0,tk:0},bl=state.blockage,CAP=12;
 I.tr=I.tr*.7+(state.rain-I.pr)*.3;I.pr=state.rain;I.tk=I.tk*.7+(state.risk-I.pk)*.3;I.pk=state.risk;
 I.crit=(CAP-3-bl*.095)/.075;I.head=LIVE?NaN:I.crit-state.rain;I.util=N["DRAIN-01"].level/CAP*100;
 I.pr30=clamp(state.rain+I.tr*15+(wx.fc3-state.rain)*.25,0,120);I.lvl30=3+I.pr30*.075+bl*.095;
 I.soil=LIVE&&wx.mode!=="live"?N["AREA-01"].soil:clamp(N["AREA-01"].soil*.6+wx.soil*.4,0,100);I.C=.35+.5*I.soil/100;I.Q=I.C*state.rain*2.22;
 I.eta=I.tr>.02&&I.head>0?I.head/I.tr:null;
 const z=.055*state.risk+.03*I.pr30+.012*I.soil+.0012*wx.cape-4.8;I.p30=100/(1+Math.exp(-z));
 I.trust={};Object.keys(N).forEach(id=>{const n=N[id];let t=100-n.loss*2.5-Math.max(0,-n.rssi-60)*.7-(100-n.battery)*.8;if(id==="DRAIN-01"){if(state.nodeFailure)t=0;else if(state.fault)t-=45}I.trust[id]=clamp(t,0,100)});
 if(LIVE)liveTrust(I);I.mt=avg(Object.values(I.trust).filter(v=>v!=null));
 I.conf=clamp(99-avg((LIVE?["RAIN-01","AREA-01"]:["RAIN-01","DRAIN-01","AREA-01","RIVER-01"]).map(i=>N[i].loss))*2.2-(state.fault?24:0)-(state.nodeFailure?16:0)-(wx.mode!=="live"?6:0),30,99);
 I.ewi=.6*state.risk+.4*I.p30;I.lead=I.tk>.05&&state.risk<50?(50-state.risk)/I.tk:null;
 I.sims=EV.map(e=>{let best=1e9,bk=0;e.p.forEach((r,k)=>{const s=k?r-e.p[k-1]:0,d=Math.abs(state.rain-r)/30+Math.abs(5*I.tr-s)/20;if(d<best){best=d;bk=k}});return{e,sim:100*Math.exp(-best),eta:Math.max(0,(e.p.indexOf(Math.max(...e.p))-bk)*5)}});
 I.best=I.sims.length?I.sims.reduce((a,b)=>b.sim>a.sim?b:a):null}
const NA=l=>m(l,"n/a","",0,"ok");
const hi=(v,w,b)=>v>=b?"bad":v>=w?"warn":"ok",lo=(v,w,b)=>v<=b?"bad":v<=w?"warn":"ok";
function m(l,v,u,p,s){return `<div class="m ${s||"ok"}"><span>${l}</span><b>${v}<small>${u}</small></b><i style="--p:${clamp(p,0,100)}%"></i></div>`}
function sec(t,tag,rows){return `<article class="card cut"><div class="c-head"><h2>${t}</h2><span class="tiny">${tag}</span></div><div class="ms">${rows.join("")}</div></article>`}
function renderInfer(){
 const I=state.inf;if(!I)return;if(LIVE&&window.AQR&&state.snap&&state.snap.assessment)return AQR.infer(state.snap,I);const f=(v,d=0)=>v.toFixed(d),c=wx.cur||{},set=(id,t)=>{$(id).textContent=t};
 set("subRisk","P(flood, 30 min) "+f(I.p30)+"% · confidence "+f(I.conf)+"%");set("subNet","mean trust "+f(I.mt)+"/100 · lead "+(I.lead?f(I.lead)+" min":"none"));
 set("subRain",rainClass(state.rain)+" · C "+f(I.C,2)+" · Q "+f(I.Q)+" L/s");set("subRiver","soil "+f(I.soil)+"% · next 3 h "+f(wx.fc3*3,1)+" mm");
 $("infGrid").innerHTML=
  (LIVE?sec("Hydraulics","NO DRAIN SENSOR",[NA("Drain capacity used"),NA("Rain headroom to overflow"),NA("Projected drain level, +30 min"),m("Peak runoff Q",f(I.Q),"L/s",I.Q/1.5,hi(I.Q,60,110)),m("Runoff coefficient C",f(I.C,2),"",I.C*100,hi(I.C,.65,.8)),NA("Time to overflow at this ramp")]):sec("Hydraulics","DRAIN ALERT 12 CM",[m("Drain capacity used",f(I.util),"%",I.util,hi(I.util,70,100)),m("Rain headroom to overflow",f(Math.max(0,I.head)),"mm/h",I.head/1.2,lo(I.head,40,15)),m("Projected drain level, +30 min",f(I.lvl30,1),"cm",I.lvl30/12*100,hi(I.lvl30,9,12)),m("Peak runoff Q",f(I.Q),"L/s",I.Q/1.5,hi(I.Q,60,110)),m("Runoff coefficient C",f(I.C,2),"",I.C*100,hi(I.C,.65,.8)),m("Time to overflow at this ramp",I.eta?f(I.eta):"none","min",I.eta?100-I.eta*2:0,I.eta?lo(I.eta,20,8):"ok")]))+
  sec("Risk model","LOGISTIC · 30 MIN",[m("Flood probability, 30 min",f(I.p30),"%",I.p30,hi(I.p30,35,65)),m("Early-warning index",f(I.ewi),"/100",I.ewi,hi(I.ewi,35,60)),m("Lead time to risk 50",I.lead?f(I.lead):"none","min",I.lead?100-I.lead*2:0,I.lead?lo(I.lead,20,8):"ok"),m("Model confidence",f(I.conf),"%",I.conf,lo(I.conf,75,55)),m("Closest archive match",I.best?f(I.best.sim):"0","%",I.best?I.best.sim:0,"ok"),m("Mean sensor trust",f(I.mt),"/100",I.mt,lo(I.mt,75,50))])+
  sec("Atmosphere and soil",wx.mode==="live"?"LIVE FEED":wx.mode==="offline"?"OFFLINE":"ARCHIVE",[m("Soil saturation",f(I.soil),"%",I.soil,hi(I.soil,70,88)),m("Rain, next 3 h (mean)",f(wx.fc3,1),"mm/h",wx.fc3/.6,hi(wx.fc3,10,30)),m("Cloud cover",f(wx.cloud),"%",wx.cloud,"ok"),m("Convective energy (CAPE)",f(wx.cape),"J/kg",wx.cape/30,hi(wx.cape,1500,2500)),m("Relative humidity",f(c.hum||0),"%",c.hum||0,"ok"),m("Sea-level pressure",f(c.pres||0),"hPa",((c.pres||1000)-980)*2.5,lo(c.pres||1010,1002,996))]);
 $("tBars").innerHTML=Object.keys(POS).map(id=>{const t=I.trust[id];if(t==null)return `<div class="tb"><span>${POS[id].n}</span><i style="--p:0%"></i><b>n/a</b></div>`;return `<div class="tb ${lo(t,75,50)}"><span>${POS[id].n}</span><i style="--p:${t}%"></i><b>${f(t)}</b></div>`}).join("");
 $("analogTxt").textContent=I.best?"Closest recorded pattern: "+I.best.e.id+" "+I.best.e.name+" ("+f(I.best.sim)+"% match). That event peaked at river +"+I.best.e.river+" cm and land depth "+I.best.e.depth+" cm, about "+I.best.eta+" min after this point.":""}
function renderStats(){const H=state.history;if(H.length<3)return;const col=k=>H.map(x=>x[k]),pk=a=>Math.max(...a),sl=a=>{const q=a.slice(-10);return(q[q.length-1]-q[0])/Math.max(1,q.length-1)},R=col(KA),D=col(KB),K=col("risk");
 $("statsRow").innerHTML=[[LA+" now",R[R.length-1].toFixed(1),UA],[LA+" mean",avg(R).toFixed(1),UA],[LA+" peak",pk(R).toFixed(1),UA],[LA+" rate",(sl(R)*RS).toFixed(2),UA+"/min"],[LB+" mean",avg(D).toFixed(1),UB],[LB+" peak",pk(D).toFixed(1),UB],[LB+" rate",(sl(D)*RS).toFixed(2),UB+"/min"],["Risk peak",pk(K),"/100"]].map(x=>`<div class="st"><span>${x[0]}</span><b>${x[1]}<small>${x[2]}</small></b></div>`).join("")}
function drawWx(){const S=wx.series,n=S.length;if(!n){$("wxChart").innerHTML="";return}const W=900,H=230,L=34,R=34,T=12,B=24,bw=(W-L-R)/n,mr=Math.max(10,...S.map(s=>s.rain)),ph=H-T-B;let g="";
 for(let i=0;i<=4;i++){const y=T+i*ph/4;g+=`<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="#ebe9df" stroke-dasharray="3 4"/><text x="${L-6}" y="${y+3}" text-anchor="end" font-size="9" font-family="DM Mono" fill="#a2a596">${(mr*(1-i/4)).toFixed(0)}</text><text x="${W-R+6}" y="${y+3}" font-size="9" font-family="DM Mono" fill="#a2a596">${100-i*25}</text>`}
 S.forEach((s,i)=>{const bh=s.rain/mr*ph,f=i>wx.now;g+=`<rect x="${(L+i*bw+.4).toFixed(1)}" y="${(T+ph-bh).toFixed(1)}" width="${Math.max(1,bw-.8).toFixed(1)}" height="${bh.toFixed(1)}" fill="${f?"#e3b36a":"#438c7b"}" opacity="${f?.6:.9}"/>`;if(i%24===0)g+=`<text x="${L+i*bw}" y="${H-8}" font-size="9" font-family="DM Mono" fill="#a2a596">${Math.round((i-wx.now)/24)}d</text>`});
 g+=`<polyline fill="none" stroke="#202820" stroke-width="1.6" points="${S.map((s,i)=>(L+(i+.5)*bw).toFixed(1)+","+(T+ph*(1-s.cloud/100)).toFixed(1)).join(" ")}"/>`;
 const nx=L+(wx.now+.5)*bw;g+=`<line x1="${nx}" x2="${nx}" y1="${T}" y2="${T+ph}" stroke="#bd643d" stroke-width="1.5" stroke-dasharray="4 3"/><text x="${nx+5}" y="${T+10}" font-size="9" font-family="DM Mono" fill="#bd643d">NOW</text>`;
 $("wxChart").innerHTML=g}
function renderSky(full){
 const c=wx.cur||{},v=x=>typeof x==="number"?+x.toFixed(1):(x??"–"),rows=[["Temperature",v(c.temp),"°C"],["Humidity",v(c.hum),"%"],["Precipitation",v(c.rain),"mm/h"],["Cloud cover",v(c.cloud),"%"],["Pressure",v(c.pres),"hPa"],["Wind",v(c.wind),"km/h"],["CAPE",Math.round(wx.cape),"J/kg"],["Updated",wx.mode==="live"&&wx.at?new Date(wx.at).toLocaleTimeString("en-IN",{hour12:false}):wx.mode==="offline"?"offline":"recorded","" ]];
 if(wx.sat)rows.push(["Satellite rain",v(wx.sat.rain_mm_h),"mm/h"],["Cloud-top temp",v(wx.sat.cloud_top_c),"°C"]);
 $("wxRows").innerHTML=rows.map(r=>`<div class="wr"><span>${r[0]}</span><b>${r[1]}<small>${r[2]}</small></b></div>`).join("");$("wxSrc").textContent=wx.mode==="live"?"LIVE · OPEN-METEO":wx.mode==="offline"?"UNREACHABLE":"RECORDED SAMPLE";
 const I=state.inf;if(I)$("lib").innerHTML='<div class="lr h"><span>ID</span><span>Pattern</span><span>Peak</span><span>River</span><span>Depth</span><span>Match</span></div>'+I.sims.map(x=>`<div class="lr ${x===I.best?"best":""}"><b>${x.e.id}</b><span>${x.e.name}</span><span>${x.e.peak} mm/h</span><span>+${x.e.river} cm</span><span>${x.e.depth} cm</span><em><i style="--p:${x.sim}%"></i>${x.sim.toFixed(0)}%</em></div>`).join("");
 if(full||!renderSky.d){drawWx();renderSky.d=1}}
$("syncBtn").onclick=()=>{if(LIVE)return;const r=Math.round(clamp(wx.cur&&wx.cur.rain||0,0,120));$("rainControl").value=r;applyControls();toast("Twin driven by "+(wx.mode==="live"?"live":"archive")+" rain",r+" mm/h applied")};
/* ================= LIVE HARDWARE LAYER =================
   ESP32 node -> LoRa -> TTGO -> Raspberry Pi (aquamesh_pi.py) -> GET /api/twin/state -> here.
   Nothing in this block invents data: a missing sensor shows "n/a" / "NO SENSOR". */
async function fetchLiveTelemetry(){
 if(!LIVE)return;
 const ac=new AbortController(),to=setTimeout(()=>ac.abort(),3500);
 try{const r=await fetch(CONFIG.liveEndpoint,{cache:"no-store",signal:ac.signal});if(!r.ok)throw Error("HTTP "+r.status);const d=await r.json();if(!d||typeof d!=="object"||Array.isArray(d)||!d.nodes||typeof d.nodes!=="object")throw Error("Invalid twin snapshot");if(d.assessment!=null){const a=d.assessment;if(typeof a!=="object"||Array.isArray(a)||!a.visual||typeof a.visual!=="object"||!a.quality||typeof a.quality!="object"||!Array.isArray(a.alerts)||!Array.isArray(a.hypotheses)||!a.pattern||!a.metrics)throw Error("Invalid assessment data")}state.snap=d;state.snapAt=Date.now()}catch(e){/* Keep the last valid snapshot briefly; deriveLive marks it unavailable after 8 s without a fresh response. */}finally{clearTimeout(to)}}
const hwOf=id=>state.snap&&state.snap.nodes?state.snap.nodes[id]:null,gwOf=()=>state.snap&&state.snap.gateway||null;
const upH=h=>!!h&&(h.health==="ONLINE"||h.health==="STALE");
const fmtDur=s=>s==null?"–":Math.floor(s/3600)+"h "+String(Math.floor(s%3600/60)).padStart(2,"0")+"m";
const fx=(v,d=0,u="")=>v==null||isNaN(v)?"–":(+v).toFixed(d)+u;
function healthLabel(id){
 if(id==="PI-EDGE")return gwOf()&&gwOf().serial_connected?"ONLINE":gwOf()?"NO UART":"NO LINK";
 if(id==="ALERT-01")return gwOf()&&gwOf().buzzer_available?"READY":"NO BUZZER";
 const h=hwOf(id);if(!h)return state.snap?"NO DATA":"NO LINK";
 return({ONLINE:"ONLINE",STALE:"STALE",OFFLINE:"OFFLINE",NO_SENSOR:"NOT INSTALLED",NO_DATA:"WAITING"})[h.health]||h.health}
function deriveLive(){
 if(state.snap&&Date.now()-state.snapAt>8000)state.snap=null;
 const S=state.snap,n=state.nodes,gw=gwOf(),R=hwOf("RAIN-01"),A=hwOf("AREA-01");
 n["RAIN-01"].ok=upH(R);n["AREA-01"].ok=upH(A);n["PI-EDGE"].ok=!!(gw&&gw.serial_connected);n["ALERT-01"].ok=!!(gw&&gw.buzzer_available);
 const AS=S&&S.assessment||null;state.as=AS;n["RAIN-01"].rain=n["RAIN-01"].ok&&AS?AS.visual.rain_intensity:0; /* animation only; the tile shows raw ADC */
 n["AREA-01"].soil=n["AREA-01"].ok&&A.soil_moisture_pct!=null?A.soil_moisture_pct:0;n["AREA-01"].soilKnown=n["AREA-01"].ok&&A.soil_moisture_pct!=null;
 n["AREA-01"].depth=0;n["AREA-01"].overflow=false;
 n["PI-EDGE"].temp=gw&&gw.cpu_temp_c!=null?gw.cpu_temp_c:0;
 ["DRAIN-01","RIVER-01"].forEach(id=>{const q=hwOf(id);n[id].ok=!!(q&&q.installed)&&upH(q)});[["RAIN-01",R],["AREA-01",A],["DRAIN-01",hwOf("DRAIN-01")],["RIVER-01",hwOf("RIVER-01")]].forEach(([id,h])=>{n[id].hw=h;n[id].loss=h&&h.packet_loss_pct!=null?h.packet_loss_pct:0;n[id].rssi=h&&h.rssi_dbm!=null?h.rssi_dbm:null});
 n["PI-EDGE"].hw=gw;
 state.rain=n["RAIN-01"].rain;state.blockage=0;state.river=0;
 state.rainAlert=n["RAIN-01"].ok&&R.status==="ALERT";
 state.gasAlert=n["AREA-01"].ok&&(!!A.gas_alert||A.status==="ALERT");
 state.nodeFailure=!n["RAIN-01"].ok||!n["AREA-01"].ok;
 state.internetLoss=!!wx.tried&&wx.mode!=="live";
 const r=AS&&AS.risk_index!=null?AS.risk_index:0; /* LIVE HEURISTIC SCORE from the backend rule engine, not a probability */
 state.lastRisk=state.risk;state.risk=r;
 if(S){state.history.push({river:0,drain:0,rain:n["RAIN-01"].rain,soil:n["AREA-01"].soilKnown?n["AREA-01"].soil:(A&&A.soil_raw!=null?A.soil_raw/40.95:0),gas:A&&A.gas_raw||0,risk:r});if(state.history.length>100)state.history.shift();
  state.events=(S.events||[]).slice(0,12).map(e=>({title:e.title,detail:e.detail,severity:e.severity,time:new Date(e.t*1000).toLocaleTimeString("en-IN",{hour12:false})}))}
 infer()}
function liveTrust(I){const N=state.nodes,t={};
 ["RAIN-01","AREA-01"].forEach(id=>{const n=N[id],h=n.hw;t[id]=!h||!n.ok?0:clamp(100-n.loss*2.5-(h.rssi_dbm!=null?Math.max(0,-h.rssi_dbm-60)*.7:0)-Math.max(0,(h.age_s||0)-6)*1.5,0,100)});
 t["DRAIN-01"]=null;t["RIVER-01"]=null;t["PI-EDGE"]=N["PI-EDGE"].ok?100:0;t["ALERT-01"]=N["ALERT-01"].ok?100:0;I.trust=t}
function valLive(id){const n=state.nodes[id],h=n.hw;
 if(id==="DRAIN-01"||id==="RIVER-01"){const q=hwOf(id);return q&&q.installed&&q.health!=="NO_DATA"?(id==="DRAIN-01"?q.drain_level_cm+" cm":q.river_level_cm+" cm"):"NO SENSOR"}
 if(id==="PI-EDGE")return n.ok?"EDGE OK":"NO UART";
 if(id==="ALERT-01")return gwOf()&&gwOf().buzzer_on?"BUZZER ON":"RISK "+state.risk;
 if(!n.ok)return h&&h.health==="OFFLINE"?"OFFLINE":"NO DATA";
 if(id==="RAIN-01")return n.rain.toFixed(0)+" mm/h"+(state.rainAlert?" !":"");
 return state.gasAlert?"GAS ALERT":n.soil.toFixed(0)+"% soil"}
function initLive(){
 document.title="AquaMesh | Live flood network";
 const L=(f,t)=>{const e=document.querySelector('label[for="'+f+'"]');if(e)e.textContent=t};
 L("rainControl","Rainfall intensity · live estimate");L("blockControl","Drain blockage · no sensor installed");L("riverControl","River level · no sensor installed");
 ["rainControl","blockControl","riverControl"].forEach(id=>$(id).disabled=true);
 document.querySelectorAll(".v-lab .rl").forEach(e=>e.style.display="none");
 const T=(id,b,sm)=>{const e=$(id);e.disabled=true;e.querySelector("b").textContent=b;e.querySelector("small").textContent=sm};
 T("overflowToggle","Soil saturated","Needs a CALIBRATED soil probe (≥ 70 %)");T("sensorFaultToggle","Gas alert","Live: GAS01 MQ over threshold");T("nodeFailureToggle","Node offline","Live: a node stopped reporting");T("internetToggle","Internet loss","Live: weather feed unreachable");
 $("explainTxt").textContent="Live mode: sensor values appear only when received from real ESP32 hardware through the gateway.";
 $("runStormBtn").disabled=true;$("runStormBtn").textContent="Live hardware only";$("syncBtn").style.display="none";
 $("resetBtn").textContent="Clear trend";$("resetBtn").title="Clear trend history (0)";$("exportBtn").title="Export latest node readings as CSV (Numpad .)";
 $("trendTitle").textContent="Last 3 minutes";$("legA").textContent="Soil probe (% if calibrated, else raw ÷ 41)";$("legB").textContent="Rain wetness index (30 s mean raw ÷ 12)";
 [...$("trendAxis").children].forEach((e,i)=>e.textContent=["−3 min","−2 min","−1 min","now"][i]);
 const tabs={"RAIN-01":"Rain","DRAIN-01":"Drain · n/a","AREA-01":"Soil + gas","RIVER-01":"River · n/a","PI-EDGE":"Pi gateway","ALERT-01":"Buzzer"};
 $("nodeTabs").querySelectorAll("button").forEach(b=>{b.lastChild.textContent=tabs[b.dataset.node]});
 $("mapAlertTitle").textContent="Live alert";selectNode("RAIN-01")}
function renderTwinLive(){
 const crit=state.gasAlert||state.rainAlert||state.risk>=75;
 $("mapAlertText").textContent=!state.snap?"No data from the field network":state.gasAlert&&state.rainAlert?"Rain AND gas alerts active · region in danger":state.gasAlert?"Gas threshold exceeded · soil/gas node GAS01":state.rainAlert?"Rain node alert · flood danger in region":state.risk>=75?"Critical inundation risk · act now":state.risk>=50?"Heavy rain and wet ground · monitor":state.risk>=28?"Water accumulation increasing":"No active hazard detected";
 $("mapAlertDot").className=crit?"crit":(state.risk>=28||!state.snap)?"warn":""}
function renderLive(){
 const S=state.snap,gw=gwOf(),N=state.nodes,A=hwOf("AREA-01"),ids=["RAIN-01","AREA-01","PI-EDGE","ALERT-01"],on=ids.filter(online).length,age=gw&&gw.last_rx_age_s;
 let lab,det,hws,pill="";
 if(!S){lab="NO LINK";det="Pi API not responding";hws="API UNAVAILABLE";pill="down"}
 else if(!gw.serial_connected){lab="UART DOWN";det="Pi up · TTGO gateway not found";hws="UART DOWN";pill="down"}
 else if(age==null){lab="WAITING";det="UART open · no packets yet";hws="WAITING"}
 else{lab="LIVE";det=gw.packets_total+" packets · last "+age.toFixed(0)+" s ago";hws="CONNECTED";pill="live"}
 $("connectionLabel").textContent=lab;$("connectionDetail").textContent=det;$("hardwareStatus").textContent=hws;
 $("modeLabel").textContent=pill==="live"?"LIVE HARDWARE":"NO SENSOR DATA";$("modeLabel").parentElement.className="mode-pill "+pill;
 $("footerTime").textContent=!S?"NO DATA":(state.gasAlert||state.rainAlert)?"ALERT ACTIVE":"SYSTEM NOMINAL";
 $("networkValue").textContent=Math.round(on/ids.length*100);$("networkMeter").style.width=(on/ids.length*100)+"%";$("onlineCount").textContent=on+" of "+ids.length+" endpoints online · 2 not installed";
 $("rainTrend").textContent=N["RAIN-01"].ok?"● LIVE · est. from wetness":"● "+healthLabel("RAIN-01");
 $("riverLabel").textContent="Soil moisture";$("riverUnit").textContent="%";$("riverNode").textContent="AREA-01";$("riverValue").textContent=N["AREA-01"].soil.toFixed(0);
 $("riverTrend").textContent=N["AREA-01"].ok?"GAS "+(A.gas_raw??"–")+(state.gasAlert?" ⚠ ALERT":" · ok"):"● "+healthLabel("AREA-01");
 $("rainControl").value=N["RAIN-01"].rain;$("rainControlVal").textContent="est. "+N["RAIN-01"].rain.toFixed(1)+" mm/h";$("blockControlVal").textContent="no sensor";$("riverControlVal").textContent="no sensor";
 setToggle("overflowToggle",!!(S&&S.assessment&&S.assessment.metrics.soil_pct>=70));setToggle("sensorFaultToggle",state.gasAlert);setToggle("nodeFailureToggle",state.nodeFailure);setToggle("internetToggle",state.internetLoss);if(window.AQR)AQR.render(S)}
function renderInspectorLive(){
 const id=state.selected,n=state.nodes[id],h=n.hw||{},gw=gwOf()||{},up=online(id);
 const link=[["Link RSSI",h.rssi_dbm!=null?fx(h.rssi_dbm,0," dBm"):"not reported by gateway"],["Packet loss",fx(h.packet_loss_pct,1," %")],["Last packet",h.age_s!=null?fx(h.age_s,0," s ago"):"–"],["Packets received",h.received??"–"]];
 let primary="–",label="",unit="",glyph="≈",rows=[],temp="–",tstate="NOT MEASURED",tcol="#8a8f82";
 if(id==="RAIN-01"){const cal=h.rain_mm_h!=null;primary=up?(cal?fx(h.rain_mm_h,1):(h.rain_raw??"–")):"–";label=cal?"RAINFALL INTENSITY (ESTIMATE)":"RAIN SENSOR · RAW ADC (UNCALIBRATED)";unit=cal?"mm/h":"raw";glyph="⌁";rows=[["Data quality",(state.as&&state.as.quality.rain)||"–"],["Rain sensor raw (ADC)",h.rain_raw??"–"],["Wet fraction, last 60 s",h.wetness_frac_60!=null?fx(h.wetness_frac_60*100,0," %"):"–"],["HX711 raw count (no physical unit)",h.pressure_raw??"–"],["Node status",h.status||"–"],["Alert threshold","raw > "+(h.alert_raw_threshold??500)],["Elevation",n.elevation+" m"],...link]}
 else if(id==="AREA-01"){const sk=n.soilKnown;primary=up?(sk?fx(n.soil,0):(h.soil_raw??"–")):"–";label=sk?"SOIL MOISTURE":"SOIL PROBE · RAW ADC (CALIBRATION REQUIRED)";unit=sk?"%":"raw";glyph="⌂";rows=[["Data quality",(state.as&&state.as.quality.soil)||"–"],["Soil sensor raw (ADC)",h.soil_raw??"–"],["Chemical sensor raw (ADC)",h.gas_raw??"–"],["Gas status",!up?"–":state.gasAlert?"ALERT":"clear"],["Gas alert threshold","raw > "+(h.gas_alert_threshold??1500)],["MPU6050 accel (raw)",h.accel_x==null?"–":`${h.accel_x}, ${h.accel_y}, ${h.accel_z}`],["MPU6050 gyro (raw)",h.gyro_x==null?"–":`${h.gyro_x}, ${h.gyro_y}, ${h.gyro_z}`],["Node status",h.status||"–"],["Elevation",n.elevation+" m"],...link]}
 else if((id==="DRAIN-01"||id==="RIVER-01")&&hwOf(id)&&hwOf(id).installed){const q=hwOf(id);primary=id==="DRAIN-01"?(q.drain_level_cm??"–"):(q.river_level_cm??"–");label=(id==="DRAIN-01"?"DRAIN WATER LEVEL":"RIVER LEVEL");unit="cm";glyph="≈";rows=[["Provenance","MEASURED HARDWARE"],...(id==="DRAIN-01"?[["Flow",(q.drain_flow_lpm??"–")+" L/min"]]:[]),["Elevation",n.elevation+" m"],...link.slice(1)]}
 else if(id==="DRAIN-01"||id==="RIVER-01"){label="NO SENSOR INSTALLED";unit="";glyph="×";rows=[["Status","Not part of the current hardware"],["Planned elevation",n.elevation+" m"],["Needs",id==="DRAIN-01"?"water-level / flow sensor + ESP32":"water-level sensor + ESP32"],["Effect","Excluded from risk, trust and network %"]]}
 else if(id==="PI-EDGE"){primary=gw.cpu_temp_c!=null?fx(gw.cpu_temp_c,1):"–";label="PROCESSOR TEMPERATURE";unit="°C";glyph="⌘";if(gw.cpu_temp_c!=null){temp=fx(gw.cpu_temp_c,1)+"°C";tstate=gw.cpu_temp_c>70?"● ELEVATED":"● NOMINAL";tcol=gw.cpu_temp_c>70?"#b55b3d":"#5e8a5c"}
  rows=[["Role","LoRa gateway + dashboard"],["UART",gw.serial_connected?gw.serial_port+" @ "+gw.baud:"not connected"],["Packets received",gw.packets_total??"–"],["Malformed lines",gw.bad_lines??"–"],["Last packet",gw.last_rx_age_s!=null?fx(gw.last_rx_age_s,0," s ago"):"none yet"],["Weather feed",state.internetLoss?"UNREACHABLE":"AVAILABLE"],["Gateway uptime",fmtDur(gw.uptime_s)],["Storage used",fx(gw.disk_used_pct,0," %")]]}
 else{primary=state.risk;label="FLOOD RISK SCORE";unit="/100";glyph="!";rows=[["Alert level",riskBand()],["Buzzer",gw.buzzer_on?"SOUNDING":"standby"],["Buzzer hardware",gw.buzzer_available?"READY · GPIO 17":"NOT FOUND"],["Trigger","FLOOD01 / GAS01 STATUS=ALERT"],["Rain alert",state.rainAlert?"ACTIVE":"clear"],["Gas alert",state.gasAlert?"ACTIVE":"clear"]]}
 $("inspectorTitle").textContent=names[id];$("inspectorStatus").textContent="● "+healthLabel(id);$("inspectorStatus").className="status-tag "+(up?"online":"offline");
 $("primaryLabel").textContent=label;$("primaryValue").textContent=primary;$("primaryUnit").textContent=unit;$("primaryGlyph").textContent=glyph;$("nodeTemp").textContent=temp;$("tempState").textContent=tstate;$("tempState").style.color=tcol;
 $("sensorList").innerHTML=rows.map(([a,b])=>`<div class="sensor-row"><span>${a}</span><b>${b}</b></div>`).join("")}
function renderRouteLive(){
 const ids=["RAIN-01","DRAIN-01","AREA-01","RIVER-01"],sym=["☂","⌁","⌂","≈"];let html="";
 ids.forEach((id,i)=>{html+=`<div class="route-node ${online(id)?"":"failed"}"><i>${online(id)?sym[i]:"×"}</i><span>${id.split("-")[0]}</span></div>`;if(i<ids.length-1)html+=`<div class="route-link ${online(ids[i])&&online(ids[i+1])?"":"broken"}"></div>`});
 $("routeDiagram").innerHTML=html;const up=ids.filter(online).length;
 $("routeCount").textContent=up+" OF 4 NODES";$("routeMessage").textContent="LoRa 433 MHz → TTGO → Pi · Drain and River not installed"}
function renderAlertsLive(){
 if(window.AQR&&state.snap&&state.snap.assessment)return AQR.alerts(state.snap);
 const S=state.snap,gw=gwOf(),R=hwOf("RAIN-01"),G=hwOf("AREA-01"),A=[];
 if(!S)A.push(["NO LINK","Dashboard cannot reach the Pi","/api/twin/state is not responding. Check that aquamesh_pi.py is running.","warn"]);
 else{
  if(state.gasAlert)A.push(["CRITICAL","Gas detected · GAS01","MQ sensor raw "+G.gas_raw+" is above "+G.gas_alert_threshold+". Gateway buzzer triggered.","critical"]);
  if(state.rainAlert)A.push(["CRITICAL","Rain / flood alert · FLOOD01","Rain sensor raw "+R.rain_raw+" is above "+R.alert_raw_threshold+". Gateway buzzer triggered.","critical"]);
  if(state.risk>=75)A.push(["CRITICAL","Surface flooding likely","Heavy rain and saturated ground reported by the nodes.","critical"]);
  else if(state.risk>=50)A.push(["WARNING","Flood risk high","Rain intensity and soil moisture are elevated.","warn"]);
  else if(state.risk>=28)A.push(["WATCH","Flood risk rising","Monitor rainfall and soil moisture.","warn"]);
  [["RAIN-01","FLOOD01"],["AREA-01","GAS01"]].forEach(([id,hw])=>{const h=hwOf(id)||{};if(h.health==="OFFLINE")A.push(["NODE OFFLINE",hw+" not reporting","No packet for "+fx(h.age_s,0)+" s. Check power and LoRa range.","warn"]);else if(h.health==="STALE")A.push(["NODE STALE",hw+" packets delayed","Last packet "+fx(h.age_s,0)+" s ago (expected every 5 s).","warn"]);else if(h.health==="NO_DATA")A.push(["WAITING",hw+" not heard yet","No packet received since the gateway started.","info"])});
  if(!gw.serial_connected)A.push(["GATEWAY","TTGO UART not found","Pi cannot open a serial port. Check the USB cable.","warn"]);
  if(state.internetLoss)A.push(["OFFLINE MODE","Weather feed unreachable","Local sensing continues; forecast inputs are blank.","info"]);
  if(!A.length)A.push(["SYSTEM NORMAL","No active flood or gas alert","Both field nodes report within thresholds.","info"])}
 A.push(["INFO","Drain and river nodes not installed","Only FLOOD01 and GAS01 exist in hardware; those twin nodes show NO SENSOR.","info"]);
 $("alertCount").textContent=A.filter(a=>a[3]!=="info").length+" ACTIVE";
 $("alertList").innerHTML=A.slice(0,4).map(a=>`<div class="alert-item"><span class="alert-symbol ${a[3]}">${a[3]==="critical"?"!":a[3]==="warn"?"△":"✓"}</span><div class="alert-copy"><b>${a[1]}</b><p>${a[2]}</p></div><span class="alert-time">${stamp()}</span></div>`).join("")}
function exportLive(){const S=state.snap;if(!S){toast("Nothing to export","No data from the Pi yet.");return}
 const rows=[["timestamp","website_node","hw_id","health","status","pkt","rain_raw","rain_mm_h_est","pressure_raw","soil_raw","soil_pct","gas_raw","accel_x","accel_y","accel_z","gyro_x","gyro_y","gyro_z","rssi_dbm","packet_loss_pct"]];
 Object.entries(S.nodes).forEach(([id,h])=>rows.push([S.timestamp,id,h.hw_id,h.health,h.status,h.pkt,h.rain_raw,h.rain_mm_h,h.pressure_raw,h.soil_raw,h.soil_moisture_pct,h.gas_raw,h.accel_x,h.accel_y,h.accel_z,h.gyro_x,h.gyro_y,h.gyro_z,h.rssi_dbm,h.packet_loss_pct]));
 const csv=rows.map(r=>r.map(x=>`"${String(x??"").replaceAll('"','""')}"`).join(",")).join("\n");const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));a.download="aquamesh-live-telemetry.csv";a.click();URL.revokeObjectURL(a.href);toast("Telemetry exported","CSV saved with the latest live readings.")}
function tick(){let d=new Date();$("clock").textContent=d.toLocaleTimeString("en-IN",{hour12:false});$("modelTime").textContent=d.toLocaleDateString("en-IN",{day:"2-digit",month:"short",year:"numeric"}).toUpperCase()+" · "+d.toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit",hour12:false})+" IST";if(!state.storm){derive();render()}}
for(let i=0;i<24;i++)derive();log("Twin initialized","Waiting for measured node telemetry.");log("Mesh routes established","Elevation graph Rain → Drain → Low Area → River.");fit();requestAnimationFrame(frame);render();tick();setInterval(tick,CONFIG.tickMs);if(LIVE){initLive();setInterval(fetchLiveTelemetry,2000)}fetchLiveTelemetry();fetchWx();setInterval(fetchWx,CONFIG.wxRefreshMs);
window.AQ={snap:()=>state.snap,live:LIVE,go:go,toast:toast,trust:()=>state.inf&&state.inf.trust,riskBand:riskBand};
})();
