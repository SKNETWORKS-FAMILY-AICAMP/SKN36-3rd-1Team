import {tab,connect} from './cdp.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const t=await tab();await t.nav('http://127.0.0.1:18305/');await t.wait('Boolean(document.querySelector("#policy-explorer .pcard"))');
await t.ev('document.querySelector("#policy-explorer .pcard summary").click()');
await t.ev('document.querySelector("#policy-explorer .catalog-ask button").click()');
const schedule='[...document.querySelectorAll("section")].find(s=>s.querySelector("h2")?.textContent.includes("최근 상담의 신청 일정"))';
const link=schedule+'?.querySelector(".pcard a[target=_blank][href*=policies]")';
for(let i=0;i<270&&!(await t.ev('Boolean('+link+')'));i++)await new Promise(r=>setTimeout(r,200));
const href=await t.ev(link+'?.href');assert.match(href,/\/POL_YC_007\//);
await t.ev(schedule+'.querySelector(".pcard summary").click()');
const existing=new Set((await (await fetch('http://127.0.0.1:18303/json/list')).json()).map(x=>x.id));
await t.ev(link+'.click()');
let newTab;for(let i=0;i<30&&!newTab;i++){await new Promise(r=>setTimeout(r,180));newTab=(await (await fetch('http://127.0.0.1:18303/json/list')).json()).find(x=>!existing.has(x.id)&&x.url===href);}
assert.ok(newTab);const d=await connect(newTab);await d.wait('Boolean(document.querySelector("h1"))');const title=await d.ev('document.querySelector("h1").textContent.trim()');await d.shot('recent-schedule-detail');d.close();t.close();
const js=await tab();await js.send('Emulation.setScriptExecutionDisabled',{value:true});const url='http://127.0.0.1:18301/policies/POL_STG_MOLIT_002/';await js.nav(url);
const html=await js.ev('document.body.innerText');const verified=Object.fromEntries(['신청 기간','제출서류','공식 원문'].map(k=>[k,html.includes(k)]));
const result={recentSchedule:{href,title},jsDisabled:{url,title:await js.ev('document.querySelector("h1").textContent.trim()'),verified,body:html,fetchXhr:js.events.filter(e=>e.method==='Network.requestWillBeSent'&&['XHR','Fetch'].includes(e.params.type)).length}};js.close();
console.log(JSON.stringify(result));await fs.writeFile(new URL('recent-schedule-nojs.json',import.meta.url),JSON.stringify(result,null,2));
