// ── Sparklines ───────────────────────────────────────────────────────────────
// Der Tagesverlauf als Miniatur direkt unter der Momentanzahl. Quelle ist derselbe
// 15-min-Puffer wie der grosse Chart (histData) – also kein zusätzlicher Abruf.
// Die Kachel beantwortet damit die Frage, die eine nackte Zahl offen lässt:
// ist das gerade viel oder wenig für heute?
function cvsCtx(id,H){
  var c=$(id);if(!c)return null;
  var W=c.clientWidth||c.offsetWidth||0;
  if(W<8)return null;
  c.width=Math.round(W*devicePixelRatio);c.height=Math.round(H*devicePixelRatio);
  c.style.height=H+'px';
  var ctx=c.getContext('2d');
  ctx.setTransform(1,0,0,1,0,0);ctx.scale(devicePixelRatio,devicePixelRatio);
  ctx.clearRect(0,0,W,H);
  return{ctx:ctx,W:W,H:H};
}
function sparkScale(vals,zero){
  var mn=Infinity,mx=-Infinity;
  for(var i=0;i<vals.length;i++){if(vals[i]<mn)mn=vals[i];if(vals[i]>mx)mx=vals[i];}
  if(!isFinite(mn)){mn=0;mx=1;}
  if(zero||mn>0)mn=Math.min(0,mn);
  if(mx<0)mx=0;
  if(mx-mn<1)mx=mn+1;
  return{mn:mn,mx:mx};
}
function drawSpark(id,vals,col){
  var g=cvsCtx(id,24);if(!g)return;
  if(!vals||vals.length<2)return;
  var ctx=g.ctx,W=g.W,H=g.H,s=sparkScale(vals,false),i;
  var px=function(k){return 1+k/(vals.length-1)*(W-2);};
  var py=function(v){return H-1.5-(v-s.mn)/(s.mx-s.mn)*(H-3);};
  ctx.beginPath();ctx.moveTo(px(0),py(s.mn));
  for(i=0;i<vals.length;i++)ctx.lineTo(px(i),py(vals[i]));
  ctx.lineTo(px(vals.length-1),py(s.mn));ctx.closePath();
  ctx.globalAlpha=.15;ctx.fillStyle=col;ctx.fill();ctx.globalAlpha=1;
  ctx.beginPath();ctx.strokeStyle=col;ctx.lineWidth=1.4;ctx.lineJoin='round';
  for(i=0;i<vals.length;i++){var x=px(i),y=py(vals[i]);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}
  ctx.stroke();
  ctx.beginPath();ctx.fillStyle=col;ctx.arc(px(vals.length-1),py(vals[vals.length-1]),1.9,0,6.2832);ctx.fill();
}
// Netz wechselt das Vorzeichen – als Balken um eine Nulllinie ist auf einen Blick
// erkennbar, wann bezogen und wann eingespeist wurde. Als Linie wäre das nicht lesbar.
function drawSparkBars(id,vals,posCol,negCol){
  var g=cvsCtx(id,24);if(!g)return;
  if(!vals||!vals.length)return;
  var ctx=g.ctx,W=g.W,H=g.H,s=sparkScale(vals,true);
  var py=function(v){return H-1.5-(v-s.mn)/(s.mx-s.mn)*(H-3);};
  var y0=py(0),bw=Math.max(1,(W-2)/vals.length-0.5);
  for(var i=0;i<vals.length;i++){
    var x=1+i/vals.length*(W-2),y=py(vals[i]);
    ctx.fillStyle=vals[i]>=0?posCol:negCol;
    ctx.fillRect(x,Math.min(y,y0),bw,Math.max(1,Math.abs(y-y0)));
  }
  ctx.strokeStyle=cv('--ink3');ctx.globalAlpha=.5;ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(0,y0);ctx.lineTo(W,y0);ctx.stroke();ctx.globalAlpha=1;
}
function updateSparks(){
  var pts=(histData&&histData.pts)?histData.pts:[];
  var note=$('spk-note');
  if(pts.length<2){if(note)note.textContent='';return;}
  drawSpark('spk-p',pts.map(function(p){return p[1];}),cv('--blue'));
  drawSpark('spk-c',pts.map(function(p){return p[2];}),cv('--cons'));
  // Vorzeichen wie in der Kachel darüber: + = Einspeisung, − = Bezug
  drawSparkBars('spk-g',pts.map(function(p){return -p[3];}),cv('--green'),cv('--red'));
  if(note){
    var t0=new Date(pts[0][0]*1000),t1=new Date(pts[pts.length-1][0]*1000);
    note.textContent=T('Verlauf {0} – {1} (Mittelwert je 15 min)',[hhmm(t0),hhmm(t1)]);
  }
}
function hhmm(d){return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
// ── Tages-Donuts ─────────────────────────────────────────────────────────────
function drawDonut(id,segs,ctrVal,ctrLbl){
  var g=cvsCtx(id,130);if(!g)return false;   // Karte gerade ausgeblendet -> Breite 0
  var ctx=g.ctx,W=g.W,H=g.H,cx=W/2,cy=H/2,R=Math.min(W,H)/2-3,ri=R*.62,tot=0;
  segs.forEach(function(s){if(s.v>0)tot+=s.v;});
  if(tot<=0){
    ctx.beginPath();ctx.arc(cx,cy,R,0,6.2832);ctx.arc(cx,cy,ri,6.2832,0,true);
    ctx.fillStyle=cv('--tint');ctx.fill();
  }else{
    var a=-Math.PI/2,live=segs.filter(function(s){return s.v>0;});
    live.forEach(function(s){
      var a2=a+s.v/tot*6.283185;
      ctx.beginPath();ctx.arc(cx,cy,R,a,a2);ctx.arc(cx,cy,ri,a2,a,true);ctx.closePath();
      ctx.fillStyle=s.c;ctx.fill();
      // Trennfuge nur bei mehr als einem Segment – sonst bekäme der volle Ring
      // an der Zwölf-Uhr-Position einen Schnitt, den es gar nicht gibt.
      if(live.length>1){ctx.strokeStyle=cv('--card');ctx.lineWidth=2;ctx.stroke();}
      a=a2;
    });
  }
  ctx.textAlign='center';
  ctx.fillStyle=cv('--ink');ctx.font='700 20px '+CFONT;
  ctx.fillText(ctrVal,cx,cy+3);
  ctx.fillStyle=cv('--ink3');ctx.font='600 9px '+CFONT;
  ctx.fillText(T(ctrLbl),cx,cy+16);
  return true;
}
function donutLegend(id,segs){
  var el=$(id);if(!el)return;
  el.innerHTML=segs.map(function(s){
    return '<span><i style="background:'+s.c+'"></i>'+esc(T(s.n))+' <b>'+s.v.toFixed(1)+' kWh</b></span>';
  }).join('');
}
function pct(a,b){return b>0.001?Math.round(a/b*100)+'%':'–';}
var donutSig='';
function updateDonuts(e){
  var card=$('card-donut');if(!card)return;
  var dp=e.dp||0,dc=e.dc||0,dgi=e.dgi||0,dgo=e.dgo||0;
  if(dp<0.01&&dc<0.01){card.style.display='none';return;}
  card.style.display='';
  // Der Status-Poll läuft jede Sekunde, die Tageszähler ändern sich aber nur mit
  // jeder SolarLog-Abfrage. Ohne diesen Vergleich würden beide Ringe 600× pro
  // Aktualisierung neu gezeichnet, ohne dass sich ein Pixel ändert.
  var sig=dp.toFixed(2)+'|'+dc.toFixed(2)+'|'+dgi.toFixed(2)+'|'+dgo.toFixed(2);
  if(sig===donutSig)return;
  donutSig=sig;
  // Produktionsseite: was selbst genutzt wurde (inkl. Batterieladung) und was ins Netz ging.
  var own=Math.max(0,dp-dgo),segP=[{n:'Selbst genutzt',v:own,c:cv('--blue')},
                                   {n:'Eingespeist',v:dgo,c:cv('--green')}];
  // Verbrauchsseite: was aus eigener Erzeugung kam und was zugekauft wurde.
  var self2=Math.max(0,dc-dgi),segC=[{n:'Aus eigener Anlage',v:self2,c:cv('--cons')},
                                     {n:'Netzbezug',v:dgi,c:cv('--orange')}];
  var okP=drawDonut('dn-prod',segP,pct(own,dp),'eigen genutzt');
  var okC=drawDonut('dn-cons',segC,pct(self2,dc),'autark');
  donutLegend('dn-prod-l',segP);
  donutLegend('dn-cons-l',segC);
  // Konnte nicht gezeichnet werden (Dashboard war ausgeblendet, Breite 0)? Dann die
  // Signatur zurücknehmen, sonst zeigten die Ringe nach dem Zurückwechseln bis zur
  // nächsten Zählerbewegung – also bis zu 10 Minuten – veraltete Werte.
  if(!okP||!okC)donutSig='';
  setTxt('donut-sub',T('{0} kWh erzeugt · {1} kWh verbraucht',[dp.toFixed(1),dc.toFixed(1)]));
}
