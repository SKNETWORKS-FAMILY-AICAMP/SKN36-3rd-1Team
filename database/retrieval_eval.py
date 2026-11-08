"""
Retrieval(검색 단계) 평가 — Qdrant Top-K 결과에 "정답 근거 원문"이 들어 있는가를 잰다. RAG 답변 평가와는 별개다.

역할 구분
- 이 모듈(임베딩·DB 담당): 검색 결과 청크에 근거가 있는지 (Recall@K, MRR, 첫 정답 순위, 실패 유형)
- 나지인·박소희(RAG 담당): 답변·출처·추가 질문의 품질 (이 모듈은 다루지 않는다)

정답(gold) 형식: 질문마다 필요한 근거 목록. 근거 하나 = (document_id, char_start, char_end, evidence_text)
- 같은 근거 문장이 문서에 여러 번 있으면 유효한 위치를 alternatives로 모두 적고, 그중 하나만 찾으면 된다.
  같은 정책의 다른 문서(예: 대출안내와 이용절차 화면)에 같은 문장이 있으면 그것도 alternatives에 넣는다.
  다른 정책 문서에 우연히 같은 문장이 있는 경우(예: 같은 신청기간)는 정답으로 보지 않는다.
- 근거가 여러 구간에 흩어진 질문은 required 근거가 여러 개다. 모두 찾아야 적중이다.

채점 규칙 (청킹 방식과 무관하게 같은 기준)
- 근거 적중: 검색된 청크 하나가 근거 구간 [char_start, char_end) 전체를 포함 (1자만 겹치는 것은 적중이 아님)
- 질문 적중(hit): 모든 required 근거가 Top-K 안에서 적중. 첫 정답 순위 = 마지막 required 근거가 처음 적중한 순위
- split_only: 어떤 청크도 근거 전체를 담지 못했지만 Top-K 청크들을 합치면 근거 전체가 덮임 (분산 근거)
- partial: required 근거 일부만 적중
- doc_only: 근거는 없고 정답 문서의 청크만 검색됨
- miss: 정답 문서도 없음
- 정답이 없는 일반 질문(answerable=False)은 채점하지 않고 검색된 정책 수만 기록한다.
- 도달 가능성(attainability): 어떤 구성의 청크 중 근거 전체를 담은 청크가 하나도 없으면 그 구성에서는 원리상 적중할 수 없다.
  구성 간 비교 시 함께 보고한다(불리한 판정을 숨기지 않기 위해).
"""

import json
import re
from pathlib import Path
from typing import Any

QUESTION_TYPES = (
    "policy_named", "natural_no_name", "eligibility", "age_income_asset", "exclusion_exception",
    "application", "numeric_table", "disambiguation", "general_no_single_answer",
)
STATUS_EXACT = "verified_exact"
STATUS_WHITESPACE = "verified_whitespace_normalized"
STATUS_AMBIGUOUS = "ambiguous_needs_review"
STATUS_NOT_FOUND = "not_found"


# ---------------------------------------------------------------------------
# 1. 근거 위치 찾기·검증 (원문 대조)
# ---------------------------------------------------------------------------
def find_evidence_spans(text: str, evidence: str) -> tuple[list[tuple[int, int]], str | None]:
    """
    원문에서 근거 문자열 위치를 모두 찾는다. 반환: ([(start, end), ...], match_mode)
    - 글자 그대로 일치하면 "exact"
    - 아니면 공백·줄바꿈 차이만 허용("whitespace"): 근거의 낱말 사이를 \\s+ 로 보고 찾는다. 낱말 자체는 같아야 한다.
    """
    exact = [(m.start(), m.start() + len(evidence)) for m in re.finditer(re.escape(evidence), text)]
    if exact:
        return exact, "exact"
    tokens = evidence.split()
    if not tokens:
        return [], None
    pattern = re.compile(r"\s+".join(re.escape(token) for token in tokens))
    flexible = [(m.start(), m.end()) for m in pattern.finditer(text)]
    return flexible, ("whitespace" if flexible else None)


def build_question_candidates(source_items: list[dict[str, Any]], documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    작성한 질문 후보(근거 문자열)를 원문과 대조해 위치·중복·검증 상태를 채운다. 원문은 바꾸지 않는다.
    source item: {"question_id", "question", "question_type", "answerable"(기본 True), "notes",
                  "gold": [{"document_id", "evidence_text", "occurrence"(1부터, 선택), "accept_any_occurrence"(선택)}]}
    결과 질문: status="candidate"(확정 전), verification="verified"/"needs_review", gold.required[...]
    """
    by_id = {document["document_id"]: document for document in documents}
    built = []
    seen_ids = set()
    for item in source_items:
        if item["question_id"] in seen_ids:
            raise ValueError(f"question_id 중복: {item['question_id']}")
        seen_ids.add(item["question_id"])
        if item["question_type"] not in QUESTION_TYPES:
            raise ValueError(f"알 수 없는 question_type: {item['question_type']}")
        answerable = item.get("answerable", True)
        required = []
        for number, gold in enumerate(item.get("gold", []), start=1):
            document = by_id.get(gold["document_id"])
            entry: dict[str, Any] = {"evidence_id": f"{item['question_id']}-E{number}", "document_id": gold["document_id"],
                                     "evidence_text_source": gold["evidence_text"]}
            if document is None:
                required.append({**entry, "status": STATUS_NOT_FOUND, "alternatives": []})
                continue
            spans, mode = find_evidence_spans(document["text"], gold["evidence_text"])
            others = sorted(other_id for other_id, other in by_id.items()
                            if other_id != gold["document_id"] and find_evidence_spans(other["text"], gold["evidence_text"])[0])
            entry.update({"policy_id": document["policy_id"], "occurrences_in_document": len(spans),
                          "other_documents_with_same_text": others, "match_mode": mode})
            if not spans:
                required.append({**entry, "status": STATUS_NOT_FOUND, "alternatives": []})
                continue
            occurrence = gold.get("occurrence")
            if occurrence is not None:
                if not 1 <= occurrence <= len(spans):
                    required.append({**entry, "status": STATUS_NOT_FOUND, "alternatives": []})
                    continue
                chosen = [spans[occurrence - 1]]
            elif len(spans) == 1 or gold.get("accept_any_occurrence"):
                chosen = spans
            else:
                required.append({**entry, "status": STATUS_AMBIGUOUS,
                                 "alternatives": [_alternative(gold["document_id"], span) for span in spans]})
                continue
            alternatives = [_alternative(gold["document_id"], span) for span in chosen]
            # 같은 정책의 다른 문서에 같은 근거 문장이 있으면 그것도 올바른 근거다 (다른 정책의 우연한 일치는 제외)
            if gold.get("accept_same_policy_documents", True):
                for other_id in others:
                    if by_id[other_id]["policy_id"] == document["policy_id"]:
                        other_spans, _mode = find_evidence_spans(by_id[other_id]["text"], gold["evidence_text"])
                        alternatives += [_alternative(other_id, span) for span in other_spans]
            start, end = chosen[0]
            required.append({**entry, "char_start": start, "char_end": end,
                             "evidence_text": document["text"][start:end],
                             "alternatives": alternatives,
                             "same_policy_alternative_documents": sorted({a["document_id"] for a in alternatives}
                                                                         - {gold["document_id"]}),
                             "status": STATUS_EXACT if mode == "exact" else STATUS_WHITESPACE})
        verified = all(gold["status"] in (STATUS_EXACT, STATUS_WHITESPACE) for gold in required)
        if answerable and not required:
            verified = False
        built.append({"question_id": item["question_id"], "question": item["question"],
                      "question_type": item["question_type"], "answerable": answerable,
                      "policy_ids": sorted({g["policy_id"] for g in required if g.get("policy_id")}),
                      "gold": {"required": required}, "notes": item.get("notes", ""),
                      "status": "candidate", "verification": "verified" if verified else "needs_review"})
    return built


def _alternative(document_id: str, span: tuple[int, int]) -> dict[str, Any]:
    return {"document_id": document_id, "char_start": span[0], "char_end": span[1]}


def load_jsonl(path: Path | str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path | str, rows: list[dict[str, Any]]) -> None:
    Path(path).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. 채점
# ---------------------------------------------------------------------------
def _contains(hit: dict[str, Any], alternative: dict[str, Any]) -> bool:
    """청크(hit)가 근거 구간 전체를 담고 있는가. 1자만 겹치는 것은 아니다."""
    return (hit.get("document_id") == alternative["document_id"] and hit.get("char_start") is not None
            and hit["char_start"] <= alternative["char_start"] and alternative["char_end"] <= hit["char_end"])


def _covered_by_union(hits: list[dict[str, Any]], alternative: dict[str, Any]) -> bool:
    """여러 청크를 이어 붙여야 근거 구간 전체가 덮이는가 (분산 근거 판정)."""
    ranges = sorted((h["char_start"], h["char_end"]) for h in hits
                    if h.get("document_id") == alternative["document_id"] and h.get("char_start") is not None)
    position = alternative["char_start"]
    for start, end in ranges:
        if start <= position < end:
            position = end
        if position >= alternative["char_end"]:
            return True
    return position >= alternative["char_end"]


def score_question(question: dict[str, Any], hits: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    """
    질문 하나 채점. hits: 검색 결과 [{rank, document_id, char_start, char_end, chunk_id, policy_id}] (rank 오름차순)
    반환: outcome(hit/split_only/partial/doc_only/miss/not_scored), first_hit_rank, reciprocal_rank, evidence별 순위
    """
    top = sorted(hits, key=lambda h: h["rank"])[:k]
    if not question["answerable"]:
        return {"question_id": question["question_id"], "outcome": "not_scored", "first_hit_rank": None,
                "reciprocal_rank": None, "distinct_policies": len({h.get("policy_id") for h in top}), "evidence": []}
    required = question["gold"]["required"]
    evidence_results = []
    for gold in required:
        rank = next((h["rank"] for h in top if any(_contains(h, alt) for alt in gold["alternatives"])), None)
        split = rank is None and any(_covered_by_union(top, alt) for alt in gold["alternatives"])
        evidence_results.append({"evidence_id": gold["evidence_id"], "rank": rank, "split_only": split})
    gold_documents = {alt["document_id"] for gold in required for alt in gold["alternatives"]}
    doc_rank = next((h["rank"] for h in top if h.get("document_id") in gold_documents), None)
    found = [result for result in evidence_results if result["rank"] is not None]
    if len(found) == len(required):
        outcome, first = "hit", max(result["rank"] for result in found)
    elif all(result["rank"] is not None or result["split_only"] for result in evidence_results):
        outcome, first = "split_only", None
    elif found:
        outcome, first = "partial", None
    elif doc_rank is not None:
        outcome, first = "doc_only", None
    else:
        outcome, first = "miss", None
    return {"question_id": question["question_id"], "outcome": outcome, "first_hit_rank": first,
            "reciprocal_rank": (1.0 / first) if first else 0.0, "first_gold_document_rank": doc_rank,
            # 표시용(판정 기준 아님): 필수 근거 중 Top-K에서 통째로 찾은 개수 / 전체 개수
            "required_found": len(found), "required_total": len(required),
            "evidence": evidence_results}


def attainability(questions: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> dict[str, list[str]]:
    """구성의 전체 청크 중 근거 전체를 담은 청크가 하나도 없는 근거 목록 {question_id: [evidence_id, ...]}"""
    by_document: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        by_document.setdefault(chunk["document_id"], []).append(chunk)
    unattainable: dict[str, list[str]] = {}
    for question in questions:
        if not question["answerable"]:
            continue
        for gold in question["gold"]["required"]:
            if not any(_contains(chunk, alt) for alt in gold["alternatives"]
                       for chunk in by_document.get(alt["document_id"], [])):
                unattainable.setdefault(question["question_id"], []).append(gold["evidence_id"])
    return unattainable


def build_from_source(source_path: Path | str, output_path: Path | str) -> list[dict[str, Any]]:
    """원본(questions_candidates_*.source.json)을 원문과 대조해 후보 JSONL을 만든다. 원문 해시를 먼저 검증한다."""
    from database import handoff_data
    source = json.loads(Path(source_path).read_text(encoding="utf-8"))
    questions = build_question_candidates(source["items"], handoff_data.load_documents(verify_hash=True))
    for question in questions:
        question["source_version"] = source["version"]
        question["data_version"] = source["data_version"]
    write_jsonl(output_path, questions)
    return questions


def summarize(scores: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    """Recall@K(질문 적중 비율), MRR, 결과 유형별 개수, 실패 질문 목록. not_scored는 분모에서 뺀다."""
    scored = [score for score in scores if score["outcome"] != "not_scored"]
    counts: dict[str, int] = {}
    for score in scores:
        counts[score["outcome"]] = counts.get(score["outcome"], 0) + 1
    hits = [score for score in scored if score["outcome"] == "hit"]
    return {"k": k, "scored_questions": len(scored), "not_scored": counts.get("not_scored", 0),
            f"recall_at_{k}": round(len(hits) / len(scored), 4) if scored else None,
            "mrr": round(sum(score["reciprocal_rank"] for score in scored) / len(scored), 4) if scored else None,
            "outcomes": counts,
            "failures": [score["question_id"] for score in scored if score["outcome"] != "hit"]}
