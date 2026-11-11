"""SSOK: 실제 검색 근거를 고정한 LLM 3종 비교."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

LLM_ROOT = Path(__file__).resolve().parents[3]
EVAL_ROOT = Path(
    os.getenv("LIFEPOLICY_EVAL_REPO", str(LLM_ROOT))
)

sys.path.insert(0, str(LLM_ROOT))
sys.path.insert(
    0,
    str(LLM_ROOT / "tests" / "evaluation" / "llm_comparison")
)

import compare_models as cm

QUESTION_IDS = (
    "RQ027",
    "RQ031",
    "RQ036",
    "RQ040",
    "RQ042",
)

OUT_DIR = (
    LLM_ROOT
    / "tests"
    / "evaluation"
    / "llm_comparison"
    / "results"
    / "real_gold_batch01"
)

SNAPSHOT = OUT_DIR / "inputs.json"


def prepare():
    from database import hybrid_search as hs
    from database import retrieval_eval as rv

    if SNAPSHOT.exists():
        print("기존 입력 스냅샷을 재사용합니다.")
        return

    source = (
        EVAL_ROOT
        / "docs"
        / "development"
        / "retrieval_eval"
        / "questions_candidates_v0.jsonl"
    )

    cases = {}

    for line in source.read_text(
        encoding="utf-8-sig"
    ).splitlines():
        if not line.strip():
            continue

        case = json.loads(line)
        cases[case["question_id"]] = case

    if any(qid not in cases for qid in QUESTION_IDS):
        raise ValueError("필수 Gold 질문이 누락됐습니다.")

    scenarios = []

    with hs.HybridSearchSession(
        profile="b2_06b_v140"
    ) as session:

        for qid in QUESTION_IDS:
            case = cases[qid]

            search = session.search_policy_evidence(
                case["question"],
                k=5,
            )

            hits = search["hits"]

            if not hits:
                raise ValueError(
                    f"{qid}: 검색 결과가 없습니다."
                )

            contexts = []

            for hit in hits:
                content = hit.get("text") or ""

                if not content.strip():
                    continue

                contexts.append({
                    "source_id": len(contexts) + 1,
                    "policy_id": hit.get("policy_id") or "",
                    "policy_name": (
                        hit.get("policy_name")
                        or hit.get("policy_id")
                        or "정책명 미상"
                    ),
                    "text": content,
                    "quality_warnings": [],
                })

            if not contexts:
                raise ValueError(
                    f"{qid}: 유효한 근거 텍스트가 없습니다."
                )

            score = rv.score_question(
                case,
                hits,
                k=5,
            )

            scenarios.append({
                "name": qid,
                "question": case["question"],
                "contexts": contexts,

                # Checker 미실행 상태를 유지
                "policy_checks": [],

                "review": (
                    "공식 문서 근거에 충실한 답변인지, "
                    "미확인 자격을 확정하지 않는지, "
                    "근거 없는 정보를 생성하지 않는지 검토"
                ),

                # 평가용 메타데이터: LLM 입력에는 전달하지 않음
                "audit": {
                    "gold_status": case.get("status"),
                    "gold_policy_ids": case.get("policy_ids"),
                    "gold_required": case["gold"]["required"],
                    "retrieval_outcome": score["outcome"],
                    "retrieval_chunks": [
                        {
                            "rank": hit.get("rank"),
                            "chunk_id": hit.get("chunk_id"),
                            "document_id": hit.get("document_id"),
                            "policy_id": hit.get("policy_id"),
                            "source_url": hit.get("source_url"),
                        }
                        for hit in hits
                    ],
                },
            })

            print(
                f"{qid}: "
                f"contexts={len(contexts)}, "
                f"gold@5={score['outcome']}",
                flush=True,
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    SNAPSHOT.write_text(
        json.dumps(
            scenarios,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\n입력 스냅샷 저장: {SNAPSHOT}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제 OpenAI API 호출",
    )
    args = parser.parse_args()

    if not SNAPSHOT.exists():
        if args.execute:
            parser.error(
                "먼저 API 호출 없이 입력 스냅샷을 생성하세요."
            )
        prepare()

    scenarios = json.loads(
        SNAPSHOT.read_text(encoding="utf-8")
    )

    if [s["name"] for s in scenarios] != list(QUESTION_IDS):
        raise ValueError("평가 질문 구성이 변경됐습니다.")

    digest = hashlib.sha256(
        SNAPSHOT.read_bytes()
    ).hexdigest()

    print("\n=== REAL GOLD GENERATOR AB ===")
    print("Questions:", len(scenarios))
    print("Models:", ", ".join(cm.MODELS))
    print("Maximum API calls:", len(scenarios) * len(cm.MODELS))
    print("Input SHA256:", digest)
    print("Gold status: candidate / preliminary")

    for scenario in scenarios:
        print(
            scenario["name"],
            "gold@5=",
            scenario["audit"]["retrieval_outcome"]
        )

    if not args.execute:
        print("\nDRY RUN 완료: OpenAI API 호출 없음")
        print("실행 시 --execute를 추가하세요.")
        return

    cm.load_scenarios = lambda: scenarios

    original_argv = sys.argv[:]
    try:
        sys.argv = [
            str(LLM_ROOT / "tests/evaluation/llm_comparison/compare_models.py"),
            "--limit", str(len(scenarios)),
            "--budget", "0.30",
            "--execute",
        ]
        cm.main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main()
