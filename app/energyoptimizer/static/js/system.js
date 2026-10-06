function doRestart(){
  if(confirm(T('EnergyOptimizer wirklich neu starten?')))
    api('/api/restart',{method:'POST'}).then(function(){setTimeout(function(){location.reload();},6000);});
}
function doLogout(){
  var go=function(){location.replace('/');};
  api('/api/logout',{method:'POST'}).then(go).catch(go);
}
function fmtSize(b){return b>=1073741824?(b/1073741824).toFixed(1)+' GB':b>=1048576?(b/1048576).toFixed(1)+' MB':Math.round(b/1024)+' kB';}
function dlLoadStats(){
  var el=$('dl-stats'),wl=$('dl-warn');if(!el)return;
  api('/api/history/stats').then(function(d){
    if(wl){
      var w='';
      if(d.werr)w=T('⚠ Letzter Schreibversuch fehlgeschlagen – Dateisystem voll oder defekt.');
      else if(d.fs_total&&d.fs_used/d.fs_total>0.8)w=T('⚠ Speicher zu {0} % belegt – Verlauf exportieren und löschen.',[Math.round(d.fs_used/d.fs_total*100)]);
      else if(d.max&&d.bytes>d.max*0.8)w=T('Hinweis: ab {0} wird der älteste Teil des Verlaufs automatisch verworfen. Vorher exportieren, wenn du ihn vollständig behalten willst.',[fmtSize(d.max)]);
      wl.textContent=w;wl.style.display=w?'block':'none';
    }
    if(!d.exists){el.textContent=T('Noch keine Daten aufgezeichnet.');return;}
    var since=d.first?new Date(d.first*1000).toLocaleDateString(LOC):'?';
    el.textContent=T('{0} seit {1} – Speicher: {2} / {3} belegt',[fmtSize(d.bytes),since,fmtSize(d.fs_used),fmtSize(d.fs_total)]);
  }).catch(function(){el.textContent=T('Nicht verfügbar.');});
}
function dlExport(){location.href='/api/history/export';}
function dlClear(){
  if(!confirm(T('Gesamten Langzeit-Verlauf wirklich unwiderruflich löschen? Vorher exportieren nicht vergessen!')))return;
  api('/api/history/clear',{method:'POST'}).then(function(){dlLoadStats();});
}
var saveOv=null;
function ovShow(ico,ttl,sub,pct,col){
  if(!saveOv)saveOv=$('save-ov');
  saveOv.style.display='flex';
  $('ov-ttl').innerHTML=ttl;
  $('ov-sub').innerHTML=sub||'';
  $('ov-ico').innerHTML=ico;
  var f=$('ov-fill');
  if(pct!==undefined){f.style.background=col||'var(--green)';setTimeout(function(){f.style.width=pct+'%';},40);}
}
function ovSpin(){return'<div class="spin-ring"></div>';}
function ovOk(){return'<div class="ov-ok"><svg viewBox="0 0 24 24"><use href="#i-check"/></svg></div>';}
$('sf').addEventListener('submit',function(e){
  e.preventDefault();
  $('msg-err').style.display='none';
  var data={};
  new FormData(this).forEach(function(v,k){data[k]=v;});
  // Nur Schalter, die in DIESEM Formular auch wirklich vorkommen, duerfen hier auf
  // 'false' ergaenzt werden – eine nicht angehakte Checkbox taucht in FormData sonst
  // gar nicht auf. Die Geraete-Schalter s0_auto..s3_auto standen hier ebenfalls, sind
  // aber laengst ins Geraete-Overlay gewandert: das Formular hat diese Felder nicht
  // mehr, also schickte jedes Speichern der Einstellungsseite s0_auto..s3_auto='false'
  // mit und schaltete die Automatik aller Steckdosen ab.
  ['mq_en','mq_disc','hb_en','sl_dev','auto_en'].forEach(function(k){data[k]=(k in data)?'true':'false';});
  // Eingabe in Franken, API und Gerät rechnen in ganzen Rappen
  ['p_buy','p_feed','p_base'].forEach(function(k){if(k in data)data[k]=Math.round((parseFloat(data[k])||0)*100);});
  ovShow(ovSpin(),T('Speichern…'),'',0);
  api('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
  .then(function(d){
    if(d.ok&&!d.reboot){
      cfg.loaded=false;setDirty(false);
      if(data.lang&&data.lang!==(window.LANG||'de')){location.reload();return;}
      // Abgewiesene Felder wie einen Fehler stehen lassen (nicht im Overlay, das
      // nach 1,2 s verschwindet): der Rest ist gespeichert, dieses Feld aber nicht.
      if(d.warn){
        saveOv.style.display='none';
        var w=$('msg-err');w.textContent=d.warn;w.style.display='block';
      }else{
        ovShow(ovOk(),T('Gespeichert!'),T('Einstellungen übernommen'),100,'var(--green)');
        // Erledigte Kategorie zuklappen und zur Uebersichtsliste zurueck: sonst
        // bleibt nach dem Speichern dieselbe Unterseite offen stehen und es sieht
        // aus, als sei nichts passiert. Nur im sauberen Fall – wurde ein Feld
        // abgewiesen (d.warn), bleibt die Pane offen, damit man es korrigieren kann.
        setTimeout(function(){saveOv.style.display='none';setBack();},1200);
      }
    }else if(d.ok){
      var t=5;
      ovShow(ovOk(),T('Gespeichert!'),T('Neustart in {0}s…',[t]),100,'var(--green)');
      var ti=setInterval(function(){t--;
        if(t>0){$('ov-sub').innerHTML=T('Neustart in {0}s…',[t]);}
        else{clearInterval(ti);
          $('ov-fill').style.width='0%';
          ovShow(ovSpin(),T('Neustart läuft…'),T('Warte auf Verbindung…'),0,'var(--blue)');
          setTimeout(function(){$('ov-fill').style.width='90%';},80);
          var tries=0,pi=setInterval(function(){
            api('/api/status').then(function(){clearInterval(pi);location.reload();})
            .catch(function(){if(++tries>35){clearInterval(pi);location.reload();}});
          },1000);
        }
      },1000);
    }else{
      saveOv.style.display='none';
      var err=$('msg-err');err.textContent=T('Fehler: {0}',[d.msg]);err.style.display='block';
    }
  }).catch(function(ex){
    saveOv.style.display='none';
    var err=$('msg-err');err.textContent=T('Netzwerkfehler: {0}',[ex]);err.style.display='block';
  });
});
var scanTmr=null;
function startScan(){
  $('scan-st').textContent=T('Suche läuft… (ca. 15-30 s)');
  $('scan-res').innerHTML='';
  api('/api/discover',{method:'POST'}).then(function(){
    if(scanTmr)clearInterval(scanTmr);
    scanTmr=setInterval(pollScan,2000);
  });
}
function pollScan(){
  api('/api/discover').then(function(d){
    var st=$('scan-st');
    if(d.done){st.innerHTML=T('{0} Gerät(e) gefunden',[d.count]);clearInterval(scanTmr);}
    else if(d.running){st.textContent=T('Suche läuft…');}
    else{st.textContent=T('Warte auf Scan-Start…');}
    var h='';
    (d.devices||[]).forEach(function(dev){
      h+='<div class="scan-dev"><div><div class="nm">'+esc(dev.name)+'</div>'
        +'<div class="meta">'+esc(dev.ip)+'  '+esc(dev.model)+'</div></div>'
        +'<button type="button" class="btn bscan" style="padding:6px 12px;font-size:.78rem"'
        +' data-ip="'+esc(dev.ip)+'" data-name="'+esc(dev.name)+'" data-id="'+esc(dev.id||'')+'">'
        +'<svg><use href="#i-plus"/></svg>&Uuml;bernehmen</button></div>';
    });
    $('scan-res').innerHTML=h;
  });
}
// Delegierter Listener statt inline onclick mit Gerätedaten: die Werte kommen
// von Shelly-Geräten im Netzwerk (Name/ID nicht vertrauenswürdig) – eingebettet
// in ein onclick-Attribut könnten sie aus dem JS-String-Kontext ausbrechen,
// selbst mit HTML-Escaping (das schützt nur den Attribut-, nicht den JS-Kontext).
$('scan-res').addEventListener('click',function(e){
  var b=e.target.closest('.bscan[data-ip]');
  if(b)addDev(b.dataset.ip,b.dataset.name,b.dataset.id);
});
function addDev(ip,name,id){
  var idx=Math.min(lastShellyCount,((cfg&&cfg.max_sh)||16)-1);
  openDevOv(idx,{name:name,ip:ip,id:id,pw:0,pri:idx+1,rt:0,rw:0,io:0,auto:false});
  $('scan-st').textContent='\u2713 '+T('{0} → Steckdose {1} – bitte prüfen & speichern',[name,idx+1]);
}
// ── Sonnenstand, Diagnose ────────────────────────────────────────────────────
// Sonnenhöhe im Browser rechnen (gleiches Verfahren wie sun.h auf dem Gerät) –
// so sieht man beim Eintragen der Koordinaten sofort, ob sie plausibel sind.
function sunElev(lat,lon){
  var D2R=Math.PI/180,n=Date.now()/86400000+2440587.5-2451545.0;
  var L=(280.460+0.9856474*n)%360,g=((357.528+0.9856003*n)%360)*D2R;
  var lam=(L+1.915*Math.sin(g)+0.020*Math.sin(2*g))*D2R,eps=(23.439-0.0000004*n)*D2R;
  var dec=Math.asin(Math.sin(eps)*Math.sin(lam));
  var ra=Math.atan2(Math.cos(eps)*Math.sin(lam),Math.cos(lam));
  var gmst=(18.697374558+24.06570982441908*n)%24;if(gmst<0)gmst+=24;
  var ha=((gmst*15+lon)-ra*180/Math.PI)*D2R,la=lat*D2R;
  return Math.asin(Math.max(-1,Math.min(1,Math.sin(la)*Math.sin(dec)+Math.cos(la)*Math.cos(dec)*Math.cos(ha))))*180/Math.PI;
}
function updateSunHint(){
  var la=parseFloat(($('f-lat')||{}).value),lo=parseFloat(($('f-lon')||{}).value);
  if(isNaN(la)||isNaN(lo)){setTxt('sun-now','');return;}
  var e=sunElev(la,lo);
  setHtml('sun-now',T(e>=10?'Sonnenhöhe jetzt: <b>{0}°</b> – der Wächter würde jetzt greifen.':'Sonnenhöhe jetzt: <b>{0}°</b> – unter 10°, der Wächter ruht.',[e.toFixed(0)]));
}
(function(){['f-lat','f-lon'].forEach(function(id){var e=$(id);if(e)e.addEventListener('input',updateSunHint);});})();
function loadRaw(){
  setTxt('raw-main','Lade…');setTxt('raw-dev','Lade…');
  api('/api/solarlog/raw').then(function(d){
    setTxt('raw-main-t',T('Letzte Leistungsabfrage ({0})',[d.src||'Solar-Log']));
    setTxt('raw-main',d.main||T('(noch keine Antwort empfangen)'));
    setTxt('raw-dev', d.dev ||T('(noch keine Geräteabfrage gelaufen)'));
    var devs=d.devs||[],h='';
    if(devs.length){
      h='<table class="devtab"><tr><th>#</th><th>Ger&auml;t</th><th>Status</th><th style="text-align:right">Leistung</th><th style="text-align:right">bisher max.</th></tr>';
      devs.forEach(function(x){
        h+='<tr><td>'+(x.i+1)+'</td><td>'+esc(x.name||'&ndash;')+'</td>'
          +'<td>'+esc(x.st||'&ndash;')+'</td>'
          +'<td class="n">'+(x.w!=null?(x.w+' W'):'&ndash;')+'</td>'
          +'<td class="n">'+(x.max?(x.max+' W'):'&ndash;')+'</td></tr>';
      });
      h+='</table><div class="hint" style="margin-top:6px">'+T('Abfrage vor {0}. Der Status ist herstellerabhängiger Klartext und wird nur angezeigt. Als ausgefallen gilt nur eine Position, die <b>schon einmal produziert hat</b> (Spalte „bisher max.“) und jetzt bei 0 W steht, während andere liefern – Objekt 782 enthält auch Zähler.',[d.age>=0?d.age+' s':'–'])+'</div>';
    }
    setHtml('raw-devs',h);
  }).catch(function(){setTxt('raw-main',T('Fehler beim Abrufen'));});
}
// ── Selbsttest ───────────────────────────────────────────────────────────────
var ST_ICO=['&#10003;','!','&#10005;','&ndash;'];
var stTimer=null;
function renderSelftest(d){
  var el=$('st-list');if(!el)return;
  if(d.running){el.innerHTML='<div class="hint">'+T('Test läuft… (SolarLog und jede Steckdose werden wirklich abgefragt)')+'</div>';return;}
  var it=(d&&d.items)||[];
  if(!it.length){el.innerHTML='';return;}
  var bad=it.filter(function(x){return x.s===2;}).length,warn=it.filter(function(x){return x.s===1;}).length;
  var head='<div class="hint" style="margin-bottom:6px">'
    +(bad?('<b style="color:var(--red)">'+T('{0} Fehler',[bad])+'</b>'):'<b style="color:var(--green)">'+T('Kein Fehler')+'</b>')
    +(warn?(' &middot; '+T('{0} Hinweis(e)',[warn])):'')+' &middot; '+(d.age>=0?T('vor {0}',[d.age+' s']):'&ndash;')+'</div>';
  el.innerHTML=head+it.map(function(x){
    return '<div class="st-row"><div class="st-i s'+x.s+'">'+ST_ICO[x.s]+'</div>'
      +'<div><div class="st-n">'+esc(T(x.n))+'</div><div class="st-d">'+esc(T(x.d))+'</div></div></div>';
  }).join('');
}
function pollSelftest(){
  api('/api/selftest').then(function(d){
    renderSelftest(d);
    if(!d.running&&stTimer){clearInterval(stTimer);stTimer=null;}
  }).catch(function(){});
}
function runSelftest(){
  setHtml('st-list','<div class="hint">'+T('Test wird gestartet…')+'</div>');
  api('/api/selftest',{method:'POST'}).then(function(){
    if(stTimer)clearInterval(stTimer);
    stTimer=setInterval(pollSelftest,1500);
    setTimeout(pollSelftest,1500);
  }).catch(function(){setHtml('st-list','<div class="hint">'+T('Start fehlgeschlagen')+'</div>');});
}
// ── Konfigurations-Sicherung ─────────────────────────────────────────────────
function cfgExport(){
  setTxt('cfg-msg','');
  location.href='/api/config/export';
}
function cfgImport(){
  var inp=$('cfg-file');if(!inp.files||!inp.files[0])return;
  var f=inp.files[0];inp.value='';
  if(!confirm(T('Einstellungen aus "{0}" übernehmen? Die aktuellen Werte werden überschrieben.',[f.name])))return;
  setTxt('cfg-msg',T('Lese Datei…'));
  var r=new FileReader();
  r.onload=function(){
    api('/api/config/import',{method:'POST',headers:{'Content-Type':'application/json'},body:r.result})
    .then(function(d){
      setTxt('cfg-msg',(d&&d.msg)||'Unbekannte Antwort');
      if(d&&d.ok){cfg.loaded=false;poll();}
    }).catch(function(){setTxt('cfg-msg',T('Fehler beim Import'));});
  };
  r.onerror=function(){setTxt('cfg-msg',T('Datei konnte nicht gelesen werden'));};
  r.readAsText(f);
}
function fmtMb(b){return b>=1073741824?(b/1073741824).toFixed(1)+' GB':Math.round(b/1048576)+' MB';}
function barCol(p){return p<60?'var(--green)':p<80?'var(--orange)':'var(--red)';}
function pollSys(){
  api('/api/sysinfo').then(function(d){
    var pct=Math.round((d.mem_total-d.mem_free)*100/d.mem_total);
    var mb=$('sys-mem-bar');mb.style.width=pct+'%';mb.style.background=barCol(pct);
    $('sys-mem-txt').textContent=T('{0} frei / {1}',[fmtMb(d.mem_free),fmtMb(d.mem_total)]);
    $('sys-cpu').innerHTML=d.cpu+'<span class="unit">%</span>';
    $('sys-proc').innerHTML=d.proc+'<span class="unit">%</span>';
    $('sys-temp').innerHTML=d.temp?d.temp.toFixed(1)+'<span class="unit">&deg;C</span>':'&ndash;';
    var u=d.uptime,dd=Math.floor(u/86400),h=Math.floor((u%86400)/3600),m=Math.floor((u%3600)/60);
    $('sys-uptime').textContent=dd>0?dd+'d '+h+'h':(h>0?h+'h '+m+'m':m+'m '+(u%60)+'s');
    setTxt('sys-boots',d.boots!=null?String(d.boots):'--');
    setTxt('sys-ver',d.version||'--');setTxt('about-ver',d.version||'--');setTxt('about-build',(d.build||'--').slice(0,7));
    if(d.disk_total){
      var dp=Math.round((d.disk_total-d.disk_free)*100/d.disk_total);
      var db=$('sys-disk-bar');db.style.width=dp+'%';db.style.background=dp<75?'var(--green)':dp<90?'var(--orange)':'var(--red)';
      setTxt('sys-disk-txt',T('{0} frei / {1}',[fmtMb(d.disk_free),fmtMb(d.disk_total)]));
    }
    var pt=d.port&&d.port!==80?':'+d.port:'';
    setHtml('sys-net',T('Adresse:')+' '+(d.mdns?'<b>http://'+esc(d.mdns)+pt+'</b> &middot; ':'')+'IP '+esc(d.ip)+pt
      +(d.mdns_err?'<br><span style="color:var(--orange)">&#9888; '+esc(d.mdns_err)+'</span>':''));
    var se=$('sys-save-err');if(se)se.style.display=d.save_err?'block':'none';
    var up=d.update;
    if(up){
      var t=T('Installiert:')+' <b>'+esc(up.current)+'</b>';
      if(!up.enabled)t+=' &middot; '+T('Update-Prüfung ausgeschaltet');
      else if(up.available)t+=' &middot; <span style="color:var(--blue)">'+T('Neu:')+' <b>'+esc(up.latest)+'</b></span>'
        +(up.url?' &middot; <a href="'+esc(up.url)+'" target="_blank" rel="noopener">'+T('Was ist neu?')+'</a>':'');
      else if(up.age<0)t+=' &middot; '+T('noch nicht geprüft');
      else if(up.error)t+=' &middot; '+T('Prüfung fehlgeschlagen');
      else t+=' &middot; '+T('aktuell');
      setHtml('upd-state',t);
      var ub=$('upd-btn');if(ub)ub.style.display=up.enabled?'':'none';
      var can=updInstallable(up),ui=$('upd-install');
      if(ui)ui.style.display=can?'':'none';
      var um=$('upd-manual');if(um)um.style.display=up.available&&!can?'block':'none';
      var nw=$('set-upd-new');if(nw)nw.style.display=up.available?'':'none';
    }
    var n=d.heartbeat;
    if(n){
      setHtml('hb-state',n.hb_age<0?T('Noch kein Lebenszeichen gesendet.')
        :(T('Letztes Lebenszeichen {0}',[fmtAge(n.hb_age)])
          +(n.hb_ok?'':' &ndash; <b style="color:var(--red)">'+T('fehlgeschlagen')+'</b>')));
    }
  }).catch(function(){});
}

// ── Angemeldete Geräte ───────────────────────────────────────────────────────
function loadSessions(){
  var el=$('sess-list');if(!el)return;
  api('/api/sessions').then(function(d){
    var list=(d&&d.sessions)||[];
    if(!list.length){el.innerHTML='<div class="empty">'+T('Keine Anmeldungen gespeichert')+'</div>';return;}
    el.innerHTML=list.map(function(x){
      var seen=new Date(x.seen*1000).toLocaleString(LOC,{dateStyle:'medium',timeStyle:'short'});
      return'<div class="sess"><div class="sess-t"><b>'+esc(x.ua||T('Browser'))+(x.current?' &middot; '+T('dieses Gerät'):'')+'</b>'
        +'<span>'+T('zuletzt aktiv {0}',[seen])+'</span></div>'
        +(x.current?'':'<button type="button" class="tchip" data-sid="'+esc(x.id)+'">'+T('Abmelden')+'</button>')+'</div>';
    }).join('');
  }).catch(function(){el.innerHTML='<div class="empty">'+T('Fehler beim Abrufen')+'</div>';});
}
(function(){var el=$('sess-list');if(el)el.addEventListener('click',function(e){
  var b=e.target.closest('[data-sid]');if(!b)return;
  api('/api/sessions/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:b.dataset.sid})}).then(loadSessions);
});})();
function sessRevokeAll(){
  if(!confirm(T('Alle anderen Geräte abmelden?')))return;
  api('/api/sessions/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({all:true})}).then(loadSessions);
}
