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
function T(s,a){return fill(D&&D[s]!=null?D[s]:s,a);}
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
  var v=n.nodeValue,k=norm(v);
  if(!k||D[k]==null)return;
  var m=v.match(/^\s*/)[0],e=v.match(/\s*$/)[0];
  n.nodeValue=m+D[k]+e;
}
function attrs(el){
  for(var i=0;i<ATTRS.length;i++){
    var a=el.getAttribute(ATTRS[i]);
    if(a){var k=norm(a);if(D[k]!=null)el.setAttribute(ATTRS[i],D[k]);}
  }
}
function walk(el){
  if(el.nodeType===3){text(el);return;}
  if(el.nodeType!==1||SKIP[el.tagName])return;
  attrs(el);
  if(inlineOnly(el)){
    var k=norm(el.innerHTML);
    if(D[k]!=null){el.innerHTML=D[k];return;}
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
      if(t.nodeType===1&&inlineOnly(t)&&D[norm(t.innerHTML)]!=null){t.innerHTML=D[norm(t.innerHTML)];continue;}
      for(var j=0;j<r.addedNodes.length;j++)walk(r.addedNodes[j]);
    }
  }
});
function start(){
  if(document.title){var k=norm(document.title);if(D[k]!=null)document.title=D[k];}
  translate(document.body);
  obs.observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:ATTRS});
}
if(document.body)start();else document.addEventListener('DOMContentLoaded',start);
})();
