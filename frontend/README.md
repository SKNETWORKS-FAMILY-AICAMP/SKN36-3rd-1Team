# SSOK — 3차 React 시연 화면

질문과 선택 조건(나이·지역·연 소득·자녀 월령)을 입력하면 답변·조건 확인·공식 출처·추가 질문을 한 화면에 보여 주는 3차 발표용 화면입니다. (이슈 #15)

화면은 세 개입니다. 위쪽 탭으로 바꿉니다. 생애주기·관계 지도는 같은 팀 기준 데이터를 한 번만 내려받아 함께 씁니다.

세 화면은 이어집니다. 상담에서 가장 최근 답변으로 나온 정책([`src/lib/recentPolicies.ts`](src/lib/recentPolicies.ts))을 관계 지도는 바로 열고 위쪽에 "방금 상담에서 나온 정책"으로 보여 주며, 생애주기는 그 정책이 든 단계·분야를 먼저 열고 '상담' 표시를 붙입니다. 답변에 실제 정책 카드가 없으면 인용한 출처 문서의 정책을 쓰고, 그것도 없으면(예시 응답 등) 질문 단어와 이름·분야·대상이 겹치는 정책을 찾아 '검색 결과(답변 근거·자격 판정 아님)'로 표시해 엽니다([`src/graph/relatedPolicies.ts`](src/graph/relatedPolicies.ts)). 생애주기의 '상담' 표시는 답변에 실제로 나온 정책에만 붙습니다.

| 화면 | 주소 | 내용 |
|---|---|---|
| 상담하기 | `#/` | 질문·내 조건 입력, 분야 카드, 답변·조건 확인·출처. 첫 질문 전 '상담 결과' 빈 자리에 **대표 상황 카드**(PRD §2 시나리오의 가상 인물 3명) |
| 생애주기 | `#/life`, `#/life?stage=청년` | 영유아 → 노년 생애단계 타임라인. 단계를 고르면 분야 칩만 먼저 보이고, 누른 분야 하나의 정책만 한 줄 목록으로 펼침. 원문 나이 기준이 확인된 정책에만 '나이' 배지(데이터의 '0~120세'처럼 사실상 제한 없는 조건은 배지 없음). 정책을 누르면 그 자리에서 요약(대상·내용·기간)과 '이 정책 물어보기'(상담 화면으로 가서 바로 질문)·'원문 보기'·'관계 지도 보기'가 펼쳐짐. 상담 화면에 나이를 넣었으면 해당 단계에 '지금 나' 표시 |
| 정책 관계 지도 | `#/map`, `#/map?policy=POL_001` | Graph DB(Neo4j)에 저장된 정책 연결 구조: 정책을 가운데 두고 기관·지역·분야·대상·생애단계·근거 문서·자격 조건 묶음(AND/OR)을 보여 줌 |

> 백엔드 연동 전에는 **예시 응답(시연용 가상 데이터)** 으로 동작합니다. 화면 맨 위 띠와 답변 카드에 '예시 응답'이 표시됩니다. '예시 정책 A'와 조건 판정 기준은 실제 정책이 아닙니다.

## 처음 볼 파일

| 경로 | 역할 |
| --- | --- |
| [`src/App.tsx`](src/App.tsx) | 화면 전환, 질문 전송, 사용자 조건 관리 |
| [`src/api/client.ts`](src/api/client.ts) | 실제 질문 API 호출과 오류 처리 |
| [`src/components/TurnView.tsx`](src/components/TurnView.tsx) | 상담 답변·조건·출처 표시 |
| [`src/components/LifeTimeline.tsx`](src/components/LifeTimeline.tsx) | 생애주기별 정책 탐색 |
| [`src/components/PolicyMap.tsx`](src/components/PolicyMap.tsx) | 정책 관계 지도 |

## 실행

Node.js 18 이상이 필요합니다. (개발 확인: Node 24.20.0, npm 11.19.0)

```bash
cd frontend
npm install
npm run dev
```

브라우저에서 Vite가 출력한 `Local` 주소를 엽니다. 백엔드 없이 시연하려면 `VITE_ASK_API_URL`을 비운 상태로 실행합니다.

| 명령 | 설명 |
|---|---|
| `npm run dev` | 개발 서버 실행 |
| `npm test` | 단위 테스트 (Vitest): 나이·자녀 월령 검증·링크 검사·조건 읽기·예시 응답 판정·답변 상태별 표시·오류 문구·화면 주소·관계 지도 그래프(가상 그래프 + 실제 팀 데이터 건수 확인) |
| | 대부분은 Node에서 HTML 문자열로 그려 확인합니다. 클릭·탭 복귀처럼 실제 화면 동작이 필요한 테스트만 파일 첫 줄에 `// @vitest-environment jsdom`을 적어 개발 의존성 `jsdom`의 가상 브라우저에서 실행합니다(예: `PolicyExplorer.dom.test.tsx`, 목록을 다시 불러와도 펼친 카드 유지) |
| `npm run typecheck` | 타입 검사 |
| `npm run build` | 타입 검사 후 `dist/`에 빌드 |
| `npm run preview` | 빌드 결과 미리 보기 |

## 백엔드 연동

`.env.example`을 `.env.local`로 복사하고 `VITE_ASK_API_URL`에 질문 API 주소를 넣으면 예시 응답 대신 백엔드를 호출합니다. `.env.local`은 커밋하지 않습니다.

기본 FastAPI CORS 설정은 `http://127.0.0.1:5173`을 허용합니다. 실제 API에 연결할 때는 `npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`로 실행합니다. 신청 상태는 API 응답에서, 화면의 신청 기간은 로컬 그래프에서 가져오므로 백엔드 DB와 프론트 그래프에 동일한 데이터 릴리스를 사용합니다.

- 요청·응답 형식: [`src/api/types.ts`](src/api/types.ts) — `docs/api/ask_contract.md`와 백엔드 `backend/api/schemas.py`를 따릅니다. 추가 질문 중 정보 요청(`ask_info`)은 나이·지역·소득 세 가지뿐입니다.
- 호출: [`src/api/client.ts`](src/api/client.ts) — `POST`로 `AskRequest`(질문, 내 조건, 최근 10개 대화 이력)를 보내고 `AskResponse`를 받습니다. 60초가 지나면 중단하고 오류 화면을 보여 줍니다. 서버 오류 응답(`{"detail": {"code", "message"}}`)은 `code`만 보고 화면이 정한 문구를 보여 줍니다. 서버 `message`는 계약에서 문장이 정해지지 않은 외부 데이터라 그대로 쓰지 않습니다.
- 설정값: [`src/config/request.ts`](src/config/request.ts) — 시간 제한, 이력 개수, 기다리는 동안 안내 문구. 실제 응답 시간을 잰 뒤 조정합니다.
- 답변 표시: [`src/lib/answerView.ts`](src/lib/answerView.ts) — 출처 검증을 통과한 `answered`만 답변 본문으로 보여 줍니다. `no_evidence`는 화면의 고정 문구, `info`는 '추가 확인이 필요해요' 안내 상자에 확정 답변이 아니라는 문구와 함께 보여 줍니다.
- 같은 질문의 답변을 기다리는 동안 같은 질문은 다시 보내지 않습니다 (중복 LLM 호출 방지).
- 예시 응답: [`src/mock/mockAsk.ts`](src/mock/mockAsk.ts)

## 화면에서 확인할 수 있는 상태

| 상태 | 확인 방법 |
|---|---|
| 정상 답변 + 조건 확인 | 분야 카드 '일자리' 클릭 |
| 내 조건을 입력하고 질문 | 나이·지역·연 소득·자녀 월령을 각각 내 조건에 입력한 뒤 질문을 보냅니다. |
| 자녀 월령 (#39) | '자녀 월령' 칸에 개월 수(0 이상 정수)를 넣습니다. 확인하려는 자녀 한 명 기준이며 신청자 나이와 따로 보냅니다(`profile.child_age_months`, ask_contract v1.8). 빈칸은 `null`, 0개월은 그대로 보냅니다. 소수·음수·문자는 보내지 않고 입력칸에 안내합니다. 자녀가 여러 명이면 한 명씩 따로 확인합니다. 자녀 월령을 되묻는 버튼(`ask_info`)은 아직 없습니다. |
| 추가 조건으로 다시 확인 | 답변의 추가 정보 버튼을 누르고 전용 입력칸에 값을 넣은 뒤 ‘다시 확인’을 누릅니다. 입력한 조건으로 원래 질문을 다시 보냅니다. |
| 안내만 (확정 답변 없음) | 분야 카드 '주거' 뒤 '혼인 기간 조건도 알려줘' 버튼 |
| 근거 없음 | 예시에 없는 질문 |
| 오류 + 다시 시도 | 질문에 "오류"를 넣어 보내기 (예시 응답일 때만) |
| 신청 주소 없음 | 분야 카드 '주거' — '신청하기' 버튼 없이 출처만 표시 |

'신청하기' 버튼은 `official_apply_url`이 있고 `source_url`과 다를 때만 나옵니다.

## 정책 관계 지도

- 데이터: 저장소의 팀 기준 데이터 `data/releases/v1.4.0-candidate/`(백엔드 `LIFEPOLICY_DATASET_DIR` 기본값과 같은 폴더)의 `neo4j/*.jsonl`을 그대로 읽습니다. 가짜 데이터는 쓰지 않고, `manifest.json` 건수와 다르면 지도 위에 경고를 띄웁니다.
- 지도 페이지를 열 때만 코드와 데이터를 내려받습니다(첫 화면 용량에 포함되지 않음). 데이터 버전이 바뀌면 [`src/graph/loadGraph.ts`](src/graph/loadGraph.ts)의 경로와 [`vite.config.ts`](vite.config.ts)의 `server.fs.allow`를 함께 고칩니다.
- 정책을 가운데 두고 기관·지역·분야·대상·생애단계·근거 문서·자격 조건을 둘레 원으로 보여 줍니다. 둘레 원을 누르면 상세 팝업, 지도 아래엔 정책 카드(자격 조건·비슷한 정책) 1장이 이어집니다. 애니메이션·팝업·확대·키보드 조작 등 세부 동작은 코드 주석에 있습니다 — [`PolicyGraph`](src/components/PolicyMap.tsx), [`ZoomCanvas`](src/components/PolicyMap.tsx), [`NodeDialog`/`NodeDetail`](src/components/PolicyMap.tsx).
- 표시 이름·순서·검색 개수·확대 배율 등 설정: [`src/config/graph.ts`](src/config/graph.ts)
- 관계가 연결돼 있어도 자격이 확정된 것은 아닙니다(CODING_STYLE §6). 화면에도 이 안내를 함께 보여 줍니다.
- 답변 카드의 '관계 지도' 버튼으로 그 정책의 지도를 엽니다. 예시 응답의 '예시 정책 A'는 실제 데이터에 없어 버튼이 나오지 않습니다.

## 구조

```text
src/
├── App.tsx              # 화면 상태(내 조건·대화·테마)와 질문 흐름
├── api/                 # 백엔드 계약(types)과 호출(client)
├── config/              # 분야 카드(categories), 대표 상황(personas), 되물을 정보 항목(infoFields), 요청 설정값(request), 관계 지도 설정(graph)
├── mock/mockAsk.ts      # 예시 응답 (sample_only)
├── graph/               # 데이터 읽기(loadGraph), 그래프 색인·조건 트리(graphModel), 원형 배치·확대(graphLayout), 생애단계 묶기(lifeStages)
├── lib/route.ts         # 화면 전환 (#/, #/life, #/map)
├── lib/answerView.ts    # 답변 상태별로 화면에 보여 줄 내용 정하기
├── lib/parseProfile.ts  # 문장 속 나이·지역·소득 읽기
├── lib/validate.ts      # 나이 입력 검증(0~120 정수), 자녀 월령 검증(0 이상 정수), 외부 링크는 http/https만 허용
├── components/          # Hero(첫 화면), PersonaStart(대표 상황), TurnView(질문·답변), LifeTimeline(생애주기), PolicyMap(관계 지도), Floating(하단 입력창·이동 버튼·알림), Icon(손그림 SVG)
└── styles.css           # 색은 :root 토큰으로 관리, 다크는 테마 버튼으로만
```

## 참고

- 움직임 줄이기(prefers-reduced-motion)를 켠 환경에서는 애니메이션을 끕니다.
