# LifePolicy AI — 3차 React 시연 화면

질문과 선택 조건(나이·지역)을 입력하면 답변·조건 확인·공식 출처·추가 질문을 한 화면에 보여 주는 3차 발표용 화면입니다. (이슈 #15)

> 백엔드 연동 전에는 **예시 응답(시연용 가상 데이터)** 으로 동작합니다. 화면 맨 위 띠와 답변 카드에 '예시 응답'이 표시됩니다. '예시 정책 A'와 조건 판정 기준은 실제 정책이 아닙니다.

## 실행

Node.js 18 이상이 필요합니다. (개발 확인: Node 24.20.0, npm 11.19.0)

```bash
cd frontend
npm install
npm run dev
```

브라우저에서 http://127.0.0.1:5173 을 엽니다.

| 명령 | 설명 |
|---|---|
| `npm run dev` | 개발 서버 실행 |
| `npm test` | 단위 테스트 (Vitest): 나이 검증·링크 검사·조건 읽기·예시 응답 판정·답변 상태별 표시·오류 문구 |
| `npm run typecheck` | 타입 검사 |
| `npm run build` | 타입 검사 후 `dist/`에 빌드 |
| `npm run preview` | 빌드 결과 미리 보기 |

## 백엔드 연동

`.env.example`을 `.env.local`로 복사하고 `VITE_ASK_API_URL`에 질문 API 주소를 넣으면 예시 응답 대신 백엔드를 호출합니다. `.env.local`은 커밋하지 않습니다.

- 요청·응답 형식: [`src/api/types.ts`](src/api/types.ts) — 나지인 작성 `docs/api/ask_contract.md` v1.0(#23 브랜치, `dev` 머지 전)과 백엔드 `backend/api/schemas.py`에 맞췄습니다. 추가 질문 중 정보 요청(`ask_info`)은 나이·지역·소득 세 가지뿐입니다.
- 호출: [`src/api/client.ts`](src/api/client.ts) — `POST`로 `AskRequest`(질문, 내 조건, 최근 10개 대화 이력)를 보내고 `AskResponse`를 받습니다. 60초가 지나면 중단하고 오류 화면을 보여 줍니다. 서버 오류 응답(`{"detail": {"code", "message"}}`)은 `code`만 보고 화면이 정한 문구를 보여 줍니다. 서버 `message`는 계약에서 문장이 정해지지 않은 외부 데이터라 그대로 쓰지 않습니다.
- 설정값: [`src/config/request.ts`](src/config/request.ts) — 시간 제한, 이력 개수, 기다리는 동안 안내 문구. 실제 응답 시간을 잰 뒤 조정합니다.
- 답변 표시: [`src/lib/answerView.ts`](src/lib/answerView.ts) — 출처 검증을 통과한 `answered`만 답변 본문으로 보여 줍니다. `no_evidence`는 화면의 고정 문구, `info`는 '추가 확인이 필요해요' 안내 상자에 확정 답변이 아니라는 문구와 함께 보여 줍니다.
- 같은 질문의 답변을 기다리는 동안 같은 질문은 다시 보내지 않습니다 (중복 LLM 호출 방지).
- 예시 응답: [`src/mock/mockAsk.ts`](src/mock/mockAsk.ts)

## 화면에서 확인할 수 있는 상태

| 상태 | 확인 방법 |
|---|---|
| 정상 답변 + 조건 확인 | 분야 카드 '일자리' 클릭 |
| 조건을 말로 알려 주고 이어서 질문 | 답변 뒤 아래 입력창에 "26살, 서울, 소득은 4000만원이야" |
| AI가 되묻기 | 답변의 '지역도 알려줄게요'·'연 소득도 알려줄게요' 버튼 (`kind: 'ask_info'`, `field`는 age·region·income) |
| 안내만 (확정 답변 없음) | 분야 카드 '주거' 뒤 '혼인 기간 조건도 알려줘' 버튼 |
| 근거 없음 | 예시에 없는 질문 |
| 오류 + 다시 시도 | 질문에 "오류"를 넣어 보내기 (예시 응답일 때만) |
| 신청 주소 없음 | 분야 카드 '주거' — '신청하기' 버튼 없이 출처만 표시 |

'신청하기' 버튼은 `official_apply_url`이 있고 `source_url`과 다를 때만 나옵니다.

## 구조

```text
src/
├── App.tsx              # 화면 상태(내 조건·대화·테마)와 질문 흐름
├── api/                 # 백엔드 계약(types)과 호출(client)
├── config/              # 분야 카드(categories), 되물을 정보 항목(infoFields), 요청 설정값(request)
├── mock/mockAsk.ts      # 예시 응답 (sample_only)
├── lib/answerView.ts    # 답변 상태별로 화면에 보여 줄 내용 정하기
├── lib/parseProfile.ts  # 문장 속 나이·지역·소득 읽기
├── lib/validate.ts      # 나이 입력 검증(0~120 정수), 외부 링크는 http/https만 허용
├── components/          # Hero(첫 화면), TurnView(질문·답변), Floating(하단 입력창·이동 버튼·알림), Icon(손그림 SVG)
└── styles.css           # 색은 :root 토큰으로 관리, 다크는 테마 버튼으로만
```

## 참고

- 기본 테마는 라이트입니다. 발표 노트북 설정과 상관없이 같은 화면으로 시작하고, 오른쪽 위 '테마' 버튼으로 다크로 바꿀 수 있습니다.
- 글꼴(Jua, Gowun Dodum)은 Google Fonts에서 불러옵니다. 발표장 인터넷이 불안하면 기본 글꼴로 바뀌므로, 필요하면 글꼴 파일을 프로젝트에 넣습니다.
- 아이콘은 이 화면을 위해 직접 그린 SVG입니다.
- 움직임 줄이기(prefers-reduced-motion)를 켠 환경에서는 애니메이션을 끕니다.
