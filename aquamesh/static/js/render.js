/* DOM renderers for every card */
function zones(){
  $('zones').innerHTML=['river','drain','land'].map(z=>{const d=S.zones[z];
    return `<div class="card zone ${d.level}" data-goal="${d.goal}" data-z="${z}">
    <h2><span>${z}</span><span><span class="chip">${d.status}</span><span class="chip">conf ${d.conf_label}</span></span></h2>
    <div class="lvl">${d.level}</div>
    <div class="why">${esc(d.reason)}</div>
    <div class="kv"><span>confidence (coverage × independence)</span><b>${(d.confidence*100).toFixed(0)}%</b></div><div class="bar"><i style="width:${d.confidence*100}%;background:var(--c)"></i></div>
    <div class="kv"><span>${d.prob_label} (Bayesian)</span><b>${(d.prob*100).toFixed(0)}%</b></div><div class="bar"><i style="width:${d.prob*100}%"></i></div>
    <div class="kv"><span>rules vs probability</span><b>${esc(d.agree)}</b></div></div>`}).join('');
  document.querySelectorAll('.zone').forEach(e=>e.onclick=()=>{goal=e.dataset.goal;$('goal').value=GOALS.includes(goal)?goal:$('goal').value;why()});
}
function hyp(){
  $('hyp').innerHTML=S.hyp.map(h=>`<div class="hyp"><span>${h.label}</span><div class="bar"><i style="width:${h.p*100}%;background:${h.p>.7?'var(--crit)':h.p>.4?'var(--warn)':'var(--blue)'}"></i></div><b>${(h.p*100).toFixed(0)}%</b></div>`).join('');
  $('abd').innerHTML=S.abduction.map(a=>`<div class="hyp" style="grid-template-columns:250px 1fr 44px"><span style="font-size:11.5px">${a.label}</span><div class="bar"><i style="width:${a.p*100}%;background:#9b59b6"></i></div><b>${(a.p*100).toFixed(0)}%</b></div>`).join('');
  const rows=Object.entries(S.contributions||{}).sort((a,b)=>Math.max(...Object.values(b[1]).map(Math.abs))-Math.max(...Object.values(a[1]).map(Math.abs)));
  $('contrib').innerHTML=rows.map(([e,c])=>`<div class="item"><b>${e}</b> <span class="dim">${Object.entries(c).filter(([k,v])=>Math.abs(v)>.1).map(([k,v])=>k+' '+(v>0?'+':'')+v).join(' · ')||'no influence'}</span></div>`).join('')||'<small>no evidence yet</small>';
}
function alerts(){
  const A=S.alerts;$('nAl').textContent=A.filter(a=>a.active).length+' active';
  $('alerts').innerHTML=A.map(a=>`<div class="item"><span class="lv-${a.level}"><b>${a.active?'●':'○'} ${esc(a.title)}</b></span> <small>${hms(a.t)}${a.t_clear?' → cleared '+hms(a.t_clear):''}</small><div class="dim" style="font-size:12px">${esc(a.detail).slice(0,330)}</div></div>`).join('')||'<small>no alerts</small>';
}
function fires(){
  $('fires').innerHTML=S.firings.map(f=>`<div class="item"><b>${f.rule}</b> <span class="dim">${f.premises.join(' ∧ ')}</span> ⇒ <b>${f.then}</b><div class="dim" style="font-size:11.5px">${esc(f.doc)}</div></div>`).join('')||'<small>no rules fired - quiescent</small>';
  $('tl').innerHTML=S.timeline.map(e=>`<div class="item"><small>${hms(e.t)}</small> <b class="${e.ev=='ON'?'lv-WARNING':'dim'}">${e.ev}</b> ${esc(e.text)} ${e.rule?'<span class="rule">'+e.rule+'</span>':''}</div>`).join('')||'<small>nothing yet</small>';
}
function health(){
  $('health').innerHTML='<table><tr><th>source</th><th>state</th><th>reliability</th><th>notes</th></tr>'+Object.entries(S.health).map(([k,v])=>
   `<tr><td>${k}</td><td><span class="dot ${v.state}"></span>${v.state}</td><td>${v.rel!=null?(v.rel*100).toFixed(0)+'%':''}</td><td class="dim">${esc((v.reasons||[]).join('; '))}</td></tr>`).join('')+'</table>';
  $('links').innerHTML='<table><tr><th>node</th><th>rx</th><th>lost</th><th>loss%</th><th>crc bad</th><th>replay/dup</th><th>reboots</th><th>RSSI</th></tr>'+S.links.map(l=>
   `<tr><td>${l.node}</td><td>${l.received}</td><td>${l.lost}</td><td>${l.loss_pct}</td><td>${l.crc_bad}</td><td>${l.replays}/${l.dups}</td><td>${l.reboots}</td><td>${l.rssi??'-'}</td></tr>`).join('')+'</table>';
  const g=S.gateway;$('gw').innerHTML=`<span class="dot ${g.connected?'OK':'DOWN'}"></span>gateway ${g.connected?'up':'DOWN'} <small>${esc(g.port||'')}</small>`;
}
function terrain(){
  const T=S.terrain,e=T.elev,o=T.open;
  const edge=(k,l)=>`<label><input type="checkbox" data-ed="${l}" ${T.connected[l]?'checked':''}> ${l.replace('>',' → ')} physically connected</label> <span class="chip" style="background:${o[k]?'#1d4a2e':'#4a1d1d'}">${o[k]?'path open':'path closed'}</span><br>`;
  $('terrain').innerHTML=`<small>Water can move A → B when elevation(A)+${T.tol} ≥ elevation(B) and the link is connected. Change values to run the elevation experiment (the camera does not measure elevation).</small><br>
   River <input type="number" step=".5" id="eR" value="${e.river}"> Drain <input type="number" step=".5" id="eD" value="${e.drain}"> Land <input type="number" step=".5" id="eL" value="${e.land}"> cm
   <button id="bTer">Apply</button><br>${edge('edge_river_drain_open','river>drain')}${edge('edge_river_land_open','river>land')}${edge('edge_drain_land_open','drain>land')}
   <small>Chain: rainfall → runoff → river/drain loading → overflow → land inundation; soil saturation amplifies runoff; obstruction reduces drainage capacity.</small>`;
  $('bTer').onclick=async()=>{await api('/api/terrain',{elev:{river:+$('eR').value,drain:+$('eD').value,land:+$('eL').value}})};
  document.querySelectorAll('[data-ed]').forEach(c=>c.onchange=()=>api('/api/terrain',{connected:{[c.dataset.ed]:c.checked}}));
  calNow();
}
function calNow(){const cal=S.cal;$('calnow').textContent=`rain dry/wet = ${cal.rain.dry}/${cal.rain.wet}   soil dry/wet = ${cal.soil.dry}/${cal.soil.wet}   latest raw: rain ${S.raw.rain??'-'}, soil ${S.raw.soil??'-'}`}
function ablate(){
  $('abl').innerHTML=['camera','rain','soil','hx','mpu','gas'].map(s=>`<label style="margin-right:6px"><input type="checkbox" data-ab="${s}" ${S.ablate.includes(s)?'checked':''}> ${s}</label>`).join('');
  document.querySelectorAll('[data-ab]').forEach(c=>c.onchange=()=>api('/api/ablate',{source:c.dataset.ab,off:c.checked}));
}
async function evlog(){
  const st=S.evidence;$('evstat').innerHTML=`${st.records} records · head <code>${st.head}</code> · audits ${st.audits.runs} (failed ${st.audits.failed}) · cloud outbox ${st.outbox}`;
  const r=await api('/api/evidence?n=14');
  if(Array.isArray(r))$('evlist').innerHTML=r.map(x=>`<div class="item"><small>#${x.id} ${hms(x.ts)} <code>${x.hash}</code></small> <b>${x.kind}</b> <span class="dim">${esc(JSON.stringify(x.payload)).slice(0,150)}</span></div>`).join('');
}
function tree(n){
  const ic={TRUE:'✔',FALSE:'✘',UNKNOWN:'?'}[n.status];
  return `<li><span class="st-${n.status}">${ic}</span> ${esc(n.text)}${n.detail?` <small>(${esc(n.detail)})</small>`:''}${n.rule?`<span class="rule">${n.rule}</span>`:''}${n.missing&&n.missing.length?`<br><small>needs: ${esc(n.missing.join('; '))}</small>`:''}${n.sources&&n.sources.length&&!n.children.length?` <small>[${n.sources.join(', ')}]</small>`:''}${n.children.length?'<ul>'+n.children.map(tree).join('')+'</ul>':''}</li>`}
async function why(){
  if(!goal)return;const r=await api('/api/why?goal='+goal);if(r.error){$('whyText').textContent=r.error;return}
  let b='';if(r.bayes)b=`<div class="dim" style="margin-top:6px">Probabilistic view: <b>${r.bayes.label} ${(r.bayes.p*100).toFixed(0)}%</b>. Evidence weights: ${Object.entries(r.bayes.contributions).map(([k,v])=>k+' '+(v>0?'+':'')+v).join(', ')||'-'}</div>`;
  $('whyText').innerHTML=`<b>${goal}</b><pre>${esc(r.text)}</pre><small>rule chain: ${r.chain.join(' → ')||'-'}</small>${b}`;
  $('tree').innerHTML='<ul>'+tree(r.tree)+'</ul>';
}
async function experiments(){
  if(expLoaded)return;SC=await api('/api/scenarios');expLoaded=true;
  if(!SC.scenarios.length){$('exp').innerHTML=`<div class="dim">LIVE mode: run the experiments physically (pour water, block the drain, unplug a node or the TTGO, replay a packet). Every conclusion, rule firing and sensor-health flag is logged and explained here. Use the "disable source" boxes for ablation, and the terrain panel for the elevation test.</div>`;$('expTitle').textContent='Experiments (physical)';return}
  $('expTitle').textContent='Experiments (simulated scenarios)';
  $('spd').innerHTML=`speed <select id="speed">${[...new Set([1,2,4,8,15,SC.speed])].sort((a,b)=>a-b).map(s=>`<option ${s==SC.speed?'selected':''}>${s}</option>`).join('')}</select>×`;
  $('speed').onchange=()=>api('/api/scenario',{speed:+$('speed').value});
  $('exp').innerHTML=SC.scenarios.map(s=>`<button data-sc="${s.id}" class="${s.id==SC.current?'on':''}"><b>${s.title}</b><small>${esc(s.expect)}</small></button>`).join('');
  document.querySelectorAll('[data-sc]').forEach(b=>b.onclick=async()=>{await api('/api/scenario',{name:b.dataset.sc});expLoaded=false;document.querySelectorAll('[data-sc]').forEach(x=>x.classList.toggle('on',x==b))});
}
