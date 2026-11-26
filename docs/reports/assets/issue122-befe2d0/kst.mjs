import {tab} from './cdp.mjs';
import assert from 'node:assert/strict';
const t=await tab();
await t.send('Page.addScriptToEvaluateOnNewDocument',{source:`
 const RealDate=Date;
 const localDay=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new RealDate());
 const split=RealDate.parse(localDay+'T00:00:00+09:00')+86400000;
 const start=performance.now();
 window.Date=class extends RealDate {constructor(...args){super(...(args.length?args:[Date.now()]));}static now(){return split-6000+performance.now()-start;}};
 const nativeFetch=window.fetch;
 window.catalogCalls=[];
 window.fetch=async function(...args){
   const resp=await nativeFetch(...args);
   if(!String(args[0]).includes('/api/policies'))return resp;
   const data=await resp.json();const next=Date.now()>=split;
   window.catalogCalls.push(next?'next':'initial');
   if(next){data.evaluated_on=new RealDate(split+32400000).toISOString().slice(0,10);
   for(const item of data.items){const s=item.application_schedule;if(s){
    s.evaluated_on=data.evaluated_on;
    if(s.days_to_start!==null)s.days_to_start--;
    if(s.days_to_end!==null)s.days_to_end--;
   }}}
   return new Response(JSON.stringify(data),{status:resp.status,headers:resp.headers});
 };
`});
await t.nav('http://127.0.0.1:18305/');
const card='[...document.querySelectorAll("#policy-explorer .pcard")].find(c=>c.textContent.includes("글로벌첨단전략산업"))';
const badge=card+'?.querySelector(".schedule-countdown")?.textContent';
await t.wait('Boolean('+badge+')');const initial=await t.ev(badge);assert.match(initial,/D-4/);
await t.ev(card+'.querySelector("summary").click()');
await t.ev('[...'+card+'.querySelectorAll("button")].find(b=>b.textContent.includes("관심 정책 저장"))?.click()');
await t.ev('[...document.querySelectorAll("#policy-explorer button")].find(b=>b.textContent.startsWith("★ 관심 정책")).click()');
for(let i=0;i<60&&await t.ev(badge)!=='마감 D-3';i++)await new Promise(r=>setTimeout(r,180));
assert.equal(await t.ev(badge),'마감 D-3');
const before=await t.ev('catalogCalls.length');
await t.ev('window.dispatchEvent(new Event("focus"))');
await t.wait('catalogCalls.length>'+before);
await t.ev('[...document.querySelectorAll("#policy-explorer button")].find(b=>b.textContent.startsWith("전체 정책")).click()');
await t.ev('[...document.querySelectorAll("#policy-explorer button")].find(b=>b.textContent.startsWith("★ 관심 정책")).click()');
assert.equal(await t.ev(badge),'마감 D-3');
await t.shot('kst-saved-next-day');
console.log(JSON.stringify({initial,next:await t.ev(badge),calls:await t.ev('catalogCalls'),assumption:'sample_only browser clock and next-day catalog response'}));t.close();
