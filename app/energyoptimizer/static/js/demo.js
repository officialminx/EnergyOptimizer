var DEMO=/[?&]demo=1\b/.test(location.search);
function api(url,opts){
  if(DEMO)return Promise.resolve(mock(url,opts));
  return fetch(url,opts).then(function(r){
    if(r.status===401){location.replace('/');return new Promise(function(){});}  // Sitzung abgelaufen -> Login-Seite
    return r.json();
  });
}
var DM={
  names:['Boiler','W\u00e4rmepumpe','Wallbox','Poolpumpe'].map(function(x){return T(x);}),
  ips:['192.168.1.51','192.168.1.52','192.168.1.53','192.168.1.54'],
  ids:['shellyplus1pm-a8032ab1','shellyplus1pm-b4e62de8','shellypro1-c0490e44','shellyplug-s-d8bf2e90'],
  pw:[2000,1800,11000,900],pri:[1,2,3,4],auto:[true,true,false,true],rt:[0,90,0,30],
  mx:[0,0,0,240],
  ws:[0,0,22,0],we:[24,24,6,24],wd:[127,127,127,31],
  on:[true,false,false,true],lock:[0,0,0,140],ov:[0,0,0,0],uptime:48213,t0:Date.now()
};
function demoStatus(){
  var t=(Date.now()-DM.t0)/1000;
  var prod=4200+Math.round(900*Math.sin(t/9)+Math.random()*120);
  var cons=2600+Math.round(700*Math.sin(t/5+1)+Math.random()*100);
  if(prod<0)prod=0;
  var shelly=[],hrs=t/3600;
  for(var i=0;i<4;i++){
    var on=DM.on[i],reach=i!==2?true:Math.random()>0.05;
    DM.lock[i]=Math.max(0,DM.lock[i]-1);
    shelly.push({idx:i,name:DM.names[i],id:DM.ids[i],pw:DM.pw[i],pri:DM.pri[i],auto:DM.auto[i],
      on:on,reach:reach,apower:on?Math.round(DM.pw[i]*(.85+Math.random()*.12)):0,
      volt:reach?229+Math.round(Math.random()*4):0,
      amp:on?+(DM.pw[i]/230*(.85+Math.random()*.1)).toFixed(2):0,
      temp:reach?38+Math.random()*9:0,
      rt_min:DM.rt[i],rt_on:DM.rt[i]>0?Math.min(DM.rt[i],Math.round(t/60)%(DM.rt[i]+20)):0,
      mx_min:DM.mx[i],cap:false,
      forced:i===3&&DM.lock[i]>0,ping:reach?Math.round(Math.random()*3):-1,lock:DM.lock[i],
      ot:(!on&&DM.auto[i])?(Math.floor(t/7)%4):0,ft:(on&&DM.auto[i])?(Math.floor(t/11)%3):0,
      ov:DM.ov[i]>0?DM.ov[i]--:0,ova:true,win:i!==2,
      e_day:+(DM.pw[i]*hrs*.4/1000).toFixed(2),e_tot:+(DM.pw[i]*hrs*.4/1000+12.5*(i+1)).toFixed(2),
      pv_day:+(DM.pw[i]*hrs*.3/1000).toFixed(2),pv_tot:+(DM.pw[i]*hrs*.3/1000+9.1*(i+1)).toFixed(2),
      on_day:Math.round(t%28800),on_tot:432000+i*86400,sw:184+i*37});
  }
  var batt=Math.max(-3000,Math.min(3000,prod-cons));
  var soc=Math.round(50+35*Math.sin(t/40));
  var energy={dp:+(prod*hrs/1000).toFixed(2),dc:+(cons*hrs/1000).toFixed(2),
    dgi:+(Math.max(0,cons-prod)*hrs/1000).toFixed(2),dgo:+(Math.max(0,prod-cons)*hrs/1000).toFixed(2),
    tp:+(prod*hrs/1000+142.3).toFixed(2),tc:+(cons*hrs/1000+98.7).toFixed(2),
    tgi:+(38.2).toFixed(2),tgo:+(81.6).toFixed(2)};
  var selfKwh=Math.max(0,(prod-Math.max(0,prod-cons))*hrs/1000);
  var cost={buy:Math.round(energy.dgi*22),sell:Math.round(energy.dgo*8),
            saved:Math.round(selfKwh*22),base:Math.round(1928/30)};
  return{prod:prod,cons:cons,grid:cons-prod,batt:batt,soc:soc,sl_age:1+Math.round(Math.random()*4),avg_s:300,
    sl_next:Math.round(60-(t%60)),failsafe:false,battblk:(batt < -100),al_n:DM.alAck?1:2,al_sev:2,
    led:'connected',shelly:shelly,energy:energy,cost:cost,mqtt:{conn:false},cfg:cfg.loaded?undefined:demoCfg()};
}
function demoCfg(){
  var sh=[];for(var i=0;i<4;i++)sh.push({name:DM.names[i],ip:DM.ips[i],id:DM.ids[i],pw:DM.pw[i],pri:DM.pri[i],
    auto:DM.auto[i],rt:DM.rt[i],mx:DM.mx[i],ws:DM.ws[i],we:DM.we[i],wd:DM.wd[i]});
  return{sl_ip:'192.168.1.40',sl_port:80,sl_user:'',sl_fprod:'101',sl_fcons:'110',sl_fgrid:'',sl_fyday:'105',sl_fcday:'111',sl_fytot:'109',sl_fctot:'115',sl_fsoc:'103',sl_fbatt:'104',sl_poll_min:1,sl_avg_s:300,sl_fsafe:30,
    on_margin:150,off_margin:200,hyst_on_s:180,hyst_off_s:180,hyst_on:3,hyst_off:3,min_on_min:7,min_off_min:5,
    batt_grd:100,fw_start:20,fw_end:24,sh_count:4,ip:'192.168.1.99',shelly:sh,
    p_buy:22,p_feed:8,p_base:1928,
    hb_en:true,hb_url_set:true,hb_min:15,
    mo_sl:15,mo_dev:15,mo_np:45,mo_inv:30,sl_dev:true,lat:47.05,lon:8.31,
    mq_en:false,mq_host:'',mq_port:1883,mq_user:'',mq_pfx:'energyoptimizer',mq_disc:true,auto_en:true,lang:(window.LANG||'de'),hostname:'energyoptimizer'};
}
var DSCAN={running:false,done:false,t:0};
function mock(url,opts){
  var m=(opts&&opts.method)||'GET';
  if(url==='/api/status')return demoStatus();
  if(url==='/api/history/stats')return{exists:true,bytes:812000,first:Math.round(DM.t0/1000)-86400*180,fs_total:26500000,fs_used:812000};
  if(url==='/api/history/clear')return{ok:true};
  if(url==='/api/update/check')return{ok:true,update:demoStatus().update};
  if(url==='/api/sysinfo'){DM.uptime+=10;
    return{mem_free:2900*1048576+Math.round(Math.random()*9e7),mem_total:3900*1048576,
      disk_free:21e9,disk_total:29e9,uptime:DM.uptime,temp:46+Math.random()*5,
      cpu:6+Math.round(Math.random()*10),proc:1+Math.round(Math.random()*3),
      boots:47,rst_txt:'Container-Start',version:'demo',update:{current:'0.0.1',latest:'0.0.2',available:true,url:'#',enabled:true,error:'',age:120},ip:'192.168.1.20',port:80,mdns:'energyoptimizer.local',mdns_err:'',
      heartbeat:{hb_age:420,hb_ok:true}};}
  if(url.indexOf('/api/alarms/ack')===0){DM.alAck=true;return{ok:true};}
  if(url==='/api/alarms'){
    var al=[{id:3,name:T('Wechselrichter ohne Leistung'),sev:2,since:Math.floor(Date.now()/1000)-5400,
             mins:90,acked:!!DM.alAck,detail:T('{0}: 0 W (andere liefern {1} W)',[T('WR Süd'),2140])},
            {id:7,name:T('Steckdose {0} nicht erreichbar',[3]),sev:1,since:Math.floor(Date.now()/1000)-1200,
             mins:20,acked:false,detail:T('{0} ({1}) antwortet nicht',['Wallbox','192.168.1.53'])}];
    return{al:al,n:al.filter(function(a){return !a.acked;}).length,max:2};
  }
  if(url==='/api/selftest'){
    if(m==='POST'){DM.stAt=Date.now();return{ok:true};}
    if(!DM.stAt)return{running:false,done:false,age:-1,items:[]};
    if(Date.now()-DM.stAt<3000)return{running:true,done:false,age:-1,items:[]};
    return{running:false,done:true,age:Math.round((Date.now()-DM.stAt)/1000),items:[
      {n:T('Netzwerk'),s:0,d:T('Verbunden, IP {0}',['192.168.1.99'])},
      {n:T('Uhrzeit'),s:0,d:'01.08.2026 21:40'},
      {n:'SolarLog',s:0,d:T('{0}: {1} W Produktion, {2} W Verbrauch',['192.168.1.40',4210,2680])},
      {n:T('Steckdose {0}',[1])+': '+DM.names[0],s:0,d:T('Erreichbar, EIN, {0} W',[1980])},
      {n:T('Steckdose {0}',[3])+': Wallbox',s:2,d:T('{0} antwortet nicht',['192.168.1.53'])},
      {n:T('Steckdose {0}',[4])+': '+DM.names[3],s:1,d:T('Erreichbar und EIN, zieht aber 0 W – Verbraucher oder Sicherung prüfen')},
      {n:T('Datenverzeichnis'),s:0,d:T('{0} von {1} MB belegt',[0,25])},
      {n:T('Einstellungsspeicher'),s:0,d:T('In Ordnung')},
      {n:'MQTT',s:3,d:T('Nicht aktiviert')},
      {n:'Heartbeat',s:0,d:T('Letztes Lebenszeichen vor {0} s',[420])},
      {n:T('Alarmzentrale'),s:1,d:T('{0} offene Störung(en) – siehe Dashboard',[2])}]};
  }
  if(url==='/api/config/import')return{ok:true,msg:T('Einstellungen übernommen. Passwörter und Heartbeat-URL bleiben unverändert.')};
  if(url==='/api/solarlog/raw')return{
    main:'{"801":{"170":{"101":"4210","110":"2680","105":"18240"}},"858":[]}',
    dev:'{"740":{"0":"10.0.0.1 / SN123","1":"10.0.0.2 / SN456","2":"Err"},"608":{"0":"Power","1":"Shutdown"},"782":{"0":"2140","1":"0"}}',
    age:120,devs:[{i:0,name:T('WR Nord'),st:'Power',w:2140,max:4100},
                  {i:1,name:T('WR Süd'),st:'Shutdown',w:0,max:3900},
                  {i:2,name:T('Zähler'),st:'FeedIn',w:524,max:0}]};
  if(url.indexOf('/api/ideas/delete')===0){
    var di=+(url.match(/id=(\d+)/)||[])[1];
    DM.ideas=(DM.ideas||[]).filter(function(o){return o.id!==di;});
    return{ok:true};
  }
  if(url==='/api/ideas'){
    if(!DM.ideas)DM.ideas=[
      {id:1,status:0,prio:2,area:1,created:0,updated:Math.floor(Date.now()/1000)-86400,
       title:T('Tagesertrag als Stundenbalken'),text:T('Im Dashboard zusätzlich zur Kurve je Stunde ein Balken – auf dem Handy leichter abzulesen.')},
      {id:2,status:1,prio:1,area:3,created:0,updated:Math.floor(Date.now()/1000)-3600,
       title:T('Sperrzeit je Gerät statt global'),text:T('Die Wallbox braucht eine längere Mindest-EIN-Zeit als der Boiler.')},
      {id:3,status:2,prio:0,area:6,created:0,updated:Math.floor(Date.now()/1000)-604800,
       title:T('Ereignisprotokoll als CSV'),text:''}];
    if(m==='POST'){
      var b=JSON.parse((opts&&opts.body)||'{}');
      var e=null;
      DM.ideas.forEach(function(o){if(o.id===b.id)e=o;});
      if(!e){e={id:(DM.ideas.length?Math.max.apply(null,DM.ideas.map(function(o){return o.id;})):0)+1,
                status:0,prio:1,area:0,created:0,updated:0,title:'',text:''};DM.ideas.push(e);}
      ['title','text','status','prio','area'].forEach(function(k){if(b[k]!==undefined)e[k]=b[k];});
      e.updated=Math.floor(Date.now()/1000);
      return{ok:true,id:e.id};
    }
    return{max:30,n:DM.ideas.length,ideas:DM.ideas};
  }
  if(url==='/api/events/clear'){DM.ev=[];return{ok:true};}
  if(url.indexOf('/api/events')===0){
    if(!DM.ev){
      var nowE=Math.floor(Date.now()/1000),E=[];
      E.push([nowE-120,52100,2,0,1,3,2180,DM.names[0]]);
      E.push([nowE-900,51320,2,3,5,1,0,DM.names[3]]);
      E.push([nowE-2400,49820,3,2,0,0,0,DM.names[2]]);
      E.push([nowE-5400,46820,2,1,2,1,-640,DM.names[1]]);
      E.push([nowE-9000,43220,4,1,3,1,0,DM.names[1]]);
      E.push([nowE-21600,30620,5,-1,0,1,0,'192.168.1.40']);
      E.push([nowE-22100,30120,6,-1,0,1,0,T('seit {0}',['31 min'])]);
      E.push([nowE-46000,10,1,-1,6,1,0,'Start #47']);
      DM.ev=E;
    }
    var fm=(url.match(/dev=([^&]+)/)||[])[1];
    var rows=DM.ev.filter(function(r){
      if(fm===undefined)return true;
      if(fm==='sys')return r[3]<0;
      return r[3]===+fm;
    });
    return{n:DM.ev.length,ev:rows};
  }
  if(url==='/api/discover'){
    if(m==='POST'){DSCAN={running:true,done:false,t:Date.now()};return{ok:true};}
    var el=(Date.now()-DSCAN.t)/1000;
    if(DSCAN.running&&el>4){DSCAN.running=false;DSCAN.done=true;}
    var devs=DSCAN.done?[{ip:'192.168.1.61',name:T('Shelly Garage'),model:'Plus 1PM',id:'shellyplus1pm-aa11bb'},
      {ip:'192.168.1.62',name:T('Shelly Keller'),model:'Plug S',id:'shellyplug-s-cc22dd'}]:[];
    return{running:DSCAN.running,done:DSCAN.done,count:devs.length,devices:devs};
  }
  if(/\/api\/shelly\/(\d+)\/(on|off|autoon|autooff)/.test(url)){
    var mm=url.match(/\/api\/shelly\/(\d+)\/(\w+)/),idx=+mm[1],cmd=mm[2];
    var mo=url.match(/min=(\d+)/);
    DM.ov[idx]=(mo&&+mo[1]>0)?(+mo[1]*60):0;
    if(cmd==='on'){DM.on[idx]=true;DM.auto[idx]=false;}
    else if(cmd==='off'){DM.on[idx]=false;DM.auto[idx]=false;}
    else if(cmd==='autoon')DM.auto[idx]=true;
    else if(cmd==='autooff')DM.auto[idx]=false;
    return{ok:true};
  }
  if(url==='/api/save')return{ok:true,reboot:false};
  if(url==='/api/restart')return{ok:true};
  if(url==='/api/refresh')return{ok:true};
  if(url==='/api/history'){
    var now=new Date(),start=new Date(now.getFullYear(),now.getMonth(),now.getDate(),0,0,0);
    var startEp=Math.floor(start.getTime()/1000),endEp=Math.floor(now.getTime()/1000),pts=[];
    for(var ep=startEp;ep<=endEp;ep+=300){
      var h=(ep-startEp)/3600;
      var prod=(h<6||h>20)?0:Math.max(0,Math.round(5200*Math.sin((h-6)/14*Math.PI)+(Math.random()*120-60)));
      var cons=Math.round(1300+600*Math.sin(h/3)+((h>17&&h<22)?1400:0)+Math.random()*80);
      var p=[ep,prod,cons,cons-prod],w=[];
      w.push(prod>2500?Math.round(1800*(.8+Math.random()*.2)):0);
      w.push((h>17&&h<22)?Math.round(900+Math.random()*300):0);
      w.push(0);
      w.push(prod>3500?Math.round(800*(.7+Math.random()*.3)):0);
      var mask=0;
      for(var wi=0;wi<4;wi++){p.push(w[wi]);if(w[wi]>0)mask|=(1<<wi);}
      p.push(mask);   // Schaltzustand als Bitmaske (Index 4+n) wie im Gerät
      pts.push(p);
    }
    return{t:endEp,n:4,dev:DM.names,pts:pts};
  }
  if(url==='/api/history_daily'){
    if(!DM.daily){
      var out=[],now=new Date();
      for(var yy=now.getFullYear()-1;yy<=now.getFullYear();yy++){
        for(var mm=0;mm<12;mm++)for(var dd=1;dd<=28;dd++){
          var dt=new Date(yy,mm,dd);
          if(dt>now)break;
          var season=Math.sin((dt.getMonth()+0.5)/12*Math.PI);
          var prod=Math.round((6000+52000*season)*(0.55+((dd*7+mm*13+yy)%40)/50));
          var cons=Math.round(prod*(0.75+((dd*3+mm)%25)/100));
          out.push([yy*10000+(mm+1)*100+dd,prod,cons,Math.round(cons*0.3),Math.round(prod*0.35),1]);
        }
      }
      DM.daily=out;
    }
    return{days:DM.daily};
  }
  if(url.indexOf('/api/energy')===0){
    var rng=(url.match(/range=(\w+)/)||[])[1]||'week';
    var monthly=rng==='year',N=monthly?12:(rng==='month'?30:7),nowd=new Date(),bk=[];
    for(var i=0;i<N;i++){var back=N-1-i,t;
      if(monthly){t=Math.floor(new Date(nowd.getFullYear(),nowd.getMonth()-back,1,12).getTime()/1000);}
      else{t=Math.floor(new Date(nowd.getFullYear(),nowd.getMonth(),nowd.getDate()-back,0,0).getTime()/1000);}
      var scale=monthly?30:1;
      var pe=Math.round((18000+Math.random()*14000)*scale),ce=Math.round((11000+Math.random()*7000)*scale);
      bk.push({t:t,pe:pe,ce:ce,fe:Math.round(pe*(0.3+Math.random()*0.3)),ie:Math.round(ce*(0.2+Math.random()*0.3)),
        pw:Math.round(pe/24/scale),cw:Math.round(ce/24/scale)});
    }
    return{range:rng,monthly:monthly,n:N,buckets:bk};
  }
  return{};
}
