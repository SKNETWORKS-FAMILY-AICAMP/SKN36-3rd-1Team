import {tab,connect} from './cdp.mjs';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const home='http://127.0.0.1:18305/', ssr='http://127.0.0.1:18301/policies/';
const t=await tab();
const log=[];
const check=async(id,f)=>{try{const actual=await f();log.push({id,status:'PASS',actual});}catch(e){log.push({id,status:'FAIL',actual:e.message});} console.log(JSON.stringify(log.at(-1)));await fs.writeFile(new URL('browser.json',import.meta.url),JSON.stringify(log,null,2));};
const cards='[...document.querySelectorAll("#policy-explorer .pcard")]';
const first=cards+'[0]';
const setup=async()=>{await t.nav(home);await t.wait('Boolean('+first+')');};
const button=(s)=>t.ev('[...document.querySelectorAll("button")].find(b=>b.textContent.trim().startsWith('+JSON.stringify(s)+')).click()');
const open=()=>t.ev(first+'.querySelector("summary").click()');
const search=async(q)=>{await t.ev('(()=>{const i=document.querySelector("input[type=search]");Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(i,'+JSON.stringify(q)+');i.dispatchEvent(new Event("input",{bubbles:true}));})()');};
const targetList=async()=>await (await fetch('http://127.0.0.1:18303/json/list')).json();
async function openDetails(parent=t){
 const existing=new Set((await targetList()).map(x=>x.id));
 const href=await parent.ev(first+'.querySelector("a[target=_blank][href*=policies]").href');
 await parent.ev(first+'.querySelector("a[target=_blank][href*=policies]").click()');
 for(let i=0;i<30;i++){
  const child=(await targetList()).find(x=>!existing.has(x.id)&&x.type==='page'&&x.url.includes('/policies/'));
  if(child){const detail=await connect(child);await detail.wait('document.readyState==="complete"');assert.equal(await detail.ev('location.href'),href);return detail}
  await new Promise(r=>setTimeout(r,150));
 }
 throw Error('new tab did not load');
}
await check('home-list',async()=>{await setup();await t.ev('localStorage.clear()');await t.send('Page.reload');await t.wait('Boolean('+first+')');assert.match(await t.ev('document.body.innerText'),/전체 정책 249/);return 'React home and 249 policies';});
await check('07-1-detail-and-07-2-home-return',async()=>{await open();const detail=await openDetails();assert.equal(await detail.ev('window.opener===null'),true);assert.equal(await detail.ev('Boolean(document.querySelector("meta[content=django-ssr]"))'),true);const title=await detail.ev('document.querySelector("h1").textContent.trim()');assert.equal(title,await t.ev(first+'.querySelector("summary b").textContent.trim()'));await detail.shot('ssr-detail');detail.close();await fetch('http://127.0.0.1:18303/json/close/'+detail.target.id);await t.send('Page.bringToFront');await new Promise(r=>setTimeout(r,700));assert.equal(await t.ev(first+'.open'),true);return {title,url:detail.target.url,openAfterReturn:true};});
await check('07-2-saved-return',async()=>{if(!await t.ev(first+'.open'))await open();await button('☆ 관심 정책 저장');await button('★ 관심 정책');await t.wait(cards+'.length===1');if(!await t.ev(first+'.open'))await open();const detail=await openDetails();detail.close();await fetch('http://127.0.0.1:18303/json/close/'+detail.target.id);await t.send('Page.bringToFront');await new Promise(r=>setTimeout(r,700));assert.equal(await t.ev(first+'.open'),true);await t.shot('saved-return');return 'Saved tab retained its open card';});
await check('07-4-compare',async()=>{await button('전체 정책');await t.wait(cards+'.length>1');await button('비교할 정책 고르기');await t.ev('[...document.querySelectorAll(".compare-pick input")].slice(0,2).forEach(i=>i.click())');await button('정책 비교하기');await t.wait('document.body.innerText.includes("자세히 비교하기")');await t.shot('compare');return 'Two policies rendered in comparison';});
await check('08-1-08-2-404-js-off',async()=>{const d=await tab();await d.send('Emulation.setScriptExecutionDisabled',{value:true});const u=ssr+'POL_YC_007/?theme=dark';await d.nav(u);assert.match(await d.ev('document.body.innerText'),/글로벌첨단전략산업/);assert.equal(await d.ev('document.documentElement.dataset.theme'),'dark');for(const id of ['POL_QA_NO_SUCH_POLICY_122','pol_yc_007']){const url=ssr+id+'/';const response=await fetch(url);assert.equal(response.status,404);await d.nav(url);assert.match(await d.ev('document.body.innerText'),/정책을 찾을 수 없어요/);assert.equal(await d.ev('[...document.links].some(a=>["http://127.0.0.1:18302/#/","http://127.0.0.1:18305/#/"].includes(a.href))'),true);}await d.shot('ssr-404');d.close();return 'SSR title with JS disabled; two HTTP 404s and home links';});
await check('08-mobile-dark-320-390',async()=>{const d=await tab();const evidence=[];for(const width of [320,390]){await d.send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:1,mobile:true});for(const [kind,url] of [['react',home],['ssr',ssr+'POL_YC_007/?theme=dark']]){await d.nav(url);if(kind==='react'){await d.wait('Boolean(document.querySelector(".pcard"))');await d.ev('if(document.documentElement.dataset.theme!=="dark")[...document.querySelectorAll("button")].find(b=>b.getAttribute("aria-label")==="화면 테마 바꾸기").click()');}const result=await d.ev('({theme:document.documentElement.dataset.theme,width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})');assert.equal(result.theme,'dark');assert.equal(result.width,width);assert.equal(result.scroll,width);evidence.push({kind,...result});await d.shot(kind+'-'+width+'-dark');}}d.close();return evidence;});
t.close();
