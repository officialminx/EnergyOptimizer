measureTopbar();
initInfo();
// Zuletzt benutzten Tab wiederherstellen (PWA-Kaltstart landet sonst immer im Dashboard)
(function(){
  var t=null;try{t=localStorage.getItem('eo_tab');}catch(e){}
  if(t&&t!=='dash'&&$('page-'+t))navTo(t);
})();
ch=initChart();
histDaySet('');
// Die Jahresübersicht steht jetzt auf derselben Seite wie das Diagramm; ihre
// Zellbreite hängt an der Kartenbreite und muss beim Drehen mitgehen.
window.addEventListener('resize',function(){ch=initChart();redrawChart();drawHeatmap();redrawLive();});
poll();setInterval(poll,1000);
pollSys();setInterval(pollSys,10000);
// Alarmdetails (Dauer, Text) auffrischen, solange etwas ansteht – der Auslöser
// ist sonst nur die Änderung von Anzahl/Schweregrad in poll().
setInterval(function(){if(alSig&&alSig.charAt(0)!=='0')fetchAlarms();},30000);
// Die Tagespunkte laufend nachziehen (Sparklines, Schaltzeiten, Tagesdiagramm);
// die aggregierten Zeiträume ändern sich nur mit dem Tageswechsel und werden beim
// Öffnen der Seite bzw. per Nachladen geholt.
fetchHistory();fetchDayPts();setInterval(fetchDayPts,60000);
