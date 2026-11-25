# web_ssr — 정책 상세보기 Django SSR (#120)

정책 상세 페이지 **한 곳**(`/policies/<policy_id>/`)만 Django가 서버에서 HTML로 완성해 보여 준다.
React 화면·FastAPI(검색·AI 상담·Checker·RAG·LLM)·Neo4j·Qdrant는 그대로다.

```text
브라우저 ─GET /policies/POL_010/─▶ Django ─(서버→서버) GET /api/policies─▶ FastAPI ─▶ 릴리스 파일
         ◀──────── 정책 내용이 이미 들어 있는 HTML ────────┘
```

| 단계 | 파일 | 하는 일 |
|---|---|---|
| URL Routing | `config/urls.py`, `policies/urls.py`, `policies/converters.py` | `/policies/<policy_id>/`만 받는다. ID 규칙 `^POL_[A-Z0-9_]+$`(최대 128자, FastAPI `selected_policy_id`와 같음). 형식이 틀리면 View 실행 전 404 |
| View | `policies/views.py` `policy_detail()` | GET만 허용. 서비스 결과로 200 / 404 / 503 화면을 고른다 |
| Service | `policies/services.py` `fetch_policy()` | FastAPI `GET /api/policies`를 `limit=500, offset=…`으로 끝까지 받아 **`policy_id`가 완전히 같은 1건**만 고른다. 정책명 검색(`q`)은 쓰지 않는다 |
| Template | `templates/policies/detail.html` (`base.html`, `error.html`) | 정책명·기관·접수 상태·신청 기간·자격 원문·제출서류·공식 링크를 HTML로 채운다(자동 이스케이프 유지) |
| JS | `static/policies/detail.js` | 제출서류 더보기/접기만. JS가 없으면 서류가 모두 펼쳐진 채 보인다 |

## 데이터와 안전 규칙

- FastAPI에 정책 단건 API가 없어 기존 목록 API(`docs/api/ask_contract.md` v1.5)를 쓴다. 단건 API가 생기면 `services.fetch_policy`만 바꾸면 된다.
- 페이지마다 `dataset_version`·`manifest_sha256`·`evaluated_on`·`total`이 같아야 하고, offset 순서·ID 중복·건수를 확인한다.
- 정책 없음 → 404. 연결 실패·타임아웃·5xx·JSON 오류·필수 필드(`policy_id`·`policy_name`·`reception_status`) 오류 → 503. 사용자 화면에는 내부 주소나 예외를 보여 주지 않는다.
- 링크는 http/https이고 계정 정보가 없는 주소만 쓴다. 값이 없는 항목은 만들어 채우지 않고 "공식 원문에서 확인"으로 안내한다.
- 접수 상태·일정은 FastAPI가 계산한 값을 React와 같은 규칙으로 보여 준다(일정 기준일이 오늘이 아니거나 상태와 어긋나면 날짜를 단정하지 않음). **접수 중 ≠ 내가 신청 가능**. 개인 자격 판정(Checker)은 React 상담에서 한다.
- DB·로그인·Admin·세션을 쓰지 않는다. 정책 목록은 메모리에 짧게 캐시한다(기본 60초, 한국 날짜가 바뀌면 새로 받음).
- 저장소 루트 `.env`(API 키)를 읽지 않는다. `web_ssr/.env` 또는 OS 환경 변수만 읽는다.

## 실행 (로컬)

FastAPI(8000) → Django(8001) → React(5173) 순서. 저장소 루트에서 실행한다.

```powershell
python -m pip install -r web_ssr/requirements.txt
```

```powershell
python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

`web_ssr/.env.example`을 `web_ssr/.env`로 복사하고 `DJANGO_DEBUG=true`로 둔 뒤:

```powershell
python web_ssr/manage.py runserver 127.0.0.1:8001
```

확인: <http://127.0.0.1:8001/policies/POL_010/>

React에서 카드 링크를 켜려면 `frontend/.env.local`에 다음을 넣고 `npm run dev`를 다시 시작한다(비워 두면 링크가 숨겨진다).

```text
VITE_POLICY_DETAIL_BASE_URL=http://127.0.0.1:8001/policies/
```

> `GET /api/policies`는 릴리스 파일만 읽으므로 Neo4j·Qdrant가 없어도 응답한다(상담 `/api/ask`는 DB가 필요).
> `DJANGO_DEBUG=false`로 운영할 때는 `DJANGO_SECRET_KEY`·`DJANGO_ALLOWED_HOSTS`를 설정하고 `collectstatic` 후 웹 서버가 `/ssr-static/`을 제공해야 한다.

## 테스트

FastAPI를 실제로 호출하지 않는다(HTTP mock, 가상 `sample_only` 데이터).

```powershell
cd web_ssr; python manage.py test policies --settings=config.settings_test
```

## SSR 확인 방법

- 개발자도구 Network → Doc → Response에 정책명·자격 원문·서류가 들어 있다(React 화면은 빈 `<div id="root">`).
- 개발자도구에서 JavaScript를 꺼도 정책 정보가 그대로 보인다.
- HTML `<meta name="ssok-render" content="django-ssr">`로 Django 페이지임을 구분할 수 있다.
