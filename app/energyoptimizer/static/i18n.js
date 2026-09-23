/* Interface language. The pages are written in German; with <html lang="en">
   this script translates static text, text that scripts insert later and the
   strings scripts build themselves via T(). */
(function(){
var LANG=(document.documentElement.getAttribute('lang')||'de').slice(0,2);
var EN=/*EN*/{};
var D=LANG==='en'?EN:null;
function norm(s){return s.replace(/\s+/g,' ').trim();}
function fill(t,a){
  return a==null?t:t.replace(/\{(\w+)\}/g,function(m,k){return a[k]!=null?a[k]:m;});
}
// Texts with changing values (server messages, stored events) are matched
// against the dictionary entries that contain placeholders.
var PAT=null,MISS={},nMiss=0;
function pats(){
  PAT=[];
  for(var k in D){
    if(k.indexOf('{')<0)continue;
    var names=[],lit=k.replace(/\{\w+\}/g,'');
    if(lit.replace(/[^A-Za-zÄÖÜäöüß]/g,'').length<4)continue;
    var re=k.replace(/[.*+?^$()|[\]\\]/g,'\\$&').replace(/\\?\{(\w+)\\?\}/g,function(m,n){names.push(n);return '(.+?)';});
    PAT.push({re:new RegExp('^'+re+'$'),n:names,v:D[k],len:k.length});
  }
  PAT.sort(function(a,b){return b.len-a.len;});
}
function look(k){
  if(!D||!k)return null;
  if(D[k]!=null)return D[k];
  if(k.length>400||!/[A-Za-zÄÖÜäöü]{3}/.test(k))return null;
  if(!PAT)pats();
  if(MISS[k])return null;
  for(var i=0;i<PAT.length;i++){
    var m=PAT[i].re.exec(k);
    if(!m)continue;
    var a={};
    for(var j=0;j<PAT[i].n.length;j++){var x=m[j+1];a[PAT[i].n[j]]=D[x]!=null?D[x]:x;}
    return fill(PAT[i].v,a);
  }
  if(++nMiss>3000){MISS={};nMiss=0;}
  MISS[k]=1;
  return null;
}
function T(s,a){var r=D?look(s):null;return fill(r!=null?r:s,a);}
window.LANG=LANG;
window.T=T;
if(!D)return;

var INLINE={B:1,I:1,EM:1,STRONG:1,CODE:1,SMALL:1,A:1,BR:1,U:1,KBD:1,SUP:1,SUB:1};
var SKIP={SCRIPT:1,STYLE:1,TEXTAREA:1,SVG:1,svg:1,CODE:1,PRE:1};
var ATTRS=['placeholder','title','aria-label','alt','data-t'];
// An element whose content is running text with some inline markup
// (<b>, <code>, links) is translated as a whole, so the sentence stays intact.
function inlineOnly(el){
  var hasEl=false,hasTxt=false;
  for(var n=el.firstChild;n;n=n.nextSibling){
    if(n.nodeType===1){
      if(!INLINE[n.tagName]||n.id)return false;
      for(var c=n.firstChild;c;c=c.nextSibling)if(c.nodeType===1&&!INLINE[c.tagName])return false;
      hasEl=true;
    }else if(n.nodeType===3&&n.nodeValue.trim())hasTxt=true;
  }
  return hasEl&&hasTxt;
}
function text(n){
  var v=n.nodeValue,k=norm(v),t=look(k);
  // The second lookup stops a translation that would itself be translated again.
  if(t==null||t===k||look(t)!=null)return;
  var m=v.match(/^\s*/)[0],e=v.match(/\s*$/)[0];
  n.nodeValue=m+t+e;
}
function attrs(el){
  for(var i=0;i<ATTRS.length;i++){
    var a=el.getAttribute(ATTRS[i]);
    if(a){var k=norm(a),t=look(k);if(t!=null&&t!==k&&look(t)==null)el.setAttribute(ATTRS[i],t);}
  }
}
function walk(el){
  if(el.nodeType===3){text(el);return;}
  if(el.nodeType!==1||SKIP[el.tagName])return;
  attrs(el);
  if(inlineOnly(el)){
    var k=norm(el.innerHTML);
    if(D[k]!=null&&D[k]!==k){el.innerHTML=D[k];return;}
  }
  for(var n=el.firstChild;n;n=n.nextSibling)walk(n);
}
function translate(root){walk(root||document.body);}
window.i18nTranslate=translate;

var obs=new MutationObserver(function(list){
  for(var i=0;i<list.length;i++){
    var r=list[i];
    if(r.type==='characterData')text(r.target);
    else if(r.type==='attributes')attrs(r.target);
    else{
      var t=r.target;
      if(t.nodeType===1&&inlineOnly(t)){var h=norm(t.innerHTML);if(D[h]!=null&&D[h]!==h){t.innerHTML=D[h];continue;}}
      for(var j=0;j<r.addedNodes.length;j++)walk(r.addedNodes[j]);
    }
  }
});
function start(){
  if(document.title){var k=norm(document.title);if(D[k]!=null&&D[k]!==k)document.title=D[k];}
  translate(document.body);
  obs.observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:ATTRS});
}
if(document.body)start();else document.addEventListener('DOMContentLoaded',start);
})();
