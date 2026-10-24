/* canvas trend charts (last 15 min) */
const CH=[
 ['Rain & soil (calibrated 0-1)',[['rain','#3b9eff'],['soil','#a0522d']],{min:0,max:1}],
 ['River: coverage & flow',[['river_cover','#22d3ee'],['river_flow','#2ecc71']],{min:0,max:1}],
 ['Drain: level & flow',[['drain_level','#f39c12'],['drain_flow','#e67e22']],{min:0,max:1}],
 ['Land: standing-water fraction',[['land_still','#2ecc71']],{min:0,max:.5,lines:[[.03,'present']]}],
 ['Posterior P(hypothesis)',[['p_rain','#3b9eff'],['p_surge','#22d3ee'],['p_block','#e74c3c'],['p_inund','#f1c40f']],{min:0,max:1}],
 ['Zone severity (rules)',[['sev_river','#22d3ee'],['sev_drain','#f39c12'],['sev_land','#2ecc71']],{min:0,max:3,step:true}],
 ['MPU fin-motion DIST (relative)',[['dist','#9b59b6']],{}],
 ['Gas MQ raw (context only)',[['mq','#95a5a6']],{}],
 ['Load cell HX raw',[['hx','#e67e22']],{}]];
$('charts').innerHTML=CH.map((c,i)=>`<div><small>${c[0]}</small><canvas class="ch" id="ch${i}"></canvas></div>`).join('');
function chart(cv,defs,o){
  const dpr=devicePixelRatio||1,w=cv.clientWidth,h=cv.clientHeight;cv.width=w*dpr;cv.height=h*dpr;
  const x=cv.getContext('2d');x.scale(dpr,dpr);x.clearRect(0,0,w,h);
  const series=defs.map(([k,c])=>({k,c,d:(H[k]||[]).filter(p=>p[1]!=null)}));
  const all=series.flatMap(s=>s.d);if(!all.length){x.fillStyle='#8a96a6';x.fillText('no data yet',8,16);return}
  const tmax=Math.max(...all.map(p=>p[0])),tmin=tmax-900;
  let mn=o.min,mx=o.max;if(mn==null){const v=all.map(p=>p[1]);mn=Math.min(...v);mx=Math.max(...v);const pad=(mx-mn||1)*.1;mn-=pad;mx+=pad}
  const X=t=>(t-tmin)/900*(w-6)+3,Y=v=>h-14-(v-mn)/(mx-mn||1)*(h-20);
  x.strokeStyle='#232c37';x.fillStyle='#8a96a6';x.lineWidth=1;
  for(let m=0;m<=15;m+=5){const xx=X(tmax-m*60);x.beginPath();x.moveTo(xx,2);x.lineTo(xx,h-14);x.stroke();x.fillText(m?'-'+m+'m':'now',Math.min(xx+2,w-26),h-3)}
  (o.lines||[]).forEach(([v,l])=>{x.strokeStyle='#5a6678';x.setLineDash([4,3]);x.beginPath();x.moveTo(0,Y(v));x.lineTo(w,Y(v));x.stroke();x.setLineDash([]);x.fillText(l,4,Y(v)-2)});
  series.forEach(s=>{x.strokeStyle=s.c;x.lineWidth=1.6;x.beginPath();let first=true,py=0;
    s.d.forEach(([t,v])=>{if(t<tmin)return;const xx=X(t),yy=Y(v);if(first){x.moveTo(xx,yy);first=false}else{if(o.step)x.lineTo(xx,py);x.lineTo(xx,yy)}py=yy});x.stroke()});
  x.fillStyle='#8a96a6';x.fillText(mx.toFixed(mx>10?0:2),w-34,10);
}
function drawCharts(){CH.forEach((c,i)=>chart($('ch'+i),c[1],c[2]))}
