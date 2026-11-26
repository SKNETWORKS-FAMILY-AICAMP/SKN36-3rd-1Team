import fs from 'node:fs/promises';
const cases=[
['sejong','세종시 위원회 청년위원은 지금 상시 신청할 수 있나요? 개별 모집 일정과 직접 신청 주소를 알려 주세요.','POL_YC_065'],
['faq','모두의 카드 한시 기준은 언제까지 적용되며, 미확정 환급률은 얼마인가요?','POL_STG_MOLIT_001'],
['normal','국민내일배움카드 공식 신청 링크와 출처를 알려 주세요.','POL_005'],
];
const result=[];
for(const [id,question,selected_policy_id] of cases){
 const request={question,profile:{age:29,region:'서울',income:null},history:[],selected_policy_id};
 const r=await fetch('http://127.0.0.1:18309/api/ask',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(request),signal:AbortSignal.timeout(75000)});const body=await r.json();result.push({id,request,http:r.status,body});console.log(JSON.stringify({id,http:r.status,answer:body.answer,checks:body.policy_checks?.map(p=>[p.policy_id,p.official_apply_url,p.reception_status]),docs:body.citations?.map(c=>c.document_id)}));await fs.writeFile(new URL('pr136-live.json',import.meta.url),JSON.stringify(result,null,2));
}
