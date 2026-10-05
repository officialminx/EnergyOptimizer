function initChart(){
  var c=$('chart'),ctx=c.getContext('2d'),W=c.parentElement.clientWidth,H=260;
  c.width=W*devicePixelRatio;c.height=H*devicePixelRatio;
  c.style.width=W+'px';c.style.height=H+'px';
  ctx.scale(devicePixelRatio,devicePixelRatio);
  return{ctx:ctx,W:W,H:H};
}
var ch,histAgg=null,histRange='day',histMode='w',histBusy=false;
var C_PROD='#0a84ff',C_CONS='#7c5cff',C_SURP='#0fb5bd',C_FEED='#34c759',C_IMP='#ff9500';
var C_SURP_F='rgba(15,181,189,.18)';   // Füllung der Überschussfläche (hell wie dunkel tragbar)
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
// Stundenanteil eines Tagespunkts: ein Punkt ist der Mittelwert seit dem vorigen Punkt,
// also zählt dessen echter Abstand. Feste 15 min waren nach einem Neustart (Punkte im
// Minutenabstand) ein Vielfaches zu viel. Ohne Vorgänger und nach einer Lücke
// (Gerät war aus, der Mittelwert deckt sie nicht ab) gilt der Regeltakt.
function ptH(pts,i){var dt=i>0?(pts[i][0]-pts[i-1][0]):900;
  if(!(dt>0)||dt>1800)dt=900;return dt/3600;}
// Zeitstempel-Brüche im Ringpuffer abfangen: ohne NTP-Zeit zählt das Gerät Sekunden
// seit dem Start. Punkte aus zwei Zeitachsen ergäben eine gestauchte Kurve und im
// Schaltzeiten-Streifen negative Laufzeiten. Zwei verschiedene Fälle:
// 1. Einzelne Uptime-Punkte VERSTREUT zwischen echten (je einer pro Neustart, bevor NTP
//    stand) – das sind Ausreisser, nicht der Beginn einer neuen Achse. Sie fallen an
//    ihrer winzigen Zahl auf (< 1e9 wie im Langzeitlog) und werden einzeln verworfen,
//    damit der Tagesverlauf drumherum vollständig bleibt.
// 2. Bleibt danach ein Rücksprung (reiner Uptime-Betrieb ohne NTP, Neustart setzt die
//    Sekunden zurück; oder ein Zeitsprung der Uhr), beginnt dort wirklich eine neue
//    Achse – der jüngere Teil gewinnt.
function sanePts(a){if(!a||!a.length)return a||[];
  var r=a.filter(function(p){return p[0]>=1000000000;});
  var b=r.length?r:a;
  var o=[b[0]];
  for(var i=1;i<b.length;i++){if(b[i][0]>o[o.length-1][0])o.push(b[i]);else o=[b[i]];}
  return o;}
// pts (Tag): [epoch,prod,cons,grid,batt,ein-Maske,Lauf-Maske,sh0_w..]; buckets (W/M/J): {t,pe,ce,fe,ie,pw,cw}
// Gewählter Tag im Verlauf ('' = heute). Heute kommt aus dem laufenden Abruf
// (histData, speist auch die Sparklines), ein anderer Tag aus dem Langzeit-Log.
var histDay='',dayView=null;
function viewData(){return histDay?dayView:histData;}
function ymd(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function histDaySet(v){
  var today=ymd(new Date());
  if(v===today)v='';
  histDay=v||'';
  var inp=$('hday-in');if(inp){inp.value=histDay||today;inp.max=today;}
  var nx=$('hday-next');if(nx)nx.disabled=!histDay;
  var tb=$('hday-today');if(tb)tb.style.visibility=histDay?'visible':'hidden';
  var dl=histDay?new Date(histDay+'T12:00:00').toLocaleDateString(LOC,{weekday:'short',day:'numeric',month:'long',year:'numeric'}):'';
  setTxt('strip-title',histDay?T('Schaltzeiten am {0}',[dl]):T('Schaltzeiten heute'));
  setTxt('sim-day',histDay?dl:T('heute'));
  // Ein einzelner Tag gehört in die Tagesansicht.
  if(histRange!=='day'){
    histRange='day';var hr=$('hrange');
    if(hr)for(var i=0;i<hr.children.length;i++)hr.children[i].classList.toggle('active',hr.children[i].getAttribute('data-r')==='day');
  }
  $('sim-res').innerHTML='';
  if(!histDay){dayView=null;chartFit();redrawChart();return;}
  api('/api/history?d='+histDay.replace(/-/g,'')).then(function(d){
    if(d)d.pts=sanePts(d.pts);dayView=d;chartFit();redrawChart();
  });
}
function histDayStep(n){
  var base=histDay?new Date(histDay+'T12:00:00'):new Date();
  base.setDate(base.getDate()+n);
  if(base>new Date())return;
  histDaySet(ymd(base));
}
function kwhSeries(bk,xl,lab){
  return {bars:true,unit:'kWh',x:xl,lab:lab||xl,series:[
    {name:T('Produktion'),color:C_PROD,data:bk.map(function(o){return (o.pe||0)/1000;})},
    {name:T('Verbrauch'),color:C_CONS,data:bk.map(function(o){return (o.ce||0)/1000;})},
    {name:T('Einspeisung'),color:C_FEED,data:bk.map(function(o){return (o.fe||0)/1000;})},
    {name:T('Netzbezug'),color:C_IMP,data:bk.map(function(o){return (o.ie||0)/1000;})}
  ]};
}
function bucketLabel(o){var d=new Date(o.t*1000);
  if(histRange==='year')return d.toLocaleDateString('de-DE',{month:'short'});
  if(histRange==='week')return d.toLocaleDateString('de-DE',{weekday:'short'});
  return String(d.getDate());}
// Ausgeschriebene Beschriftung für den Tooltip (die Achse bleibt knapp)
function bucketFull(o){var d=new Date(o.t*1000);
  if(histRange==='year')return d.toLocaleDateString('de-DE',{month:'long',year:'numeric'});
  return d.toLocaleDateString('de-DE',{weekday:'short',day:'2-digit',month:'2-digit',year:'numeric'});}
// Vereinheitlichte Serien-Struktur je Range+Mode (Linien oder Balken).
function buildSeries(){
  if(histRange==='day'){
    var vd=viewData();
    var pts=(vd&&vd.pts)?vd.pts:[];
    if(pts.length<2)return null;
    if(histMode==='w'){
      var n=pts.length,t0=pts[0][0],t1=pts[n-1][0],span=Math.max(1,t1-t0);
      var nDev=(vd.n||0),dev=vd.dev||[];
      var ser=[{name:T('Produktion'),color:C_PROD,data:pts.map(function(p){return p[1];})},
               {name:T('Verbrauch'),color:C_CONS,data:pts.map(function(p){return p[2];})},
               {name:T('Überschuss'),color:C_SURP,dash:true,data:pts.map(function(p){return Math.max(0,p[1]-p[2]);})}];
      for(var i=0;i<nDev;i++){(function(ii){ser.push({name:dev[ii]||T('Gerät {0}',[ii+1]),color:SHCOL[ii%SHCOL.length],thin:true,data:pts.map(function(p){return Math.max(0,p[7+ii]||0);})});})(i);}
      var plotX=pts.map(function(p){return (p[0]-t0)/span;});
      var lab=pts.map(function(p){var d=new Date(p[0]*1000);
        return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')+' Uhr';});
      var xt=[],tk=Math.min(6,n-1);for(var k=0;k<=tk;k++){var ep=t0+span*k/tk,d=new Date(ep*1000);xt.push({f:tk?k/tk:0,t:String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')});}
      return {bars:false,unit:'W',plotX:plotX,xticks:xt,lab:lab,series:ser,fill:{a:0,b:1,c:C_SURP_F}};
    }
    var hb=[];for(var h=0;h<24;h++)hb.push({pe:0,ce:0,fe:0,ie:0});
    for(var q=0;q<pts.length;q++){var pp=pts[q],hh=new Date(pp[0]*1000).getHours(),qh=ptH(pts,q);
      hb[hh].pe+=pp[1]*qh;hb[hh].ce+=pp[2]*qh;var gg=pp[3];if(gg>0)hb[hh].ie+=gg*qh;else hb[hh].fe+=(-gg)*qh;}
    var xl=[];for(var h2=0;h2<24;h2++)xl.push(String(h2).padStart(2,'0'));
    return kwhSeries(hb,xl,xl.map(function(h){return h+':00 &ndash; '+h+':59 Uhr';}));
  }
  var b=(histAgg&&histAgg.buckets)?histAgg.buckets:[];
  if(!b.length)return null;
  if(histMode==='w'){
    var n2=b.length;
    var ser2=[{name:T('Produktion'),color:C_PROD,data:b.map(function(o){return o.pw;})},
              {name:T('Verbrauch'),color:C_CONS,data:b.map(function(o){return o.cw;})},
              {name:T('Überschuss'),color:C_SURP,dash:true,data:b.map(function(o){return Math.max(0,o.pw-o.cw);})}];
    var plx=b.map(function(o,i){return n2>1?i/(n2-1):0;});
    var xt2=[],ev=n2>12?Math.ceil(n2/8):1;for(var i2=0;i2<n2;i2++)if(i2%ev===0)xt2.push({f:n2>1?i2/(n2-1):0,t:bucketLabel(b[i2])});
    return {bars:false,unit:'W',plotX:plx,xticks:xt2,lab:b.map(bucketFull),series:ser2,fill:{a:0,b:1,c:C_SURP_F}};
  }
  return kwhSeries(b,b.map(bucketLabel),b.map(bucketFull));
}
function fmtW(v){return v>=1000?(v/1000).toFixed(v>=10000?0:1)+'k':(''+Math.round(v));}
function niceMax(m,unit){if(m<=0)return unit==='kWh'?1:500;
  var step=unit==='kWh'?(m>=40?10:m>=15?5:m>=4?1:0.5):(m>=2000?1000:500);
  return Math.ceil(m/step)*step;}
var chGeo=null,chHover=-1,curS=null,tipHideT=null;
// ── Überschussfläche ─────────────────────────────────────────────────────────
// Füllt die Fläche zwischen zwei Serien, aber nur dort, wo die obere über der
// unteren liegt – also genau die Energie, die für die Steckdosen zur Verfügung
// stand. Die Kreuzungspunkte werden interpoliert: ohne das würde die Fläche erst
// beim nächsten Messpunkt beginnen bzw. enden und bei 15-min-Abtastung um bis zu
// eine Viertelstunde daneben liegen.
function fillCross(A,B,S,px,k){
  var d0=A[k]-B[k],d1=A[k+1]-B[k+1],f=(d0===d1)?0:d0/(d0-d1);
  if(!(f>=0))f=0;
  if(f>1)f=1;
  return{x:px(S.plotX[k]+(S.plotX[k+1]-S.plotX[k])*f),v:A[k]+(A[k+1]-A[k])*f};
}
function fillBetween(ctx,S,px,yOf,ia,ib,col){
  var A=S.series[ia]&&S.series[ia].data,B=S.series[ib]&&S.series[ib].data;
  if(!A||!B)return;
  var n=S.plotX.length,i=0,k,c;
  ctx.fillStyle=col;
  while(i<n){
    while(i<n&&!(A[i]>B[i]))i++;
    if(i>=n)break;
    var s=i;
    while(i<n&&A[i]>B[i])i++;
    var e=i-1,up=[],dn=[];
    if(s>0){c=fillCross(A,B,S,px,s-1);up.push([c.x,yOf(c.v)]);dn.push([c.x,yOf(c.v)]);}
    for(k=s;k<=e;k++){var x=px(S.plotX[k]);up.push([x,yOf(A[k])]);dn.push([x,yOf(B[k])]);}
    if(e+1<n){c=fillCross(A,B,S,px,e);up.push([c.x,yOf(c.v)]);dn.push([c.x,yOf(c.v)]);}
    if(up.length<2)continue;
    ctx.beginPath();ctx.moveTo(up[0][0],up[0][1]);
    for(k=1;k<up.length;k++)ctx.lineTo(up[k][0],up[k][1]);
    for(k=dn.length-1;k>=0;k--)ctx.lineTo(dn[k][0],dn[k][1]);
    ctx.closePath();ctx.fill();
  }
}
function drawSeries(S){
  if(!ch||ch.W<1)return;
  var ctx=ch.ctx,W=ch.W,H=ch.H,Ml=44,Mr=14,Mt=12,Mb=22;
  var x0=Ml,y1=H-Mb,cw=W-Ml-Mr,chH=y1-Mt,i,j;
  ctx.clearRect(0,0,W,H);
  chGeo=null;
  if(!S){ctx.fillStyle=cv('--ink3');ctx.font='12px '+CFONT;ctx.textAlign='center';
    ctx.fillText(T(histRange!=='day'?'Noch keine Daten für diesen Zeitraum':histDay?'Für diesen Tag enthält das Langzeit-Log keine Messpunkte':'Noch keine Verlaufsdaten – Erfassung alle 15 min'),W/2,H/2);return;}
  var maxV=0;for(j=0;j<S.series.length;j++)for(i=0;i<S.series[j].data.length;i++)if(S.series[j].data[i]>maxV)maxV=S.series[j].data[i];
  maxV=niceMax(maxV,S.unit);
  ctx.font='9px '+CFONT;
  for(var g=0;g<=4;g++){var v=maxV*g/4,y=y1-(v/maxV*chH);
    ctx.strokeStyle=cv('--tint');ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(W-Mr,y);ctx.stroke();
    ctx.fillStyle=cv('--ink3');ctx.textAlign='right';ctx.fillText(S.unit==='kWh'?(''+(Math.round(v*10)/10)):fmtW(v),x0-5,y+3);}
  ctx.textAlign='center';
  var yOf=function(v){return y1-(Math.max(0,v)/maxV*chH);};
  if(S.bars){
    var n=S.x.length,ns=S.series.length,slot=cw/n,gw=slot*0.72,bw=gw/ns,evx=n>16?Math.ceil(n/12):1;
    for(i=0;i<n;i++){var bx=x0+i*slot+(slot-gw)/2;
      for(j=0;j<ns;j++){var vv=S.series[j].data[i],yy=yOf(vv);ctx.fillStyle=S.series[j].color;ctx.fillRect(bx+j*bw,yy,Math.max(1,bw-1),y1-yy);}
      if(i%evx===0){ctx.fillStyle=cv('--ink3');ctx.fillText(S.x[i],x0+i*slot+slot/2,H-6);}}
    chGeo={x0:x0,y1:y1,cw:cw,chH:chH,maxV:maxV,Mt:Mt,n:n,slot:slot};
  }else{
    var px=function(f){return x0+f*cw;};
    if(S.fill)fillBetween(ctx,S,px,yOf,S.fill.a,S.fill.b,S.fill.c);
    for(j=0;j<S.series.length;j++){var se=S.series[j];
      ctx.beginPath();ctx.strokeStyle=se.color;ctx.lineWidth=se.thin?1.5:2;ctx.lineJoin='round';ctx.setLineDash(se.dash?[4,3]:[]);
      for(i=0;i<S.plotX.length;i++){var xx=px(S.plotX[i]),yv=yOf(se.data[i]);if(i===0)ctx.moveTo(xx,yv);else ctx.lineTo(xx,yv);}
      ctx.stroke();ctx.setLineDash([]);}
    ctx.fillStyle=cv('--ink3');for(var t=0;t<S.xticks.length;t++)ctx.fillText(S.xticks[t].t,px(S.xticks[t].f),H-6);
    chGeo={x0:x0,y1:y1,cw:cw,chH:chH,maxV:maxV,Mt:Mt,n:S.plotX.length,slot:0};
  }
  // Fadenkreuz über dem fertigen Chart, damit es keine Linie verdeckt
  if(chHover>=0&&chHover<chGeo.n){
    var hx=S.bars?(x0+(chHover+0.5)*chGeo.slot):(x0+S.plotX[chHover]*cw);
    ctx.strokeStyle=cv('--ink3');ctx.lineWidth=1;ctx.setLineDash([3,3]);
    ctx.beginPath();ctx.moveTo(hx,Mt);ctx.lineTo(hx,y1);ctx.stroke();ctx.setLineDash([]);
    if(!S.bars)for(j=0;j<S.series.length;j++){
      ctx.beginPath();ctx.fillStyle=S.series[j].color;
      ctx.arc(hx,yOf(S.series[j].data[chHover]),3.2,0,6.2832);ctx.fill();
      ctx.lineWidth=1.5;ctx.strokeStyle=cv('--card');ctx.stroke();
    }
  }
}
// ── Chart-Tooltip ────────────────────────────────────────────────────────────
function tipFmt(v,unit){return unit==='kWh'?((v==null?0:v).toFixed(2)+' kWh'):wInt(v||0);}
function chartHide(){
  if(chHover<0)return;
  chHover=-1;
  var t=$('chart-tip');if(t)t.style.display='none';
  drawSeries(curS);
}
// Tooltip neu aufbauen und platzieren. Das Messen der Breite (offsetWidth) erzwingt
// ein Layout, deshalb nur aufrufen, wenn sich der Messpunkt wirklich geändert hat –
// nicht bei jeder Mausbewegung innerhalb desselben Punktes.
function chartTip(i){
  var c=$('chart'),tip=$('chart-tip');
  if(!c||!tip||!curS||!chGeo)return;
  var h='<div class="tt-h">'+((curS.lab&&curS.lab[i])||'')+'</div>';
  for(var j=0;j<curS.series.length;j++){var se=curS.series[j];
    h+='<div class="tt-r"><span class="tt-n"><span class="dot" style="background:'+se.color+'"></span>'
      +esc(se.name)+'</span><span class="tt-v">'+tipFmt(se.data[i],curS.unit)+'</span></div>';}
  tip.innerHTML=h;
  tip.style.display='block';
  // innerhalb des Chart-Bereichs halten, damit der Tooltip am Rand nicht abgeschnitten wird
  var hx=curS.bars?(chGeo.x0+(i+0.5)*chGeo.slot):(chGeo.x0+curS.plotX[i]*chGeo.cw);
  var wrapW=c.parentElement.clientWidth,tw=tip.offsetWidth;
  var left=hx-tw/2;
  if(left<2)left=2;
  if(left+tw>wrapW-2)left=wrapW-tw-2;
  tip.style.left=left+'px';
  tip.style.top='6px';
}
function chartPointer(e){
  var c=$('chart');
  if(!c||!curS||!chGeo||!chGeo.n)return;
  var r=c.getBoundingClientRect(),x=e.clientX-r.left,i;
  if(curS.bars){
    i=Math.floor((x-chGeo.x0)/chGeo.slot);
  }else{
    var f=(x-chGeo.x0)/chGeo.cw,best=1e9;i=0;
    for(var k=0;k<curS.plotX.length;k++){var dd=Math.abs(curS.plotX[k]-f);if(dd<best){best=dd;i=k;}}
  }
  if(i<0)i=0; if(i>=chGeo.n)i=chGeo.n-1;
  if(e.pointerType==='touch'){clearTimeout(tipHideT);tipHideT=setTimeout(chartHide,3000);}
  if(i===chHover)return;   // derselbe Messpunkt -> weder Redraw noch Tooltip nötig
  chHover=i;drawSeries(curS);chartTip(i);
}
function renderLegend(S){var el=$('chart-legend');if(!el)return;var h='';
  if(S)for(var i=0;i<S.series.length;i++)h+='<span><span class="dot" style="background:'+S.series[i].color+'"></span> '+esc(S.series[i].name)+'</span>';
  el.innerHTML=h;}
var CT={day:'Tagesverlauf',week:'Wochenverlauf',month:'Monatsverlauf',year:'Jahresverlauf'};
// Kopfzeile: die Summen des gezeigten Zeitraums sind die Aussage – die frühere
// Angabe "96 Punkte" beschrieb nur die Abtastung.
function chartInfo(){
  var el=$('chart-info');if(!el)return;
  var p=0,c=0,i,ok;
  if(histRange==='day'){
    var vd=viewData(),pts=(vd&&vd.pts)||[];
    ok=pts.length>1;
    for(i=0;i<pts.length;i++){var q=ptH(pts,i);p+=pts[i][1]*q;c+=pts[i][2]*q;}
  }else{
    var bk=(histAgg&&histAgg.buckets)||[];
    ok=bk.length>0;
    for(i=0;i<bk.length;i++){p+=bk[i].pe||0;c+=bk[i].ce||0;}
  }
  el.textContent=ok?T('{0} kWh erzeugt · {1} kWh verbraucht',[(p/1000).toFixed(1),(c/1000).toFixed(1)]):'';
}
function redrawChart(){var tt=$('chart-title');if(tt)tt.textContent=CT[histRange]||'Verlauf';
  var S=buildSeries();curS=S;
  if(!S||chHover>=(S.bars?S.x.length:S.plotX.length)){chHover=-1;var t=$('chart-tip');if(t)t.style.display='none';}
  drawSeries(S);renderLegend(S);chartInfo();drawStrip();renderKpis();
  if(chHover>=0)chartTip(chHover);}   // Werte unter dem stehenden Zeiger mitziehen
(function(){
  var c=$('chart');if(!c)return;
  var mv=rafPtr(chartPointer),hide=function(){mv.cancel();chartHide();};
  c.addEventListener('pointermove',mv,{passive:true});
  c.addEventListener('pointerdown',mv,{passive:true});
  c.addEventListener('pointerleave',hide);
  c.addEventListener('pointercancel',hide);
})();
// ── Schaltzeiten-Streifen / Laufzeit-Timeline ────────────────────────────────
// Ein Balken je Steckdose auf derselben Zeitachse wie der Chart darüber. Damit
// ist ohne Umweg über das Protokoll erkennbar, ob eine Schaltung tatsächlich in
// die Mittagsspitze fiel. Ein Block entspricht dem Abstand zweier Messungen: der
// Zustand gilt als gehalten, sobald ihn die nächste Messung bestätigt – die
// letzte Messung bekommt deshalb noch keine Fläche.
var stripRows=[],stripGeo=null;
function stripBuild(){
  stripRows=[];stripGeo=null;
  // Bewusst nicht an den gewählten Diagramm-Zeitraum gebunden: der Streifen zeigt
  // immer den heutigen Tag und bleibt damit auch in der Wochen-/Monatsansicht stehen.
  var d=viewData(),pts=(d&&d.pts)?d.pts:[],n=d?(d.n||0):0;
  if(!n||pts.length<2)return false;
  var t0=pts[0][0],t1=pts[pts.length-1][0];
  if(t1<=t0)return false;
  // Zwei Ebenen je Gerät: die Freigabe der Steckdose (Ein-Maske, Index 5) und – bei
  // Geräten mit Eigenregelung – der tatsächliche Betrieb (Lauf-Maske, Index 6). Bei
  // einem Entfeuchter mit eigenem Hygrostat sind das zwei völlig verschiedene Balken.
  var rwf=d.rw||[];
  for(var i=0;i<n;i++){
    var bits=function(mi,sh){
      var segs=[],tot=0,open=-1;
      for(var k=0;k<pts.length-1;k++){
        var on=((pts[k][mi]||0)>>sh)&1;
        if(on&&open<0)open=pts[k][0];
        else if(!on&&open>=0){segs.push({a:open,b:pts[k][0]});tot+=pts[k][0]-open;open=-1;}
      }
      if(open>=0){segs.push({a:open,b:t1});tot+=t1-open;}
      return{segs:segs,total:tot};
    };
    var onl=bits(5,i),runl=bits(6,i);
    stripRows.push({i:i,name:(d.dev&&d.dev[i])||T('Gerät {0}',[i+1]),
                    col:SHCOL[i%SHCOL.length],segs:onl.segs,total:onl.total,
                    self:!!rwf[i],rsegs:runl.segs,rtotal:runl.total});
  }
  stripGeo={t0:t0,t1:t1};
  return true;
}
function rrect(ctx,x,y,w,h,r){
  if(w<2*r)r=w/2;
  if(h<2*r)r=h/2;
  ctx.beginPath();
  ctx.moveTo(x+r,y);ctx.lineTo(x+w-r,y);ctx.quadraticCurveTo(x+w,y,x+w,y+r);
  ctx.lineTo(x+w,y+h-r);ctx.quadraticCurveTo(x+w,y+h,x+w-r,y+h);
  ctx.lineTo(x+r,y+h);ctx.quadraticCurveTo(x,y+h,x,y+h-r);
  ctx.lineTo(x,y+r);ctx.quadraticCurveTo(x,y,x+r,y);
  ctx.closePath();
}
var STRIP_ROW=30,STRIP_TOP=6,STRIP_ML=44,STRIP_MR=14;
function drawStrip(){
  var card=$('card-strip');if(!card)return;
  if(!stripBuild()){card.style.display='none';return;}
  card.style.display='';
  var rows=stripRows,H=STRIP_TOP+rows.length*STRIP_ROW+18;
  var g=cvsCtx('strip',H);if(!g)return;
  var ctx=g.ctx,W=g.W,x0=STRIP_ML,cw=W-STRIP_ML-STRIP_MR;
  var t0=stripGeo.t0,t1=stripGeo.t1,span=Math.max(1,t1-t0);
  var px=function(t){return x0+(t-t0)/span*cw;};
  var yEnd=STRIP_TOP+rows.length*STRIP_ROW;
  // Stundenraster – Schrittweite so, dass die Beschriftung nicht verklebt
  var hstep=span>15*3600?3:span>6*3600?2:1,d0=new Date(t0*1000);
  var tick=new Date(d0.getFullYear(),d0.getMonth(),d0.getDate(),d0.getHours(),0,0);
  if(tick.getTime()/1000<t0)tick=new Date(tick.getTime()+3600000);
  while(tick.getHours()%hstep!==0)tick=new Date(tick.getTime()+3600000);
  ctx.font='9px '+CFONT;ctx.textAlign='center';
  for(var tk=tick.getTime()/1000;tk<=t1;tk+=hstep*3600){
    var xg=px(tk);
    ctx.strokeStyle=cv('--tint');ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(xg,STRIP_TOP+12);ctx.lineTo(xg,yEnd);ctx.stroke();
    ctx.fillStyle=cv('--ink3');ctx.fillText(hhmm(new Date(tk*1000)),xg,H-5);
  }
  rows.forEach(function(r,ri){
    var yTop=STRIP_TOP+ri*STRIP_ROW,yTrack=yTop+14,hT=13;
    ctx.font='600 10px '+CFONT;
    ctx.textAlign='left';ctx.fillStyle=cv('--ink2');
    ctx.fillText(r.name.length>24?(r.name.slice(0,23)+'…'):r.name,x0,yTop+9);
    ctx.textAlign='right';ctx.fillStyle=cv('--ink3');
    // Bei Eigenregelung ist die gelaufene Zeit die Aussage, nicht die Freigabezeit.
    var lbl=r.self
      ? (r.rtotal?T('{0} gelaufen ({1}×)',[fmtDur(r.rtotal),r.rsegs.length])
                 :T(r.total?'freigegeben, nicht gelaufen':'nicht gelaufen'))
      : (r.total?(fmtDur(r.total)+' ('+r.segs.length+'×)'):T('nicht gelaufen'));
    ctx.fillText(lbl,x0+cw,yTop+9);
    rrect(ctx,x0,yTrack,cw,hT,4);ctx.fillStyle=cv('--tint2');ctx.fill();
    // Freigabe blass, echter Lauf voll deckend darüber – ohne die blasse Ebene wäre
    // nicht zu sehen, dass die Automatik das Gerät sehr wohl freigegeben hatte.
    ctx.globalAlpha=r.self?0.3:1;
    r.segs.forEach(function(s){
      var xa=px(s.a),xb=px(s.b);
      rrect(ctx,xa,yTrack,Math.max(2.5,xb-xa),hT,3);ctx.fillStyle=r.col;ctx.fill();
    });
    ctx.globalAlpha=1;
    if(r.self)r.rsegs.forEach(function(s){
      var xa=px(s.a),xb=px(s.b);
      rrect(ctx,xa,yTrack,Math.max(2.5,xb-xa),hT,3);ctx.fillStyle=r.col;ctx.fill();
    });
  });
  var sum=0,nseg=0;
  rows.forEach(function(r){
    if(r.self){sum+=r.rtotal;nseg+=r.rsegs.length;}
    else      {sum+=r.total; nseg+=r.segs.length;}
  });
  setTxt('strip-sum',nseg?T(nseg===1?'{0} Laufzeit · {1} Einschaltphase':'{0} Laufzeit · {1} Einschaltphasen',[fmtDur(sum),nseg])
                         :T('heute noch nichts geschaltet'));
  stripSummary();
}
function stripSummary(){
  var info=$('strip-info');if(!info)return;
  var anySelf=stripRows.some(function(r){return r.self;});
  info.innerHTML=stripRows.length
    ? (T('Heute vom ersten bis zum letzten Messpunkt – zum Ablesen einer Laufzeit auf einen Balken zeigen.')
       +(anySelf?T(' Blass = Steckdose freigegeben, deckend = Gerät lief wirklich.'):''))
    : '';
}
function stripPointer(e){
  var c=$('strip'),info=$('strip-info');
  if(!c||!info||!stripGeo||!stripRows.length)return;
  var r=c.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
  var ri=Math.floor((y-STRIP_TOP)/STRIP_ROW);
  if(ri<0||ri>=stripRows.length){stripSummary();return;}
  var row=stripRows[ri],cw=r.width-STRIP_ML-STRIP_MR;
  var f=Math.min(1,Math.max(0,(x-STRIP_ML)/cw));
  var t=stripGeo.t0+f*(stripGeo.t1-stripGeo.t0),hit=null,rhit=null;
  row.segs.forEach(function(s){if(t>=s.a&&t<=s.b)hit=s;});
  if(row.self)row.rsegs.forEach(function(s){if(t>=s.a&&t<=s.b)rhit=s;});
  var span=function(s,suf){return hhmm(new Date(s.a*1000))+' &ndash; '+hhmm(new Date(s.b*1000))
    +' &middot; '+fmtDur(s.b-s.a)+(suf||'');};
  var h;
  if(row.self){
    // Der interessante Fall: freigegeben, aber nicht gelaufen – genau das erklärt
    // eine unerfüllte Mindest-Laufzeit.
    h='<b>'+esc(row.name)+'</b> &middot; '+(rhit?span(rhit,T(' gelaufen'))
      :(hit?span(hit,T(' freigegeben, Gerät aus (kein Bedarf)'))
           :T('um {0} aus',[hhmm(new Date(t*1000))])));
  }else{
    h='<b>'+esc(row.name)+'</b> &middot; '+(hit?span(hit)
      :T('um {0} aus',[hhmm(new Date(t*1000))]));
  }
  if(info.innerHTML!==h)info.innerHTML=h;   // gleicher Text -> kein Neuaufbau
}
(function(){
  var c=$('strip');if(!c)return;
  var mv=rafPtr(stripPointer);
  c.addEventListener('pointermove',mv,{passive:true});
  c.addEventListener('pointerdown',mv,{passive:true});
  c.addEventListener('pointerleave',function(){mv.cancel();stripSummary();});
})();
// Canvas an die aktuelle Kartenbreite anpassen (Fenster gedreht, Karte war beim
// letzten Zeichnen ausgeblendet).
function chartFit(){
  var c=$('chart');if(!c)return;
  var cw=c.parentElement.clientWidth;
  if(!ch||ch.W!==cw)ch=initChart();
}
// Die 15-min-Tagespunkte speisen drei Dinge: das Diagramm in der Tagesansicht, den
// Schaltzeiten-Streifen und die Sparklines auf dem Dashboard. Sie werden deshalb
// unabhängig vom gewählten Diagramm-Zeitraum geholt – vorher standen Streifen und
// Sparklines fest, sobald man das Diagramm auf Woche/Monat/Jahr stellte.
var dayBusy=false;
function fetchDayPts(){
  if(dayBusy)return;dayBusy=true;
  api('/api/history').then(function(d){
    dayBusy=false;if(d)d.pts=sanePts(d.pts);histData=d;
    updateSparks();
    if(histRange==='day'){chartFit();redrawChart();}   // redrawChart() zeichnet den Streifen mit
    else drawStrip();
  }).catch(function(){dayBusy=false;});
}
function fetchHistory(){
  if(histRange==='day'){if(histDay)histDaySet(histDay);else fetchDayPts();return;}
  if(histBusy)return;histBusy=true;
  api('/api/energy?range='+histRange).then(function(d){
    histBusy=false;histAgg=d;
    chartFit();redrawChart();
  }).catch(function(){histBusy=false;});
}
(function(){
  var hr=$('hrange');if(hr)hr.addEventListener('click',function(e){var b=e.target.closest('button');if(!b||!hr.contains(b))return;
    histRange=b.getAttribute('data-r');for(var i=0;i<hr.children.length;i++)hr.children[i].classList.toggle('active',hr.children[i]===b);
    $('hday').style.display=histRange==='day'?'':'none';fetchHistory();});
  var hm=$('hmode');if(hm)hm.addEventListener('click',function(e){var b=e.target.closest('button');if(!b||!hm.contains(b))return;
    histMode=b.getAttribute('data-m');for(var i=0;i<hm.children.length;i++)hm.children[i].classList.toggle('active',hm.children[i]===b);redrawChart();});
})();
var dailyData=null,dailyBusy=false;
function fetchDailyHistory(){
  if(dailyBusy)return;dailyBusy=true;
  api('/api/history_daily').then(function(d){
    dailyBusy=false;dailyData=d.days||[];
    populateYearSelect();
    drawDailyChart();
    drawHeatmap();
  }).catch(function(){dailyBusy=false;});
}
// Zwei Jahres-Auswahlen: eine beim Import-Diagramm (Einstellungen), eine bei der
// Jahresübersicht (Verlauf). Beide speisen sich aus derselben Datenquelle, sollen
// aber unabhängig bedienbar sein – der Verlauf-Tab braucht sonst einen Umweg
// über die Einstellungen, nur um das Jahr zu wechseln.
function populateYearSelect(){
  if(!dailyData)return;
  var years={};
  dailyData.forEach(function(r){years[Math.floor(r[0]/10000)]=1;});
  var yrs=Object.keys(years).sort();
  var opts=yrs.map(function(y){return '<option value="'+y+'">'+y+'</option>';}).join('');
  ['daily-year','hm-year'].forEach(function(id){
    var sel=$(id);if(!sel)return;
    var prev=sel.value;
    sel.innerHTML=opts;
    sel.value=(yrs.indexOf(prev)>=0)?prev:yrs[yrs.length-1];
  });
}
var DAILY_MONTH_NAMES=['Jan','Feb','Mär','Apr','Mai','Jun','Jul','Aug','Sep','Okt','Nov','Dez'].map(function(x){return T(x);});
function drawDailyChart(){
  var c=$('daily-chart');if(!c||!dailyData)return;
  var W=c.parentElement.clientWidth,H=260;
  c.width=W*devicePixelRatio;c.height=H*devicePixelRatio;
  c.style.width=W+'px';c.style.height=H+'px';
  var ctx=c.getContext('2d');ctx.setTransform(1,0,0,1,0,0);ctx.scale(devicePixelRatio,devicePixelRatio);
  ctx.clearRect(0,0,W,H);
  var margin={top:12,right:14,bottom:22,left:42};
  var ph=H-margin.bottom,cw=W-margin.left-margin.right,chH=ph-margin.top;
  var year=parseInt(($('daily-year')||{}).value||0,10);
  var rows=dailyData.filter(function(r){return Math.floor(r[0]/10000)===year;});
  if(!rows.length){
    ctx.fillStyle=cv('--ink3');ctx.font='12px '+CFONT;ctx.textAlign='center';
    ctx.fillText(dailyData.length?T('Keine Daten für {0}',[year||'?']):T('Noch keine Langzeit-Historie importiert'),W/2,H/2);
    updateDailySummary([]);return;
  }
  var maxV=0;rows.forEach(function(r){maxV=Math.max(maxV,r[1],r[2]);});
  if(maxV<1000)maxV=1000;
  maxV=Math.ceil(maxV/1000)*1000;
  ctx.strokeStyle=cv('--tint');ctx.lineWidth=1;ctx.fillStyle=cv('--ink3');ctx.font='9px '+CFONT;ctx.textAlign='right';
  var yStep=maxV/4;
  for(var v=0;v<=maxV;v+=yStep){
    var y=ph-(v/maxV*chH);
    ctx.beginPath();ctx.moveTo(margin.left,y);ctx.lineTo(W-margin.right,y);ctx.stroke();
    ctx.fillText((v/1000).toFixed(1)+'k',margin.left-5,y+3);
  }
  ctx.textAlign='center';ctx.fillStyle=cv('--ink3');
  for(var m=0;m<12;m++)ctx.fillText(DAILY_MONTH_NAMES[m],margin.left+(m+0.5)/12*cw,H-4);
  var n=rows.length,bw=Math.max(1,cw/n);
  for(var i=0;i<n;i++){
    var r=rows[i],x=margin.left+i*bw;
    var yp=ph-(Math.min(r[1],maxV)/maxV*chH),yc=ph-(Math.min(r[2],maxV)/maxV*chH);
    ctx.fillStyle='rgba(10,132,255,.65)';ctx.fillRect(x,yp,Math.max(1,bw*0.9),ph-yp);
    ctx.fillStyle='rgba(124,92,255,.55)';ctx.fillRect(x,yc,Math.max(1,bw*0.4),ph-yc);
  }
  updateDailySummary(rows);
  drawHeatmap();
}
function updateDailySummary(rows){
  var el=$('daily-summary');if(!el)return;
  if(!rows.length){el.innerHTML='';return;}
  var prod=0,cons=0,low=false;
  rows.forEach(function(r){prod+=r[1];cons+=r[2];if(r[5]&2)low=true;});
  var auto=cons>0?Math.max(0,Math.min(100,Math.round((1-Math.max(0,cons-prod)/cons)*100))):0;
  el.innerHTML=
    '<div class="stat"><small>'+T('Produktion')+'</small><span class="val csol tnum">'+(prod/1000).toFixed(0)+' kWh</span></div>'+
    '<div class="stat"><small>'+T('Verbrauch')+'</small><span class="val ccon tnum">'+(cons/1000).toFixed(0)+' kWh</span></div>'+
    '<div class="stat"><small>'+T('Autarkiegrad')+'</small><span class="val tnum">'+auto+'%</span></div>'
    +(low?'<div class="hint" style="grid-column:1/-1">'+T('Teilzeitraum mit geringerer Datenqualität (Inbetriebnahme/Umbau)')+'</div>':'');
}
// ── Jahres-Heatmap (Kalenderraster) ──────────────────────────────────────────
var HM_COL=['#e6ecf2','#b8e3d8','#6fcbb4','#2ea88a','#12705c'];
var HM_COL_DARK=['#232933','#1c4a44','#1f7a68','#28a888','#4ad6b0'];
var hmCells=[];
function hmPalette(){
  var dark=document.documentElement.getAttribute('data-theme');
  var isDark=dark?dark==='dark':(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches);
  return isDark?HM_COL_DARK:HM_COL;
}
function drawHeatmap(){
  var c=$('hm-chart'),box=$('card-hm');if(!c||!box)return;
  if(!dailyData||!dailyData.length){box.style.display='none';return;}
  var year=parseInt(($('hm-year')||{}).value||0,10);
  var rows=dailyData.filter(function(r){return Math.floor(r[0]/10000)===year;});
  if(!rows.length){box.style.display='none';return;}
  box.style.display='';

  var pal=hmPalette();
  for(var i=0;i<5;i++){var el=$('hm-l'+i);if(el)el.style.background=pal[i];}

  var byDay={};rows.forEach(function(r){byDay[r[0]]=r;});
  var maxV=0;rows.forEach(function(r){if(r[1]>maxV)maxV=r[1];});
  if(maxV<=0)maxV=1;

  // Erster Montag-Raster-Slot: Spalte = Kalenderwoche, Zeile = Wochentag (Mo oben)
  var jan1=new Date(year,0,1),startDow=(jan1.getDay()+6)%7;
  var days=((year%4===0&&year%100!==0)||year%400===0)?366:365;
  var cols=Math.ceil((startDow+days)/7);

  var W=Math.max(c.parentElement.clientWidth,cols*9+34),LEFT=26,TOP=14;
  var cell=Math.max(6,Math.floor((W-LEFT-4)/cols)),gap=Math.max(1,Math.floor(cell/7));
  var H=TOP+7*cell+6;
  c.width=W*devicePixelRatio;c.height=H*devicePixelRatio;
  c.style.width=W+'px';c.style.height=H+'px';
  var ctx=c.getContext('2d');ctx.setTransform(1,0,0,1,0,0);ctx.scale(devicePixelRatio,devicePixelRatio);
  ctx.clearRect(0,0,W,H);

  hmCells=[];
  ctx.font='8px '+CFONT;ctx.fillStyle=cv('--ink3');ctx.textAlign='right';
  ['Mo','Mi','Fr','So'].map(function(x){return T(x);}).forEach(function(n,k){
    var row=[0,2,4,6][k];
    ctx.fillText(n,LEFT-4,TOP+row*cell+cell*0.75);
  });
  ctx.textAlign='left';
  var lastMon=-1;
  for(var d=0;d<days;d++){
    var dt=new Date(year,0,1+d);
    var col=Math.floor((startDow+d)/7),row=(dt.getDay()+6)%7;
    var key=year*10000+(dt.getMonth()+1)*100+dt.getDate();
    var rec=byDay[key];
    var x=LEFT+col*cell,y=TOP+row*cell;
    var lvl=-1;
    if(rec){var f=rec[1]/maxV;lvl=f<=0.02?0:f<0.25?1:f<0.5?2:f<0.75?3:4;}
    ctx.fillStyle=(lvl<0)?cv('--tint2'):pal[lvl];
    ctx.fillRect(x,y,cell-gap,cell-gap);
    if(rec)hmCells.push({x:x,y:y,s:cell-gap,r:rec});
    if(dt.getDate()===1&&dt.getMonth()!==lastMon){
      lastMon=dt.getMonth();
      ctx.fillStyle=cv('--ink3');
      ctx.fillText(DAILY_MONTH_NAMES[lastMon],x,TOP-4);
    }
  }
  setTxt('hm-info',T('{0} Tage · Bestwert {1} kWh',[rows.length,(maxV/1000).toFixed(1)]));
}
(function(){
  var c=$('hm-chart');if(!c)return;
  function hover(e){
    var r=c.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top,hit=null;
    for(var i=0;i<hmCells.length;i++){var q=hmCells[i];
      if(x>=q.x&&x<q.x+q.s&&y>=q.y&&y<q.y+q.s){hit=q.r;break;}}
    if(!hit){setTxt('hm-hover','');return;}
    var ds=String(hit[0]),dd=ds.slice(6,8)+'.'+ds.slice(4,6)+'.'+ds.slice(0,4);
    setHtml('hm-hover','<b>'+dd+'</b>: '+T('{0} kWh erzeugt, {1} kWh verbraucht',[(hit[1]/1000).toFixed(1),(hit[2]/1000).toFixed(1)]));
  }
  var mv=rafPtr(hover);
  c.addEventListener('pointermove',mv,{passive:true});
  c.addEventListener('pointerdown',mv,{passive:true});
  c.addEventListener('pointerleave',function(){mv.cancel();setTxt('hm-hover','');});
  // Tipp auf einen Tag: dieser Tag oben im Tagesverlauf.
  c.addEventListener('click',function(e){
    var r=c.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
    for(var i=0;i<hmCells.length;i++){var q=hmCells[i];
      if(x>=q.x&&x<q.x+q.s&&y>=q.y&&y<q.y+q.s){
        var ds=String(q.r[0]);
        histDaySet(ds.slice(0,4)+'-'+ds.slice(4,6)+'-'+ds.slice(6,8));
        var top=$('page-chart');if(top)top.querySelector('.card').scrollIntoView({block:'start'});
        return;
      }}
  });
})();
// ── Kennzahlen zum gezeigten Zeitraum ────────────────────────────────────────
function renderKpis(){
  var el=$('h-kpis');if(!el)return;
  var p=0,c=0,fe=0,ie=0,i,ok=false;
  if(histRange==='day'){
    var vd=viewData(),pts=(vd&&vd.pts)||[];ok=pts.length>1;
    for(i=0;i<pts.length;i++){var q=ptH(pts,i),g=pts[i][3];p+=pts[i][1]*q;c+=pts[i][2]*q;if(g>0)ie+=g*q;else fe+=-g*q;}
  }else{
    var bk=(histAgg&&histAgg.buckets)||[];ok=bk.length>0;
    for(i=0;i<bk.length;i++){p+=bk[i].pe||0;c+=bk[i].ce||0;fe+=bk[i].fe||0;ie+=bk[i].ie||0;}
  }
  if(!ok||(p<=0&&c<=0)){el.innerHTML='';return;}
  var self=Math.max(0,p-fe);
  var k=[];
  function add(l,v,t){k.push('<div class="kpi" title="'+esc(T(t))+'"><small>'+T(l)+'</small><b>'+v+'</b></div>');}
  if(p>0)add('Eigenverbrauch',Math.round(self/p*100)+' %','Anteil der eigenen Produktion, der im Haus genutzt statt eingespeist wurde');
  if(c>0)add('Autarkie',Math.round(Math.max(0,1-ie/c)*100)+' %','Anteil des Verbrauchs, der nicht aus dem Netz kam');
  add('Selbst genutzt',(self/1000).toFixed(1)+' kWh','Eigene Produktion, die im Haus verbraucht wurde');
  add('Eingespeist',(fe/1000).toFixed(1)+' kWh','An das Netz abgegebene Energie');
  if(cfg.p_buy>0||cfg.p_feed>0){
    var save=self/1000*(cfg.p_buy||0)/100+fe/1000*(cfg.p_feed||0)/100;
    add('Ersparnis',save.toFixed(2)+' CHF','Eigenverbrauch zum Bezugspreis plus Einspeisevergütung');
  }
  el.innerHTML=k.join('');
}
// ── Simulation eines Tages ───────────────────────────────────────────────────
function simDefaults(c){
  var m={'sim-onm':'on_margin','sim-offm':'off_margin','sim-mon':'min_on_min','sim-moff':'min_off_min'};
  Object.keys(m).forEach(function(id){var el=$(id);if(el&&document.activeElement!==el)el.value=c[m[id]]!=null?c[m[id]]:'';});
}
function runSim(){
  var out=$('sim-res');if(!out)return;
  out.innerHTML='<div class="hint" style="margin-top:10px">'+T('Rechne…')+'</div>';
  var body={};
  [['sim-onm','on_margin'],['sim-offm','off_margin'],['sim-mon','min_on_min'],['sim-moff','min_off_min']].forEach(function(x){
    var v=parseInt($(x[0]).value,10);if(!isNaN(v))body[x[1]]=v;});
  var q=histDay?('?d='+histDay.replace(/-/g,'')):'';
  api('/api/simulate'+q,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(function(r){
    if(!r||!r.points){out.innerHTML='<div class="empty-note">'+T('Für diesen Tag liegen keine Messpunkte vor.')+'</div>';return;}
    if(!r.loads||!r.loads.length){out.innerHTML='<div class="empty-note">'+T('Keine Geräte mit Automatik und eingetragener Leistung – nichts zu simulieren.')+'</div>';return;}
    var d=String(r.day),t0=new Date(+d.slice(0,4),+d.slice(4,6)-1,+d.slice(6,8)).getTime()/1000,span=86400;
    var h='';
    r.loads.forEach(function(l){
      var bars=(l.on||[]).map(function(iv){
        var a=Math.max(0,(iv[0]-t0)/span*100),b=Math.min(100,(iv[1]-t0)/span*100);
        return'<i style="left:'+a.toFixed(2)+'%;width:'+Math.max(0.4,b-a).toFixed(2)+'%"></i>';}).join('');
      h+='<div class="sim-row"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(l.name||'')+'</span>'
        +'<span class="sim-bar" role="img" aria-label="'+esc(T('{0}: {1} kWh simuliert',[l.name||'',l.kwh]))+'">'+bars+'</span>'
        +'<span class="tnum">'+T('{0} kWh',[l.kwh.toFixed(1)])+'</span></div>';
    });
    h+='<div class="sim-axis"><span>0</span><span>6</span><span>12</span><span>18</span><span>24</span></div>';
    var more=Math.max(0,r.real.feed_kwh-r.sim.feed_kwh),imp=r.sim.import_kwh-r.real.import_kwh;
    h+='<div class="kpis sim-sum">'
      +'<div class="kpi"><small>'+T('Einspeisung real')+'</small><b>'+r.real.feed_kwh.toFixed(1)+' kWh</b></div>'
      +'<div class="kpi"><small>'+T('Einspeisung simuliert')+'</small><b>'+r.sim.feed_kwh.toFixed(1)+' kWh</b></div>'
      +'<div class="kpi"><small>'+T('Zusätzlich selbst genutzt')+'</small><b>'+more.toFixed(1)+' kWh</b></div>'
      +'<div class="kpi"><small>'+T('Netzbezug simuliert')+'</small><b>'+r.sim.import_kwh.toFixed(1)+' kWh</b></div>'
      +'</div><div class="hint" style="margin-top:8px">'+T('Grundlage: {0} Messpunkte à 15 min. Netzbezug gegenüber real: {1} kWh.',[r.points,(imp>=0?'+':'')+imp.toFixed(1)])+'</div>';
    out.innerHTML=h;
  }).catch(function(){out.innerHTML='<div class="empty-note">'+T('Fehler beim Abrufen')+'</div>';});
}
function uploadDailyCsv(){
  var inp=$('daily-file');if(!inp.files||!inp.files[0])return;
  var st=$('daily-upload-status');st.textContent=T('Lade hoch…');
  var fd=new FormData();fd.append('file',inp.files[0]);
  var xhr=new XMLHttpRequest();
  xhr.open('POST','/api/history_daily_import');
  xhr.upload.onprogress=function(e){if(e.lengthComputable)st.textContent=T('Lade hoch…')+' '+Math.round(e.loaded/e.total*100)+'%';};
  xhr.onload=function(){
    var msg=T('Fehler');
    try{msg=JSON.parse(xhr.responseText).msg||msg;}catch(e){}
    st.textContent=msg;
    if(xhr.status===200)fetchDailyHistory();
  };
  xhr.onerror=function(){st.textContent=T('Netzwerkfehler beim Hochladen');};
  xhr.send(fd);
  inp.value='';
}
