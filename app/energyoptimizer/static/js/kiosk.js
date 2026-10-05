// ── Kiosk-Ansicht ────────────────────────────────────────────────────────────
// Wandanzeige ohne Bedienelemente: grosse Zahlen, aus der Entfernung lesbar.
// Über die Adresse …/#kiosk direkt erreichbar, damit ein Tablet ohne Umweg über
// das Dashboard startet, und mit Wake Lock, damit der Bildschirm anbleibt.
var kioskWL=null;
var KWD=['Sonntag','Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag'].map(function(x){return T(x);});
var KMO=['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'].map(function(x){return T(x);});
function kioskLock(){
  if(navigator.wakeLock&&navigator.wakeLock.request)
    navigator.wakeLock.request('screen').then(function(w){kioskWL=w;}).catch(function(){});
}
function kioskActive(){var el=$('kiosk');return !!el&&el.className==='on';}
function kioskSet(on){
  var el=$('kiosk');if(!el)return;
  el.className=on?'on':'';
  if(on){kioskLock();if(lastSt)updateKiosk(lastSt);}
  else if(kioskWL){try{kioskWL.release();}catch(e){}kioskWL=null;}
}
function kioskOn(){kioskSet(true);try{location.hash='kiosk';}catch(e){}}
function kioskOff(){
  kioskSet(false);
  try{
    if(history.replaceState)history.replaceState(null,'',location.pathname+location.search);
    else location.hash='';
  }catch(e){}
}
function updateKiosk(d){
  if(!kioskActive())return;
  var now=new Date(),e=d.energy||{};
  setTxt('k-clock',hhmm(now));
  setTxt('k-date',T('{0}, {1}. {2} {3}',[KWD[now.getDay()],now.getDate(),KMO[now.getMonth()],now.getFullYear()]));
  setTxt('k-prod',wInt(d.prod));
  setTxt('k-prod-d',e.dp!=null?T('{0} kWh heute',[e.dp.toFixed(1)]):'');
  setTxt('k-cons',wInt(d.cons));
  setTxt('k-cons-d',e.dc!=null?T('{0} kWh heute',[e.dc.toFixed(1)]):'');
  var imp=d.grid>0;
  setTxt('k-grid-l',imp?'Netzbezug':'Einspeisung');
  setTxt('k-net',wInt(Math.abs(d.grid)));
  var kn=$('k-net');if(kn)kn.className=imp?'cgn':'cgp';
  setTxt('k-net-d',imp?(e.dgi!=null?T('{0} kWh bezogen',[e.dgi.toFixed(1)]):'')
                      :(e.dgo!=null?T('{0} kWh eingespeist',[e.dgo.toFixed(1)]):''));
  var surp=d.prod-d.cons;
  setTxt('k-surp',(surp>=0?'+':'-')+wInt(Math.abs(surp)));
  var ks=$('k-surp');if(ks)ks.className=surp>=0?'cgp':'cgn';
  setTxt('k-surp-d',surp>=0?'steht zur Verfügung':'wird zugekauft');
  var bt=$('k-batt-t'),hasB=(d.batt!==undefined&&d.batt!==null);
  if(bt)bt.style.display=hasB?'':'none';
  if(hasB){
    // Richtung in die Nebenzeile: in der grossen Zeile stünde sie sonst neben der
    // Zahl und würde die Kachel als einzige zweizeilig machen.
    var bdir=d.batt>50?'lädt':d.batt<-50?'liefert':'im Ruhezustand';
    setTxt('k-batt',wInt(Math.abs(d.batt)));
    setTxt('k-batt-d',T(bdir)+(d.soc!=null?(' · '+T('{0} % geladen',[Math.round(d.soc)])):''));
  }
  setHtml('k-chips',(d.shelly||[]).map(function(s){
    // Eigenregelung: eine freigegebene, aber stillstehende Steckdose ist auf der
    // Wandanzeige sonst von einem laufenden Gerät nicht zu unterscheiden.
    var ready=(s.rw&&s.on&&s.reach&&s.run!==1);
    return '<span class="k-chip'+(s.on&&s.reach&&!ready?' on':'')+'">'+esc(s.name)+' &middot; '
      +(!s.reach?'offline':ready?T('bereit'):(s.on?wInt(s.apower):T('aus')))+'</span>';
  }).join(''));
  var al=$('k-al');
  if(al){
    if(d.al_n>0){al.style.display='';al.textContent=T(d.al_n===1?'{0} offene Störung':'{0} offene Störungen',[d.al_n]);}
    else al.style.display='none';
  }
  setTxt('k-foot','SolarLog '+fmtAge(d.sl_age==null?-1:d.sl_age)+(d.failsafe?' · '+T('Fail-Safe aktiv'):'')
    +(d.battblk?' · '+T('Batterie-Vorrang aktiv'):''));
}
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&kioskActive())kioskOff();});
// Der Wake Lock geht verloren, sobald der Tab in den Hintergrund gerät – beim
// Zurückkommen erneut anfordern, sonst schläft die Wandanzeige nach einmaligem
// Wegschalten für immer ein.
document.addEventListener('visibilitychange',function(){if(!document.hidden&&kioskActive())kioskLock();});
if(location.hash==='#kiosk')kioskSet(true);
