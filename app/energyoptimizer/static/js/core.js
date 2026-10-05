var cfg={},histData=null;
var LOC=window.LANG==='en'?'en-GB':'de-CH';
// Farben je Steckdose (16 Plätze): die ersten vier wie bisher, danach gut
// unterscheidbare Töne, die in hell und dunkel tragen.
var SHCOL=['#ff9500','#ff375f','#af52de','#5ac8fa','#34c759','#ffcc00','#5856d6','#ff6482',
           '#30b0c7','#a2845e','#64d2ff','#bf5af2','#ff9f0a','#32d74b','#0a84ff','#8e8e93'];
// Canvas kennt keine CSS-Variablen: ein 'font' mit var(--sf) ist ungültig und wird
// stillschweigend verworfen (die Beschriftung landet dann in der 10px-Standardschrift,
// egal was dasteht). Deshalb hier der ausgeschriebene Stack.
var CFONT='-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",system-ui,sans-serif';
function $(id){return document.getElementById(id);}
// CSS-Variablen gepuffert: getComputedStyle erzwingt jedes Mal einen Style-Recalc,
// und ein einziger Chart-Redraw fragt ~20 Farben ab. Ungepuffert kostet allein das
// mehr als das eigentliche Zeichnen – beim Hovern (Redraw je Mausbewegung) sichtbar.
// Der Puffer gilt bis zum Themenwechsel, dann liefern die Variablen neue Werte.
var cvCache={};
function cv(name){var v=cvCache[name];
  if(v===undefined){v=getComputedStyle(document.documentElement).getPropertyValue(name).trim();cvCache[name]=v;}
  return v;}
function cvFlush(){cvCache={};}
// Ein Pointer-Handler je Frame: pointermove feuert auf Trackpads deutlich öfter als
// der Bildschirm neu zeichnet. Ohne Bündelung läuft Treffersuche + Redraw + Tooltip
// mehrfach pro Frame – genau das Ruckeln, das man beim Fahren über den Verlauf sieht.
function rafPtr(fn){
  var ev=null,id=0;
  var w=function(e){ev=e;if(id)return;
    id=requestAnimationFrame(function(){id=0;var q=ev;ev=null;if(q)fn(q);});};
  w.cancel=function(){if(id){cancelAnimationFrame(id);id=0;}ev=null;};
  return w;
}
function applyTheme(t){
  if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);
  else document.documentElement.removeAttribute('data-theme');
  cvFlush();
  redrawChart();drawDailyChart();drawHeatmap();redrawLive();
}
// Alles, was aus dem letzten Status-Poll gezeichnet wird – nach Themenwechsel und
// Grössenänderung nötig, da Canvas-Inhalte weder Farbe noch Breite selbst nachziehen.
function redrawLive(){
  updateSparks();
  donutSig='';   // Farben/Breite haben sich geändert -> Zeichnen erzwingen
  if(lastSt&&lastSt.energy)updateDonuts(lastSt.energy);
}
function toggleTheme(){
  var sysDark=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches;
  var cur=document.documentElement.getAttribute('data-theme');
  var isDark=cur?cur==='dark':sysDark;
  var next=isDark?'light':'dark';
  try{localStorage.setItem('theme',next);}catch(e){}
  applyTheme(next);
}
(function(){try{var saved=localStorage.getItem('theme');if(saved)applyTheme(saved);}catch(e){}})();
// Ohne festes data-theme folgt die Seite dem System – dann muss der Farbpuffer beim
// Umschalten des OS-Themes verworfen werden, sonst zeichnen die Canvas in Altfarben.
(function(){var m=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)');
  if(!m||!m.addEventListener)return;
  m.addEventListener('change',function(){
    if(document.documentElement.getAttribute('data-theme'))return;
    cvFlush();redrawChart();drawDailyChart();drawHeatmap();redrawLive();});})();
function kw(w){return(w/1000).toFixed(2);}
function wInt(w){return w>=1000?kw(w)+' kW':Math.round(w)+' W';}
function setFlash(el){el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash');}
function setTxt(id,t){var e=$(id);if(e&&e.textContent!==t)e.textContent=t;}
function setHtml(id,h){var e=$(id);if(e&&e.innerHTML!==h)e.innerHTML=h;}

// ── Info-Icons ───────────────────────────────────────────────────────────────
// Erklärender Fliesstext steht nicht mehr dauerhaft unter jedem Feld, sondern
// hinter einem (i) neben der Beschriftung. Im Markup genügen zwei Marker:
//   <i class="ib" data-i="key"></i>        … das Icon hinter dem Text
//   <div class="ip" data-i="key">…</div>   … der zugehörige Text (erst zugeklappt)
// Symbol, Tastaturbedienung und ARIA hängt initInfo() an – das spart in der HTML
// pro Icon eine Zeile SVG und hält die 900 Zeilen Markup lesbar.
function infoPanel(key){return document.querySelector('.ip[data-i="'+key+'"]');}
function infoSet(b,on){
  var p=infoPanel(b.getAttribute('data-i'));if(!p)return;
  p.classList.toggle('on',on);
  b.classList.toggle('on',on);
  b.setAttribute('aria-expanded',on?'true':'false');
}
function infoToggle(b){infoSet(b,!b.classList.contains('on'));}
// Zum Schliessen von aussen (z.B. wenn der erklärte Abschnitt verschwindet)
function infoClose(key){
  var b=document.querySelector('.ib[data-i="'+key+'"]');
  if(b)infoSet(b,false);
}
function initInfo(){
  var n=document.querySelectorAll('.ib');
  for(var i=0;i<n.length;i++){
    var b=n[i];
    if(b.firstChild)continue;              // schon eingerichtet
    b.innerHTML='<svg><use href="#i-info"/></svg>';
    b.setAttribute('role','button');
    b.setAttribute('tabindex','0');
    b.setAttribute('aria-label','Erklärung');
    b.setAttribute('aria-expanded','false');
  }
}
document.addEventListener('click',function(e){
  var b=e.target.closest?e.target.closest('.ib'):null;
  if(!b)return;
  // Die Icons sitzen in <label>/.auto-lbl neben Schaltern – ohne stopPropagation
  // würde ein Tipp aufs (i) den Schalter mitbetätigen.
  e.preventDefault();e.stopPropagation();
  infoToggle(b);
});
document.addEventListener('keydown',function(e){
  if(e.key!=='Enter'&&e.key!==' ')return;
  var b=e.target.closest?e.target.closest('.ib'):null;
  if(!b)return;
  e.preventDefault();
  infoToggle(b);
});
var lastNum={};
function tweenW(id,toW,signed,tiny){
  var el=$(id);if(!el)return;
  var known=(lastNum[id]!=null),to=toW,from=(known?lastNum[id]:to);
  lastNum[id]=to;
  function fmt(w){
    var s='',v=w;
    if(signed==='+'||signed==='-'){s=signed;v=Math.abs(w);}
    else if(signed===true){s=w>=0?'+':'';}
    if(tiny)return s+wInt(v);
    return s+kw(v)+'<span class="unit">kW</span>';
  }
  // Der Wert kommt aus dem Sekunden-Poll, aendert sich aber nur mit jeder
  // SolarLog-Abfrage. Ohne diesen Ausstieg liefe 650 ms lang ein rAF-Lauf, der
  // 60x pro Sekunde dasselbe innerHTML neu parst - genau die Hauptthread-Last,
  // die die Animationen im Energiefluss ins Stocken bringt.
  var txt=fmt(to);
  if(known&&to===from){if(el.innerHTML!==txt)el.innerHTML=txt;return;}
  if(Math.abs(to-from)>5)setFlash(el);
  var d=650,t0=performance.now(),last=null;
  function put(s){if(s!==last){last=s;el.innerHTML=s;}}
  put(txt);
  function step(now){
    var p=Math.min(1,(now-t0)/d),e=1-Math.pow(1-p,3),v=from+(to-from)*e;
    put(fmt(v));
    if(p<1)requestAnimationFrame(step);else put(txt);
  }
  requestAnimationFrame(step);
}

// ── Kleine Helfer für Lokalspeicher (privater Modus: localStorage wirft) ──────
function lsGet(k,d){try{var v=localStorage.getItem(k);return v===null?d:v;}catch(e){return d;}}
function lsSet(k,v){try{localStorage.setItem(k,v);}catch(e){}}

// ── Einklappbare Dashboard-Karten (Zustand je Karte gemerkt) ─────────────────
function cardClosed(key){return lsGet('eo_card_'+key,'')==='0';}
function applyCard(key){
  var c=$('card-'+key);if(!c)return;
  var closed=cardClosed(key);
  c.classList.toggle('closed',closed);
  var b=c.querySelector('.card-tog');if(b)b.setAttribute('aria-expanded',closed?'false':'true');
}
function toggleCard(key){
  lsSet('eo_card_'+key,cardClosed(key)?'1':'0');
  applyCard(key);
  // Canvas in einer zugeklappten Karte haben Breite 0 – nach dem Aufklappen neu zeichnen.
  if(!cardClosed(key))redrawLive();
}
['flow','eday','cost'].forEach(applyCard);

// ── Was macht der Optimizer mit einem Gerät gerade? ──────────────────────────
// Der Server liefert je Gerät einen Grund-Code mit Werten (Devices.explain); hier
// wird daraus der Satz. Rückgabe: {t: Text, c: Farbklasse on|wait|warn|err|''}.
function whyText(w,s){
  if(!w)return{t:'',c:''};
  var on=s&&s.on;
  switch(w.c){
    case 'offline':return{t:T('nicht erreichbar'),c:'err'};
    case 'manual':return{t:T(w.on?'von Hand EIN – noch {0}':'von Hand AUS – noch {0}',[fmtRest(w.s||0)])+(w.ret?T(', danach Automatik'):''),c:w.on?'on':''};
    case 'schedule':return{t:T('Wochenprogramm läuft – noch {0}',[fmtRest((w.m||0)*60)]),c:'on'};
    case 'master_off':return{t:T('Optimizer ausgeschaltet – bleibt, wie es ist'),c:on?'on':''};
    case 'auto_off':return{t:T('Automatik aus – nur Handbetrieb'),c:on?'on':''};
    case 'catchup':return{t:T('Nachlauf aus dem Netz: {0}/{1} min',[w.m,w.q]),c:'on'};
    case 'failsafe':return{t:T('Fail-Safe – keine aktuellen Messwerte'),c:'err'};
    case 'external':return{t:T('von Hand eingeschaltet'),c:'on'};
    case 'battery_off':return{t:T('Batterie entlädt – schaltet in {0} Messung(en) ab',[w.n]),c:'warn'};
    case 'switching_off':return{t:T('Defizit – schaltet in {0} Messung(en) ab',[w.n]),c:'warn'};
    case 'no_demand':return{t:T('freigegeben, Gerät meldet keinen Bedarf'),c:'wait'};
    case 'running_min_on':return{t:T('läuft auf Überschuss · Mindestlaufzeit noch {0}',[fmtRest(w.s||0)]),c:'on'};
    case 'running':return{t:w.s>0?T('läuft auf Überschuss seit {0}',[fmtRest(w.s)]):T('läuft auf Überschuss'),c:'on'};
    case 'window':return{t:T('ausserhalb des Freigabefensters (ab {0} Uhr)',[w.h]),c:''};
    case 'daycap':return{t:T('Tageslimit von {0} min erreicht',[w.m]),c:''};
    case 'nd_pause':return{t:T('kein Bedarf gemeldet – neuer Versuch in {0}',[fmtRest(w.s||0)]),c:''};
    case 'battery':return{t:T('Batterie entlädt – wartet'),c:'warn'};
    case 'min_off':return{t:T('Sperrzeit nach dem Ausschalten noch {0}',[fmtRest(w.s||0)]),c:''};
    case 'switching_on':return{t:T('Überschuss reicht – schaltet in {0} Messung(en) ein',[w.n]),c:'wait'};
    case 'waiting':return{t:w.miss>0?T('wartet auf {0} Überschuss – es fehlen {1}',[wInt(w.need),wInt(w.miss)]):T('wartet auf Überschuss'),c:'wait'};
  }
  return{t:'',c:''};
}
