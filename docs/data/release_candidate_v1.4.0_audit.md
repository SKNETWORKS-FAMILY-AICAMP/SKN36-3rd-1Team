# SSOK — 후보 릴리스 v1.4.0-candidate 품질 감사 및 검증 보고서

> **2026-11-06 신청 주소 추가 교정:** 현재 후보에서는 기존 정책 219건의 `official_apply_url`만 교정했습니다. 인계 원본 보존과 후보 레코드 무변경은 다른 의미입니다. 최신 변경 목록·검증 상태는 [PR #16 신청 주소 검증 보고서](pr16_application_url_review.md)를 따릅니다. 아래의 과거 검증 결과는 실제 DB 적재 완료를 뜻하지 않습니다.

> ⚠️ **2026-11-06 공식 원문 대조로 내용이 바뀌었습니다.** 이 문서의 신규 문서 22건·청크 36건, 후보 규모(문서 268 · 청크 593 · 노드 1,082 · 관계 3,710), `verified_extracted`·`reference_restructured` 판정은 대조 **전** 값입니다. 요약 문서 상당수가 공식 원문과 달라 제외·교체했고, 현재 후보는 문서 261 · 청크 599 · 노드 1,074 · 관계 3,703입니다. 최신 판정은 [official_source_crosscheck_report.md](official_source_crosscheck_report.md)를 따릅니다.

- **문서 버전**: v1.1.0 (참조 무결성 교정 및 안정화 감사본)
- **작성일자**: 2026-11-06
- **릴리스 버전**: `v1.4.0-candidate`
- **배포 상태**: `CANDIDATE / PENDING_DB_REVIEW`
- **기준 베이스라인**: `v1.3.2-verified` (manifest SHA-256: `9cdc54322b269bb245f9d0a99602171bf0937087ca57ee11432b5c778378bb85`, 100% 무변경 보존; 2026-11-06 OS 간 줄바꿈 기준(LF) 통일 및 data_contract.md 개정(official_apply_url Nullable)으로 manifest 해시 재계산, 데이터 내용 변경 없음)
- **후보 패키지 위치**: [`data/releases/v1.4.0-candidate/`](../../data/releases/v1.4.0-candidate)
- **정밀 증분 명세서**: [`release_delta_manifest.json`](../../data/releases/v1.4.0-candidate/release_delta_manifest.json)
- **책임 파트**: SSOK 데이터 아키텍처팀

---

## 1. 릴리스 빌드 개요 및 실제 파일 집계 (Release Summary)

본 후보 릴리스는 STEP A의 정책 최신성 검증, STEP B의 스키마 호환성 설계, STEP C의 공식 근거 문서 수집 PoC를 거쳐 생성되었으며, 외부 독립 검사에서 지적된 **Neo4j 29건 참조 결함을 표준 스키마 매핑으로 전수 교정하고 루트 조건 그룹 9건을 추가하여 안정화한 공식 후보 패키지**입니다.

### 1.1 데이터 규모 증감 실측 대비표 (Delta Table)

| 항목 | v1.3.2-verified (기존) | v1.4.0-candidate (최종 수정본) | 증감 (Delta) | 호환성 및 무결성 판정 |
| :--- | :---: | :---: | :---: | :--- |
| **통합 정책 (`Policy`)** | 237건 | **246건** | **+9건 신규** (기존 1건 병합) | 충돌 0건, 완전 호환 |
| **근거 문서 (`Document`)** | 246건 | **268건** | **+22건 공식 근거** | 4단계 검증 분류 적용 |
| **벡터 청크 (`Chunk`)** | 557건 | **593건** | **+36건 맞춤 청크** | UUIDv5 규격 충돌 0건 |
| **소관 기관 (`Organization`)** | 116개 | **124개** | **+8개 기관 증분** | PK 유일성 100% |
| **조건 그룹 (`ConditionGroup`)** | 343개 | **352개** | **+9개 (루트 UNKNOWN)** | 3값 논리 DAG 호환 |
| **카테고리 / 생애단계 / 지역 등** | 92개 | **92개** | **0개 (기존 100% 재사용)** | 스키마 불변 준수 |
| **총 그래프 노드 (`Total Nodes`)** | 1,034개 | **1,082개** | **+48개 노드 증분** | DDL 제약조건 100% 충족 |
| **총 그래프 관계 (`Relationship`)** | 3,615건 | **3,710건** | **+95건 관계 증분** | **Broken References = 0건** |

---

## 2. 신규 정책 및 공식 근거 문서 목록

### 2.1 신규 정책 9건 명세
1. **`POL_STG_MOLIT_002`**: 청년 전세보증금반환보증 보증료 지원 (국토교통부 청년주거정책과)
2. **`POL_STG_MOHW_001`**: 출산 관련 서비스 통합처리 행복출산 원스톱 (행정안전부 / 보건복지부)
3. **`POL_STG_DGU_001`**: 다자녀 입학축하금 지원 (서울특별시 동대문구 가족정책과)
4. **`POL_STG_NTS_001`**: 중소기업 취업자 소득세 감면 (국세청 / 기획재정부)
5. **`POL_STG_MOIS_001`**: 다자녀가구 자동차취득세 감면 (행정안전부 지방세특례제도과)
6. **`POL_STG_EX_001`**: 고속도로 다자녀가구 통행료 할인 (한국도로공사 / 국토교통부)
7. **`POL_STG_MOLIT_001`**: K-패스 대중교통비 환급지원 (국토교통부 대도시권광역교통위원회)
8. **`POL_STG_SEOUL_001`**: 서울특별시 청년수당 (서울특별시 미래청년기획단)
9. **`POL_STG_ICN_001`**: 중소기업 재직청년 복지공유제 지원 (인천광역시 서구 청년정책일자리과)

### 2.2 공식 근거 문서 22건 4단계 정밀 검증 분류표

| 문서 식별자 (`document_id`) | 연결 정책 (`policy_id`) | 문서 유형 (`doc_type`) | 검증 분류 (`verification_status`) | 문서명 (`title`) | 실제 원본 경로 (`local_raw_path`) | 원본 SHA-256 (앞 16자리) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `DOC_STG_001_NOTICE` | `POL_001` | 공고문 (notice) | **`verified_official_raw`** | 26년 1차 신혼·신생아 매입임대Ⅰ 모집공고문(정정) | `data/raw/lh/26년1차_...공고문...pdf` | `95d7229ed0ec3efe` |
| `DOC_STG_001_FAQ` | `POL_001` | 질의응답 (faq) | **`verified_official_raw`** | 26년 신혼·신생아 매입임대Ⅰ 입주자 FAQ | `data/raw/lh/26년신혼·신생아매입임대Ⅰ_QnA.hwp` | `1771a7cf06fcdf12` |
| `DOC_STG_001_LAW` | `POL_001` | 법령 (law) | **`verified_extracted`** | 공공주택 특별법 제49조(공공임대주택 공급체계) | `data/staging/raw/law/공공주택특별법_제49조.md` | `0c7489cfc635646c` |
| `DOC_STG_MOLIT_002_GDL` | `POL_STG_MOLIT_002` | 지침 (guideline) | `reference_restructured` | 2026년 청년 전세보증금반환보증 보증료 지원 사업안내서 | `data/staging/raw/guideline/...사업안내.md` | `4573d48571ac0b61` |
| `DOC_STG_MOLIT_002_FAQ` | `POL_STG_MOLIT_002` | 질의응답 (faq) | `reference_restructured` | 전세보증금반환보증 보증료 지원 질의회시집(FAQ) | `data/staging/raw/faq/...보증료지원_FAQ.md` | `ab10e44e013f2e0f` |
| `DOC_STG_MOHW_001_GDL` | `POL_STG_MOHW_001` | 지침 (guideline) | `reference_restructured` | 행복출산 원스톱서비스 통합신청 표준처리지침 | `data/staging/raw/guideline/...표준처리지침.md` | `4d5b84a0090dfe7a` |
| `DOC_STG_MOHW_001_LAW` | `POL_STG_MOHW_001` | 법령 (law) | **`verified_extracted`** | 전자정부법 제9조의2(공공서비스의 통합제공) | `data/staging/raw/law/전자정부법_제9조의2.md` | `87e27df38a22bc4d` |
| `DOC_STG_DGU_001_ORD` | `POL_STG_DGU_001` | 조례 (ordinance) | **`verified_extracted`** | 동대문구 출산 및 양육지원에 관한 조례 제8조 | `data/staging/raw/ordinance/...조례_제8조.md` | `8d3152e660a823e9` |
| `DOC_STG_DGU_001_NOT` | `POL_STG_DGU_001` | 공고문 (notice) | `reference_restructured` | 2026년 동대문구 다자녀 입학축하금 신청안내 공고 | `data/staging/raw/notice/...입학축하금_신청안내.md` | `669c9ef67cabedc1` |
| `DOC_STG_NTS_001_LAW` | `POL_STG_NTS_001` | 법령 (law) | **`verified_extracted`** | 조세특례제한법 제30조(중소기업 취업자 소득세 감면) | `data/staging/raw/law/조세특례제한법_제30조.md` | `3102dae8cf975045` |
| `DOC_STG_NTS_001_FAQ` | `POL_STG_NTS_001` | 질의응답 (faq) | `reference_restructured` | 국세청 연말정산 상담 FAQ (중소기업 취업자 소득세 감면) | `data/staging/raw/faq/...소득세감면_FAQ.md` | `488aaaeda1144bdc` |
| `DOC_STG_MOIS_001_LAW` | `POL_STG_MOIS_001` | 법령 (law) | **`verified_extracted`** | 지방세특례제한법 제22조의2(출산 및 양육 지원 감면) | `data/staging/raw/law/지방세특례제한법_제22조의2.md` | `3850e33a26cc34b8` |
| `DOC_STG_MOIS_001_NOT` | `POL_STG_MOIS_001` | 공고문 (notice) | `reference_restructured` | 다자녀가구 자동차 취득세 감면 신청안내문 | `data/staging/raw/notice/...감면_신청안내.md` | `4763c37ffee712ef` |
| `DOC_STG_EX_001_LAW` | `POL_STG_EX_001` | 법령 (law) | **`verified_extracted`** | 유료도로법 시행령 제8조(통행료 감면차량 및 비율) | `data/staging/raw/law/유료도로법_시행령_제8조.md` | `279324caba32cc62` |
| `DOC_STG_EX_001_FAQ` | `POL_STG_EX_001` | 질의응답 (faq) | `reference_restructured` | 한국도로공사 다자녀 통행료 할인 신청 공식 Q&A | `data/staging/raw/faq/...통행료할인_FAQ.md` | `df8b92a4f2e47a36` |
| `DOC_STG_MOLIT_001_GDL` | `POL_STG_MOLIT_001` | 지침 (guideline) | `reference_restructured` | 대중교통비 환급지원(K-패스) 사업 추진 및 운영지침 | `data/staging/raw/guideline/...운영지침.md` | `32412554f8f12be0` |
| `DOC_STG_MOLIT_001_FAQ` | `POL_STG_MOLIT_001` | 질의응답 (faq) | `reference_restructured` | K-패스 공식 이용안내 및 적립 FAQ | `data/staging/raw/faq/...이용안내_FAQ.md` | `ec0c93742809edca` |
| `DOC_STG_SEOUL_001_ORD` | `POL_STG_SEOUL_001` | 조례 (ordinance) | **`verified_extracted`** | 서울특별시 청년 기본 조례 제12조(청년 경제활동 지원) | `data/staging/raw/ordinance/...청년기본조례_제12조.md` | `03783aac3e351e1a` |
| `DOC_STG_SEOUL_001_NOT` | `POL_STG_SEOUL_001` | 공고문 (notice) | `reference_restructured` | 2026년도 서울 청년수당 참여자 모집공고 | `data/staging/raw/notice/...모집공고.md` | `9cf6609732a11cd6` |
| `DOC_STG_SEOUL_001_FAQ` | `POL_STG_SEOUL_001` | 질의응답 (faq) | `reference_restructured` | 서울청년수당 참여자 자주 묻는 질문 FAQ | `data/staging/raw/faq/...서울청년수당_FAQ.md` | `970c5b68e514e3fa` |
| `DOC_STG_ICN_001_ORD` | `POL_STG_ICN_001` | 조례 (ordinance) | **`verified_extracted`** | 인천 서구 청년 기본 조례 및 복지공유제 지원조례 | `data/staging/raw/ordinance/...청년기본조례_복지공유제.md` | `62be8d1c378071d5` |
| `DOC_STG_ICN_001_NOT` | `POL_STG_ICN_001` | 공고문 (notice) | `reference_restructured` | 2026년 인천 서구 재직청년 복지공유제 참여자 모집공고 | `data/staging/raw/notice/...복지공유제_공고.md` | `dd8d7e651c62fc67` |

- **분류 요약**:
  * **`verified_official_raw` (2건)**: 로컬 디스크 상에 실제 LH 원본 파일(공고문 PDF 804KB, QnA HWP 180KB)을 직접 보유하고 검증 완료한 문서.
  * **`verified_extracted` (8건)**: 국가법령정보센터 및 자치법규정보시스템의 공식 법률/시행령/조례 조문 원문을 직접 추출한 문서.
  * **`reference_restructured` (12건)**: 정부24, 복지로, 소관부처 포털의 공식 안내·지침·FAQ를 구조화하여 정제한 문서.
  * **`unverified_reference` (0건)**: 출처가 불분명하거나 미검증된 문서는 일절 포함하지 않음.

---

## 3. 품질 및 참조 무결성 전수 검증 결과 (독립 검증 통과)

```
┌────────────────────────────────────────────────────────────────────────┐
│                        후보 릴리스 품질 검증 성적서                    │
├──────────────────────────────┬──────────────┬────────────┬─────────────┤
│ 검증 항목                    │ 검증 결과    │ 기준 수치  │ 판정        │
├──────────────────────────────┼──────────────┼────────────┼─────────────┤
│ Policy 식별자(ID) 유일성     │ 100% 유일    │ 246건 전수 │ PASS        │
│ Document 식별자(ID) 유일성   │ 100% 유일    │ 268건 전수 │ PASS        │
│ Chunk 식별자(ID) 유일성      │ 100% 유일    │ 593건 전수 │ PASS        │
│ Qdrant Point ID (UUIDv5)     │ 충돌 0건     │ 593건 전수 │ PASS        │
│ Graph 관계 참조 무결성       │ 결함 0건     │ 3,710개    │ PASS        │
│ (Broken Links Count)         │ (Broken = 0) │            │             │
│ 기존 Handoff 매니페스트 해시 │ 100% 불변    │ SHA-256    │ PASS        │
└──────────────────────────────┴──────────────┴────────────┴─────────────┘
```

---

## 4. 정밀 타겟 롤백 프로토콜 (Exact Item List Based)

단순 접두어나 와일드카드 삭제 방식을 전면 배제하고, [`release_delta_manifest.json`](../../data/releases/v1.4.0-candidate/release_delta_manifest.json)에 기록된 **정확한 증분 ID 목록만을 타겟팅하여 삭제**하는 안전한 롤백 절차를 수립하였습니다.

- **타겟 정책 (9건)**: `POL_STG_MOLIT_002`, `POL_STG_MOHW_001`, `POL_STG_DGU_001`, `POL_STG_NTS_001`, `POL_STG_MOIS_001`, `POL_STG_EX_001`, `POL_STG_MOLIT_001`, `POL_STG_SEOUL_001`, `POL_STG_ICN_001`
- **타겟 문서 (22건)**: `DOC_STG_001_NOTICE` ~ `DOC_STG_ICN_001_NOT` 22건
- **타겟 소관기관 (8건)**: `AGENCY_국세청_기획재정부`, `AGENCY_국토교통부_대도시권광역교통위원회`, `AGENCY_서울특별시_동대문구_가족정책과`, `AGENCY_서울특별시_미래청년기획단`, `AGENCY_인천광역시_서구_청년정책일자리과`, `AGENCY_한국도로공사_국토교통부`, `AGENCY_행정안전부_보건복지부`, `AGENCY_행정안전부_지방세특례제도과` (기존 116개에서 124개로 증가한 순수 신규 8개 기관)
- **타겟 조건그룹 (9건)**: `GRP_POL_STG_MOLIT_002_ROOT` ~ `GRP_POL_STG_ICN_001_ROOT`
- **타겟 Qdrant Point ID (36건)**: 명세서에 수록된 36개 UUIDv5 포인트 전수
- **타겟 관계 (95건)**: `release_delta_manifest.json` 내 `exact_added_relationships` 95개 튜플 전수

---

## 5. 결론 및 DB 담당자(주민혁 님) 최종 협의 요약

본 후보 릴리스는 **모든 참조 결함을 0건으로 완벽히 교정하고 실제 수치와 매니페스트를 100% 일치**시켰습니다.

1. **상태 승인**: 현재 `CANDIDATE / PENDING_DB_REVIEW` 상태를 유지하며, 실제 테스트 서버 구동 후 실측 벤치마크를 수행하여 `v1.4.0-verified`로 승격 승인.
2. **Neo4j 스키마**: `Document` 노드의 4개 선택적 필드(`doc_type`, `law_article`, `effective_date`, `version`) 적재 승인.
3. **Qdrant 인덱스**: `doc_type` Keyword 페이로드 인덱스 생성 일정 확정.
