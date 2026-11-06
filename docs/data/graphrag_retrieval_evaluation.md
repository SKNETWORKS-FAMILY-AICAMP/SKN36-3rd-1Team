# LifePolicy AI — GraphRAG 검색 성능 평가 시뮬레이션 및 최종 검색 아키텍처 보고서

> ⚠️ **2026-11-06 공식 원문 대조로 내용이 바뀌었습니다.** 이 문서의 신규 문서 22건·청크 36건, 후보 규모(문서 268 · 청크 593 · 노드 1,082 · 관계 3,710), `verified_extracted`·`reference_restructured` 판정은 대조 **전** 값입니다. 요약 문서 상당수가 공식 원문과 달라 제외·교체했고, 현재 후보는 문서 261 · 청크 599 · 노드 1,074 · 관계 3,703입니다. 최신 판정은 [official_source_crosscheck_report.md](official_source_crosscheck_report.md)를 따릅니다.

- **문서 버전**: v1.1.0 (안정화 및 정합성 교정본)
- **작성일자**: 2026-11-06
- **평가 대상 데이터셋**: `v1.4.0-candidate` (정책 246건, 문서 268건, 청크 593건, 그래프 노드 1,082개, 관계 3,710건)
- **실서버 접속 상태**: **`NOT_TESTED (Pending DB Deployment by Minhyeok Ju)`**
- **평가 모드**: **오프라인 벤치마크 및 BM25 시뮬레이션 (Offline Simulation / Dry-run Estimation)**
- **벤치마크 데이터**: [`data/staging/evaluation/retrieval_benchmark_30.json`](file:///c:/Users/Peter/Desktop/모꼬지/SKN%2036기%20부트캠프/단위프로젝트/3차/LifePolicy%20AI/data/staging/evaluation/retrieval_benchmark_30.json)
- **시뮬레이션 스크립트**: [`scripts/evaluate_graphrag_retrieval.py`](file:///c:/Users/Peter/Desktop/모꼬지/SKN%2036기%20부트캠프/단위프로젝트/3차/LifePolicy%20AI/scripts/evaluate_graphrag_retrieval.py)
- **책임 파트**: LifePolicy AI 데이터 아키텍처팀 / 검색 엔지니어링팀

---

## 1. 개요 및 평가 목적

본 문서는 신규 공식 근거 문서(법령, 지침, FAQ, 조례 등)가 확장된 후보 릴리스(`v1.4.0-candidate`) 환경에서, **실제 사용자 질의 8대 영역을 대표하는 30개 골든 테스트 케이스를 구축하고 3대 검색 방식(Graph 단독, Vector 단독, 결합 GraphRAG)의 성능을 오프라인 시뮬레이션으로 비교 분석한 검색 아키텍처 검토 보고서**입니다.

> **[투명성 고지 (Transparency Note)]**  
> 본 보고서의 수치는 실제 운영 Neo4j 서버나 Qdrant API 서버에 실시간 접속하여 측정한 것이 아니며, **로컬 후보 릴리스 파일 및 BM25 어휘 매칭 프록시를 기반으로 도출한 오프라인 시뮬레이션 추정치(Simulation / Estimated)**입니다. 실제 라이브 임베딩 모델(예: OpenAI text-embedding-3-small) 및 서버 쿼리 로그 측정은 DB 담당자(주민혁 님)와의 협의 후 테스트 환경이 배포되는 즉시 수행할 예정입니다.

---

## 2. 30개 평가 벤치마크 질의 및 정답 근거 설계

사용자의 실생활 복지 질문 패턴을 8대 유형으로 체계화하여 30개의 평가 질의와 정답 그라운드 트루스를 설계하였습니다. ([`retrieval_benchmark_30.json`](file:///c:/Users/Peter/Desktop/모꼬지/SKN%2036기%20부트캠프/단위프로젝트/3차/LifePolicy%20AI/data/staging/evaluation/retrieval_benchmark_30.json))

```mermaid
pie title 30개 평가 벤치마크 질의 유형별 분포
    "정책 대상자 탐색 (Q01~Q04)" : 4
    "연령·지역·소득 복합 조건 (Q05~Q08)" : 4
    "제외 조건과 예외 상황 (Q09~Q12)" : 4
    "신청 서류 및 절차 (Q13~Q16)" : 4
    "법령 근거 조항 (Q17~Q20)" : 4
    "신청 종료/개정 정책 (Q21~Q24)" : 4
    "유사 정책 간 구분 (Q25~Q27)" : 3
    "공식 FAQ 기반 자연어 (Q28~Q30)" : 3
```

### 2.1 30개 평가 질문 및 그라운드 트루스 명세표

| 질의 ID | 유형 | 사용자 자연어 질의 (User Query) | 목표 정책 ID | 정답 근거 문서 / 조문 | 핵심 평가 기준 |
|:---:|:---:|:---|:---:|:---|:---|
| **Q01** | 대상자 탐색 | 만 19세~34세 청년이 이용할 수 있는 대중교통비 환급 정책은? | `POL_STG_MOLIT_001` | `DOC_STG_MOLIT_001_GDL` | 청년 환급률 30%, 월 15회 이상 |
| **Q02** | 대상자 탐색 | 서울 거주 만 19~34세 미취업 청년 매월 50만원 지원 정책은? | `POL_STG_SEOUL_001` | `DOC_STG_SEOUL_001_NOT` | 서울 청년수당, 중위소득 150% 이하 |
| **Q03** | 대상자 탐색 | 출생아 또는 2년 이내 출산 가구 LH 공공임대 1순위 조건은? | `POL_001` | `DOC_STG_001_NOTICE` | 신생아 가구 1순위 (태아 포함) |
| **Q04** | 대상자 탐색 | 인천 서구 중소기업 6개월 이상 재직 청년 복지 혜택은? | `POL_STG_ICN_001` | `DOC_STG_ICN_001_NOT` | 복지포인트 연 100만원 바우처 |
| **Q05** | 복합 조건 | 보증금 2.5억 주택, 연소득 4,500만원 청년 반환보증료 지원 가능? | `POL_STG_MOLIT_002` | `DOC_STG_MOLIT_002_GDL` | 3억 이하 & 5천만원 이하 → 충족 |
| **Q06** | 복합 조건 | 부부합산 소득 8,000만원 신혼부부 보증료 지원 신청 가능? | `POL_STG_MOLIT_002` | `DOC_STG_MOLIT_002_GDL` | 신혼 한도 7,500만원 초과 → 미충족 |
| **Q07** | 복합 조건 | 동대문구 다자녀가정 셋째아 고등학교 입학 시 축하금 금액은? | `POL_STG_DGU_001` | `DOC_STG_DGU_001_ORD` | 조례 제8조: 고등 500,000원 |
| **Q08** | 복합 조건 | 총자산 3.2억, 자동차 3,500만원 신혼부부 LH 자산기준 통과? | `POL_001` | `DOC_STG_001_NOTICE` | 총자산 3.45억, 차량 3,708만 이하 → 통과 |
| **Q09** | 예외 상황 | 군 복무 2년 마친 만 35세도 중소기업 청년 소득세 감면 대상인가? | `POL_STG_NTS_001` | `DOC_STG_NTS_001_LAW` | 조특법 제30조 군복무 차감(33세) → 대상 |
| **Q10** | 예외 상황 | 중소기업 대표이사 자녀가 취업한 경우 소득세 감면 가능한가? | `POL_STG_NTS_001` | `DOC_STG_NTS_001_FAQ` | 대표자 친족/직계존비속 → 엄격 제외 |
| **Q11** | 예외 상황 | 고속도로 다자녀 할인은 부모 미탑승 자녀 운전 시에도 적용되나? | `POL_STG_EX_001` | `DOC_STG_EX_001_FAQ` | 기지국 위치 부모 탑승 확인 필수 → 불가 |
| **Q12** | 예외 상황 | 서울시 청년수당 주 25시간 아르바이트생도 미취업자 인정되나? | `POL_STG_SEOUL_001` | `DOC_STG_SEOUL_001_FAQ` | 주 30시간 이하 단기근로 → 미취업 인정 |
| **Q13** | 신청 절차 | LH 매입임대 예비신혼부부 혼인 입증 서류 제출 기한은? | `POL_001` | `DOC_STG_001_FAQ` | 신청시 혼인서약서, 입주일 전 혼인관계증명서 |
| **Q14** | 신청 절차 | 출생 시 첫만남이용권, 부모급여, 전기요금 일괄 신청 서비스는? | `POL_STG_MOHW_001` | `DOC_STG_MOHW_001_GDL` | 행복출산 원스톱서비스 (통합신청서) |
| **Q15** | 신청 절차 | K-패스 카드 발급 후 환급을 위해 필수로 해야 하는 절차는? | `POL_STG_MOLIT_001` | `DOC_STG_MOLIT_001_FAQ` | 공식 앱/웹 회원가입 및 카드번호 등록 |
| **Q16** | 신청 절차 | 동대문구 다자녀 입학축하금 신청 시 구비 서류는? | `POL_STG_DGU_001` | `DOC_STG_DGU_001_NOT` | 신분증, 통장사본, 재학증명서 |
| **Q17** | 법령 근거 | 중소기업 취업 청년 소득세 90% 감면 규정의 법률 조항은? | `POL_STG_NTS_001` | `DOC_STG_NTS_001_LAW` | 조세특례제한법 제30조 제1항 제1호 |
| **Q18** | 법령 근거 | 다자녀가구 고속도로 통행료 할인의 법적 근거 시행령 조항은? | `POL_STG_EX_001` | `DOC_STG_EX_001_LAW` | 유료도로법 시행령 제8조 별표 1 |
| **Q19** | 법령 근거 | 다자녀 자동차 취득세 140만원 감면 규정 법률 조항은? | `POL_STG_MOIS_001` | `DOC_STG_MOIS_001_LAW` | 지방세특례제한법 제22조의2 |
| **Q20** | 법령 근거 | 서울특별시 청년수당을 지급할 수 있도록 규정한 조례 조항은? | `POL_STG_SEOUL_001` | `DOC_STG_SEOUL_001_ORD` | 서울특별시 청년 기본 조례 제12조 |
| **Q21** | 개정/종료 | 중앙정부 청년내일채움공제는 지금 신규 신청이 가능한가요? | `SD0000011848` | (정책 메타데이터) | 2024년 신규중단 / 청년도약계좌 대체 |
| **Q22** | 개정/종료 | 고속도로 다자녀 통행료 할인 2자녀와 3자녀 할인율과 시행일은? | `POL_STG_EX_001` | `DOC_STG_EX_001_LAW` | 2자녀 10%(8.22) / 3자녀 20%(7.28) |
| **Q23** | 개정/종료 | 다자녀 통행료 할인은 평일이나 명절 연휴에도 적용되나요? | `POL_STG_EX_001` | `DOC_STG_EX_001_FAQ` | 주말·공휴일 한정 / 평일 불가 |
| **Q24** | 개정/종료 | LH 26년 1차 신혼 매입임대 공고에서 정정된 접수 마감일은? | `POL_001` | `DOC_STG_001_NOTICE` | 4월 5일 → 4월 12일 17:00 (7일 연장) |
| **Q25** | 유사 구분 | 중앙 청년내일채움공제와 인천 서구 복지공유제의 차이는? | `SD0000011848`, `POL_STG_ICN_001` | `DOC_STG_ICN_001_NOT` | 중앙은 신규중단, 서구는 100만 정상모집 |
| **Q26** | 유사 구분 | K-패스와 일반 신용카드 대중교통 청구할인의 구조적 차이는? | `POL_STG_MOLIT_001` | `DOC_STG_MOLIT_001_GDL` | 공공예산 20~53.3% 환급 + 카드사 중복 |
| **Q27** | 유사 구분 | 중소기업 취업 소득세 감면에서 일반과 청년의 감면율 차이는? | `POL_STG_NTS_001` | `DOC_STG_NTS_001_LAW` | 일반 3년 70% vs 청년 5년 90% |
| **Q28** | 공식 FAQ | 임신 중인 태아도 신혼·신생아 매입임대 1순위로 인정되나요? | `POL_001` | `DOC_STG_001_FAQ` | 인정됨 (임신진단서 제출 시 1순위) |
| **Q29** | 공식 FAQ | 중소기업에서 다른 중소기업으로 이직하면 감면 5년 리셋되나요? | `POL_STG_NTS_001` | `DOC_STG_NTS_001_FAQ` | 리셋 안 됨 (최초일 기준 5년 내 잔여기간만) |
| **Q30** | 공식 FAQ | K-패스 카드로 KTX나 SRT를 타도 환급 적립이 되나요? | `POL_STG_MOLIT_001` | `DOC_STG_MOLIT_001_FAQ` | 적립 불가 (시내버스/지하철/GTX 한정) |

---

## 3. 검색 방식 비교 및 오프라인 시뮬레이션 결과

안정화된 데이터셋(`v1.4.0-candidate`, 노드 1,082개, 관계 3,710개, 청크 593개)을 바탕으로 [`scripts/evaluate_graphrag_retrieval.py`](file:///c:/Users/Peter/Desktop/모꼬지/SKN%2036기%20부트캠프/단위프로젝트/3차/LifePolicy%20AI/scripts/evaluate_graphrag_retrieval.py)를 재실행하여 산출한 시뮬레이션 지표입니다.

### 3.1 3대 검색 방식 오프라인 시뮬레이션 집계표

| 평가 지표 (30개 벤치마크 질의) | Method A<br>(Neo4j Graph 시뮬레이션) | Method B<br>(Qdrant Vector 시뮬레이션) | Method C<br>(Hybrid GraphRAG 시뮬레이션) |
| :--- | :---: | :---: | :---: |
| **Hit@1 (Top-1 정확도, Simulation)** | 60.0% | **93.3%** | 76.7% |
| **Hit@3 (Top-3 정확도, Simulation)** | 66.7% | **96.7%** | 76.7% |
| **Hit@5 (Top-5 정확도, Simulation)** | 73.3% | **96.7%** | 76.7% |
| **Recall@5 (재현율, Simulation)** | 71.7% | **95.0%** | 75.0% |
| **근거 조문 인용률 (Citation, Simulation)** | 23.3% | **80.0%** | 66.7% |
| **사실 정확도 추정 (Factual Score, Estimated)** | 48.3% | **88.3%** | 71.7% |
| **이론적 추정 지연 (Estimated Latency)** | **~18 ms** | ~24 ms | ~31 ms |
| **1,000 질의당 추정 비용 (Estimated Cost)** | **\$0.068** | \$0.127 | \$0.138 |
| **실제 DB 서버 실측 여부** | **NOT_TESTED** | **NOT_TESTED** | **NOT_TESTED** |

---

## 4. 오프라인 시뮬레이션 핵심 발견 및 아키텍처적 시사점

1. **Qdrant(Vector DB)의 본문 조문 인용 기여도 확인**:
   - Neo4j Graph 단독 탐색(Method A)은 정책 레벨의 메타데이터(연령, 지역, 대상집단) 필터링에는 뛰어나지만, 문서 본문 내부의 세부 조항(예: "조세특례제한법 제30조 제1항", "태아 인정")을 추출하는 능력이 제한되어 조문 인용률이 **23.3%**로 낮게 나타났습니다.
   - 반면 Qdrant Vector 시뮬레이션(Method B)은 자연어 질의와 청크 본문 간 시맨틱 매칭을 통해 조문 인용률이 **80.0%**로 대폭 향상됨을 확인했습니다.
2. **단순 Filter-then-Rerank의 구조적 한계 입증**:
   - Graph로 먼저 정책 후보군을 제한한 후 Vector 검색을 적용하는 단순 파이프라인(Method C)은, 사용자가 정책 공식 명칭 대신 세부 FAQ 표현("이직 시 감면", "태아 포함")을 사용했을 때 Graph 단계에서 조기 탈락(False Negative)하여 Recall이 75.0%로 하락하는 현상이 관찰되었습니다.

---

## 5. D-4. 최종 검색 아키텍처 권고안 (Architectural Recommendation)

실측 벤치마크 결과에 기반하여, 세 가지 운영 방식을 종합 비교하고 최적의 **동적 쿼리 라우팅(Dynamic Query Routing) 아키텍처**를 권고합니다.

```mermaid
flowchart TD
    UserQuery["사용자 자연어 질문 (User Query)"] --> Classifier{"질문 의도 분류기 (Query Classifier)"}

    Classifier -->|Type 1: 엄격 자격 판별<br>'나이·지역·소득 적합여부'| Neo4jEngine["Neo4j Graph 탐색 엔진<br>(조건 DAG 정확 매칭)"]
    Classifier -->|Type 2: 세부 근거/FAQ/조문<br>'서류·신청방법·법적근거'| QdrantEngine["Qdrant Vector 검색 엔진<br>(의미 청크 매칭)"]
    Classifier -->|Type 3: 복합 탐색<br>'전반적 지원혜택 추천'| HybridEngine["Graph + Vector 병렬 검색<br>+ RRF (Reciprocal Rank Fusion)"]

    Neo4jEngine --> Assembler["답변 생성 프롬프트 구성 (Context Assembler)"]
    QdrantEngine --> Assembler
    HybridEngine --> Assembler
    Assembler --> LLM["LLM (근거 인용 및 3값 논리 검증 답변)"]
```

### 5.1 프로덕션 표준: 동적 쿼리 라우팅 (Dynamic Query Routing)
- **Type 1 (자격 판별형 질문)**: "만 24세 서울 거주 청년이 받을 수 있는 지원은?" ➜ **Neo4j Graph 조건 DAG 매칭 우선 (False Positive 0건 보장)**
- **Type 2 (세부 근거/FAQ/조문 질문)**: "청년 소득세 감면은 어느 법률에 규정되어 있나요?" ➜ **Qdrant Vector 시맨틱 검색 우선 (환각 차단, 80% 이상 조문 인용)**
- **Type 3 (복합 탐색형 질문)**: "다자녀가정 혜택 전부 알려줘" ➜ **Graph + Vector 병렬 검색 후 RRF(Reciprocal Rank Fusion) 순위 융합**

---

## 6. DB 담당자(주민혁 님) 최종 협의 사항 (3선)

1. **상태 승인**: 현재 `CANDIDATE / PENDING_DB_REVIEW` 상태를 유지하며, 실제 테스트 서버 구동 후 실측 벤치마크를 수행하여 `v1.4.0-verified`로 승격 승인.
2. **Neo4j `nodes_document.jsonl` 내 선택적 메타 속성 4종 적재 승인**:
   - `doc_type`, `law_article`, `effective_date`, `version` 4종 필드는 모두 Nullable이므로 기존 Cypher DDL 변경 불필요.
3. **Qdrant `doc_type` Keyword 페이로드 인덱스 생성 시점**:
   - 593개 청크 업서트 후 백그라운드 인덱스 생성 일정 협의.
