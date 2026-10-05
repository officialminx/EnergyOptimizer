function loadCfg(c){
  if(!c)return;cfg=Object.assign({},c,{loaded:true});
  $('f-sl-ip').value=c.sl_ip||'';
  $('f-sl-port').value=c.sl_port||80;
  $('f-sl-user').value=c.sl_user||'';
  $('f-sl-fprod').value=c.sl_fprod||'';
  $('f-sl-fcons').value=c.sl_fcons||'';
  $('f-sl-fgrid').value=c.sl_fgrid||'';
  $('f-sl-fyday').value=c.sl_fyday||'';
  $('f-sl-fcday').value=c.sl_fcday||'';
  $('f-sl-fytot').value=c.sl_fytot||'';
  $('f-sl-fctot').value=c.sl_fctot||'';
  $('f-sl-fsoc').value=c.sl_fsoc||'';
  $('f-sl-fbatt').value=c.sl_fbatt||'';
  $('f-src').value=c.src||'solarlog';
  $('f-src-host').value=c.src_host||'';
  $('f-src-port').value=c.src_port||'';
  $('f-em-pv').value=c.em_pv_ip||'';
  $('f-mb-preset').value=c.mb_preset||'sma';
  $('f-mb-unit').value=(c.mb_unit!=null?c.mb_unit:3);
  ['prod','grid','batt','soc'].forEach(function(k){$('f-mb-'+k).value=c['mb_'+k]||'';});
  ['prod','cons','grid','batt','soc'].forEach(function(k){$('f-mqs-'+k).value=c['mqs_'+k]||'';});
  srcShow();
  $('f-src-poll').value=c.src_poll_s||30;
  $('f-sh-poll').value=c.sh_poll_s||30;
  $('f-dry-run').checked=!!c.dry_run;
  $('f-sl-avg').value=(c.sl_avg_s!=null?c.sl_avg_s:300);
  $('f-sl-fsafe').value=(c.sl_fsafe!=null?c.sl_fsafe:30);
  $('f-onm').value=(c.on_margin!=null?c.on_margin:150);
  $('f-offm').value=(c.off_margin!=null?c.off_margin:200);
  $('f-hon').value=(c.hyst_on_s!=null?c.hyst_on_s:180);
  $('f-hoff').value=(c.hyst_off_s!=null?c.hyst_off_s:180);
  updateHystHint();
  $('f-mon').value=(c.min_on_min!=null?c.min_on_min:7);
  $('f-moff').value=(c.min_off_min!=null?c.min_off_min:5);
  presetMark();
  simDefaults(c);
  $('f-fws').value=(c.fw_start!=null?c.fw_start:20);
  $('f-fwe').value=(c.fw_end!=null?c.fw_end:24);
  $('f-battg').value=(c.batt_grd!=null?c.batt_grd:100);
  // Die API führt alle Preise in ganzen Rappen; eingegeben werden sie in Franken.
  $('f-p-buy').value=((c.p_buy||0)/100).toFixed(2);
  $('f-p-feed').value=((c.p_feed||0)/100).toFixed(2);
  $('f-p-base').value=((c.p_base||0)/100).toFixed(2);
  $('f-hb-en').checked=!!c.hb_en;
  $('f-hb-url').value='';
  setHtml('hb-url-hint',T(c.hb_url_set?'Eine Ping-URL ist gespeichert (wird aus Sicherheitsgründen nicht angezeigt).'
                                    :'<b>Noch keine Ping-URL gesetzt</b> – es wird kein Lebenszeichen gesendet.'));
  $('f-hb-min').value=c.hb_min||15;
  $('f-mo-sl').value=(c.mo_sl!=null?c.mo_sl:15);
  $('f-mo-dev').value=(c.mo_dev!=null?c.mo_dev:15);
  $('f-mo-np').value=(c.mo_np!=null?c.mo_np:45);
  $('f-mo-inv').value=(c.mo_inv!=null?c.mo_inv:30);
  $('f-sl-dev').checked=!!c.sl_dev;
  $('f-lat').value=(c.lat!=null?c.lat:47.05);
  $('f-lon').value=(c.lon!=null?c.lon:8.31);
  updateSunHint();
  $('f-mq-en').checked=!!c.mq_en;
  $('f-auto-en').checked=c.auto_en!==false;
  $('f-mq-host').value=c.mq_host||'';
  $('f-mq-port').value=c.mq_port||1883;
  $('f-mq-user').value=c.mq_user||'';
  $('f-mq-pfx').value=c.mq_pfx||'energyoptimizer';
  $('f-mq-disc').checked=c.mq_disc!==false;
  $('f-hostname').value=c.hostname||'energyoptimizer';
  $('f-lang').value=c.lang||'de';
  var pt=location.port?':'+location.port:'';
  $('ipnote').innerHTML='<b>http://'+esc(c.hostname||'energyoptimizer')+'.local'+pt+'</b> &nbsp;|&nbsp; IP <b>'+esc(c.ip)+pt+'</b>';
  setBaseline();
}
// ── Datenquelle: nur die Felder der gewählten Quelle zeigen ──────────────────
function srcShow(){
  var v=($('f-src')||{}).value||'solarlog';
  document.querySelectorAll('.src-sec,.src-lbl').forEach(function(el){
    el.classList.toggle('on',(el.dataset.src||'').split(' ').indexOf(v)>=0);
  });
  var port=$('f-src-port');if(port)port.placeholder=v==='modbus'?'502':'80';
  mbPreset(true);
}
// Modbus-Hersteller: Unit-ID vorschlagen und die eigenen Register nur bei "Eigene".
function mbPreset(keepUnit){
  var p=($('f-mb-preset')||{}).value;
  var box=document.querySelector('.mb-custom');if(box)box.classList.toggle('on',p==='custom');
  if(keepUnit!==true){var u=$('f-mb-unit');if(u)u.value=p==='huawei'?1:p==='sma'?3:u.value;}
}
function srcTest(){
  var out=$('src-test');if(!out)return;
  out.textContent=T('Teste Verbindung…');
  var v=$('f-src').value,body={src:v};
  if(v==='solarlog'){body.sl_ip=$('f-sl-ip').value.trim();body.sl_port=$('f-sl-port').value||'80';body.sl_pass=$('f-sl-pass').value;}
  else{
    body.src_host=$('f-src-host').value.trim();body.src_port=$('f-src-port').value||0;body.em_pv_ip=$('f-em-pv').value.trim();
    body.mb_preset=$('f-mb-preset').value;body.mb_unit=$('f-mb-unit').value;
    ['prod','grid','batt','soc'].forEach(function(k){body['mb_'+k]=$('f-mb-'+k).value.trim();});
    ['prod','cons','grid','batt','soc'].forEach(function(k){body['mqs_'+k]=$('f-mqs-'+k).value.trim();});
  }
  api('/api/source/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  .then(function(d){
    out.innerHTML=(d&&d.ok?'<b style="color:var(--green)">&#10003;</b> ':'<b style="color:var(--red)">&#10005;</b> ')+esc(T((d&&d.msg)||'Fehler beim Abrufen'));
  }).catch(function(){out.textContent=T('Fehler beim Abrufen');});
}
// ── Steuerung: Voreinstellungen ──────────────────────────────────────────────
var PRESETS={
  safe:{on_margin:300,off_margin:100,hyst_on_s:300,hyst_off_s:120,min_on_min:10,min_off_min:10},
  bal:{on_margin:150,off_margin:200,hyst_on_s:180,hyst_off_s:180,min_on_min:7,min_off_min:5},
  agg:{on_margin:50,off_margin:400,hyst_on_s:60,hyst_off_s:300,min_on_min:5,min_off_min:3}
};
var PRESET_TXT={
  safe:'Schaltet erst bei deutlichem Überschuss und schnell wieder ab – kaum Netzbezug, dafür bleibt mehr Sonne ungenutzt.',
  bal:'Die Standardwerte: guter Kompromiss zwischen genutzter Sonne und Netzbezug.',
  agg:'Nutzt auch knappen Überschuss und hält Geräte bei kurzen Wolken an – mehr Eigenverbrauch, gelegentlich etwas Netzbezug.'
};
var PRESET_FIELDS={on_margin:'f-onm',off_margin:'f-offm',hyst_on_s:'f-hon',hyst_off_s:'f-hoff',min_on_min:'f-mon',min_off_min:'f-moff'};
function presetMark(){
  var hit='';
  Object.keys(PRESETS).forEach(function(k){
    var ok=Object.keys(PRESET_FIELDS).every(function(f){return +$(PRESET_FIELDS[f]).value===PRESETS[k][f];});
    if(ok)hit=k;
  });
  var box=$('presets');if(!box)return;
  box.querySelectorAll('button').forEach(function(b){b.classList.toggle('active',b.dataset.p===hit);b.setAttribute('aria-pressed',b.dataset.p===hit?'true':'false');});
  setTxt('preset-hint',hit?T(PRESET_TXT[hit]):T('Eigene Werte'));
}
(function(){
  var box=$('presets');if(!box)return;
  box.addEventListener('click',function(e){
    var b=e.target.closest('button[data-p]');if(!b)return;
    var p=PRESETS[b.dataset.p];
    Object.keys(PRESET_FIELDS).forEach(function(f){$(PRESET_FIELDS[f]).value=p[f];});
    updateHystHint();presetMark();checkDirty();
  });
  Object.keys(PRESET_FIELDS).forEach(function(f){var el=$(PRESET_FIELDS[f]);if(el)el.addEventListener('input',presetMark);});
})();
var devOvIdx=null,devOvId='';
var devOvKind='sh';   // 'sh' = Shelly (mit IP), 'ext' = generischer MQTT-Schalter
function openDevOv(idx,prefill,kind){
  devOvKind=kind||'sh';
  var isExt=(devOvKind==='ext');
  devOvIdx=idx;
  var d=prefill||(isExt?(cfg.ext&&cfg.ext[idx]):(cfg.shelly&&cfg.shelly[idx]))||{};
  devOvId=d.id||'';
  $('dov-ttl').textContent=T(isExt?'Externer Schalter {0}':'Gerät {0}',[idx+1]);
  $('dov-ip-row').style.display=isExt?'none':'';
  $('dov-idline').style.display=isExt?'none':'';
  // Der Hinweis zu den externen Schaltern steckt hinter dem Info-Icon neben "Name";
  // bei einer echten Steckdose verschwindet das Icon samt aufgeklapptem Text.
  $('dov-ext-ib').style.display=isExt?'':'none';
  if(!isExt)infoClose('devext');
  if(!isExt)$('dov-idline').innerHTML=d.id?('&#128279; ID: '+esc(d.id)):T('ID wird beim nächsten Poll gelernt');
  $('dov-name').value=d.name||'';
  $('dov-name').placeholder=T(isExt?'z. B. Fingerbot Küche':'z. B. Boiler');
  $('dov-ip').value=d.ip||'';
  $('dov-pw').value=d.pw||0;
  $('dov-pw').min=isExt?0:1;
  $('dov-pri').value=d.pri||(idx+1);
  $('dov-pri').max=isExt?((cfg&&cfg.max_ex)||16):((cfg&&cfg.max_sh)||16);
  $('dov-st-row').style.display=isExt?'':'none';
  $('dov-st').value=d.st||'';
  var live=!isExt&&lastSt&&(lastSt.shelly||[]).filter(function(x){return x.idx===idx;})[0];
  var lh=live?learnHtml(live):'';
  $('dov-learn').innerHTML=lh?lh.replace(/^<div class="learn">|<\/div>$/g,''):'';
  $('dov-learn').style.display=lh?'':'none';
  $('dov-rt').value=d.rt||0;
  $('dov-mx').value=d.mx||0;
  // Eigenregelung braucht die gemessene Leistung der Steckdose – ein externer
  // MQTT-Schalter liefert keine, dort bleibt der Abschnitt ausgeblendet.
  $('dov-self').style.display=isExt?'none':'';
  $('dov-rw').value=d.rw||0;
  $('dov-io').value=d.io||0;
  $('dov-auto').checked=!!d.auto;
  $('dov-ws').value=(d.ws!=null?d.ws:0);
  $('dov-we').value=(d.we!=null?d.we:24);
  renderWdays(d.wd!=null?d.wd:0x7F);
  renderSched(d.sch||'');
  $('dov-msg').style.display='none';
  $('dev-ov').style.display='flex';
}
// ── Zeitschaltuhr-Editor ─────────────────────────────────────────────────────
// Serverformat je Programm: "start,ende,tage,aktiv" (Minuten seit Mitternacht),
// Programme durch Semikolon getrennt – identisch in settings.json, /api/status (Abschnitt cfg) und der Sicherung.
var MAX_SCHED_UI=4;
// Die Programme stehen als Liste im Dialog, nicht als vier feste Zeilen: hinzugefügt
// und gelöscht wird per Knopf. schList ist dabei die Wahrheit, das Formular nur ihre
// Darstellung – vor jedem Neuzeichnen wandern die Eingaben mit schRead() zurück.
var schList=[];
function schM2T(m){var h=Math.floor(m/60),mi=m%60;return (h<10?'0':'')+h+':'+(mi<10?'0':'')+mi;}
function schT2M(t){
  var p=(t||'').split(':');
  if(p.length<2)return -1;
  var h=parseInt(p[0],10),mi=parseInt(p[1],10);
  if(isNaN(h)||isNaN(mi)||h<0||h>23||mi<0||mi>59)return -1;
  return h*60+mi;
}
// Nie belegte Plätze ("0,0,0,0") sind kein Programm, sondern nur eine Lücke im
// Serverformat – sie erscheinen nicht als leere Zeile im Dialog.
function schParse(s){
  var out=[];
  (s||'').split(';').forEach(function(part){
    var f=part.split(',');
    if(f.length<3)return;
    var o={s:parseInt(f[0],10)||0,e:parseInt(f[1],10)||0,d:parseInt(f[2],10)||0,
           on:(f.length<4)||f[3]==='1'};
    if(!o.s&&!o.e&&!o.d)return;
    out.push(o);
  });
  return out.slice(0,MAX_SCHED_UI);
}
function renderSched(str){schList=schParse(str);schDraw();}
function schRead(){
  for(var i=0;i<schList.length;i++){
    if(!$('sch-s-'+i))continue;
    var st=schT2M($('sch-s-'+i).value),en=schT2M($('sch-e-'+i).value);
    if(st>=0)schList[i].s=st;      // unlesbare Eingabe: alten Wert behalten,
    if(en>=0)schList[i].e=en;      // statt das Programm auf 00:00 zu stellen
    schList[i].d=schMask(i);
    schList[i].on=!!($('sch-on-'+i)&&$('sch-on-'+i).checked);
  }
}
function schAdd(){
  schRead();
  if(schList.length>=MAX_SCHED_UI)return;
  // Plausible Vorgabe (10–14 Uhr, täglich), damit ein neues Programm mit zwei
  // Klicks steht statt mit acht.
  schList.push({s:600,e:840,d:0x7F,on:true});
  schDraw();
}
function schDel(i){
  schRead();
  schList.splice(i,1);
  schDraw();
}
function schDraw(){
  var el=$('dov-sch');if(!el)return;
  var h='';
  for(var i=0;i<schList.length;i++){
    var p=schList[i];
    h+='<div class="sch-row'+(p.on?'':' off')+'" id="schr-'+i+'">'
      +'<div class="sch-top">'
        +'<label class="tgl"><input type="checkbox" id="sch-on-'+i+'"'+(p.on?' checked':'')
          +' onchange="schToggle('+i+')"><span class="sl"></span></label>'
        +'<input type="time" id="sch-s-'+i+'" value="'+schM2T(p.s)+'" onchange="schInfo('+i+')">'
        +'<span class="sch-sep">&ndash;</span>'
        +'<input type="time" id="sch-e-'+i+'" value="'+schM2T(p.e)+'" onchange="schInfo('+i+')">'
        +'<button type="button" class="sch-del" onclick="schDel('+i+')" aria-label="Programm l&ouml;schen" '
          +'title="Programm l&ouml;schen">&#10005;</button>'
      +'</div>'
      +'<div class="wdays" id="sch-d-'+i+'"></div>'
      +'<div class="sch-info" id="sch-i-'+i+'"></div>'
    +'</div>';
  }
  if(!schList.length)
    h+='<div class="sch-none">Kein Programm &ndash; das Ger&auml;t l&auml;uft nur nach '
      +'&Uuml;berschuss bzw. von Hand.</div>';
  if(schList.length<MAX_SCHED_UI)
    h+='<button type="button" class="sch-add" onclick="schAdd()"><span>+</span>Programm hinzuf&uuml;gen</button>';
  else
    h+='<div class="sch-none">'+T('Mehr als {0} Programme je Gerät sind nicht vorgesehen.',[MAX_SCHED_UI])+'</div>';
  el.innerHTML=h;
  for(var k=0;k<schList.length;k++){schDays(k,schList[k].d);schInfo(k);}
}
function schDays(i,mask){
  var el=$('sch-d-'+i);if(!el)return;
  el.innerHTML=WDN.map(function(n,k){
    return '<button type="button" class="wday'+((mask>>k&1)?' on':'')+'" data-d="'+k+'">'+n+'</button>';
  }).join('');
  el.querySelectorAll('.wday').forEach(function(b){
    b.addEventListener('click',function(){b.classList.toggle('on');schInfo(i);});
  });
}
function schMask(i){
  var m=0,el=$('sch-d-'+i);if(!el)return 0;
  el.querySelectorAll('.wday').forEach(function(b){
    if(b.classList.contains('on'))m|=(1<<parseInt(b.getAttribute('data-d'),10));
  });
  return m;
}
function schToggle(i){
  var row=$('schr-'+i),cb=$('sch-on-'+i);
  if(row&&cb)row.className='sch-row'+(cb.checked?'':' off');
  schInfo(i);
}
// Laufzeit und Sonderfälle sofort sichtbar machen – ein Programm ohne Wochentag
// oder mit gleicher Start-/Endzeit läuft nie, das darf nicht still passieren.
function schInfo(i){
  var el=$('sch-i-'+i);if(!el)return;
  var on=$('sch-on-'+i)&&$('sch-on-'+i).checked;
  if(!on){el.textContent=T('Programm ausgesetzt');return;}
  var s=schT2M($('sch-s-'+i).value),e=schT2M($('sch-e-'+i).value),d=schMask(i);
  if(s<0||e<0){el.textContent=T('Ungültige Zeit');return;}
  if(s===e){el.textContent=T('Start und Ende gleich – das Programm läuft nie');return;}
  if(!d){el.textContent=T('Kein Wochentag gewählt – das Programm läuft nie');return;}
  var dur=e-s;if(dur<0)dur+=1440;
  var days=(d===0x7F)?T('täglich'):WDN.filter(function(n,k){return d>>k&1;}).join(' ');
  el.textContent=days+' · '+Math.floor(dur/60)+' h '+(dur%60)+' min'+(e<s?T(' (über Mitternacht)'):'');
}
// Serverformat: nur die tatsächlich vorhandenen Programme, in der Reihenfolge des
// Dialogs. Ein gelöschtes Programm hinterlässt keine Lücke mehr.
function schCollect(){
  schRead();
  var parts=[];
  for(var i=0;i<schList.length;i++){
    var p=schList[i];
    parts.push(p.s+','+p.e+','+p.d+','+(p.on?'1':'0'));
  }
  return parts.join(';');
}
var WDN=['Mo','Di','Mi','Do','Fr','Sa','So'].map(function(x){return T(x);});
function renderWdays(mask){
  var el=$('dov-wd');if(!el)return;
  if(!mask)mask=0x7F;   // keine Auswahl = keine Einschränkung
  el.innerHTML=WDN.map(function(n,i){
    return '<button type="button" class="wday'+((mask>>i&1)?' on':'')+'" data-d="'+i+'">'+n+'</button>';
  }).join('');
  el.querySelectorAll('.wday').forEach(function(b){
    b.addEventListener('click',function(){b.classList.toggle('on');});
  });
}
function wdaysMask(){
  var m=0,el=$('dov-wd');if(!el)return 0x7F;
  el.querySelectorAll('.wday').forEach(function(b){
    if(b.classList.contains('on'))m|=(1<<parseInt(b.getAttribute('data-d'),10));
  });
  return m||0x7F;   // alles abgewählt = keine Einschränkung statt "nie"
}
function closeDevOv(){$('dev-ov').style.display='none';devOvIdx=null;}
function saveDevOv(){
  var i=devOvIdx;if(i==null)return;
  var isExt=(devOvKind==='ext');
  var pfx=isExt?'e':'s';
  var data={};
  if(!isExt){
    var ip=$('dov-ip').value.trim();
    if(ip && !/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)){
      $('dov-msg').textContent=T('Ungültige IP-Adresse');$('dov-msg').style.display='block';return;
    }
    data[pfx+i+'_ip']=ip;
    data[pfx+i+'_id']=devOvId;
  }
  data[pfx+i+'_name']=$('dov-name').value.trim();
  data[pfx+i+'_pw']=$('dov-pw').value||(isExt?0:1);
  data[pfx+i+'_pri']=$('dov-pri').value||(i+1);
  data[pfx+i+'_rt']=$('dov-rt').value||0;
  data[pfx+i+'_mx']=$('dov-mx').value||0;
  if(!isExt){
    data[pfx+i+'_rw']=$('dov-rw').value||0;
    data[pfx+i+'_io']=$('dov-io').value||0;
  }else{
    data[pfx+i+'_st']=$('dov-st').value.trim();
  }
  data[pfx+i+'_auto']=$('dov-auto').checked?'true':'false';
  data[pfx+i+'_ws']=$('dov-ws').value||0;
  data[pfx+i+'_we']=$('dov-we').value||24;
  data[pfx+i+'_wd']=wdaysMask();
  data[pfx+i+'_sch']=schCollect();
  ovShow(ovSpin(),T('Speichern…'),'',0);
  api('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
  .then(function(d){
    if(d.ok){
      ovShow(ovOk(),T('Gespeichert!'),'',100,'var(--green)');
      cfg.loaded=false;
      setTimeout(function(){saveOv.style.display='none';closeDevOv();poll();},900);
    }else{
      saveOv.style.display='none';
      $('dov-msg').textContent=T('Fehler: {0}',[d.msg]);$('dov-msg').style.display='block';
    }
  }).catch(function(ex){
    saveOv.style.display='none';
    $('dov-msg').textContent=T('Netzwerkfehler: {0}',[ex]);$('dov-msg').style.display='block';
  });
}
function removeDevOv(){
  var i=devOvIdx;if(i==null)return;
  var isExt=(devOvKind==='ext');
  if(!confirm(T(isExt?'Externen Schalter wirklich entfernen?':'Gerät wirklich entfernen?')))return;
  var pfx=isExt?'e':'s';
  var data={};
  data[pfx+i+'_name']='';
  if(!isExt){data[pfx+i+'_ip']='';data[pfx+i+'_id']='';}
  ovShow(ovSpin(),T('Entfernen…'),'',0);
  api('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
  .then(function(){
    saveOv.style.display='none';
    cfg.loaded=false;
    closeDevOv();
    poll();
  }).catch(function(){saveOv.style.display='none';});
}
function sh(i,on){api('/api/shelly/'+i+'/'+(on?'on':'off'),{method:'POST'}).then(function(){if(cfg.shelly&&cfg.shelly[i])cfg.shelly[i].auto=false;poll();});}
function toggleAuto(i,on){autoTog[i]=Date.now();api('/api/shelly/'+i+'/'+(on?'autoon':'autooff'),{method:'POST'}).then(function(){if(cfg.shelly&&cfg.shelly[i])cfg.shelly[i].auto=on;poll();});}
function extSet(i,on){api('/api/ext/'+i+'/'+(on?'on':'off'),{method:'POST'}).then(function(){if(cfg.ext&&cfg.ext[i])cfg.ext[i].auto=false;poll();});}
function extToggleAuto(i,on){autoTog['e'+i]=Date.now();api('/api/ext/'+i+'/'+(on?'autoon':'autooff'),{method:'POST'}).then(function(){if(cfg.ext&&cfg.ext[i])cfg.ext[i].auto=on;poll();});}
function toggleExtTimer(i){var el=$('extc-'+i);if(!el)return;el.style.display=(el.style.display==='none')?'flex':'none';}
function extTimer(i,k){
  var mins=(k==='m24')?minsUntilMidnight():parseInt(k,10);
  var el=$('extc-'+i);if(el)el.style.display='none';
  api('/api/ext/'+i+'/on?min='+mins,{method:'POST'}).then(function(){
    if(cfg.ext&&cfg.ext[i])cfg.ext[i].auto=false;
    poll();
  });
}
function cancelExtTimer(i,retAuto){
  api('/api/ext/'+i+'/'+(retAuto?'autoon':'on'),{method:'POST'}).then(function(){
    if(cfg.ext&&cfg.ext[i])cfg.ext[i].auto=!!retAuto;
    poll();
  });
}
