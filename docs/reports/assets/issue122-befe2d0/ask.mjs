import fs from 'node:fs/promises';
const ageQ='이 정책의 나이 기준을 충족하나요?';
const cases=[
['rent35-1',ageQ,35,'POL_009'],['rent35-2',ageQ,35,'POL_009'],['rent35-3',ageQ,35,'POL_009'],
['age76-1','국민내일배움카드의 나이 기준을 충족하나요?',76,'POL_005'],
['age76-2','국민내일배움카드의 나이 기준을 충족하나요?',76,'POL_005'],
...[74,75,80].map(a=>['age'+a,'국민내일배움카드의 나이 기준을 충족하나요?',a,'POL_005']),
['RQ040','공무원도 국민내일배움카드를 발급받을 수 있나요?',30,null],
['RQ042','부모님 집에 월세를 내고 살면 청년월세 지원을 받을 수 있나요?',25,null],
['multi','교육비·구직·교통 지원 정책이 있나요?',29,null],
['official','청년내일저축계좌 공식 신청 자격기준과 문의처를 알려 주세요.',25,'POL_BOK_004'],
['bok061','신청 자격과 이용료의 기준을 구분해 주세요.',35,'POL_BOK_061'],
['gov061','공식 신청 자격기준과 필수 예외를 알려 주세요.',29,'POL_GOV_061'],
];
let out=[];
for(const [id,question,age,selected_policy_id] of cases){
 const request={question,profile:{age,region:'서울',income:null},history:[],selected_policy_id};
 try{const r=await fetch('http://127.0.0.1:18304/api/ask',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(request),signal:AbortSignal.timeout(75000)});const body=await r.json();out.push({id,request,http:r.status,body});console.log(id,r.status,body.detail?.code??body.status??'');}
 catch(e){out.push({id,request,error:e.name});console.log(id,e.name)}
 await fs.writeFile(new URL('ask-api.json',import.meta.url),JSON.stringify(out,null,2));
 if(out.length===1&&out[0].http===503)break;
}
