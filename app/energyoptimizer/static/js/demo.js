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
      on_day:Math.round(t%28800),on_tot:432000+i*86400,sw:184+i*37,
      real:on,virt:false,learned:[1840,0,0,620][i]});
    var sh=shelly[i];
    sh.st=sh.ov?'manual':!sh.auto?'disabled':sh.on?(sh.forced?'catchup':'surplus'):'waiting';
    sh.why=!reach?{c:'offline'}:sh.forced?{c:'catchup',m:sh.rt_on,q:sh.rt_min}
      :!sh.auto?{c:'auto_off',on:on}:on?{c:'running',s:1800+i*600}
      :!sh.win?{c:'window',h:DM.ws[i]}:sh.ot>0?{c:'switching_on',n:Math.max(1,6-sh.ot)}
      :{c:'waiting',need:DM.pw[i]+150,miss:Math.max(0,DM.pw[i]+150-Math.max(0,prod-cons))};
  }
  var ext=[{idx:0,name:T('Fingerbot Küche'),pw:600,pri:1,auto:true,on:false,rt_min:0,rt_on:0,forced:false,
    mx_min:0,cap:false,ot:0,ft:0,ov:0,ova:false,win:true,real:false,virt:false,st:'waiting',
    why:{c:'waiting',need:750,miss:Math.max(0,750-Math.max(0,prod-cons))},fb:false,fb_age:12,fb_bad:false,
    sch:false,schp:false,schr:-1,schn:-1,schs:false,lock:0}];
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
    has_prod:true,src:'solarlog',dry:!!DM.dry,up:Math.round(t)+3600,enabled:true,ext:ext,
    sl_next:Math.round(60-(t%60)),failsafe:false,battblk:(batt < -100),al_n:DM.alAck?1:2,al_sev:2,
    led:'connected',shelly:shelly,energy:energy,cost:cost,mqtt:{conn:false},cfg:cfg.loaded?undefined:demoCfg()};
}
function demoCfg(){
  var sh=[];for(var i=0;i<4;i++)sh.push({name:DM.names[i],ip:DM.ips[i],id:DM.ids[i],pw:DM.pw[i],pri:DM.pri[i],
    auto:DM.auto[i],rt:DM.rt[i],mx:DM.mx[i],ws:DM.ws[i],we:DM.we[i],wd:DM.wd[i]});
  return{sl_ip:'192.168.1.40',sl_port:80,sl_user:'',sl_fprod:'101',sl_fcons:'110',sl_fgrid:'',sl_fyday:'105',sl_fcday:'111',sl_fytot:'109',sl_fctot:'115',sl_fsoc:'103',sl_fbatt:'104',sl_avg_s:300,sl_fsafe:30,
    on_margin:150,off_margin:200,hyst_on_s:180,hyst_off_s:180,hyst_on:6,hyst_off:6,min_on_min:7,min_off_min:5,
    batt_grd:100,fw_start:20,fw_end:24,sh_count:4,ex_count:1,max_sh:16,max_ex:16,ip:'192.168.1.99',shelly:sh,
    ext:[{name:T('Fingerbot Küche'),pw:600,pri:1,auto:true,rt:0,mx:0,ws:0,we:24,wd:127,sch:'',st:'zigbee2mqtt/fingerbot'}],
    src:'solarlog',src_host:'',src_port:0,em_pv_ip:'',mb_preset:'sma',mb_unit:3,mb_prod:'',mb_grid:'',mb_batt:'',mb_soc:'',
    mqs_prod:'',mqs_cons:'',mqs_grid:'',mqs_batt:'',mqs_soc:'',src_poll_s:30,sh_poll_s:30,dry_run:!!DM.dry,
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
  if(url==='/api/save'){
    try{var sv=JSON.parse((opts&&opts.body)||'{}');if(sv.dry_run!==undefined)DM.dry=sv.dry_run==='true'||sv.dry_run===true;}catch(e){}
    return{ok:true,reboot:false};
  }
  if(url==='/api/sessions')return{sessions:[
    {id:'a1b2c3d4e5f6',created:Math.floor(DM.t0/1000)-86400*40,seen:Math.floor(Date.now()/1000),ua:'Safari · Mac',current:true},
    {id:'f6e5d4c3b2a1',created:Math.floor(DM.t0/1000)-86400*90,seen:Math.floor(Date.now()/1000)-3600*5,ua:'Safari · iPhone',current:false},
    {id:'0a0b0c0d0e0f',created:Math.floor(DM.t0/1000)-86400*200,seen:Math.floor(Date.now()/1000)-86400*12,ua:'Script',current:false}]};
  if(url==='/api/sessions/revoke')return{ok:true,n:2};
  if(url==='/api/source/test')return{ok:true,msg:T('Verbunden: {prod} W Produktion, {cons} W Verbrauch.').replace('{prod}',4210).replace('{cons}',2680)};
  if(/\/api\/shelly\/\d+\/learned/.test(url))return{ok:true,pw:1840};
  if(url==='/api/history/days')return{days:[]};
  if(url.indexOf('/api/simulate')===0){
    var sd=(url.match(/d=(\d{8})/)||[])[1],base=sd?new Date(+sd.slice(0,4),+sd.slice(4,6)-1,+sd.slice(6,8)):new Date();
    var t0s=Math.floor(new Date(base.getFullYear(),base.getMonth(),base.getDate()).getTime()/1000);
    return{day:+(sd||ymd(base).replace(/-/g,'')),points:52,step:900,prod_kwh:31.2,
      loads:[{kind:'shelly',idx:0,name:DM.names[0],pw:2000,on:[[t0s+9.5*3600,t0s+15*3600]],kwh:11,pv_kwh:10.4},
             {kind:'shelly',idx:1,name:DM.names[1],pw:1800,on:[[t0s+11*3600,t0s+13.5*3600]],kwh:4.5,pv_kwh:4.1},
             {kind:'shelly',idx:3,name:DM.names[3],pw:900,on:[[t0s+10*3600,t0s+12*3600],[t0s+13.75*3600,t0s+16*3600]],kwh:3.8,pv_kwh:3.8}],
      real:{feed_kwh:14.6,import_kwh:6.1},sim:{feed_kwh:5.2,import_kwh:6.9}};
  }
  if(url==='/api/restart')return{ok:true};
  if(url==='/api/refresh')return{ok:true};
  if(url==='/api/history'||url.indexOf('/api/history?')===0){
    var hd=(url.match(/d=(\d{8})/)||[])[1];
    var now=new Date(),start=new Date(now.getFullYear(),now.getMonth(),now.getDate(),0,0,0);
    if(hd){start=new Date(+hd.slice(0,4),+hd.slice(4,6)-1,+hd.slice(6,8));}
    var startEp=Math.floor(start.getTime()/1000),endEp=hd?startEp+86400-900:Math.floor(now.getTime()/1000),pts=[];
    for(var ep=startEp;ep<=endEp;ep+=300){
      var h=(ep-startEp)/3600;
      var prod=(h<6||h>20)?0:Math.max(0,Math.round(5200*Math.sin((h-6)/14*Math.PI)+(Math.random()*120-60)));
      var cons=Math.round(1300+600*Math.sin(h/3)+((h>17&&h<22)?1400:0)+Math.random()*80);
      var p=[ep,prod,cons,cons-prod,0],w=[];
      w.push(prod>2500?Math.round(1800*(.8+Math.random()*.2)):0);
      w.push((h>17&&h<22)?Math.round(900+Math.random()*300):0);
      w.push(0);
      w.push(prod>3500?Math.round(800*(.7+Math.random()*.3)):0);
      var mask=0;
      for(var wi=0;wi<4;wi++){if(w[wi]>0)mask|=(1<<wi);}
      // Wie im Gerät: Ein-Maske (Index 5), Lauf-Maske (6), danach die Leistungen.
      p.push(mask,0);for(var wj=0;wj<4;wj++)p.push(w[wj]);
      pts.push(p);
    }
    return{t:endEp,n:4,dev:DM.names,rw:[0,0,0,0],pts:pts,day:hd?+hd:undefined};
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
