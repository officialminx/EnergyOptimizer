// ── Alarmzentrale ────────────────────────────────────────────────────────────
// Die Liste wird nicht im 1-Sekunden-Poll mitgeschleppt: /api/status liefert nur
// Anzahl und Schweregrad, die Details holt das UI erst, wenn sich daran etwas
// ändert (oder man das Dashboard öffnet).
var alSig=null,alBusy=false;
function alarmSince(a){
  if(a.since){var d=new Date(a.since*1000),p=function(v){return(v<10?'0':'')+v;};
    var t=p(d.getHours())+':'+p(d.getMinutes());
    if(d.toDateString()!==new Date().toDateString())t=p(d.getDate())+'.'+p(d.getMonth()+1)+'. '+t;
    return T('seit {0}',[t]);}
  return T('seit {0}',[(a.mins||0)+' min']);
}
function renderAlarms(d){
  var box=$('alarms');if(!box)return;
  var list=(d&&d.al)||[];
  if(!list.length){box.style.display='none';box.innerHTML='';return;}
  var h='';
  list.forEach(function(a){
    h+='<div class="alarm s'+a.sev+(a.acked?' ack':'')+'">'
      +'<div class="alarm-i"><svg><use href="#'+(a.sev>=2?'i-warn':'i-warn')+'"/></svg></div>'
      +'<div class="alarm-b"><div class="alarm-t">'+esc(T(a.name))+'</div>'
      +'<div class="alarm-d">'+esc(T(a.detail||''))+' &middot; '+alarmSince(a)+'</div></div>'
      +(a.acked?'':'<button type="button" class="alarm-ack" onclick="ackAlarm('+a.id+')">'+T('Quittieren')+'</button>')
      +'</div>';
  });
  box.innerHTML=h;box.style.display='block';
}
function fetchAlarms(){
  if(alBusy)return;alBusy=true;
  api('/api/alarms').then(function(d){alBusy=false;renderAlarms(d);}).catch(function(){alBusy=false;});
}
function ackAlarm(id){
  api('/api/alarms/ack?id='+id,{method:'POST'}).then(function(){alSig=null;fetchAlarms();}).catch(function(){});
}
// Aus poll(): nur bei Änderung nachladen, damit der Sekundentakt schlank bleibt
function syncAlarms(d){
  var n=d.al_n||0,sev=(d.al_sev==null?-1:d.al_sev),sig=n+'/'+sev;
  var b=$('nav-al');
  if(b){
    if(n>0){b.style.display='grid';b.textContent=n>9?'9+':String(n);
      b.style.background=(sev>=2?'var(--red)':'var(--orange)');}
    else b.style.display='none';
  }
  if(sig!==alSig){alSig=sig;fetchAlarms();}
}
// ── Ereignisprotokoll ────────────────────────────────────────────────────────
var EV_RST={0:'unbekannt',1:'Kaltstart (Stromzufuhr)',2:'externer Reset',3:'Software-Neustart',
  4:'Absturz (Ausnahme/Panic)',5:'Interrupt-Watchdog',6:'Task-Watchdog',7:'Watchdog',
  8:'Deep-Sleep',9:'Unterspannung (Brownout)',10:'SDIO',11:'USB',12:'JTAG',13:'eFuse-Fehler',
  14:'Spannungseinbruch',15:'CPU blockiert'};
var EV_R={1:'Überschuss reichte',2:'Defizit über Ausschalt-Puffer',3:'manuell',
  4:'über MQTT',5:'Schlechtwetter-Nachlauf',6:'Nachlauf beendet',7:'Fail-Safe',
  8:'Befehl fehlgeschlagen',9:'ausserhalb des Freigabefensters',10:'Befristung abgelaufen',
  11:'Batterie-Vorrang – Speicher entlädt',
  12:'Gerät zeigt keinen Bedarf (Eigenregelung)',18:'Zeitschaltuhr',
  19:'Tageslimit erreicht'};
function evR(rs){return EV_R[rs]?esc(T(EV_R[rs])):'';}
function evTime(ep,up){
  if(!ep)return '+'+(up<3600?Math.round(up/60)+'m':Math.round(up/3600)+'h');
  var d=new Date(ep*1000),p=function(v){return (v<10?'0':'')+v;};
  var t=p(d.getHours())+':'+p(d.getMinutes());
  if(d.toDateString()===new Date().toDateString())return t;
  return p(d.getDate())+'.'+p(d.getMonth()+1)+'. '+t;
}
function evName(tx,dev){return tx?esc(tx):(dev>=0?T('Steckdose {0}',[dev+1]):T('System'));}
function evRow(e){
  var ep=e[0],up=e[1],ty=e[2],dev=e[3],rs=e[4],fl=e[5],sp=e[6],tx=e[7]||'';
  var on=(fl&1)!==0,hasSp=(fl&2)!==0,nm=evName(tx,dev);
  var ic='i-bolt',cls='',t='',m='';
  if(ty===2){                                   // Schaltung
    ic='i-power';
    if(rs===8){cls='err';t=T('{0}: Schaltbefehl fehlgeschlagen',[nm]);}
    else{cls=on?'on':'off';t=nm+' '+T(on?'EIN':'AUS');}
    m=evR(rs);
    if(hasSp)m+=(m?' &middot; ':'')+T('Überschuss')+' '+(sp>=0?'+':'&minus;')+wInt(Math.abs(sp));
  }else if(ty===3){                             // Erreichbarkeit
    ic='i-wifi';cls=on?'on':'err';
    t=T(on?'{0} wieder erreichbar':'{0} nicht erreichbar',[nm]);
    m=on?'':T('Mehrere Abfragen in Folge ohne Antwort');
  }else if(ty===4){                             // Automatik-Schalter
    ic='i-sliders';cls=on?'on':'off';
    t=T(on?'{0}: Automatik ein':'{0}: Automatik aus',[nm]);m=evR(rs);
  }else if(ty===5){                             // SolarLog
    ic='i-server';cls=on?'on':'err';
    t=T(on?'SolarLog wieder erreichbar':'SolarLog antwortet nicht');m=esc(T(tx));
  }else if(ty===6){                             // Fail-Safe
    ic='i-warn';cls=on?'err':'on';
    t=T(on?'Fail-Safe ausgelöst – Automatik abgeschaltet':'Fail-Safe aufgehoben');m=esc(T(tx));
  }else if(ty===7){                             // IP-Wechsel
    ic='i-refresh';cls='warn';
    t=T('Steckdose {0}: neue IP {1}',[dev+1,esc(tx)]);m=T('per Geräte-ID wiedergefunden');
  }else if(ty===8){                             // Einstellungen
    ic='i-save';t=T('Einstellungen gespeichert');m=evR(rs);
  }else if(ty===1){                             // Neustart
    ic='i-chip';cls=on?'warn':'';
    t=T('Neustart')+' &ndash; '+esc(T(EV_RST[rs]||'unbekannt'));m=esc(T(tx));
  }else if(ty===9){                             // Fehler
    ic='i-warn';cls='err';t=T('Systemfehler');m=esc(T(tx));
  }else if(ty===10){                            // Störung der Alarmzentrale
    ic='i-warn';cls=on?'err':'on';
    t=esc(T(tx))+(on?'':' &ndash; '+T('behoben'));
    m=T(on?'Störung erkannt':'Entwarnung');
  }else if(ty===11){                            // Fehlanmeldungen
    ic='i-warn';cls='err';t=T('Wiederholte Fehlanmeldungen');m=esc(T(tx));
  }else if(ty===12){                            // Eigenregelung: Betrieb erkannt/beendet
    ic='i-info';cls=on?'on':'off';
    t=T(on?'{0} läuft an':'{0} läuft nicht mehr',[nm]);
    m=T('am gemessenen Verbrauch der Steckdose erkannt');
  }else{t=T('Ereignis {0}',[ty]);m=esc(T(tx));}
  return '<div class="ev"><div class="ev-i '+cls+'"><svg><use href="#'+ic+'"/></svg></div>'
    +'<div class="ev-b"><div class="ev-t">'+t+'</div>'+(m?'<div class="ev-m">'+m+'</div>':'')+'</div>'
    +'<div class="ev-ts">'+evTime(ep,up)+'</div></div>';
}
var evBusy=false;
function fetchEvents(){
  if(evBusy)return;evBusy=true;
  var f=($('ev-filter')||{}).value||'';
  api('/api/events?n=120'+(f!==''?'&dev='+encodeURIComponent(f):'')).then(function(d){
    evBusy=false;
    var rows=(d&&d.ev)||[];
    setHtml('ev-list',rows.length?rows.map(evRow).join('')
      :'<div class="hint" style="padding:14px 0;text-align:center">'+T('Noch keine Ereignisse aufgezeichnet.')+'</div>');
    setTxt('ev-info',(d&&d.n)?T('{0} gespeichert',[d.n]):'');
  }).catch(function(){evBusy=false;});
}
function evExport(){location.href='/api/events/export';}
function evClear(){
  if(!confirm(T('Ereignisprotokoll unwiderruflich löschen?')))return;
  api('/api/events/clear',{method:'POST'}).then(fetchEvents).catch(function(){});
}
// Filter-Auswahl an die aktuell eingerichteten Steckdosen anpassen
function syncEvFilter(list){
  var sel=$('ev-filter');if(!sel)return;
  var sig=list.map(function(s){return s.idx+':'+s.name;}).join('|');
  if(sel.getAttribute('data-sig')===sig)return;
  sel.setAttribute('data-sig',sig);
  var prev=sel.value,h='<option value="">Alle Ereignisse</option><option value="sys">Nur System</option>';
  list.forEach(function(s){h+='<option value="'+s.idx+'">'+esc(s.name)+'</option>';});
  sel.innerHTML=h;sel.value=prev;
}
