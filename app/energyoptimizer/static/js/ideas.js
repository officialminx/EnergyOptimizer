// ── Verbesserungen (Ideenbuch) ──────────────────────────────────────────────
// Eigene Ablage auf dem Geraet, unabhaengig von den Einstellungen: jede Notiz
// wird einzeln sofort gespeichert, deshalb wirkt hier weder die Speicherleiste
// noch discardSettings().
var IDEA_AR=['Allgemein','Dashboard','Verlauf & Statistik','Geräte & Steuerung',
             'Meldungen & Alarme','MQTT / Home Assistant','System & Wartung','Bedienung / Web-Oberfläche'];
var IDEA_ST=['offen','eingeplant','umgesetzt','verworfen'];
// Der Server misst in Bytes, das Eingabefeld in Zeichen – ein Umlaut zaehlt
// dort doppelt. Ohne diese Umrechnung wuerde ein Text mit vielen Umlauten beim
// Speichern stillschweigend abgeschnitten.
var IDEA_T_MAX=89,IDEA_X_MAX=699;
var ideaEnc=(typeof TextEncoder!=='undefined')?new TextEncoder():null;
function ideaBytes(s){
  s=String(s||'');
  return ideaEnc?ideaEnc.encode(s).length:unescape(encodeURIComponent(s)).length;
}
var ideaCache=[],ideaEditId=0,ideaMax=30;
function ideaDate(ep){
  if(!ep)return '';
  var d=new Date(ep*1000);
  return ('0'+d.getDate()).slice(-2)+'.'+('0'+(d.getMonth()+1)).slice(-2)+'.'+d.getFullYear();
}
function ideaRow(i){
  return '<div class="idea p'+i.prio+' st'+i.status+'">'
    +'<div class="idea-b"><div class="idea-t">'+esc(i.title)+'</div>'
    +(i.text?'<div class="idea-x">'+esc(i.text)+'</div>':'')
    +'<div class="idea-m"><span class="badge b-st'+i.status+'">'+esc(IDEA_ST[i.status]||'')+'</span>'
    +'<span class="badge b-off">'+esc(IDEA_AR[i.area]||'')+'</span>'
    +(i.prio===2?'<span class="badge b-lock">'+T('hohe Priorität')+'</span>':'')
    +(i.prio===0?'<span class="badge b-off">'+T('niedrige Priorität')+'</span>':'')
    +'<span class="idea-nr">#'+i.id+(i.updated?' · '+ideaDate(i.updated):'')+'</span>'
    +'</div></div>'
    +'<div class="idea-act">'
    +'<button type="button" title="Bearbeiten" onclick="ideaEdit('+i.id+')"><svg><use href="#i-pen"/></svg></button>'
    +'<button type="button" class="rm" title="Löschen" onclick="ideaDel('+i.id+')"><svg><use href="#i-trash"/></svg></button>'
    +'</div></div>';
}
// Offene Punkte zuerst, darin die wichtigsten oben – Erledigtes/Verworfenes
// bleibt als Nachweis stehen, wandert aber ans Ende.
function ideaSort(a,b){
  var ga=a.status>=2?1:0,gb=b.status>=2?1:0;
  if(ga!==gb)return ga-gb;
  if(a.prio!==b.prio)return b.prio-a.prio;
  return a.id-b.id;
}
function ideaLoad(){
  api('/api/ideas').then(function(d){
    ideaCache=(d&&d.ideas)||[];
    ideaMax=(d&&d.max)||ideaMax;
    setHtml('idea-list',ideaCache.length
      ?ideaCache.slice().sort(ideaSort).map(ideaRow).join('')
      :'<div class="empty">'+T('Noch nichts notiert – „Neue Verbesserung“ legt den ersten Punkt an.')+'</div>');
    ideaLeft();
  }).catch(function(){});
}
function ideaErr(m){var e=$('idea-msg');if(!e)return;e.textContent=m;e.style.display='block';}
function ideaLeft(){
  var el=$('idea-left');if(!el)return;
  var left=IDEA_X_MAX-ideaBytes($('idea-text').value);
  el.textContent=T('{0} von {1} Notizen belegt · noch {2} Zeichen',[ideaCache.length,ideaMax,left]);
  el.classList.toggle('warn',left<60);
}
function ideaOpen(o){
  ideaEditId=o?o.id:0;
  $('idea-title').value =o?o.title:'';
  $('idea-text').value  =o?o.text:'';
  $('idea-area').value  =String(o?o.area:0);
  $('idea-prio').value  =String(o?o.prio:1);
  $('idea-status').value=String(o?o.status:0);
  $('idea-msg').style.display='none';
  $('idea-ed').classList.add('on');
  ideaLeft();
  $('idea-title').focus();
}
function ideaNew(){
  if(ideaCache.length>=ideaMax){
    ideaErr(T('Es sind bereits {0} Notizen gespeichert. Bitte zuerst erledigte löschen.',[ideaMax]));
    return;
  }
  ideaOpen(null);
}
function ideaEdit(id){
  for(var i=0;i<ideaCache.length;i++)if(ideaCache[i].id===id){ideaOpen(ideaCache[i]);return;}
}
function ideaCancel(){
  $('idea-ed').classList.remove('on');
  $('idea-msg').style.display='none';
  ideaEditId=0;
}
function ideaSave(){
  var t=$('idea-title').value.trim(),x=$('idea-text').value;
  if(!t){ideaErr(T('Bitte einen Titel angeben.'));return;}
  if(ideaBytes(t)>IDEA_T_MAX){ideaErr(T('Titel zu lang – bitte kürzen (Umlaute zählen doppelt).'));return;}
  if(ideaBytes(x)>IDEA_X_MAX){ideaErr(T('Beschreibung zu lang – bitte kürzen (Umlaute zählen doppelt).'));return;}
  api('/api/ideas',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:ideaEditId,title:t,text:x,
      area:+$('idea-area').value,prio:+$('idea-prio').value,status:+$('idea-status').value})})
  .then(function(d){
    if(!d||!d.ok){ideaErr((d&&d.msg)||T('Speichern fehlgeschlagen.'));return;}
    ideaCancel();ideaLoad();
  }).catch(function(){ideaErr(T('Keine Verbindung zum Gerät.'));});
}
function ideaDel(id){
  if(!confirm(T('Notiz #{0} unwiderruflich löschen?',[id])))return;
  api('/api/ideas/delete?id='+id,{method:'POST'}).then(function(d){
    if(d&&!d.ok){ideaErr(d.msg||T('Löschen fehlgeschlagen.'));return;}
    if(ideaEditId===id)ideaCancel();
    ideaLoad();
  }).catch(function(){});
}
function ideaExport(){location.href='/api/ideas/export';}
