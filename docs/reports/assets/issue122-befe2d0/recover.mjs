import {tab} from './cdp.mjs';
import assert from 'node:assert/strict';
const t=await tab();const url='http://127.0.0.1:18307/policies/POL_YC_007/';
await t.nav(url);assert.equal(t.events.filter(e=>e.method==='Network.responseReceived'&&e.params.response.url===url).at(-1).params.response.status,503);
assert.match(await t.ev('document.body.innerText'),/정책 정보를 잠시 불러올 수 없어요/);
await t.shot('ssr-503');const back=await t.ev('[...document.links].find(a=>a.href.includes(":18305")).href');assert.equal(back,'http://127.0.0.1:18305/#/');
await fetch('http://127.0.0.1:18306/recover');await t.send('Page.reload');await t.wait('document.body.innerText.includes("글로벌첨단전략산업")');
assert.equal(t.events.filter(e=>e.method==='Network.responseReceived'&&e.params.response.url===url).at(-1).params.response.status,200);
await t.shot('ssr-503-recovered');console.log('PASS same-tab same-URL reload: HTTP 503 -> 200');t.close();
