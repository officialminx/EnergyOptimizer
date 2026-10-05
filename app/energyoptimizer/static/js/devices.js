function badge(s){
  if(!s.reach)return'<span class="badge b-err">OFFLINE</span>';
  if(s.lock>0){var m=Math.floor(s.lock/60),sc=s.lock%60;
    return'<span class="badge b-lock">&#128274; '+(m>0?m+'m ':'')+sc+'s</span>';}
  return s.on?'<span class="badge b-on">EIN</span>':'<span class="badge b-off">AUS</span>';
}
// Eigenregelung: zweites Abzeichen neben EIN/AUS. Ein Entfeuchter mit eigenem
// Hygrostat laeuft nicht, weil die Steckdose frei ist, sondern wenn ER es will –
// ohne diese Anzeige haelt man eine eingeschaltete Steckdose fuer Betrieb.
function runBadge(s){
  if(!s.rw||!s.reach||!s.on)return'';
  if(s.run===1)return'<span class="badge b-run">&#9881; '+T('LÄUFT')+'</span>';
  if(s.run===3)return'<span class="badge b-idle">'+T('ANLAUF')+'</span>';
  return'<span class="badge b-idle">'+T('KEIN BEDARF')+'</span>';
}
function pingTxt(p){
  if(p==null||p<0)return T('noch nie');
  if(p<60)return T('vor {0}',[p+'s']);
  var m=Math.floor(p/60),sc=p%60;return T('vor {0}',[m+'m'+(sc>0?' '+sc+'s':'')]);
}
function shellySkeleton(s){
  return'<div class="shelly-card s-off" id="shc-'+s.idx+'">'
    +'<div class="sc-top"><div class="sc-name" id="shn-'+s.idx+'"></div><span id="shb-'+s.idx+'"></span></div>'
    +'<div class="sc-id" id="shid-'+s.idx+'"></div>'
    +'<div class="sc-meta" id="shmeta-'+s.idx+'"></div>'
    +'<div class="sc-meta" id="shkpi-'+s.idx+'"></div>'
    +'<div class="sc-live" id="shlv-'+s.idx+'"></div>'
    +'<div class="sc-rt" id="shrt-'+s.idx+'"></div>'
    +'<div id="shdem-'+s.idx+'"></div>'
    +'<div id="shhy-'+s.idx+'"></div>'
    +'<div id="shlk-'+s.idx+'"></div>'
    +'<div id="shov-'+s.idx+'"></div>'
    +'<div id="shsc-'+s.idx+'"></div>'
    +'<div class="sc-ping" id="shp-'+s.idx+'"></div>'
    +'<div class="sc-actions">'
      +'<label class="tgl"><input type="checkbox" id="sha-'+s.idx+'" onchange="toggleAuto('+s.idx+',this.checked)"><span class="sl"></span></label>'
      +'<span class="auto-lbl">Auto</span>'
      +'<button type="button" class="btn bon" onclick="sh('+s.idx+',1)">EIN</button>'
      +'<button type="button" class="btn boff" onclick="sh('+s.idx+',0)">AUS</button>'
      +'<button type="button" class="sc-cfg-btn" onclick="toggleTimer('+s.idx+')" title="Befristet einschalten"><svg><use href="#i-clock"/></svg></button>'
      +'<button type="button" class="sc-cfg-btn" onclick="openDevOv('+s.idx+')" title="Einstellungen"><svg><use href="#i-sliders"/></svg></button>'
    +'</div>'
    +'<div class="tchips" id="shtc-'+s.idx+'" style="display:none">'
      +TDUR.map(function(t){return '<button type="button" class="tchip" onclick="shTimer('+s.idx+',\''+t.k+'\')">'+t.l+'</button>';}).join('')
    +'</div></div>';
}
function emptySkeleton(i){
  return'<div class="shelly-card s-empty" id="shc-'+i+'">'
    +'<div class="sc-empty-ico"><svg><use href="#i-plus"/></svg></div>'
    +'<div class="sc-empty-txt">'+T('Gerät {0} einrichten',[i+1])+'</div>'
    +'<button type="button" class="btn bscan" onclick="openDevOv('+i+')"><svg><use href="#i-plus"/></svg>Hinzuf&uuml;gen</button>'
    +'</div>';
}
var autoTog={};
// Befristetes Schalten: 'm24' = bis Mitternacht (Minuten erst beim Klick berechnet)
var TDUR=[{k:'30',l:'30 min'},{k:'60',l:'1 h'},{k:'120',l:'2 h'},{k:'240',l:'4 h'},{k:'m24',l:T('bis 24 Uhr')}];
function toggleTimer(i){
  var el=$('shtc-'+i);if(!el)return;
  el.style.display=(el.style.display==='none')?'flex':'none';
}
function minsUntilMidnight(){
  var n=new Date(),m=(24*60)-(n.getHours()*60+n.getMinutes());
  return Math.max(1,Math.min(1440,m));
}
function shTimer(i,k){
  var mins=(k==='m24')?minsUntilMidnight():parseInt(k,10);
  var el=$('shtc-'+i);if(el)el.style.display='none';
  api('/api/shelly/'+i+'/on?min='+mins,{method:'POST'}).then(function(){
    if(cfg.shelly&&cfg.shelly[i])cfg.shelly[i].auto=false;
    poll();
  });
}
// Befristung vorzeitig beenden: zurück in den Zustand, den das Gerät vorher hatte
function cancelTimer(i,retAuto){
  api('/api/shelly/'+i+'/'+(retAuto?'autoon':'on'),{method:'POST'}).then(function(){
    if(cfg.shelly&&cfg.shelly[i])cfg.shelly[i].auto=!!retAuto;
    poll();
  });
}
function fmtDur(sec){
  if(sec<60)return sec+' s';
  var h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60);
  if(h>=24)return Math.floor(h/24)+' d '+(h%24)+' h';
  return h>0?(h+' h '+m+' min'):(m+' min');
}
function fmtRest(sec){
  if(sec<60)return sec+' s';
  var h=Math.floor(sec/3600),m=Math.round((sec%3600)/60);
  return h>0?(h+' h '+m+' min'):(m+' min');
}
function lvv(svg,txt,dim){return'<span class="lvv'+(dim?' dim':'')+'"><svg><use href="#'+svg+'"/></svg>'+txt+'</span>';}
// Eigenregelung im Klartext: der haeufigste Ratefall ist "Steckdose EIN, Zaehler
// bei 3 W – ist das Geraet kaputt?". Nein: sein eigener Sensor meldet keinen Bedarf.
function demandHtml(s){
  if(!s.rw)return'';
  var ic='<svg><use href="#i-info"/></svg>';
  if(s.nd>0){
    return'<div class="dem wait">'+ic+'<span>'+T('Kein Bedarf gemeldet – Steckdose abgeschaltet. Neuer Versuch in {0}.',[fmtRest(s.nd)])+'</span></div>';
  }
  if(!s.on||!s.reach)return'';
  if(s.run===1){
    return'<div class="dem">'+ic+'<span>'+T('Gerät läuft ({0} W ≥ {1} W Laufschwelle) – nur diese Zeit zählt als Laufzeit.',[s.apower,s.rw])+'</span></div>';
  }
  if(s.run===3){
    return'<div class="dem">'+ic+'<span>'+T('Anlaufkarenz – das Gerät hat noch ein paar Minuten Zeit, bevor „kein Bedarf“ gilt.')+'</span></div>';
  }
  var t=T('Steckdose frei, aber Gerät läuft nicht ({0} W &lt; {1} W): sein eigener Sensor meldet keinen Bedarf',[s.apower,s.rw]);
  if(s.io>0){
    var left=Math.max(0,s.io*60-(s.idle||0));
    t+=T('. Abschaltung in {0}',[fmtRest(left)]);
  }else{
    t+=T(' – die Steckdose bleibt abrufbereit');
  }
  return'<div class="dem">'+ic+'<span>'+t+'.</span></div>';
}
// Tagessoll (Mindest-Laufzeit) und Tageslimit (Maximal-Laufzeit) auf der Kachel.
// Beide zählen dieselbe heutige Laufzeit, deshalb eine gemeinsame Zeile – identisch
// für Shelly-Geräte und externe Schalter.
function runtimeHtml(s){
  var h='';
  if(s.rt_min>0){
    var done=s.rt_on>=s.rt_min;
    h+='<span style="color:'+(done?'#0a8e95':'#c96d00')+'">&#9203; '+T('Heute {0}/{1} min',[s.rt_on,s.rt_min])
      +(done?' &#10003;':'')+'</span>';
    if(s.forced)h+=' <span class="badge" style="background:rgba(124,92,255,.16);color:#6a4fe0">&#127783; Nachlauf</span>';
  }
  if(s.mx_min>0){
    // Ohne Tagessoll steht hier die einzige Laufzeitangabe – dann mit "Heute",
    // sonst nur der Riegel, damit die Zahl nicht doppelt dasteht.
    if(h)h+=' &middot; ';
    h+='<span style="color:'+(s.cap?'var(--red)':'var(--ink3)')+'">&#128683; '
      +T(s.rt_min>0?'Limit {0}/{1} min':'Heute {0}/{1} min',[s.rt_on,s.mx_min])+'</span>';
    if(s.cap)h+=' <span class="badge" style="background:rgba(255,59,48,.14);color:var(--red)">Tageslimit erreicht</span>';
  }
  return h;
}
// Zeitschaltuhr auf der Kachel: läuft gerade ein Programm, wurde es von Hand
// ausgesetzt, oder wann kommt das nächste? Ohne diese Zeile wäre ein Gerät, das
// bei Regen fröhlich Strom zieht, nicht erklärbar.
function schedHtml(s,kind){
  if(!s.schp)return'';
  var ic='<svg><use href="#i-clock"/></svg>';
  if(s.sch){
    var rest=(s.schr>0)?T(' – noch {0}',[fmtRest(s.schr*60)]):'';
    return'<div class="sch-run">'+ic+T('Zeitschaltuhr läuft')+rest
      +'<button type="button" onclick="'+(kind==='ext'?'extSet':'sh')+'('+s.idx+',0)">'+T('aussetzen')+'</button></div>';
  }
  if(s.schs){
    return'<div class="sch-run idle">'+ic+T('Zeitschaltuhr von Hand ausgesetzt – das nächste Programm läuft wieder.')+'</div>';
  }
  if(s.schn>0){
    return'<div class="sch-run idle">'+ic+T('Nächstes Programm in {0}',[fmtRest(s.schn*60)])+'</div>';
  }
  return'';
}
function updateShelly(s){
  var card=$('shc-'+s.idx);if(!card)return;
  var st=!s.reach?'s-err':s.lock>0?'s-lock':s.on?'s-on':'s-off';
  if(card.className!=='shelly-card '+st)card.className='shelly-card '+st;
  setTxt('shn-'+s.idx,s.name);
  setHtml('shb-'+s.idx,badge(s)+runBadge(s));
  setHtml('shid-'+s.idx,s.id?'&#128279; '+esc(s.id):T('ID wird beim nächsten Poll gelernt'));
  var eTxt=(s.e_day!=null)?' · '+T('{0} kWh heute',[s.e_day.toFixed(2)]):'';
  setTxt('shmeta-'+s.idx,T('{0} W (nom.) · Prio {1}',[s.pw,s.pri])+eTxt);
  // Nutzen-Kennzahlen: welcher Anteil des Verbrauchs kam aus eigener Produktion,
  // wie lange lief das Gerät, wie oft hat das Relais geschaltet.
  var kpi=[];
  if(s.e_tot>0.01&&s.pv_tot!=null)kpi.push('☀️ '+T('{0}% aus PV',[Math.round(s.pv_tot/s.e_tot*100)]));
  // Mit Eigenregelung sind die Betriebsstunden echte Laufzeit (gemessen am
  // Verbrauch), nicht die Zeit, in der die Steckdose freigegeben war.
  if(s.on_day)kpi.push('⏱ '+T(s.rw?'{0} gelaufen heute':'{0} heute',[fmtDur(s.on_day)]));
  if(s.on_tot)kpi.push(T('{0} gesamt',[fmtDur(s.on_tot)]));
  if(s.sw)kpi.push(T('{0} Schaltungen',[s.sw]));
  setTxt('shkpi-'+s.idx,kpi.join(' · '));
  if(s.reach){
    setHtml('shlv-'+s.idx,
      lvv('i-volt',s.apower+' W')+lvv('i-grid',s.volt+' V')+lvv('i-amp',(s.amp!=null?s.amp.toFixed(2):'0.00')+' A')+lvv('i-temp',(s.temp!=null?s.temp.toFixed(1):'0.0')+' &deg;C'));
  }else{
    setHtml('shlv-'+s.idx,lvv('i-volt','-- W',1)+lvv('i-grid','-- V',1)+lvv('i-amp','-- A',1)+lvv('i-temp','-- &deg;C',1));
  }
  setHtml('shrt-'+s.idx,runtimeHtml(s));
  setHtml('shdem-'+s.idx,demandHtml(s));
  // Hysterese: die Automatik "sammelt" Messungen, bevor sie schaltet. Ohne diese
  // Anzeige wirkt ein Gerät untätig, obwohl die Schaltung längst vorbereitet ist.
  var hyHtml='';
  if(s.auto&&s.reach&&!s.forced&&!(s.lock>0)){
    var need=s.on?(cfg.hyst_off||3):(cfg.hyst_on||3),have=(s.on?s.ft:s.ot)||0;
    if(have>0){
      var pips='';for(var k=0;k<need;k++)pips+='<span class="pip'+(k<have?' f':'')+'"></span>';
      var nx=slNextTxt();
      hyHtml='<div class="hyst '+(s.on?'off':'on')+'"><span class="pips">'+pips+'</span>'
        +T(s.on?'Ausschalten {0}/{1}':'Einschalten {0}/{1}',[have,need])
        +(nx?'<span class="nx">&middot; '+T('nächste Bewertung {0}',[nx])+'</span>':'')+'</div>';
    }
  }
  setHtml('shhy-'+s.idx,hyHtml);
  var lkHtml='';
  if(s.lock>0){
    var maxL=s.on?(cfg.min_on_min||7)*60:(cfg.min_off_min||5)*60;
    var lp=Math.min(100,Math.round(s.lock/maxL*100));
    lkHtml='<div class="lock-bar-wrap"><div class="lock-bar-lbl">'+T('Gesperrt noch {0}',[Math.floor(s.lock/60)+'m '+(s.lock%60)+'s'])+'</div>'
      +'<div class="lock-bar-track"><div class="lock-bar-fill" style="width:'+lp+'%"></div></div></div>';
  }
  setHtml('shlk-'+s.idx,lkHtml);
  // Befristung bzw. geschlossenes Freigabefenster – beides erklärt, warum das
  // Gerät gerade nicht dem folgt, was man sonst erwarten würde.
  var ovHtml='';
  if(s.ov>0){
    ovHtml='<div class="ovr"><svg><use href="#i-clock"/></svg>'+T(s.on?'Befristet EIN – noch {0}':'Befristet AUS – noch {0}',[fmtRest(s.ov)])+(s.ova?T(', danach Automatik'):'')
      +'<button type="button" onclick="cancelTimer('+s.idx+','+(s.ova?1:0)+')">'+T('jetzt beenden')+'</button></div>';
  }else if(s.win===false){
    ovHtml='<div class="win-closed"><svg><use href="#i-clock"/></svg>'+T('Ausserhalb des Freigabefensters – die Automatik schaltet jetzt nicht')+'</div>';
  }
  setHtml('shov-'+s.idx,ovHtml);
  setHtml('shsc-'+s.idx,schedHtml(s,'sh'));
  setTxt('shp-'+s.idx,T('Angepingt: {0}',[pingTxt(s.ping)]));
  var cb=$('sha-'+s.idx);
  if(cb && document.activeElement!==cb && !(autoTog[s.idx] && (Date.now()-autoTog[s.idx])<4000)){
    if(cb.checked!==!!s.auto)cb.checked=!!s.auto;
  }
}
var shSig=null;
var MAX_SHELLY_UI=4;
var lastShellyCount=0;
function renderShelly(list){
  lastShellyCount=list.length;
  var sig=list.map(function(s){return s.idx;}).join(',')+'|'+MAX_SHELLY_UI;
  if(sig!==shSig){
    var h='';
    list.forEach(function(s){h+=shellySkeleton(s);});
    for(var i=list.length;i<MAX_SHELLY_UI;i++)h+=emptySkeleton(i);
    $('shelly-live').innerHTML=h;
    shSig=sig;
  }
  list.forEach(updateShelly);
  syncEvFilter(list);
}
function fmtAge(s){
  if(s<0)return T('noch nie');
  if(s<60)return T('vor {0}',[s+'s']);
  if(s<3600)return T('vor {0}',[Math.floor(s/60)+'m']);
  return T('vor {0}',[Math.floor(s/3600)+'h']);
}
function fmtSecs(v){
  if(v<60)return v+' s';
  var m=Math.floor(v/60),sc=v%60;return m+':'+(sc<10?'0':'')+sc+' min';
}
// Zeit bis zur nächsten SolarLog-Abfrage – nur dann rückt die Hysterese weiter,
// deshalb erscheint der Wert überall dort, wo Tick-Zähler angezeigt werden.
var lastSt=null;
function slNextTxt(){
  if(!lastSt||lastSt.sl_next==null||lastSt.sl_next<0)return'';
  return lastSt.sl_next<=0?T('jetzt'):T('in {0}',[fmtSecs(lastSt.sl_next)]);
}
