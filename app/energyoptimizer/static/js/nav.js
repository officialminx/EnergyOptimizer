// ── Navigation ───────────────────────────────────────────────────────────────
// Die Untermenüs kleben unter der Topbar – deren Höhe ist nicht konstant
// (Safe-Area, Umbruch bei schmalen Geräten), deshalb einmal messen statt raten.
function measureTopbar(){
  var t=document.querySelector('.topbar');
  if(t)document.documentElement.style.setProperty('--topbar-h',t.offsetHeight+'px');
}
addEventListener('resize',measureTopbar);addEventListener('orientationchange',measureTopbar);
function navTo(page){
  document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active');});
  var pg=$('page-'+page);if(pg)pg.classList.add('active');
  document.querySelectorAll('.btm-nav button').forEach(function(b){b.classList.remove('active');});
  var btn=document.querySelector('.btm-nav [data-page="'+page+'"]');if(btn)btn.classList.add('active');
  try{localStorage.setItem('eo_tab',page);}catch(e){}
  // Das Nachladen gehört hierher und nicht in den Klick-Handler: beim Neuladen der
  // Seite stellt der gemerkte Reiter direkt über navTo() zu – die Verlaufsseite blieb
  // dann leer, bis man den Reiter noch einmal antippte.
  if(page==='chart'){fetchHistory();fetchDayPts();fetchDailyHistory();chartRedrawAll();}
  if(page==='settings')dlLoadStats();
  if(page==='devices')fetchEvents();
  // Canvas-Inhalte auf ausgeblendeten Seiten werden nicht nachgeführt (Breite 0) –
  // beim Zurückwechseln aufs Dashboard deshalb einmal neu zeichnen.
  if(page==='dash')redrawLive();
  scrollTo(0,0);
}
document.querySelectorAll('.btm-nav button').forEach(function(b){
  b.addEventListener('click',function(){
    var p=this.dataset.page;
    // Zweiter Tipp auf „Einstellungen": zurück zur Übersichtsliste
    if(this.classList.contains('active')&&p==='settings'){setBack();return;}
    navTo(p);
  });
});
// Alle Canvas der Verlaufs-Seite neu zeichnen. Solange die Seite ausgeblendet war,
// hatten ihre Canvas Breite 0 – ohne Neuzeichnen blieben sie beim Einblenden leer.
function chartRedrawAll(){ch=initChart();redrawChart();drawHeatmap();}
function subTo(scope,key){
  var nav=document.querySelector('.subnav[data-sub="'+scope+'"]');if(!nav)return;
  nav.querySelectorAll('button').forEach(function(b){b.classList.toggle('active',b.dataset.subKey===key);});
  var host=$('page-'+scope);if(!host)return;
  host.querySelectorAll('.subpage').forEach(function(s){s.classList.toggle('active',s.dataset.subPage===key);});
}
document.querySelectorAll('.subnav[data-sub] button').forEach(function(b){
  b.addEventListener('click',function(){subTo(this.parentNode.dataset.sub,this.dataset.subKey);});
});
// Einstellungen: Index-Liste <-> Detail-Pane
function setTo(sec){
  document.querySelectorAll('.set-row').forEach(function(r){r.classList.toggle('active',r.dataset.set===sec);});
  document.querySelectorAll('.pane').forEach(function(p){p.classList.toggle('active',p.dataset.pane===sec);});
  $('page-settings').classList.add('sub');
  // Das Import-Diagramm liegt in dieser Pane und war bis eben ausgeblendet. Die
  // Tageswerte dazu holt sonst nur die Verlaufsseite – ohne den Abruf hier blieb das
  // Diagramm leer, wenn man direkt in die Einstellungen ging.
  if(sec==='data'){fetchDailyHistory();drawDailyChart();}
  // Die Notizen hängen an keinem Status-Poll – beim Öffnen einmal holen.
  if(sec==='ideas')ideaLoad();
  scrollTo(0,0);
}
function setBack(){$('page-settings').classList.remove('sub');scrollTo(0,0);}
document.querySelectorAll('.set-row').forEach(function(r){
  r.addEventListener('click',function(){setTo(this.dataset.set);});
});
// Speichern-Leiste: erscheint nur, wenn ein Feld wirklich vom zuletzt geladenen
// Stand abweicht. Ein blosses Merken von 'change' genuegte nicht – Datei-Auswahl,
// Jahres-Dropdown und vor allem das Passwort-Autofill des Browsers feuerten
// 'change' ohne echte Aenderung. Nach dem Speichern schreibt loadCfg() alle Felder
// neu, das Autofill schlug erneut zu und die Leiste kam sofort wieder hoch.
// Datei-Felder und das Jahres-Dropdown haben bewusst kein name-Attribut und
// bleiben so – wie beim Absenden – aussen vor.
var setBase=null;
function setSnap(){
  return [].map.call($('sf').querySelectorAll('input[name]:not([type=file]),select[name]'),
    function(el){return el.name+'\x1f'+(el.type==='checkbox'?(el.checked?'1':'0'):el.value);}
  ).join('\x1e');
}
function setDirty(on){$('savebar').classList.toggle('on',!!on);}
function checkDirty(){if(setBase!==null)setDirty(setSnap()!==setBase);}
// Was gerade vom Geraet geladen wurde, ist per Definition der gespeicherte Stand.
function setBaseline(){setBase=setSnap();setDirty(false);}
// Der Hysterese-Hinweis haengt an drei Feldern – ohne Nachfuehren zeigte er nach
// einer Aenderung des Intervalls weiter die alte Messungszahl.
addEventListener('DOMContentLoaded',function(){
  ['f-hon','f-hoff','f-sl-poll'].forEach(function(id){
    var e=$(id);if(e)e.addEventListener('input',updateHystHint);});
});
$('sf').addEventListener('input',checkDirty);
$('sf').addEventListener('change',checkDirty);
// Beschriftung eines Eingabefelds fuer Fehlermeldungen (das <label> davor).
function fieldLabel(el){
  var p=el.previousElementSibling;
  while(p&&p.tagName!=='LABEL')p=p.previousElementSibling;
  return p?p.textContent.replace(/\s+/g,' ').trim():el.name;
}
function saveSettings(){
  var f=$('sf'),err=$('msg-err');
  // requestSubmit() bricht bei einem einzigen ungueltigen Feld stillschweigend ab.
  // Da immer nur eine Pane sichtbar ist (auf der Index-Liste sogar keine), kann der
  // Browser die Fehlerblase meist gar nicht anzeigen – der Knopf tat dann scheinbar
  // nichts und die Leiste "Nicht gespeicherte Aenderungen" blieb stehen. Deshalb
  // selbst pruefen, die betroffene Pane oeffnen und benennen, was klemmt.
  var bad=null,els=f.querySelectorAll('input[name]:not([type=file]),select[name]');
  for(var i=0;i<els.length&&!bad;i++)if(!els[i].checkValidity())bad=els[i];
  if(bad){
    var pane=bad.closest('.pane');
    if(pane&&pane.dataset.pane)setTo(pane.dataset.pane);
    err.textContent=T('Nicht gespeichert – "{0}": {1}',[fieldLabel(bad),bad.validationMessage||T('ungültiger Wert')]);
    err.style.display='block';
    setTimeout(function(){try{bad.focus();bad.reportValidity();}catch(e){}},60);
    return;
  }
  err.style.display='none';
  if(f.requestSubmit)f.requestSubmit();
  else f.dispatchEvent(new Event('submit',{cancelable:true}));
}
// Netz fuer alle uebrigen Absende-Wege (Enter in einem Textfeld): 'invalid' feuert
// pro Feld und bubbelt nicht – deshalb in der Capture-Phase. Sonst bliebe auch der
// Enter-Weg wirkungslos, ohne dass irgendetwas sichtbar wuerde.
var invLast=0;
$('sf').addEventListener('invalid',function(e){
  var now=Date.now();
  if(now-invLast<300)return;   // pro Versuch nur das erste betroffene Feld melden
  invLast=now;
  var el=e.target,pane=el.closest?el.closest('.pane'):null;
  if(pane&&pane.dataset.pane)setTo(pane.dataset.pane);
  var err=$('msg-err');
  err.textContent=T('Nicht gespeichert – "{0}": {1}',[fieldLabel(el),el.validationMessage||T('ungültiger Wert')]);
  err.style.display='block';
},true);
// Verwerfen: die Formularwerte kommen beim nächsten Poll frisch vom Gerät.
function discardSettings(){cfg.loaded=false;setDirty(false);}
function forceRefresh(){
  var b=$('btn-refresh');if(b)b.classList.add('spinning');
  api('/api/refresh',{method:'POST'}).then(function(){
    // dem ESP kurz Zeit für die erzwungene Abfrage geben, dann Daten neu holen
    setTimeout(function(){poll();fetchHistory();fetchDayPts();if(b)b.classList.remove('spinning');},1500);
  }).catch(function(){if(b)b.classList.remove('spinning');});
}
