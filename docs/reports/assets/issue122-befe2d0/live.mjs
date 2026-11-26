import {tab,connect} from './cdp.mjs';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
let cases=[
['RQ040',30,'공무원도 국민내일배움카드를 발급받을 수 있나요?'],
['RQ042',25,'부모님 집에 월세를 내고 살면 청년월세 지원을 받을 수 있나요?'],
['multi',29,'교육비·구직·교통 지원 정책이 있나요?'],
['selected-007',29,null,'글로벌첨단전략산업'],
['official',25,null,'청년내일저축계좌'],
['bok061',35,null,'장애아가족양육지원'],
['gov061',29,null,'장애인 평생교육이용권 우수이용자'],
...[74,75,76,80].map(a=>['age'+a,a,null,'국민내일배움카드']),
];
const out=[]; if(process.argv[2])cases=cases.filter(c=>c[0]===process.argv[2]);
for(const [id,age,question,search] of cases){
 const t=await tab();
 try{
 await t.nav('http://127.0.0.1:18305/');
 await t.wait('Boolean(document.querySelector("#age"))');
 const input=async(selector,value)=>t.ev('(()=>{const i=document.querySelector('+JSON.stringify(selector)+');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(i,'+JSON.stringify(value)+');i.dispatchEvent(new Event("input",{bubbles:true}));})()');
 await input('#age',String(age));
 if(question){
 await input('input[aria-label="정책 질문"]',question);
 await t.ev('document.querySelector("button.go").click()');
 }else{
 await t.wait('Boolean(document.querySelector("#policy-explorer .pcard"))');
 await input('input[type=search]',search);
 const card='[...document.querySelectorAll("#policy-explorer .pcard")].find(c=>c.querySelector("summary").textContent.includes('+JSON.stringify(search)+'))';
 await t.wait('Boolean('+card+')');
 await t.ev(card+'.querySelector("summary").click()');
 await t.ev(card+'.querySelector(".catalog-ask button").click()');
 }
 let hit,started;
 for(let i=0;i<330;i++){
 started=t.events.find(e=>e.method==='Network.requestWillBeSent'&&e.params.request.url.endsWith('/api/ask')&&e.params.request.method==='POST');
 if(started)hit=t.events.find(e=>e.method==='Network.loadingFinished'&&e.params.requestId===started.params.requestId);
 if(hit)break;
 await new Promise(r=>setTimeout(r,180));
 }
 assert.ok(hit,'No completed ask request');
 const request=JSON.parse(started.params.request.postData);
 const http=t.events.find(e=>e.method==='Network.responseReceived'&&e.params.requestId===hit.params.requestId).params.response.status;
 const body=JSON.parse((await t.send('Network.getResponseBody',{requestId:hit.params.requestId})).body);
 await new Promise(r=>setTimeout(r,500));
 let returnState=null;
 const dom=await t.ev('(()=>{const cards=[...document.querySelectorAll(".turn .policy-list details.pcard")]; const before=cards.filter(c=>c.open).length; if(cards.length&&!cards[0].open)cards[0].querySelector("summary").click();return {cardCount:cards.length,openCount:before, cards:cards.map(c=>({name:c.querySelector("summary b")?.textContent,detail:[...c.querySelectorAll("a")].find(a=>a.href.includes(":18301/"))?.href,official:[...c.querySelectorAll(".policy-official li")].map(r=>[r.textContent,r.querySelector("a")?.href]),exclusions:c.querySelector(".policy-exclusions")?.textContent??null,text:c.textContent.slice(0,6200)})),links:[...document.querySelectorAll(".turn .srcs a")].filter(a=>a.href.startsWith("https:")).slice(0,40).map(a=>a.href)}})()');
 if(id==='selected-007'&&dom.cards[0]?.detail){
 const pre=await t.ev('document.body.innerText');
 await t.ev('[...document.querySelectorAll(".turn .policy-list details.pcard a")].find(a=>a.href.includes(":18301/")).click()');
 let target; for(let i=0;i<30&&!target;i++){await new Promise(r=>setTimeout(r,180));target=(await (await fetch("http://127.0.0.1:18303/json/list")).json()).find(x=>x.url===dom.cards[0].detail);}
 assert.ok(target);
 const detail=await connect(target);const name=await detail.ev('document.querySelector("h1")?.textContent');detail.close();
 await fetch('http://127.0.0.1:18303/json/close/'+target.id);await t.send('Page.bringToFront');await new Promise(r=>setTimeout(r,600));
 returnState={name,questionStillVisible:(await t.ev('document.body.innerText')).includes(request.question),beforeAfterEqual:pre===await t.ev('document.body.innerText'),selectedPolicyId:request.selected_policy_id};
 }
 await t.ev('document.querySelector(".turn .policy-official, .turn .policy-exclusions")?.scrollIntoView({block:"center",behavior:"instant"})'); await new Promise(r=>setTimeout(r,400)); await t.shot('live-'+id);
 out.push({id,request,http,body,dom,returnState});
 console.log(JSON.stringify({id,http,policy:request.selected_policy_id,checks:body.policy_checks?.map(p=>p.policy_id),cards:dom.cardCount,returnState}));
 }catch(e){out.push({id,error:e.message});console.log(id,e.message)}
 finally{await fs.writeFile(new URL(process.argv[2]?'ask-browser-'+process.argv[2]+'.json':'ask-browser.json',import.meta.url),JSON.stringify(out,null,2));t.close();await fetch('http://127.0.0.1:18303/json/close/'+t.target.id);}
}
