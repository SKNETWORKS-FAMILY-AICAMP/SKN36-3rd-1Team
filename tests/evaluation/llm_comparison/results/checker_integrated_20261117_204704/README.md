# Checker 연계 LLM 3종 비교 (#82)

PR #56의 Generator 단독 평가(`policy_checks=[]`)를 보완해, 같은 5개 실제 질문
(RQ027/031/036/040/042)을 **실제 검색 → 실제 Checker(check_eligibility) → 실제 Generator**로
이어지는 `run_ask_pipeline` end-to-end 경로로 다시 돌렸다. 모델은 PR #56과 동일하게
GPT-5.4-nano/mini/5.4 3종, 질문당 프로필은 입력 없음(age/income=None, region="")으로
고정했다 — 질문 은행에 프로필이 없고, 실제 서비스에서도 정보 미입력 사용자가 가장 흔한
경로이기 때문이다.

실행: `tests/evaluation/llm_comparison/compare_checker_integrated.py --execute --budget 0.30`
(`.venv/Scripts/python.exe`, 레포 루트에서 실행)

## 결과 요약

- 15/15 호출 성공, `ask_error` 없음, 인용 형식은 `run_ask_pipeline`의 `_citations()`가
  자체 검증하므로(실패 시 `AskError` 발생) 전부 통과한 셈이다.
- 총 비용 **$0.083581** (예산 $0.30 중). 모델별 합계: nano $0.0037 / mini $0.0164 / 5.4 $0.0638.
- `num_needs_check_conditions`로 Checker가 실제로 개입했는지 확인됨 — RQ040(POL_005, 국민내일배움카드)은
  조건 16개가 모두 `needs_check`로 보류돼, 이 정책의 그래프 데이터 품질 경고가 이번 평가에서도
  그대로 재현됐다(별도 데이터 이슈, 이 비교 자체의 결함 아님).

## PR #56 대비 달라진 점

PR #56 보고서는 GPT-5.4-mini에서 RQ040 긍정/부정 표현 오류, RQ042 핵심 조건 누락을 지적했다.
이번 Checker 연계 평가에서는 **세 모델 모두 두 질문에서 정답 방향(제외 대상 해당)을 정확히
안내**했다 — `comparison.jsonl`의 해당 answer 필드 참고. 실제 Checker 판정과
`_align_answer_with_checks`의 접수·자격 교정이 더해지면서 PR #56에서 관찰된 오류가
재현되지 않은 것으로 보인다. 다만 평가 문항이 5개뿐이라 일반화하기엔 표본이 작다.

## 비용·속도 경향 (PR #56과 동일한 결론)

- nano가 가장 저렴·빠르지만 RQ042 등에서 다소 장황하고 조건부 안내가 많다.
- mini가 비용 대비 가장 균형 잡힌 응답.
- 5.4가 가장 느리고 비싸며(mini 대비 지연 2~3배, 비용 3~5배), 품질 차이는 이번 5문항에서는
  두드러지지 않았다.

## 완료 기준 진행 상황 (이슈 #82)

- [x] Checker 연계 평가 결과 기록 (이 디렉터리 + 이슈 #82 코멘트)
- [ ] 운영 모델 최종 선정 — 팀 리뷰 필요(이 결과는 참고 자료이며, 민지 혼자 확정하지 않음)
