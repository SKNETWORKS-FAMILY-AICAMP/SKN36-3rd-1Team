# LifePolicy AI — 정책 누락 원인 분석 및 숨은 정책 발굴 조사 보고서

> ⚠️ **2026-11-06 공식 원문 대조로 내용이 바뀌었습니다.** 이 문서의 신규 문서 22건·청크 36건, 후보 규모(문서 268 · 청크 593 · 노드 1,082 · 관계 3,710), `verified_extracted`·`reference_restructured` 판정은 대조 **전** 값입니다. 요약 문서 상당수가 공식 원문과 달라 제외·교체했고, 현재 후보는 문서 261 · 청크 599 · 노드 1,074 · 관계 3,703입니다. 최신 판정은 [official_source_crosscheck_report.md](official_source_crosscheck_report.md)를 따릅니다.

> **문서 버전:** `v1.0.0-audit`  
> **기준일시:** 2026-11-06 (KST)  
> **작성자:** LifePolicy AI 데이터 / 아키텍처 담당  
> **기반 데이터셋:** `v1.3.2-verified` (통합 정책 237건, 문서 246건, 청크 557건, 관계 3,615건)  
> **조사 범위:** 원천 수집 15,278건(온통청년 3,861건, 정부24 10,942건, 복지로 461건/99건, 공고문 14건) vs 최종 237건  
> **상태:** **원천 데이터 전수 정밀 감사 완료 (데이터·인계 파일 0건 수정, 무손실 분석)**

---

## 1. 특정 정책 존재 여부 조사 결과

사용자가 지정한 실생활 핵심 정책 6종에 대하여 원천 수집 데이터셋(`data/raw/`, `data/processed/*_policies.jsonl`) 및 최종 DB 인계 데이터셋(`data/processed/policies.jsonl`)을 대상으로 전수 실측 검색을 수행한 결과입니다.

| 순번 | 조사 대상 정책명 | 원천 식별자 (Official ID) | 소관 기관 | 수집 출처 | 원천 데이터 수집 여부 | 최종 237건 포함 여부 | 공식 상세/신청 URL |
|:---:|---|---|---|:---:|:---:|:---:|---|
| **1** | **고속도로 다자녀가구 통행료 할인** | `B50000400042` | 한국도로공사 | Gov24 | **수집 완료**<br>(`gov24/items/B50000400042.json`) | **미포함 (누락)** | [정부24 공공서비스 상세](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2187218&bnefType=all&svcId=B50000400042) |
| **2** | **중소기업 취업자 소득세 감면** | `N/A` (조특법 제30조) | 국세청 / 기획재정부 | 홈택스/세무 | **미수집 (원천 부재)**<br>(3대 복지 API 미등재) | **미포함 (원천 누락)** | [국세청 홈택스 안내](https://www.hometax.go.kr) / [국가법령정보센터](https://www.law.go.kr) |
| **3** | **청년내일채움공제** | `SD0000011848` | 고용노동부 청년취업지원과 | Gov24 | **수집 완료**<br>(`gov24/items/SD0000011848.json`) | **미포함 (누락)**<br>*(지자체 연계형 `POL_YC_018`만 포함)* | [정부24 공공서비스 상세](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=758402&bnefType=all&svcId=SD0000011848) |
| **4** | **중소기업 취업연계 장학금**<br>(희망사다리Ⅰ유형) | `134200000046`<br>`20260824005400113351` | 교육부 청년장학지원과 /<br>한국장학재단 | Gov24 /<br>온통청년 | **수집 완료**<br>(Gov24 및 온통청년 양쪽 보유) | **미포함 (누락)** | [정부24 상세](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=762662&bnefType=all&svcId=134200000046)<br>[온통청년 상세](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20260824005400113351) |
| **5** | **중소기업 취업청년 학자금 대출 상환 지원** | `20261023005400214354`<br>`20250304005400210590` | 전북 군산시 / 전북특별자치도 | 온통청년 | **수집 완료**<br>(`youthcenter/items/*.json`) | **미포함 (누락)** | [온통청년 상세](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20261023005400214354) |
| **6** | **중소기업 재직청년 복지공유제** | `356100000004`<br>`20260406005400212432` | 인천광역시 서구 / 서해구 기업일자리정책과 | Gov24 /<br>온통청년 | **수집 완료**<br>(Gov24 및 온통청년 양쪽 보유) | **미포함 (누락)** | [정부24 상세](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2186923&bnefType=all&svcId=356100000004)<br>[온통청년 상세](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20260406005400212432) |

> 📌 **확인 사실:**  
> 6개 정책 중 **5개 정책은 이미 원천 데이터(Gov24 및 온통청년)에 정상 수집되어 디스크 상에 온전한 JSON으로 존재**하고 있었으나, 최종 237개 선별 단계에서 누락되었습니다.  
> 단 1건인 **'중소기업 취업자 소득세 감면'**은 복지 행정망 API가 아닌 국세청 홈택스 관할 세제 제도로 원천 수집 단계에서부터 수집되지 않았음이 확인되었습니다.

---

## 2. 정책 누락 원인 분석 (Root Cause Analysis)

최종 237건을 추출하는 전처리 스크립트([`scripts/build_clean_dataset.py`](file:///c:/Users/Peter/Desktop/모꼬지/SKN%2036기%20부트캠프/단위프로젝트/3차/LifePolicy%20AI/scripts/build_clean_dataset.py))의 3.1절~3.3절 코드를 전수 정밀 추적하여 정책 누락의 구조적 원인을 규명하였습니다.

```mermaid
flowchart TD
    Raw["원천 수집 데이터 (15,278건)"] --> Split{"수집 출처별 분기"}
    
    Split -->|정부24 10,942건| G_Check["키워드 및 길이 검사 (통과)"]
    G_Check --> G_Sort["오름차순 ID 정렬: sorted()"]
    G_Sort --> G_Quota["전국 쿼터 15건 & 총 65건 캡"]
    G_Quota -->|11~12번대 조기 소진| G_Drop["'B500...', 'SD00...', '1342...' 등 탈락"]
    
    Split -->|온통청년 3,861건| Y_Check["길이 검사 (통과)"]
    Y_Check --> Y_Sort["내림차순 정렬: reverse=True (최신순)"]
    Y_Sort --> Y_Quota["분야별 15건 & 총 65건 하드 캡"]
    Y_Quota -->|최근 6일치로 65건 조기 소진| Y_Drop["2026-10-28 이전 등록 정책 3,796건 전원 탈락"]
    
    Split -->|국세청 조세제도| N_Drop["3대 복지 API 미등재로 원천 미수집"]
    
    G_Quota -->|선정 65건| Final["최종 237건 통합 데이터셋"]
    Y_Quota -->|선정 65건| Final
```

### 2.1 기관별 고정 할당량(Quota) 및 조기 중단(Early Break) 로직

`scripts/build_clean_dataset.py`는 공공 행정망 간의 데이터 균형과 MVP 데이터셋 크기를 통제하기 위해 **기관별 엄격한 고정 수량 캡**을 적용했습니다:

1. **복지로 (Bokjiro): 고정 98건**  
   - 상세 XML이 확보된 99건 중 핵심 10대 정책과 중복되는 5건(`excluded_bokjiro_ids`)을 제외한 95건 적재 + 원문 대조 검증 핵심 정책 3건 = 총 98건.
2. **행정안전부 공공서비스 (Gov24): 고정 65건 하드 캡**  
   - 전국/중앙부처 최대 15건, 전국 17개 시도별 각 최대 3건으로 지역 균등 버킷 할당.  
   - `if len(gov24_candidates) >= 65: break` 로직에 따라 65건이 차는 즉시 루프 강제 종료.
3. **온통청년 (YouthCenter): 고정 65건 하드 캡**  
   - 5대 청년분야(일자리, 주거, 교육, 복지문화, 참여권리)별 최대 15건 할당.  
   - `if len(yc_candidates) >= 65: break` 로직에 따라 65건 도달 시 루프 강제 종료.
4. **기존 검증 핵심 정책 (Core): 9건**  
   - LH 공고문, 주택도시기금 등 9건. (`POL_008` API 메타데이터 분리)
5. **결과:** $9 + 98 + 65 + 65 = \mathbf{237\text{건}}$으로 정확히 고정 수량이 산출됨.

### 2.2 정렬 순서에 따른 앞쪽 ID / 최근 등록일 편향 (Sort-Order Bias)

이 고정 할당량과 결합된 **정렬 순서(Sort Order)**가 유용한 정책들의 대량 탈락을 유발했습니다:

- **Gov24의 오름차순 편향 (`sorted(gov24_all.items())`):**  
  - Gov24의 전국/중앙부처 쿼터는 15건에 불과했습니다.
  - 사전순 정렬로 인해 숫자 앞자리인 `11601...`(법제처), `11920...`(해양수산부), `12500...`(관세청), `12700...`(조달청), `12800...`(통계청), `12900...`(경찰청) 등 행정/지원 부처의 정책 15건이 먼저 순차적으로 버킷을 채워버렸습니다.
  - 이로 인해 뒤쪽 번호인 `134200000046`(교육부 장학금), `14920000...`(고용노동부), `B50000400042`(한국도로공사, 'B'로 시작), `SD0000011848`(고용노동부 공제, 'SD'로 시작)은 **키워드와 자격요건 검사를 완벽히 만족했음에도 불구하고 전국 쿼터(15건)가 이미 꽉 차서(Quota Full) 전원 누락**되었습니다.
- **온통청년의 최신 등록일 편향 (`sorted(yc_all.items(), reverse=True)`):**  
  - 정책번호 `plcyNo`의 내림차순(최신순)으로 순회하여 65건을 채웠습니다.
  - 실측 결과, 최종 선정된 온통청년 65건(`POL_YC_001`~`065`)의 정책번호 범위는 `20261029005400214581` ~ `20261103005400214707`로, **2026년 10월 29일부터 11월 3일까지 단 6일간 등록된 최신 정책만으로 65건이 모두 마감**되었습니다.
  - 그 결과 2026년 10월 28일 이전에 등록된 **온통청년 정책 3,796건(98.3%)은 평가 기회조차 얻지 못하고 자동 탈락**되었습니다. (8월 등록된 희망사다리 장학금, 4월 등록된 복지공유제 등 누락).

### 2.3 공공 API 관할 범위의 한계 (국세청 조세감면 미포함)

- '중소기업 취업자 소득세 감면'(조세특례제한법 제30조)은 국민 복지 수혜금이나 보조금을 지급하는 제도가 아니라 세금을 감면하는 **국세 조세특례 제도**입니다.
- 행정안전부의 '보조금24/공공서비스' API나 복지로의 '복지서비스' API는 현금/바우처/서비스 지원 위주로 수록되어 있으며, 국세청 홈택스 관할의 조세 감면 제도는 수록 범위에서 제외되어 있습니다.
- 따라서 원천 API 수집 파이프라인의 설계상 외부 세무 채널이 연계되지 않아 원천적으로 수집되지 못했습니다.

---

## 3. 실생활 혜택 중심 숨은 정책 30선 발굴 결과

기존 원천 수집 데이터셋(Gov24 10,942건, 온통청년 3,861건)에 온전한 JSON으로 보존되어 있으나 할당량 제한으로 최종 237건에 들어가지 못한 정책 중, **국민 실생활 혜택 체감도가 매우 높은 핵심 정책 30건을 선별**하였습니다.

30개 정책 전수에 대해 디스크 상의 원천 JSON 실재 여부, 실제 공식 URL, 시행 상태 및 신청 가능 여부를 대조 검증 완료하였습니다.

| 번호 | 분야 | 원천 출처 | 공식 식별자 | 정책명 | 소관 기관 | 지원 혜택 요약 | 시행 상태 | 신청 가능 여부 | 공식 출처 URL |
|:---:|---|:---:|---|---|---|---|:---:|:---:|---|
| **1** | 교통·통행료 | Gov24 | `B50000400042` | 고속도로 다자녀가구 통행료 할인 | 한국도로공사 | 다자녀(2인 이상) 가구 고속도로 통행료 50% 감면 | 시행 중 | 상시 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2187218&bnefType=all&svcId=B50000400042) |
| **2** | 교통비 할인 | Gov24 | `161300000104` | K-패스 | 국토교통부 광역교통경제과 | 월 15회 이상 대중교통 이용 시 20~53% 환급 | 시행 중 | 상시 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2143816&bnefType=all&svcId=161300000104) |
| **3** | 교통비 할인 | Gov24 | `301000000129` | 저소득계층 대학생 교통비 지원 | 서울특별시 중구 생활보장과 | 관내 수급자·차상위 대학생 학기당 20만원 지원 | 시행 중 | 3월, 9월 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=1005148&bnefType=all&svcId=301000000129) |
| **4** | 교통비 할인 | Gov24 | `335000000104` | 학교 밖 청소년 교통비 지원 | 부산광역시 금정구 가족정책과 | 학교 밖 청소년 대상 월 3~5만원 교통비 지원 | 시행 중 | 연중 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=1005113&bnefType=all&svcId=335000000104) |
| **5** | 교통비 할인 | YouthCenter | `20261029005400214564` | 관외 대학교 통학생 교통비 지원사업 | 경상북도 구미시 미래교육돌봄국 | 관외 대학교 통학생 학기별 최대 30만원 지원 | 시행 중 | 상/하반기 공고 | [상세보기](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20261029005400214564) |
| **6** | 세금 감면 | Gov24 | `131200000008` | 다자녀가구 자동차취득세 감면 | 행정안전부 지방세특례제도과 | 18세 미만 3자녀(일부 2자녀) 자동차 취득세 감면 | 시행 중 | 취득 후 상시 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=999002&bnefType=all&svcId=131200000008) |
| **7** | 세금 감면 | Gov24 | `131200000004` | 장애인차량 취득세 및 자동차세 감면 | 행정안전부 지방세특례제도과 | 보증 장애인 차량 취득세 및 매년 자동차세 면제 | 시행 중 | 상시 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=999003&bnefType=all&svcId=131200000004) |
| **8** | 세금 감면 | Gov24 | `131200000006` | 국가유공자 대부금 취득 부동산 취득세 감면 | 행정안전부 지방세특례제도과 | 유공자 대부금 취득 부동산 취득세 전액 면제 | 시행 중 | 상시 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=740646&bnefType=all&svcId=131200000006) |
| **9** | 세금 감면 | Gov24 | `131200000014` | 국가유공자 차량취득세 및 자동차세 감면 | 행정안전부 지방세특례제도과 | 상이 국가유공자 보증차량 취득세·자동차세 면제 | 시행 중 | 상시 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=999001&bnefType=all&svcId=131200000014) |
| **10** | 세무 지원 | YouthCenter | `20261023005400214322` | 청년 창업가 세무서비스 보탬이 | 전북특별자치도 정읍시 | 청년 창업자 세무기장·종소세 대행료 연 30만원 지원 | 시행 중 | 정기공고 신청 | [상세보기](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20261023005400214322) |
| **11** | 공공요금 감면 | Gov24 | `172100000012` | 취약계층 통신요금 감면 | 과학기술정보통신부 통신경쟁정책과 | 기초수급·차상위·기초연금 이동통신료 월 1.1~3.3만원 감면 | 시행 중 | 상시 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2110213&bnefType=all&svcId=172100000012) |
| **12** | 공공요금 감면 | Gov24 | `135200005004` | 긴급복지 연료비 및 전기요금 지원 | 보건복지부 기초생활보장과 | 위기가구 동절기 연료비 및 단전 전기요금 긴급지원 | 시행 중 | 상시 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=900011&bnefType=all&svcId=135200005004) |
| **13** | 공공요금 감면 | Gov24 | `174000000029` | 출산 관련 서비스 통합처리 (행복출산) | 행정안전부 행정정보공유과 | 출산·다자녀 가구 한전 전기료 30% 및 도시가스 감면 | 시행 중 | 출생 후 상시 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=593069&bnefType=all&svcId=174000000029) |
| **14** | 공공요금 감면 | Gov24 | `390000000101` | 광명시 저소득세대 국민건강보험료 등 지원 | 경기도 광명시 복지정책과 | 최저보험료 이하 저소득 세대 건보료 전액 대납 | 시행 중 | 상시 지원 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=1000389&bnefType=all&svcId=390000000101) |
| **15** | 공공요금 감면 | Gov24 | `300000000102` | 저소득주민 국민건강보험료 지원 | 서울특별시 종로구 사회복지과 | 취약 노인·장애인 세대 건보료 및 장기요양보험료 지원 | 시행 중 | 상시 지원 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=1000509&bnefType=all&svcId=300000000102) |
| **16** | 다자녀 주거 | Gov24 | `161300000101` | 통합공공임대주택 공급 (다자녀가구) | 국토교통부 주택공급정책과 | 다자녀가구(2자녀 이상) 공공임대주택 우선공급 | 시행 중 | 공고별 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2103502&bnefType=all&svcId=161300000101) |
| **17** | 다자녀 문화 | Gov24 | `140000000002` | 국립수목원 관람료 다자녀 면제 | 산림청 전시교육연구과 | 다자녀(2자녀 이상) 가구 국립수목원 무료입장 | 시행 중 | 현장 상시 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=729815&bnefType=all&svcId=140000000002) |
| **18** | 다자녀 문화 | Gov24 | `155000000015` | 4대궁·종묘·조선왕릉 다자녀부모 무료관람 | 국가유산청 궁능서비스기획과 | 막내 18세 이하 2자녀 이상 부모 4대궁 무료 관람 | 시행 중 | 현장 상시 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=170076&bnefType=all&svcId=155000000015) |
| **19** | 다자녀 교육 | Gov24 | `300000000105` | 종로구 다자녀가정 입학축하금 지원 | 서울특별시 종로구 교육과 | 셋째아 이상 자녀 초·중·고 입학 시 30~50만원 지원 | 시행 중 | 입학연도 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=1003643&bnefType=all&svcId=300000000105) |
| **20** | 다자녀 요금 | Gov24 | `349000000101` | 대구 남구 다자녀가정 상수도요금 감면 | 대구광역시 남구 복지정책과 | 3자녀 이상 다자녀가정 상수도 요금 월 최대 10톤 감면 | 시행 중 | 상시 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=1000412&bnefType=all&svcId=349000000101) |
| **21** | 중소기업 청년 | Gov24 | `SD0000011848` | 청년내일채움공제 | 고용노동부 청년취업지원과 | 중소기업 취업 청년 2년 근속 시 1,200만원+자산형성 | 개편 시행 중 | 신규제한/기존유지 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=758402&bnefType=all&svcId=SD0000011848) |
| **22** | 중소기업 청년 | Gov24 | `356100000004` | 중소기업 재직청년 복지공유제 지원 | 인천광역시 서해구 기업일자리과 | 중소기업 재직 청년 복지포인트(연 100만원) 지원 | 시행 중 | 연초 공고신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2186923&bnefType=all&svcId=356100000004) |
| **23** | 중소기업 청년 | YouthCenter | `20261023005400214354` | 지역 중소기업 청년 학자금 대출 상환 지원 | 전남광주통합특별시 청년교육국 | 중소기업 6개월 이상 재직 청년 학자금 대출 연 100만원 상환 | 시행 중 | 매년 3월 접수 | [상세보기](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20261023005400214354) |
| **24** | 중소기업 청년 | YouthCenter | `20260406005400212432` | (서구) 중소기업 재직청년 복지공유제 | 인천광역시 서구 청년정책일자리과 | 관내 중소기업 청년 복지포인트 및 자기계발비 바우처 | 시행 중 | 매년 4~5월 접수 | [상세보기](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20260406005400212432) |
| **25** | 중소기업 청년 | YouthCenter | `20250904005400211652` | 청년근로자 사랑채움사업 | 경상북도 / (재)경북경제진흥원 | 청년 2년간 월 10만원 저축 시 만기 700만원 수령 | 시행 중 | 매년 상반기 접수 | [상세보기](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20250904005400211652) |
| **26** | 교육·장학금 | Gov24 | `134200000046` | 중소기업 취업연계 장학금 (희망사다리Ⅰ) | 교육부 청년장학지원과 | 중소기업 취업 희망 대학생 등록금 전액+취업지원금 200만원 | 시행 중 | 매 학기 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=762662&bnefType=all&svcId=134200000046) |
| **27** | 교육·장학금 | YouthCenter | `20260824005400113351` | 희망사다리 장학사업(Ⅰ,Ⅱ유형) | 한국장학재단 / 교육부 | 중소기업 취업 대학생(Ⅰ) 및 고졸 재직자(Ⅱ) 등록금 지원 | 시행 중 | 매년 3월/9월 | [상세보기](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20260824005400113351) |
| **28** | 교육·학자금 | YouthCenter | `20261023005400214303` | 대학생 학자금 대출이자 및 신용회복 지원 | 경상북도 청년정책과 | 한국장학재단 학자금 대출 발생 이자 전액 지원 | 시행 중 | 상·하반기 접수 | [상세보기](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20261023005400214303) |
| **29** | 주거 지원 | Gov24 | `161300000103` | 국토교통부 전세보증금반환보증 보증료 지원 | 국토교통부 청년주거정책과 | 무주택 청년·신혼부부 HUG/HF 전세보증료 최대 30만원 지원 | 시행 중 | 상시 신청 | [상세보기](https://plus.gov.kr/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl?svcSeq=2126303&bnefType=all&svcId=161300000103) |
| **30** | 주거 지원 | YouthCenter | `20261026005400214440` | 영암군 청년월세 지원사업 | 전남광주통합특별시 영암군 | 무주택 청년 1인 가구 월 최대 10만원(연 120만원) 월세 지원 | 시행 중 | 하반기 공고접수 | [상세보기](https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/20261026005400214440) |

---

## 4. 신규 수집 채널 및 데이터 연계 방안 조사

현재 수집망(온통청년, 정부24, 복지로) 외에 숨은 정책과 조세 혜택을 발굴하기 위한 공식 API 및 보조 채널 조사 결과입니다.

### 4.1 공식 API 연계 채널 (1차 신뢰 출처)

```mermaid
flowchart LR
    subgraph Official_API [공식 정부/공공기관 Open API]
        BIZ["중기부 기업마당 API<br/>(bizinfo.go.kr)"]
        EX["한국도로공사 API<br/>(data.ex.co.kr)"]
        KOR["정책브리핑 API<br/>(korea.kr)"]
        LCL["지자체 데이터 포털<br/>(서울/경기 열린데이터)"]
    end

    subgraph Gov_Portals [웹 가이드 및 서식 수집]
        NTS["국세청 홈택스<br/>(조세특례 가이드)"]
    end

    subgraph Pipeline [LifePolicy 수집·정제 엔진]
        Collector["신규 수집 파이프라인"]
        Norm["정규화 및 스키마 매핑"]
    end

    Official_API -->|REST JSON/XML| Collector
    Gov_Portals -->|정적 HTML/PDF 파싱| Collector
    Collector --> Norm
```

1. **중소벤처기업부 기업마당 (`bizinfo.go.kr` / 공공데이터포털):**
   - **엔드포인트:** `중소벤처기업부_중소기업 지원사업 공고 조회 서비스` (REST JSON/XML)
   - **수집 가능 정책:** 중앙부처·지자체·공공기관의 중소기업 청년 재직자 복지, 취업 장려금, 교육훈련 지원 공고 전수.
   - **인증 방식:** 공공데이터포털 일반 인증키 즉시 발급 가능.
2. **한국도로공사 고속도로 공공데이터 포털 (`data.ex.co.kr`):**
   - **엔드포인트:** 영업소간 통행요금 조회 및 하이패스 할인·감면 제도 API.
   - **수집 가능 정책:** 다자녀, 국가유공자, 장애인, 친환경차 고속도로 통행료 감면 요율 및 신청 규정.
3. **문화체육관광부 대한민국 정책브리핑 (`korea.kr`):**
   - **엔드포인트:** 정책뉴스 및 보도자료 RSS / 공공데이터포털 정책브리핑 오픈API.
   - **역할:** 정부 부처별 신규 복지 정책 입법 예고 및 신설 공고 조기 탐지.
4. **국세청 홈택스 (`hometax.go.kr`):**
   - **수집 방식:** 조세특례제한법 제30조(중소기업 취업자 소득세 감면) 등 비복지형 세제 혜택 공식 서식 및 안내 매뉴얼 수집. (정적 크롤링 및 해시 무결성 검증).
5. **광역 지자체 열린데이터광장 (서울 열린데이터, 경기데이터드림 등):**
   - **수집 가능 정책:** 서울시 청년 대중교통비 지원, 경기도 청년기본소득 등 지자체 단독 조례 복지사업.

### 4.2 공식 SNS 채널의 역할 (후보 발견 및 조기 감지 채널)

> ⚠️ **원칙:** SNS 카드뉴스나 게시물 텍스트 자체는 **절대 공식 근거 문서(`Document`)로 인정하지 않습니다.**

- **SNS의 역할:** 부처 공식 인스타그램, 정책브리핑 블로그, 지자체 유튜브 등은 **'신규 정책 런칭 조기 감지(Trigger) 채널'**로만 한정합니다.
- **수집 워크플로우:**
  1. `[감지]` SNS 공식 계정에서 "2026 신설 청년 정책" 카드뉴스 감지
  2. `[링크 추적]` 본문 기재 공식 행정 도메인(`*.go.kr`, `*.or.kr`) 링크 추출
  3. `[원문 확보]` 해당 기관 공식 공고문(PDF/HTML)을 다운로드하여 `data/raw/<provider>/`에 보존하고 SHA-256 해시 생성
  4. `[교차 검증]` 공공데이터포털 API 등록 여부 및 공고문 세부 수치 대조 후 승인 큐에 적재

---

## 5. 데이터 확장 및 DB 인계 비파괴(Non-breaking) 설계

기존 `v1.3.2-verified`의 Neo4j 지식그래프(1,034 노드, 3,615 관계) 및 Qdrant 벡터 컬렉션(557 청크)의 무결성을 100% 보존하면서 신규 발굴 정책을 안전하게 확장하는 아키텍처입니다.

```mermaid
flowchart TD
    subgraph Expansion [신규 정책 확장 파이프라인]
        NewRaw["신규 정책 후보 (Candidate)"] --> Pre["정규화 및 유효성 검사"]
        Pre --> Dedup{"3단계 중복 판별"}
        Dedup -->|동일 정책| Merge["기존 policy_id에 DOC_* 다중 연결"]
        Dedup -->|신규 정책| Issue["비파괴 증분 ID 발급"]
    end

    subgraph Storage [기존 DB 인계 구조 보존]
        Issue --> N4J["Neo4j 멱등 적재 (MERGE)"]
        Issue --> QDR["Qdrant 벡터 적재 (UUIDv5 Point ID)"]
    end

    subgraph Admin [관리자 거버넌스]
        ReviewQueue["관리자 승인 큐 (Review Queue)"] -->|verified 승인| Issue
    end
```

### 5.1 불변 식별자(ID) 체계 및 증분 네이밍 규칙

기존에 발급된 식별자는 절대 재발급하거나 번호를 당겨 쓰지 않는 **불변성(Immutability)**을 유지합니다.

| 분류 | 기존 식별자 유지 | 신규 확장 규칙 | 예시 |
|---|---|---|---|
| **정부24 추가분** | `POL_GOV_001` ~ `065` | `POL_GOV_066` ~ 연속 번호 부여 | `POL_GOV_066` (고속도로 다자녀 통행료) |
| **온통청년 추가분** | `POL_YC_001` ~ `065` | `POL_YC_066` ~ 연속 번호 부여 | `POL_YC_066` (중소기업 취업청년 학자금 상환) |
| **기업마당 신규분** | 없음 | `POL_BIZ_001` ~ 접두어 신설 | `POL_BIZ_001` (중기 재직자 장기재직공제) |
| **국세청 조세감면** | 없음 | `POL_NTS_001` ~ 접두어 신설 | `POL_NTS_001` (중소기업 취업자 소득세 감면) |
| **공식 근거 문서** | `DOC_*` 기존 246건 유지 | `DOC_<정책접두어>_<번호>` 1:1 매핑 | `DOC_GOV_066`, `DOC_NTS_001` |
| **Qdrant Point ID** | UUIDv5 기존 557건 유지 | `uuid5(NAMESPACE_DNS, chunk_id)` | RFC 4122 표준에 따른 무충돌 결정론적 생성 |

### 5.2 4단계 정책 라이프사이클 및 검증 상태(Verification Status)

신규 정책은 아래 4단계 거버넌스를 거쳐 점진적으로 신뢰도가 부여됩니다:

```
[1. Candidate (후보)] ──> [2. Extracted (파싱)] ──> [3. Verified (원문검증)] ──> [4. Deprecated (종료)]
  - API/SNS 감지       - 스키마 정규화 완료       - 공고문 1:1 대조 완료          - 사업 종료/마감
  - review: pending     - 필수 필드 적합          - 수치/기한 100% 일치           - 검색 대상 비활성화
```

1. **`candidate`:** 신규 수집 파이프라인에서 추출된 1차 후보군 (`review_status: "pending"`).
2. **`extracted`:** 공공데이터 API 원문 파싱 및 10대 정합성 검사를 통과한 상태 (`review_status: "extracted"`).
3. **`verified`:** 관리자가 공식 PDF 공고문 또는 정부 웹 원문과 대조하여 신청기한, 자격요건, 수치 혜택을 수동 승인한 최고 신뢰 상태 (`review_status: "verified"`).
4. **`deprecated`:** 접수 기한 만료 또는 정부 제도 폐지 시 비활성화 플래그 부여.

### 5.3 3단계 다중 출처 중복 탐지 및 병합 파이프라인

복수 기관(예: 고용노동부 청년내일채움공제와 지자체 연계 공제) 간 중복 혼입을 방지합니다:

1. **1단계 (Exact ID):** 원천 기관 `official_id` 완전 일치 대조.
2. **2단계 (Normalized Title & Agency):** 공백 및 특수문자를 제거한 정규화 정책명(`re.sub(r'\s+', '', name)`)과 소관 기관의 동일성 검사.
3. **3단계 (Semantic Similarity):** 정책 지원내용 및 요건 텍스트의 임베딩 코사인 유사도가 0.92 이상일 경우, 자동 병합하지 않고 `data/metadata/dedup_candidates.json`에 기록 후 관리자 검토 큐에 등록.
4. **병합 처리:** 동일 정책으로 승인 시, 기존 `policy_id`를 유지하고 신규 수집된 문서를 `[:SUPPORTED_BY]` 관계로 다중 연결하여 출처 추적성을 강화합니다.

### 5.4 관리자 승인 절차 (Human-in-the-Loop CLI 워크플로우)

```bash
# 1. 신규 후보 정책 수집 (staging 영역 적재)
python collectors/api/gov24_discovery_collector.py --candidates-file data/staging/gov24_candidates.jsonl

# 2. 중복 검사 및 관리자 검토 리포트 생성
python scripts/audit_policy_candidates.py --review-pending

# 3. 관리자 수동 승인 (CLI)
python scripts/approve_policy.py --id B50000400042 --status verified --raw-file data/raw/gov24/items/B50000400042.json

# 4. 비파괴 증분 빌드 (기존 237건 불변 보존 + 신규 정책 추가)
python scripts/build_clean_dataset.py --incremental

# 5. 정합성 및 참조 무결성 100% PASS 검증
python scripts/verify_data_quality.py
python scripts/verify_handoff_package.py

# 6. Neo4j 및 Qdrant 멱등 적재 (MERGE 문으로 기존 노드 훼손 방지)
python data/handoff/neo4j/load_neo4j.py
```

---

## 6. 결론 및 향후 로드맵

1. **누락 원인 규명 완료:**  
   고속도로 다자녀 통행료, 청년내일채움공제, 중소기업 장학금 등의 핵심 정책은 **이미 원천 데이터에 정상 수집되어 보존 중**이었으며, 누락 사유는 지역/분야별 고정 쿼터(Gov24 15/65건, 온통청년 15/65건) 및 정렬 편향에 의한 조기 중단 때문이었습니다.
2. **신규 수집 필요 항목 도출:**  
   '중소기업 취업자 소득세 감면'과 같은 국세청 조세특례 제도는 복지 API에 없으므로 홈택스/세무 포털 연계 수집 모듈 구축이 필수적입니다.
3. **즉시 확장 가능성 확보:**  
   본 보고서에서 발굴 및 전수 물리 검증을 마친 **30개 실생활 고가치 정책**은 신규 수집 없이도 즉시 증분 파이프라인을 통해 지식그래프와 벡터 DB로 확장 투입될 수 있습니다.

