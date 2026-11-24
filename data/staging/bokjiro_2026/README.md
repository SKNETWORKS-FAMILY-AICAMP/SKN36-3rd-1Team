# 복지로 2026 추가 정책 묶음

복지로 상세 XML 361건(2026-11-12~15 수집, 기준연도 2026)의 검수 결과와, 그중 후보 데이터에 넣은 정책의 빌드 입력이다.
검수 기록·판단 근거: `docs/data/reports/bokjiro_2026_youth_policies_review.md`.

| 파일 | 내용 | 후보 빌드가 읽나 |
| --- | --- | --- |
| `selection.json` | 후보 데이터에 넣을 정책(현재 9건)과 분류·신청처 | 간접(아래 스크립트) |
| `policies.jsonl`, `documents.jsonl`, `payload_chunks.jsonl` | `scripts/stage_bokjiro_2026_policies.py`가 만든 빌드 입력 | 읽음 |
| `handoff/targets.jsonl` | 361건 기본 정보·검수 판정·원본 해시·이름 중복 후보 | 읽지 않음 |
| `handoff/review_rows.jsonl` | 검수표 1,300행(표 A 신청 경로, B 보충 선정기준, C 기타 정량 조건)과 행별 판정 | 읽지 않음 |
| `handoff/source_text/<servId>.txt` | XML에서 저장소 수집 코드(`parse_bokjiro_detail_xml`)로 만든 본문 | 넣은 정책만 근거 문서 본문으로 |
| `handoff/manifest.json` | 입력 파일 해시와 건수 | 읽지 않음 |

- 원본 XML은 저장소 규칙상 `data/raw/bokjiro/detail/`(Git 제외)에 둔다. 해시는 `targets.jsonl`의 `raw_source_sha256`이다.
- 인계 파일은 저장소 밖 검수 폴더의 도구(`4차_준비/bokjiro_gemini/tools/export_repo_handoff.py`)로 만들었다. 다시 만들 일이 없으면 이 파일만으로 이어서 작업할 수 있다.

## 정책을 더 넣을 때

1. 팀이 선별 기준을 정한다(조직 전용·사업 종료·내용 부족 제외, 건수).
2. `selection.json`에 정책을 추가한다(`policy_id`는 `POL_BOK_108`부터, 분류·생애단계·대상집단·신청처).
3. `python scripts/stage_bokjiro_2026_policies.py`
   - 검수 행이 모두 OK가 아니거나, 기존 정책과 이름 중복 후보이거나, 조직 대상 의심인데 확인 사유(`org_suspect_reviewed`)가 없으면 멈춘다.
4. `python scripts/build_incremental_release_candidate.py`
5. 건수를 고정한 테스트·복원 기대값을 갱신하고, Neo4j·Qdrant를 재적재한다(새 청크만 임베딩).
