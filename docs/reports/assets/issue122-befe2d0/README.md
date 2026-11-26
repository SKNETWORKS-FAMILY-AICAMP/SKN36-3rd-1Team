# #122 재현용 증빙

2026-11-26 KST, dev `befe2d026bb59511966f212dd40c968c0dcea5f2`. 가상 프로필을 사용한 실제 응답과 독립 브라우저 시험을 포함한다. 캡처에 표시된 정책 및 출처는 당시 릴리스와 R3D 검색 결과 기준이다.

[#122 검증표](../../issue122-e2e-verification-matrix.md) · [상담 및 #136 리뷰](../../issue75-browser-regression-20261125.md)

- `ask-api.json`, `ask-browser.json`에는 API 직접 요청과 React 제출 각각의 요청·응답을 기록했다. `ask-browser-selected-007.json`은 상세 전후 비교 결과다. 별도 대표 상담 재실행의 `browser-citations.json`에는 근거 출처 영역의 렌더링 링크를, `release-document-check.json`에는 릴리스 문서와 정책의 관계를 기록했다.
- `browser.json`, `schedule.json`, `recent-schedule-nojs.json`, `mobile-links.json`에는 Chrome DOM과 SSR 측정값이 있다. `ssr-503*.png`는 테스트용 프록시 장애와 정상 복구다. `kst-saved-next-day.png`는 가상 브라우저 시계와 목록 응답을 사용했다.
- `pr136-*.json`은 Open 상태였던 #136 HEAD의 독립 검증이다.
- `*.log`에는 기설치 환경의 회귀 실행 결과가 있다.

## 재현

[#122의 기동 명령](../../issue122-e2e-verification-matrix.md#재현-명령)을 따른다. 스크립트는 JSON 및 PNG를 자신과 같은 디렉터리에 쓰므로 아래 파일을 별도 작업 디렉터리로 복사해 실행한다. Chrome은 별도 프로필 디렉터리, 루프백 CDP 포트 18303, Node 24로 실행했다.

```powershell
chrome --headless=new --remote-debugging-port=18303 --user-data-dir=<임시-프로필-경로> about:blank
node browser.mjs
node schedule.mjs
node kst.mjs
node mobile.mjs
node ask.mjs
node live.mjs
node live.mjs selected-007
node recent.mjs
```

`node fault.mjs`로 :18306 프록시를 켠 뒤, #122의 별도 Django :18307에서 `node recover.mjs`를 한 번 실행한다. 복구된 Django에서 404 복귀를 확인하려면 node back404.mjs를 실행한다. 재실행 전 프록시를 다시 시작해 503 상태를 초기화한다. `node parity.mjs`는 :18308에서 #136 목록 API 사본을 켠 상태에서 실행한다. :18309에 R3D와 연결한 #136을 켜면 `node pr136-live.mjs`와 `node links.mjs`로 실제 후보 상담 및 신청 URL 비교를 재현한다. `ask.mjs`, `live.mjs`, `recent.mjs`, `pr136-live.mjs`, `links.mjs`에는 일부 실제 모델 호출이 포함된다.
