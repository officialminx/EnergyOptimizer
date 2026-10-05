var pollFails=0;
// Hinweis auf eine neue Version. "Später" blendet ihn nur für genau diese Version aus.
function updDismissed(v){try{return localStorage.getItem('upd-dismiss')===v;}catch(e){return false;}}
function updDismiss(){
  var u=lastSt&&lastSt.update;if(!u)return;
  try{localStorage.setItem('upd-dismiss',u.latest);}catch(e){}
  $('upd-banner').style.display='none';
}
function updCheck(){
  var b=$('upd-btn');if(!b||b.disabled)return;
  var old=b.textContent,res=$('upd-res');
  b.disabled=true;b.textContent=T('Suche läuft …');if(res)res.textContent='';
  api('/api/update/check',{method:'POST'}).then(function(r){
    var u=r&&r.update;
    if(u&&lastSt){lastSt.update=u;updateBanner(u);}
    if(res)res.textContent=!u||u.error?T('Prüfung fehlgeschlagen')
      :u.available?T('Version {0} ist verfügbar',[u.latest]):T('Du hast die neueste Version');
    poll();
  }).catch(function(){if(res)res.textContent=T('Prüfung fehlgeschlagen');})
  .then(function(){b.disabled=false;b.textContent=old;});
}
function updateBanner(u){
  var b=$('upd-banner');if(!b)return;
  var show=!!(u&&u.available&&!updDismissed(u.latest));
  if(show){setTxt('upd-t',T('Version {0} ist verfügbar',[u.latest]));$('upd-link').href=u.url||'#';}
  b.style.display=show?'flex':'none';
}
function poll(){
  api('/api/status').then(function(d){
    pollFails=0;lastSt=d;
    $('conn-lost-ov').style.display='none';
    // Keine Vorzeichen als Bedeutungsträger: die Richtung steht in Worten darunter.
    if(d.has_prod===false){setHtml('sp','&ndash;');setTxt('sp-dir',T('nicht gemessen'));}
    else{tweenW('sp',d.prod,false);setTxt('sp-dir','');}
    $('sp').classList.add('csol');
    tweenW('sc',d.cons,false);$('sc').classList.add('ccon');
    var sg=$('sg'),TH=15;tweenW('sg',Math.abs(d.grid),false);
    sg.className='val tnum '+(d.grid>TH?'cgn':d.grid<-TH?'cgp':'');
    setTxt('sg-dir',d.grid>TH?T('Bezug'):d.grid<-TH?T('Einspeisung'):T('ausgeglichen'));
    $('ts').textContent=new Date().toLocaleTimeString(LOC);
    updateSlAge(d.sl_age);
    updateAvgChip(d.avg_s);
    var surp=d.has_prod===false?-d.grid:d.prod-d.cons;
    setTxt('ssurp-l',surp>=0?T('Überschuss'):T('Defizit'));
    var sv=$('ssurp'),st2=kw(Math.abs(surp))+' kW';
    if(sv.textContent!==st2){sv.textContent=st2;setFlash(sv);}
    sv.className='surplus-val tnum '+(surp>=0?'cgp':'cgn');
    var bp=d.prod>0?Math.min(100,Math.round(Math.abs(surp)/d.prod*100)):0;
    var bar=$('ssurp-bar');bar.style.width=bp+'%';bar.className='surplus-fill '+(surp>=0?'pos':'neg');
    updateFlow(d.prod,d.cons,d.grid,d.batt,d.soc);
    updateEnergy(d.energy);
    updateCost(d.cost);
    renderShelly(d.shelly||[]);
    renderExt(d.ext||[]);
    renderOptimizer(d);
    var db=$('dry-banner');if(db)db.style.display=d.dry?'flex':'none';
    updateStatusLine(d);
    syncAlarms(d);
    updateBanner(d.update);
    updateKiosk(d);
    var lm={connected:{cls:'green'},wifi_connecting:{cls:'blue'},disconnected:{cls:'red'},scan:{cls:'blue'},off:{cls:'off'}};
    var ls=lm[d.led]||lm.off;
    $('conn-dot').className='dot '+ls.cls;
    // Klasse und Text statt outerHTML: das Ersetzen des Elements kostete jeden
    // Sekunden-Poll einen Neuaufbau samt Layout, obwohl sich fast nie etwas aendert.
    var mqb=$('mq-badge');
    if(mqb){
      var mqc='badge '+(d.mqtt&&d.mqtt.conn?'b-on':'b-off');
      if(mqb.className!==mqc)mqb.className=mqc;
      setTxt('mq-badge',d.mqtt&&d.mqtt.conn?'Verbunden':'Getrennt');
    }
    if(!cfg.loaded)loadCfg(d.cfg);
  }).catch(function(){
    pollFails++;
    if(pollFails>=3)$('conn-lost-ov').style.display='flex';
  });
}
// Die Hysterese ist als Dauer eingestellt, gezaehlt wird aber in Abfragen - die
// Bewertung laeuft nur nach einer SolarLog-Abfrage. Der Hinweis rechnet beides
// gegeneinander auf (gleiche Aufrundung wie hyst_ticks() im Server), damit
// sichtbar ist, was ein geaendertes Intervall aus der Einstellung macht.
function hystTicks(secs,pollS){
  var ps=Math.max(1,pollS||30);
  return Math.min(720,Math.max(1,Math.ceil((secs||0)/ps)));
}
function updateHystHint(){
  var el=$('hyst-hint');if(!el)return;
  var ps=parseInt($('f-src-poll').value,10)||30;
  var on=parseInt($('f-hon').value,10)||0,off=parseInt($('f-hoff').value,10)||0;
  var tOn=hystTicks(on,ps),tOff=hystTicks(off,ps);
  el.innerHTML=T('So lange muss der Überschuss (bzw. das Defizit) anhalten, bevor geschaltet wird. Gezählt wird in Abfragen – bei <b>{0} s</b> Intervall sind das <b>{1}</b> Messung(en) zum Einschalten und <b>{2}</b> zum Abschalten. Als Dauer bleibt die Einstellung erhalten, wenn du das Intervall änderst.',[ps,tOn,tOff]);
}
