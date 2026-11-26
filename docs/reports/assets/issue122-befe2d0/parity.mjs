import {tab} from './cdp.mjs';
import fs from 'node:fs/promises';
const t=await tab();await t.nav('http://127.0.0.1:18305/');await t.wait('Boolean(document.querySelector("#policy-explorer .pcard"))');
const out=[];
for(const q of ['모두의카드','모두의 카드','K-패스','k-패스','K패스','k패스','위원회','글로벌첨단전략산업']){
 await t.ev('(()=>{const i=document.querySelector("input[type=search]");Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(i,'+JSON.stringify(q)+');i.dispatchEvent(new Event("input",{bubbles:true}));})()');
 const react=await t.ev('[...document.querySelectorAll("#policy-explorer .pcard a")].filter(a=>a.hash.startsWith("#/map?policy=")).map(a=>a.hash.split("=").at(-1)).sort()');
 const query='/api/policies?limit=500&q='+encodeURIComponent(q);
 const read=port=>fetch('http://127.0.0.1:'+port+query).then(r=>r.json());
 const [dev,pr]=await Promise.all([read(18300),read(18308)]);
 const ids=r=>r.items.map(p=>p.policy_id).sort();
 out.push({q,react,dev:ids(dev),pr136:ids(pr),parity:JSON.stringify(react)===JSON.stringify(ids(pr)),selected:pr.items.filter(p=>['POL_YC_065','POL_YC_007'].includes(p.policy_id)).map(p=>({id:p.policy_id,apply:p.official_apply_url,source:p.source_url,status:p.reception_status}))});
}
console.log(JSON.stringify(out,null,2));await fs.writeFile(new URL('pr136-parity.json',import.meta.url),JSON.stringify(out,null,2));t.close();
