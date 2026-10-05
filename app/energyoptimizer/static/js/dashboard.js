// ── Statuszeile: was tut die Automatik gerade und was kommt als Nächstes ──────
// Beantwortet ohne Kopfrechnen die zwei Fragen, die man vor dem Dashboard hat:
// "läuft gerade etwas?" und "warum schaltet Gerät X (nicht)?".
function updateStatusLine(d){
  var el=$('sline');if(!el)return;
  var list=d.shelly||[],surp=d.has_prod===false?-d.grid:d.prod-d.cons;
  var cls='idle',ico='i-bolt',main='',next='';

  var stale=(d.sl_age==null||d.sl_age<0||d.sl_age>Math.max(180,(cfg.src_poll_s||30)*2));
  if(d.failsafe){
    cls='err';ico='i-warn';
    main=T('Fail-Safe aktiv – Automatik abgeschaltet');
    next=T('Letzte Messwerte {0}. Die Automatik wird von selbst wieder scharf, sobald Daten ankommen.',[fmtAge(d.sl_age==null?-1:d.sl_age)]);
  }else if(d.sl_age!=null&&d.sl_age<0){
    cls='idle';ico='i-server';
    main=T('Warte auf die erste Abfrage der Datenquelle');
    var tgt=cfg.src==='solarlog'||!cfg.src?cfg.sl_ip:cfg.src==='mqtt'?cfg.mq_host:cfg.src_host;
    next=(tgt?T('Ziel: {0}',[esc(tgt)]):'')+(slNextTxt()?' &middot; '+T('nächster Versuch {0}',[slNextTxt()]):'');
  }else if(cfg.auto_en===false){
    cls='warn';ico='i-sliders';
    main=T('Automatik ausgeschaltet');
    next=T('Die Steckdosen bleiben, wie sie sind. Einschalten unter Einstellungen → Steuerung oder in Home Assistant.');
  }else if(stale){
    cls='warn';ico='i-warn';
    main=T('Datenquelle antwortet nicht');
    next=T('Letzte Daten {0}. Die Automatik rechnet bis dahin mit dem letzten bekannten Wert',[fmtAge(d.sl_age)])
        +((cfg.sl_fsafe>0)?T(', der Fail-Safe greift nach {0} min.',[cfg.sl_fsafe]):'.');
  }else{
    // Normalbetrieb: erst beschreiben was läuft, dann was als Nächstes ansteht
    var running=[],ready=[],forced=null,autoCount=0;
    list.forEach(function(s){
      if(s.auto)autoCount++;
      if(s.forced)forced=s;
      if(!s.on||!s.reach)return;
      // Eigenregelung: eine eingeschaltete Steckdose ist noch kein laufendes Gerät –
      // sonst behauptet die Statuszeile "Entfeuchter läuft", während er stillsteht.
      if(s.rw&&s.run!==1)ready.push(s.name); else running.push(s.name);
    });
    if(forced)      main=T('Nachlauf: {0} läuft ({1}/{2} min)',[esc(forced.name),forced.rt_on,forced.rt_min]);
    else if(running.length===1) main=T('{0} läuft',[esc(running[0])]);
    else if(running.length>1)   main=T('{0} Steckdosen laufen',[running.length])+' &middot; '+esc(running.join(', '));
    else if(ready.length===1)   main=T('{0} abrufbereit – kein Bedarf',[esc(ready[0])]);
    else if(ready.length>1)     main=T('{0} Geräte abrufbereit – kein Bedarf',[ready.length]);
    else                        main=T('Keine Steckdose aktiv');
    if(running.length&&ready.length)
      main+=' &middot; '+T('{0} abrufbereit ohne Bedarf',[ready.length]);
    main+=' &middot; '+T(surp>=0?'Überschuss {0} kW':'Defizit {0} kW',[kw(Math.abs(surp))]);
    cls=(running.length||surp>0)?'ok':'idle';
    ico=running.length?'i-plug':'i-bolt';

    // Batterie-Vorrang: erklärt den sonst rätselhaften Fall "Überschuss da, es
    // schaltet trotzdem nichts ein" – die Leistung käme aus dem Speicher.
    if(d.battblk){
      cls='warn';ico='i-batt';
      main+=' &middot; '+T('Batterie entlädt');
      next=T('Batterie-Vorrang: der Speicher deckt gerade eine Lücke ({0} Entladung). Die Automatik schaltet nichts ein und laufende Geräte ab – Freigabe, sobald die Entladung unter {1} fällt.',[wInt(Math.abs(d.batt||0)),wInt(Math.round((cfg.batt_grd||0)/2))]);
    }else{
      // Nächste Aktion: die dringendere von "schaltet gleich ab" und "schaltet gleich ein"
      var offCand=null,onCand=null,ndCand=null;
      list.forEach(function(s){
        if(!s.auto||!s.reach||s.forced)return;
        // Wartesperre nach "kein Bedarf": das Gerät ist kein Einschaltkandidat,
        // die Statuszeile muss aber sagen, warum trotz Überschuss nichts passiert.
        if(s.nd>0){ if(!ndCand)ndCand=s; return; }
        if(s.on){ if(!offCand||s.pri>offCand.pri)offCand=s; }
        else    { if(!onCand ||s.pri<onCand.pri) onCand=s;  }
      });
      if(offCand&&offCand.ft>0){
        next=T('{0} schaltet in {1} Messung(en) ab (Defizit)',[esc(offCand.name),Math.max(1,(cfg.hyst_off||3)-offCand.ft)])
            +(slNextTxt()?' &middot; '+T('nächste Bewertung {0}',[slNextTxt()]):'');
        cls='warn';
      }else if(onCand){
        var need=(onCand.pw||0)+(cfg.on_margin||0);
        if(onCand.lock>0){
          next=T('{0}: AUS-Sperre noch {1}',[esc(onCand.name),fmtSecs(onCand.lock)]);
        }else if(surp>=need){
          next=T('{0} schaltet in {1} Messung(en) ein',[esc(onCand.name),Math.max(1,(cfg.hyst_on||3)-(onCand.ot||0))])
              +(slNextTxt()?' &middot; '+T('nächste Bewertung {0}',[slNextTxt()]):'');
        }else{
          next=T('{0} startet ab {1} Überschuss – es fehlen {2}',[esc(onCand.name),wInt(need),wInt(need-surp)]);
        }
      }else if(ndCand){
        next=T('{0}: eigener Sensor meldete keinen Bedarf – neuer Versuch in {1}.',[esc(ndCand.name),fmtRest(ndCand.nd)]);
      }else if(!autoCount){
        next=T('Keine Steckdose auf Automatik – es wird nichts selbsttätig geschaltet.');
      }else if(!next){
        next=T('Alle Automatik-Geräte im Zielzustand.');
      }
    }
  }

  if(el.className!=='sline '+cls)el.className='sline '+cls;
  var iu=el.querySelector('.sline-ico use');
  if(iu&&iu.getAttribute('href')!=='#'+ico)iu.setAttribute('href','#'+ico);
  setHtml('sline-main',main);
  setHtml('sline-next',next);
}
// Die Kacheln zeigen einen Mittelwert, die Regelung rechnet mit dem Rohwert. Ohne
// diesen Hinweis waere nicht erklaerbar, warum ein ruhiger Ueberschuss neben einem
// Tick-Zaehler steht, der gerade weiterspringt.
function updateAvgChip(s){
  var el=$('avg-chip');if(!el)return;
  if(!s){if(el.style.display!=='none')el.style.display='none';return;}
  if(el.style.display==='none')el.style.display='';
  setTxt('avg-chip-t',s>=60?(Math.round(s/60)+' min'):(s+' s'));
}
function updateSlAge(age){
  var el=$('sl-age'),t=$('sl-age-t');if(!el||!t)return;
  if(age===undefined||age===null)age=-1;
  t.textContent=fmtAge(age);
  // gilt als veraltet, wenn deutlich älter als das 10s-Polling (>30s) oder noch nie
  el.className='card-sub'+((age<0||age>=30)?' stale':'');
}
function updateFlow(prod,cons,grid,batt,soc){
  var TH=15; // W-Schwelle gegen Flimmern bei ~0
  // Linien alle vom Aussenknoten -> Mitte gezeichnet: Klasse 'on' animiert
  // Zufluss (Knoten->Mitte); 'out' kehrt die Richtung (Mitte->Knoten) um.
  function line(id,on,out,extra){
    var el=$(id);if(!el)return;
    // Nur bei echter Aenderung schreiben: ein Klassenwechsel setzt die Animation
    // im Compositor neu auf, und der Poll laeuft jede Sekunde durch diese Zeile.
    var c='fl-anim'+(on?' on':'')+(out?' out':'')+(extra?(' '+extra):'');
    if(el.className!==c)el.className=c;
  }
  // Vorzeichen-Konvention: + = Energie wird produziert/eingespeist, - = Energie wird verbraucht/bezogen
  var gridImp=grid>TH,gridExp=grid<-TH;
  setTxt('flow-prod',lastSt&&lastSt.has_prod===false?'–':wInt(prod));
  setTxt('flow-cons',wInt(cons));
  setTxt('flow-grid',wInt(Math.abs(grid)));
  setTxt('flow-grid-dir',gridImp?T('Bezug'):(gridExp?T('Einspeisung'):'—'));

  // Solar -> Mitte: Zufluss, sobald Produktion vorhanden
  line('fl-solar',prod>TH,false);
  // Haus: Verbrauch = Abfluss aus der Mitte
  line('fl-home',cons>TH,true);
  // Netz: Bezug (grid>0) = Zufluss/orange, Einspeisung (grid<0) = Abfluss/gruen
  line('fl-grid',Math.abs(grid)>TH,grid<0,gridImp?'imp':(gridExp?'exp':''));

  // Batterie nur zeigen, wenn Felder konfiguriert (batt im Status vorhanden)
  var hasBatt=(batt!==undefined&&batt!==null);
  var bnode=$('flow-batt-node'),bbase=$('fl-batt-base');
  if(bnode)bnode.style.display=hasBatt?'':'none';
  if(bbase)bbase.style.display=hasBatt?'':'none';
  var BTH=50; // W-Schwelle: Batterie-Leistung unter 50W gilt als inaktiv
  if(hasBatt){
    var bAbs=Math.abs(batt),charging=batt>BTH,discharging=batt<-BTH;
    setTxt('flow-batt',wInt(bAbs>BTH?bAbs:0));
    setTxt('flow-soc',(charging?T('lädt'):discharging?T('entlädt'):T('Ruhe'))+(soc!==undefined&&soc!==null?' · '+Math.round(soc)+'%':''));
    // batt>0 laden = Abfluss (in die Batterie), batt<0 entladen = Zufluss
    line('fl-batt',bAbs>BTH,batt>0);
  }else{line('fl-batt',false,false);}

  // Hub pulsiert, sobald irgendwo Energie fliesst
  var hub=$('fl-hub'),active=(prod>TH||cons>TH||Math.abs(grid)>TH||(hasBatt&&Math.abs(batt)>BTH));
  if(hub){var hc='fl-hub'+(active?' live':'');if(hub.className!==hc)hub.className=hc;}
}
function fmtKwh(v,sign){return(sign||'')+(v==null?0:Math.abs(v)).toFixed(2)+' kWh';}
// Beträge kommen als Rappen vom Gerät – hier eine einzige Umrechnung, damit
// nirgends Rundungsfehler durch mehrfaches Hin- und Herrechnen entstehen.
function chf(rp){return (rp/100).toFixed(2)+' CHF';}
function updateCost(c){
  var card=$('card-cost');if(!card)return;
  if(!c){card.style.display='none';return;}
  card.style.display='';
  setTxt('c-buy',chf(c.buy));setTxt('c-sell',chf(c.sell));setTxt('c-saved',chf(c.saved));
  // Grundpreis ist optional – ohne hinterlegten Monatsbetrag bleibt die Kachel weg,
  // statt eine Nullzeile anzuzeigen.
  var base=c.base||0,bt=$('c-base-tile');
  if(bt)bt.style.display=base>0?'':'none';
  setTxt('c-base',chf(base));
  var net=c.sell+c.saved-c.buy-base;
  setHtml('c-net',T('Heute unter dem Strich')+' '+(net>=0?'<b style="color:var(--green)">+'+chf(net)+'</b>'
                         :'<b style="color:var(--red)">'+chf(net)+'</b>'));
}
function updateEnergy(e){
  if(!e)return;
  setTxt('e-dp',fmtKwh(e.dp));setTxt('e-dc',fmtKwh(e.dc));
  setTxt('e-dgi',fmtKwh(e.dgi));setTxt('e-dgo',fmtKwh(e.dgo));
  setTxt('e-total',T('Gesamt: {0} produziert · {1} verbraucht',[fmtKwh(e.tp),fmtKwh(e.tc)]));
  // Leerer Zustand: kurz nach dem Start stehen alle Tageszähler auf 0 – das sähe
  // sonst wie ein Messfehler aus.
  var none=!(e.dp>0.005||e.dc>0.005||e.dgi>0.005||e.dgo>0.005);
  var em=$('e-empty');if(em)em.style.display=none?'':'none';
  var dn=$('card-donut');if(dn)dn.style.display=none?'none':'';
  if(!none)updateDonuts(e);
}
// ── Was macht der Optimizer gerade? Eine Zeile je Gerät ──────────────────────
var optSig='';
function renderOptimizer(d){
  var card=$('card-opt'),list=$('opt-list');if(!card||!list)return;
  var rows=[];
  (d.shelly||[]).forEach(function(s){rows.push({k:'sh',s:s,p:s.virt?(s.on?s.pw:0):(s.reach&&s.apower>0?s.apower:0)});});
  (d.ext||[]).forEach(function(s){rows.push({k:'ext',s:s,p:s.on?(s.pw||0):0});});
  card.style.display=rows.length?'':'none';
  if(!rows.length)return;
  var sig=rows.map(function(r){return r.k+r.s.idx;}).join(',');
  if(sig!==optSig){
    list.innerHTML=rows.map(function(r){
      var id=r.k+'-'+r.s.idx;
      return'<button type="button" class="opt-row" id="opt-'+id+'" onclick="showDevice(\''+r.k+'\','+r.s.idx+')">'
        +'<span class="opt-dot" aria-hidden="true"></span>'
        +'<span style="min-width:0"><span class="opt-n" id="optn-'+id+'"></span><span class="opt-w" id="optw-'+id+'" style="display:block"></span></span>'
        +'<span id="optb-'+id+'"></span><span class="opt-p" id="optp-'+id+'"></span></button>';
    }).join('');
    optSig=sig;
  }
  var nOn=0,pw=0;
  rows.forEach(function(r){
    var id=r.k+'-'+r.s.idx,w=whyText(r.s.why,r.s);
    var el=$('opt-'+id);if(!el)return;
    var c='opt-row'+(w.c?' '+w.c:'');if(el.className!==c)el.className=c;
    setTxt('optn-'+id,r.s.name);
    setTxt('optw-'+id,w.t);
    setHtml('optb-'+id,r.s.virt?'<span class="badge b-sim">'+T('SIMULIERT')+'</span>':'');
    setTxt('optp-'+id,r.p>0?wInt(r.p):'');
    if(r.s.on){nOn++;pw+=r.p;}
  });
  setTxt('opt-sum',nOn?T('{0} von {1} an · {2}',[nOn,rows.length,wInt(pw)]):T('alle aus'));
}
