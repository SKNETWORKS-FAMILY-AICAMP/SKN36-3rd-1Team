import {tab,connect} from './cdp.mjs';
import assert from 'node:assert/strict';
const t=await tab();await t.nav('http://127.0.0.1:18307/policies/POL_FAKE_404/');
const link=await t.ev('[...document.links].find(a=>a.href.endsWith("/#/")).href');assert.equal(link,'http://127.0.0.1:18305/#/');
await t.ev('[...document.links].find(a=>a.href.endsWith("/#/")).click()');
let h=t;
for(let i=0;i<35;i++){if(await t.ev('location.port==="18305"'))break;const r=(await (await fetch('http://127.0.0.1:18303/json/list')).json()).find(a=>a.url===link);if(r){h=await connect(r);break};await new Promise(r=>setTimeout(r,150));}
await h.wait('location.port==="18305" && document.body.innerText.includes("나에게 필요한 정부 지원 정책")');
console.log('PASS 404 home link navigated to running React'); if(h!==t)h.close();t.close();
