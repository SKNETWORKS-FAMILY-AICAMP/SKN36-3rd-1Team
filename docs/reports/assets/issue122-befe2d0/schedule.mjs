import {tab,connect} from './cdp.mjs';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const t=await tab(); await t.nav('http://127.0.0.1:18305/');await t.wait('Boolean(document.querySelector("#policy-explorer .pcard"))');
const all=(await (await fetch('http://127.0.0.1:18300/api/policies?limit=500')).json()).items;
const rows=[];
for(const id of ['POL_YC_065','POL_BOK_065','POL_004','POL_009','POL_YC_007']){
 const p=all.find(p=>p.policy_id===id);
 const q=p.policy_name;
 await t.ev('(()=>{const i=document.querySelector("input[type=search]");Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(i,'+JSON.stringify(q)+');i.dispatchEvent(new Event("input",{bubbles:true}));})()');
 const card='[...document.querySelectorAll("#policy-explorer .pcard")].find(c=>[...c.querySelectorAll("a")].some(a=>a.hash.includes('+JSON.stringify(id)+')))';
 await t.wait('Boolean('+card+')');
 const dom=await t.ev('((c)=>({calendar:!!c.querySelector(".calendar-link"),countdown:c.querySelector(".schedule-countdown")?.textContent??null,summary:c.querySelector("summary").textContent,apply:[...c.querySelectorAll("a")].some(a=>a.textContent.includes("신청하기"))}))('+card+')');
 assert.equal(dom.calendar,id==='POL_YC_007');
 assert.equal(!!dom.countdown,id==='POL_YC_007');
 rows.push({id,query:q,application_schedule:p.application_schedule,dom});
 if(id==='POL_YC_007'){
 const link='[...'+card+'.querySelectorAll("a")].find(a=>a.href.includes("/policies/"))';
 await t.ev(card+'.querySelector("summary").click()');
 const href=await t.ev(link+'.href');await t.ev(link+'.click()');
 for(let n=0;n<35;n++){const child=(await (await fetch('http://127.0.0.1:18303/json/list')).json()).find(a=>a.url===href);if(child){const d=await connect(child);await d.wait('Boolean(document.querySelector("h1"))');assert.equal(await d.ev('document.querySelector("h1").textContent.trim()'),q);rows.at(-1).detail=href;d.close();break}await new Promise(r=>setTimeout(r,150));}
 assert.equal(rows.at(-1).detail,href);
 }
}
await fs.writeFile(new URL('schedule.json',import.meta.url),JSON.stringify(rows,null,2));console.log(rows);t.close();
