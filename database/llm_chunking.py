"""
LLM 보조 청킹(C1 후보) — LLM은 "섹션 제목과 원문 시작 문자열"만 제안하고, 경계·본문은 원문에서 검증해 만든다.

강의 노트북(Advanced RAG, 2-2. Qdrant-Langgraph.ipynb 셀 9~21)과 같은 흐름이다.
  ① LLM이 [{title, start_text}] 목록을 낸다 (Pydantic ListSection과 같은 모양의 JSON)
  ② 원문에서 start_text 위치를 찾는다 (강의: full_text.find(start_text))
  ③ 위치 순으로 정렬해 "다음 섹션 시작 전까지"를 한 섹션으로 자른다
강의 코드와 다른 점 (이 프로젝트의 원문 보존·근거 규칙 때문)
  - 찾지 못한 제안을 그냥 오류로 멈추지 않고, 문제 종류를 기록한 뒤 그 제안만 버린다.
    남은 제안이 부족하거나 원문 보존 검사에 실패하면 문서 전체를 B3(규칙 기반) 결과로 대체한다.
  - 반복 제목, 원문에 없는 제목, 경계 역전, 줄 중간 시작, 페이지 표지 안 시작, 페이지 불일치를 검사한다.
  - 청크 본문은 항상 원문[char_start:char_end]이다. LLM이 쓴 문장(title 포함)은 본문에 넣지 않는다.
    title은 section_title 메타데이터로만 쓴다.
  - 긴 문서는 페이지(PDF)·줄(HTML) 단위 창(window)으로 나눠 보낸다. 위치는 원문 전체 기준으로 되돌린다.
  - 섹션이 CHUNK_SIZE보다 길면 B3와 같은 split_long_span(avoid_orphan_label=True)으로 자르고,
    짧은 청크는 B2와 같은 merge_short_chunks 규칙으로 합친다 (버전 이름 C1).

이 모듈은 네트워크를 직접 호출하지 않는다. proposer(프롬프트 문자열 → 응답 문자열) 함수를 받는다.
- 테스트: 가짜 proposer
- 실제 실행: make_ollama_section_proposer(model=...) — 사용할 모델은 사용자 승인 후 정한다(기본 모델 없음).
같은 제안(proposals)을 다시 넣으면 결과가 같다(chunk_document_with_sections). 제안을 JSON으로 저장해 두면 재현할 수 있다.
"""

import hashlib
import json
import re
import time
from typing import Any, Callable

import requests

from database import chunking as ck
from database import embedding as em

C1_VERSION = "C1"
C1_PRE_MERGE_VERSION = "C1PRE"
PROMPT_VERSION = "SECTION_PROMPT_V1"
TARGET_SOURCE_TYPES = ("pdf", "html")     # API 문서는 [필드명] 구조가 있어 대상이 아니다 (B3로 대체)

# 창 하나의 최대 글자 수. 근거: 강의처럼 문서 전체를 한 번에 보내면 DOC_LH_001(35,657자)은
# 로컬 모델의 문맥 길이를 넘을 수 있다. 6,000자는 NHUF_003·005(약 6,500자)를 2개 창으로 나누는 정도의 크기다.
# 실험 설정값이며 make_windows(window_chars=)로 바꿀 수 있다.
DEFAULT_WINDOW_CHARS = 6000
# start_text 최소 길이: 너무 짧으면("1.", "□") 원문 여러 곳과 맞아 위치가 모호해진다.
MIN_START_TEXT_CHARS = 4
# 제목 존재 확인 범위: 시작 위치부터 이 글자 수 안에 (공백 제거 후) 제목 글자가 있어야 한다.
TITLE_SEARCH_CHARS = 200
MIN_VALID_SECTIONS = 2                    # analyze_structure와 같은 기준: 제목 2개 미만이면 섹션 경계 근거가 없다

SECTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start_text": {"type": "string"},
                    "page": {"type": ["integer", "null"]},
                },
                "required": ["title", "start_text"],
            },
        },
    },
    "required": ["sections"],
}

PROMPT_TEMPLATE = """다음은 정부 정책 공고문의 일부입니다. 본문에 실제로 있는 섹션 제목과 각 섹션의 시작 위치를 찾으세요.

반환 형식(JSON만 출력):
{{"sections": [{{"title": "원문에 있는 섹션 제목", "start_text": "섹션 첫 줄의 원문 글자 그대로", "page": 페이지 번호 또는 null}}]}}

규칙:
- 원문에 존재하는 제목만 반환하세요. 제목을 새로 만들거나 요약하지 마세요.
- start_text는 원문에 글자 그대로 있어야 합니다. 띄어쓰기·기호·번호까지 바꾸지 말고, 섹션 첫 줄의 앞부분 {min_chars}자 이상을 복사하세요.
- 섹션은 원문에 나오는 순서대로 반환하세요.
- 목차, 표의 칸 이름, 목록의 한 항목, 금액·나이·기간 같은 수치 조건은 섹션 제목이 아닙니다.
- "--- [Page N] ---" 줄은 페이지 표시입니다. 제목이 아닙니다. page에는 그 섹션이 시작되는 페이지 번호를 넣으세요(없으면 null).

공고문:
{window_text}
"""


class SectionProposalError(RuntimeError):
    """proposer 응답을 해석할 수 없음 (JSON 형식 오류 등)."""


# ---------------------------------------------------------------------------
# 1. 창 나누기와 프롬프트
# ---------------------------------------------------------------------------
def make_windows(document: dict[str, Any], window_chars: int = DEFAULT_WINDOW_CHARS) -> list[dict[str, Any]]:
    """
    문서를 LLM에 보낼 창으로 나눈다. 반환: [{"start", "end", "pages": [페이지 번호...]}] (원문 전체 기준 위치)
    - PDF: 페이지 표지 경계에서만 나눈다. 페이지들을 window_chars를 넘지 않게 묶는다(한 페이지가 더 길면 그 페이지 혼자).
    - HTML: 줄 경계에서만 나눈다.
    창은 원문을 빠짐없이·겹침없이 덮는다.
    """
    text = document["text"]
    if not text:
        return []
    if document["source_type"] == "pdf":
        markers = ck.find_page_markers(text)
        boundaries = [(start, page) for start, _end, page in markers]
        if not boundaries or boundaries[0][0] > 0:
            boundaries.insert(0, (0, None))
        units = [(start, boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text), page)
                 for i, (start, page) in enumerate(boundaries)]
    else:
        units = [(match.start(), match.end(), None) for match in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)
                 if match.end() > match.start()]
    windows: list[dict[str, Any]] = []
    for start, end, page in units:
        if windows and end - windows[-1]["start"] <= window_chars:
            windows[-1]["end"] = end
            if page is not None:
                windows[-1]["pages"].append(page)
        else:
            windows.append({"start": start, "end": end, "pages": [page] if page is not None else []})
    return windows


def build_section_prompt(window_text: str) -> str:
    """창 하나의 프롬프트. 원문을 그대로 넣는다 (원문 안의 문장은 데이터일 뿐 지시가 아니다)."""
    return PROMPT_TEMPLATE.format(window_text=window_text, min_chars=MIN_START_TEXT_CHARS)


def parse_proposals(raw: str) -> list[dict[str, Any]]:
    """proposer 응답(JSON 문자열)을 제안 목록으로 바꾼다. 형식이 틀리면 SectionProposalError."""
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise SectionProposalError(f"JSON 해석 실패: {type(error).__name__}") from error
    sections = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections, list):
        raise SectionProposalError("sections 목록이 없음")
    return sections


# ---------------------------------------------------------------------------
# 2. 제안 검증 (원문 대조)
# ---------------------------------------------------------------------------
def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _page_at(markers: list[tuple[int, int, int]], position: int) -> int | None:
    page = None
    for start, _end, number in markers:
        if start <= position:
            page = number
        else:
            break
    return page


def validate_proposals(document: dict[str, Any], proposals: list[dict[str, Any]]) -> dict[str, Any]:
    """
    제안을 원문과 대조한다. proposals: [{"title", "start_text", "page"(선택), "window_start", "window_end"}]
    반환: {"starts": [(원문 위치, 제목, 2)], "accepted": [...], "issues": [{"index", "issue", "title", ...}]}

    문제 종류 (해당 제안만 버린다)
      invalid_format        title·start_text가 문자열이 아님 / 비어 있음
      start_text_too_short  start_text가 MIN_START_TEXT_CHARS보다 짧음
      start_text_not_found  창 안에 start_text가 글자 그대로 없음 (LLM이 문장을 바꿔 쓴 경우 포함)
      mid_line_start        start_text가 줄 중간에서 시작함 (줄 앞에 기호·번호 말고 다른 글자가 있음)
      inside_excluded_span  페이지 표지 줄 안에서 시작함
      title_not_in_source   제목 글자(공백 제거)가 시작 위치 근처 원문에 없음 → 존재하지 않는 제목
      duplicate_start       이미 인정한 섹션과 같은 줄(같은 원문 시작 위치)을 가리킴 → 하나만 인정
      boundary_inversion    앞 섹션보다 앞 위치만 가리킴 (순서 역전)
      page_mismatch         page가 실제 시작 위치의 PDF 페이지와 다름

    위치 고르기 (2026-11-06 수정)
    - 같은 제목이라도 원문 위치가 다르면 각각 경계로 인정한다. (요약 상자 "대출대상"과 본문 "대출 대상"처럼)
      이전 버전은 제목이 같으면 위치와 상관없이 뒤의 것을 버려(duplicate_title), 본문 섹션이 앞 섹션 제목을 물려받았다.
      반복 제목은 거부하지 않고 accepted 항목의 repeated_title=True로 표시만 한다.
    - start_text가 창 안에 여러 번 있으면, "앞 섹션의 줄 시작보다 뒤"에 있는 일치 위치를 순서대로 보며
      줄 시작·제외 구간·제목 근접·페이지 조건을 모두 통과하는 첫 위치를 고른다. (창 전체의 첫 일치를 무조건 쓰지 않는다)
    - 통과하는 위치가 없으면 가장 앞선 후보의 실패 이유로 거부한다.
    """
    text = document["text"]
    markers = ck.find_page_markers(text) if document["source_type"] == "pdf" else []
    accepted: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    seen_lines: set[int] = set()
    last_line_start = -1

    def occurrence_problem(position: int, title: str, page: Any) -> tuple[str | None, dict[str, Any]]:
        """일치 위치 하나의 검사. 반환: (실패 이유 또는 None, 부가 정보)"""
        line_start = ck._line_start(text, position)
        info: dict[str, Any] = {"position": position, "line_start": line_start}
        if re.search(r"[가-힣A-Za-z]", text[line_start:position]):
            return "mid_line_start", info
        if any(start <= line_start < end for start, end, _page in markers):
            return "inside_excluded_span", info
        if _compact(title) not in _compact(text[line_start:line_start + TITLE_SEARCH_CHARS]):
            return "title_not_in_source", info
        actual_page = _page_at(markers, line_start) if markers else None
        info["actual_page"] = actual_page
        if markers and page is not None and page != actual_page:
            return "page_mismatch", {**info, "proposed_page": page}
        return None, info

    for index, proposal in enumerate(proposals):
        title = proposal.get("title") if isinstance(proposal, dict) else None
        start_text = proposal.get("start_text") if isinstance(proposal, dict) else None

        def reject(issue: str, **extra: Any) -> None:
            issues.append({"index": index, "issue": issue, "title": title if isinstance(title, str) else None, **extra})

        if not isinstance(title, str) or not isinstance(start_text, str) or not title.strip() or not start_text.strip():
            reject("invalid_format")
            continue
        start_text = start_text.strip()
        if len(start_text) < MIN_START_TEXT_CHARS:
            reject("start_text_too_short")
            continue
        window_start = int(proposal.get("window_start", 0))
        window_end = int(proposal.get("window_end", len(text)))
        page = proposal.get("page")

        # 창 안의 모든 일치 위치 (start_text 전체가 창 안에 있어야 한다)
        occurrences: list[int] = []
        search_from = window_start
        while True:
            found = text.find(start_text, search_from, window_end)
            if found == -1:
                break
            occurrences.append(found)
            search_from = found + 1
        if not occurrences:
            reject("start_text_not_found")
            continue

        after_previous = [p for p in occurrences if ck._line_start(text, p) > last_line_start]
        chosen: dict[str, Any] | None = None
        first_failure: tuple[str, dict[str, Any]] | None = None
        valid_candidates = 0
        for position in after_previous:
            problem, info = occurrence_problem(position, title, page)
            if problem is None:
                valid_candidates += 1
                if chosen is None:
                    chosen = info
            elif first_failure is None:
                first_failure = (problem, info)
        if chosen is None:
            if first_failure is not None:
                reject(first_failure[0], **first_failure[1])
            elif any(ck._line_start(text, p) in seen_lines for p in occurrences):
                reject("duplicate_start", position=occurrences[0])        # 이미 인정한 줄을 다시 가리킴
            else:
                reject("boundary_inversion", position=occurrences[0])     # 앞 섹션보다 앞에만 있음
            continue

        line_start = chosen["line_start"]
        seen_lines.add(line_start)
        last_line_start = line_start
        repeated = _compact(title) in seen_titles
        seen_titles.add(_compact(title))
        accepted.append({"index": index, "title": title.strip(), "start": line_start, "page": chosen.get("actual_page"),
                         "occurrences_in_window": len(occurrences), "valid_candidates_after_previous": valid_candidates,
                         "repeated_title": repeated})

    starts = sorted((item["start"], item["title"][:60], 2) for item in accepted)
    return {"starts": starts, "accepted": accepted, "issues": issues}


# ---------------------------------------------------------------------------
# 3. 청킹 (결정적 단계: 같은 제안 → 같은 청크)
# ---------------------------------------------------------------------------
def _coverage_ok(document: dict[str, Any], chunks: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = ck.verify_coverage(document, chunks, excluded)
    restore_mismatch = sum(chunk["text"] != document["text"][chunk["char_start"]:chunk["char_end"]] for chunk in chunks)
    coverage["restore_mismatch"] = restore_mismatch
    coverage["ok"] = (coverage["missing_chars"] == 0 and coverage["unintended_overlap_chars"] == 0
                      and coverage["excluded_in_chunks"] == 0 and restore_mismatch == 0)
    return coverage


def _attach_section_titles(pre_chunks: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    C1 최종 청크에 원래 섹션 제목을 모두 남긴다 (짧은 섹션 병합 + 짧은 청크 병합 양쪽).
    - section_titles: 합쳐진 병합 전 청크들의 제목 목록을 순서대로·중복 없이 모은 것
    - section_title : section_titles를 " + "로 이은 문자열 (기존 검색·임베딩 코드가 쓰는 필드 그대로.
      embedding.section_header_for_embedding이 " + " 단위로 나눠 각 제목을 처리한다)
    본문(text)·위치는 바꾸지 않는다.
    """
    titles_by_id = {chunk["chunk_id"]: chunk.get("section_titles") or [chunk["section_title"]] for chunk in pre_chunks}
    for chunk in chunks:
        titles: list[str] = []
        for source_id in chunk.get("merged_from", []):
            for title in titles_by_id.get(source_id, []):
                if title not in titles:
                    titles.append(title)
        if titles:
            chunk["section_titles"] = titles
            chunk["section_title"] = em.TITLE_GROUP_SEPARATOR.join(titles)
    return chunks


def chunk_document_with_sections(
    document: dict[str, Any],
    proposals: list[dict[str, Any]],
    *,
    min_valid_sections: int = MIN_VALID_SECTIONS,
    fallback: Callable[[dict[str, Any]], tuple[list[dict[str, Any]], dict[str, Any]]] = ck.chunk_document_b3,
    upstream_issues: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    검증된 제안으로 C1 청크를 만든다. 실패하면 fallback(기본 B3) 결과를 돌려주고 그 사유를 기록한다.
    반환: (청크 리스트, 처리 기록). 처리 기록: chunking_version("C1" 또는 대체 버전), used_fallback, fallback_reasons,
          proposals, accepted_sections, issues, coverage
    """
    issues = list(upstream_issues or [])
    fallback_reasons: list[str] = []
    validation = {"starts": [], "accepted": [], "issues": []}
    if document["source_type"] not in TARGET_SOURCE_TYPES:
        fallback_reasons.append(f"대상 문서 유형 아님: {document['source_type']}")
    else:
        validation = validate_proposals(document, proposals)
        issues.extend(validation["issues"])
        if len(validation["accepted"]) < min_valid_sections:
            fallback_reasons.append(f"검증 통과 섹션 {len(validation['accepted'])}개 < {min_valid_sections}개")

    if not fallback_reasons:
        pre_chunks, report = ck._chunk_document_core(
            document, version=C1_PRE_MERGE_VERSION, html_starts=ck._heading_starts_html, avoid_orphan_label=True,
            starts_override=validation["starts"], override_method="llm_section",
            short_piece_policy="forward_keep_titles")
        chunks = _attach_section_titles(pre_chunks, ck.merge_short_chunks(document, pre_chunks, version=C1_VERSION))
        coverage = _coverage_ok(document, chunks, report["excluded_spans"])
        if coverage["ok"]:
            return chunks, {**report, "chunking_version": C1_VERSION, "used_fallback": False, "fallback_reasons": [],
                            "pre_merge_chunk_count": len(pre_chunks), "chunk_count": len(chunks),
                            "proposals": len(proposals), "accepted_sections": validation["accepted"],
                            "issues": issues, "coverage": coverage}
        fallback_reasons.append("원문 보존 검사 실패 (누락·중복·제외 구간·복원 불일치)")

    chunks, report = fallback(document)
    coverage = _coverage_ok(document, chunks, report["excluded_spans"])
    return chunks, {**report, "used_fallback": True, "fallback_reasons": fallback_reasons,
                    "proposals": len(proposals), "accepted_sections": validation["accepted"],
                    "issues": issues, "coverage": coverage}


def propose_sections(
    document: dict[str, Any], proposer: Callable[[str], str], *, window_chars: int = DEFAULT_WINDOW_CHARS,
    window_indices: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    창마다 proposer를 불러 제안을 모은다. 반환: (제안 목록, 창별 호출 기록, 호출 단계 문제 목록)
    proposer 예외·JSON 오류는 그 창만 실패로 기록하고 다음 창으로 넘어간다 (오류 메시지에 비밀값을 넣지 않는다).
    window_indices: 이 번호의 창만 호출한다 (None이면 전체). 유료 API를 창 단위로 나눠 승인받아 실행하기 위해 쓴다.
    """
    proposals: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for window_index, window in enumerate(make_windows(document, window_chars)):
        if window_indices is not None and window_index not in window_indices:
            continue
        prompt = build_section_prompt(document["text"][window["start"]:window["end"]])
        started = time.perf_counter()
        record = {"window": window_index, "start": window["start"], "end": window["end"], "pages": window["pages"],
                  "prompt_chars": len(prompt), "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}
        try:
            raw = proposer(prompt)
            record["response_sha256"] = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()
            sections = parse_proposals(raw)
        except Exception as error:   # noqa: BLE001 - 실패를 기록하고 대체 경로로 간다
            record["error"] = f"{type(error).__name__}: {str(error)[:200]}"
            issues.append({"window": window_index, "issue": "proposer_failed", "error": record["error"]})
            sections = []
        record["seconds"] = round(time.perf_counter() - started, 3)
        record["proposals"] = len(sections)
        calls.append(record)
        for section in sections:
            item = dict(section) if isinstance(section, dict) else {"_raw": section}
            item.update({"window_start": window["start"], "window_end": window["end"]})
            proposals.append(item)
    return proposals, calls, issues


class SavedProposalError(RuntimeError):
    """저장된 C1 제안을 쓸 수 없음 (파일 없음·호출 실패 창·창 범위 불일치)."""


def _window_result_succeeded(result: dict[str, Any]) -> bool:
    calls = result.get("api_call_log") or []
    completed = any(call.get("status") == 200 and call.get("response_status") == "completed" for call in calls)
    return (completed and result.get("status", "success") == "success" and not result.get("simulated")
            and isinstance(result.get("proposals"), list))


def load_saved_proposals(document: dict[str, Any], directory) -> list[dict[str, Any]]:
    """
    저장된 실제 LLM 응답(c1_openai_<문서>_w<창>.json)에서 문서의 제안 전체를 창 범위와 함께 읽는다. API를 호출하지 않는다.
    - 모든 창 파일이 있어야 하고, 각 창은 실제 호출 성공(completed)이어야 한다 (시뮬레이션 결과는 거부).
    - 저장된 창 범위가 현재 make_windows 결과와 다르면(원문·창 규칙 변경) 거부한다.
    """
    from pathlib import Path
    folder = Path(directory)
    windows = make_windows(document)
    proposals: list[dict[str, Any]] = []
    for index, window in enumerate(windows):
        path = folder / f"c1_openai_{document['document_id']}_w{index}.json"
        if not path.is_file():
            raise SavedProposalError(f"저장된 제안 없음: {path.name}")
        result = json.loads(path.read_text(encoding="utf-8"))
        if not _window_result_succeeded(result):
            raise SavedProposalError(f"실제 호출 성공 창이 아님: {path.name}")
        if list(result["plan"]["window_range"]) != [window["start"], window["end"]]:
            raise SavedProposalError(f"창 범위 불일치: {path.name}")
        proposals += [{**{key: item.get(key) for key in ("title", "start_text", "page")},
                       "window_start": window["start"], "window_end": window["end"]} for item in result["proposals"]]
    return proposals


def chunk_document_llm(
    document: dict[str, Any], proposer: Callable[[str], str], *, window_chars: int = DEFAULT_WINDOW_CHARS,
    model_info: dict[str, Any] | None = None, window_indices: set[int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    LLM 보조 청킹 진입 함수: 제안 수집(propose_sections) → 검증·청킹(chunk_document_with_sections).
    처리 기록에 prompt_version, window_chars, model_info, llm_calls(창별 해시·시간)가 추가된다.
    재현: report의 raw 제안 대신, 반환된 report["proposals_used"]를 저장해 chunk_document_with_sections에 다시 넣는다.
    """
    if document["source_type"] not in TARGET_SOURCE_TYPES:
        chunks, report = chunk_document_with_sections(document, [])
        return chunks, {**report, "prompt_version": PROMPT_VERSION, "llm_calls": [], "model_info": model_info or {}}
    proposals, calls, call_issues = propose_sections(document, proposer, window_chars=window_chars,
                                                     window_indices=window_indices)
    chunks, report = chunk_document_with_sections(document, proposals, upstream_issues=call_issues)
    return chunks, {**report, "prompt_version": PROMPT_VERSION, "window_chars": window_chars,
                    "model_info": model_info or {}, "llm_calls": calls, "proposals_used": proposals}


# ---------------------------------------------------------------------------
# 4. Ollama proposer (실제 호출은 사용자 승인 후. 이 함수 자체는 연결만 만든다)
# ---------------------------------------------------------------------------
def make_ollama_section_proposer(
    model: str,
    *,
    base_url: str | None = None,
    timeout: float = 300,
    num_ctx: int = 8192,
    seed: int = 0,
    think: bool | None = None,
    http_post: Callable[..., Any] = requests.post,
    metrics_sink: list[dict[str, Any]] | None = None,
) -> Callable[[str], str]:
    """
    Ollama /api/chat 로 섹션 제안을 받는 proposer를 만든다. JSON 스키마 형식 강제, temperature 0, seed 고정.
    - model은 필수다 (생성 모델 최종 선택은 미확정이므로 기본값을 두지 않는다).
    - num_ctx 8192: 창 6,000자(한국어) + 프롬프트를 담기 위한 값 (모델별로 확인 필요).
    - think: Qwen3 계열처럼 생각 모드가 있는 모델에서 False로 끄고 싶을 때만 넘긴다(None이면 보내지 않음).
    - metrics_sink에 Ollama가 돌려준 시간·토큰 수(total_duration, prompt_eval_count, eval_count)를 남긴다.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model 이름이 필요합니다 (생성 모델은 승인 후 지정).")
    url = f"{em.resolve_ollama_base_url(base_url)}/api/chat"

    def propose(prompt: str) -> str:
        body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}],
                                "format": SECTION_JSON_SCHEMA, "stream": False,
                                "options": {"temperature": 0, "seed": seed, "num_ctx": num_ctx}}
        if think is not None:
            body["think"] = think
        response = http_post(url, json=body, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if metrics_sink is not None:
            metrics_sink.append({key: data.get(key) for key in
                                 ("model", "total_duration", "load_duration", "prompt_eval_count", "eval_count")})
        return data["message"]["content"]

    return propose


# ---------------------------------------------------------------------------
# 5. OpenAI proposer (유료 API — 호출은 사용자 승인 범위 안에서만)
# ---------------------------------------------------------------------------
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_MODEL_ENV = "OPENAI_CHUNKING_MODEL"         # 모델 이름 설정용 환경 변수 (인자로 넘기면 인자가 우선)
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

# 단가 (USD / 100만 토큰). 출처: OpenAI 공식 모델 페이지 developers.openai.com/api/docs/models/gpt-5.6-terra
# (2026-11-06 확인). 추론(reasoning) 토큰은 출력 토큰으로 과금된다. 272K 초과 입력은 단가가 달라지므로 호출하지 않는다.
# 단가가 바뀌면 이 표를 고치거나 make_openai_section_proposer(prices=)로 넘긴다. 표에 없는 모델은 비용을 계산할 수 없어 호출하지 않는다.
OPENAI_PRICES_PER_1M: dict[str, dict[str, float]] = {
    "gpt-5.6-terra": {"input": 2.00, "cached_input": 0.20, "output": 12.00},
}
OPENAI_LONG_CONTEXT_INPUT_TOKENS = 272_000
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Structured Outputs strict 모드용 스키마: 모든 속성 required + additionalProperties false (page는 null 허용)
OPENAI_SECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "start_text": {"type": "string"},
                    "page": {"type": ["integer", "null"]},
                },
                "required": ["title", "start_text", "page"],
            },
        },
    },
    "required": ["sections"],
}


class BudgetExceededError(RuntimeError):
    """다음 호출의 최악 비용을 더하면 예산 상한을 넘을 수 있어 호출하지 않았다."""


class CallLimitError(RuntimeError):
    """허용한 호출 수(max_calls)를 다 썼다."""


class OpenAIRequestError(RuntimeError):
    """OpenAI 요청 실패. 메시지에는 HTTP 상태와 오류 종류만 넣는다 (키·요청 본문·원문 없음)."""

    def __init__(self, message: str, status: int | None = None, kind: str | None = None):
        super().__init__(message)
        self.status = status
        self.kind = kind


class CostTracker:
    """
    호출별 실제 사용량(usage)으로 누적 비용을 계산하고, 호출 "전에" 최악 비용으로 예산을 검사한다.
    최악 비용 = 입력 추정 토큰 × 입력 단가 + max_output_tokens × 출력 단가 (캐시 할인은 계산하지 않음)
    주의: 이 검사는 프로그램 안의 안전장치다. OpenAI 계정 쪽 과금을 막지 못하므로 사용량 화면에서 따로 확인한다.
    """

    def __init__(self, prices: dict[str, float], budget_usd: float, max_calls: int):
        if not prices or any(key not in prices for key in ("input", "cached_input", "output")):
            raise ValueError("단가(input, cached_input, output)가 필요합니다.")
        self.prices = dict(prices)
        self.budget_usd = float(budget_usd)
        self.max_calls = int(max_calls)
        self.attempts = 0
        self.records: list[dict[str, Any]] = []
        self.unmeasured: list[dict[str, Any]] = []     # 사용량을 받지 못한 시도 (연결 오류·시간 초과)

    @property
    def spent_usd(self) -> float:
        """응답 usage로 계산한 실제 비용 합계."""
        return round(sum(record["cost_usd"] for record in self.records), 6)

    @property
    def reserved_usd(self) -> float:
        """사용량을 모르는 시도의 최악 비용 합계. 과금됐을 수 있으므로 예산 검사에 포함한다."""
        return round(sum(item["worst_case_usd"] for item in self.unmeasured), 6)

    @property
    def committed_usd(self) -> float:
        return round(self.spent_usd + self.reserved_usd, 6)

    def worst_case_usd(self, estimated_input_tokens: int, max_output_tokens: int) -> float:
        return (estimated_input_tokens * self.prices["input"] + max_output_tokens * self.prices["output"]) / 1_000_000

    def check_before_call(self, estimated_input_tokens: int, max_output_tokens: int) -> float:
        if self.attempts >= self.max_calls:
            raise CallLimitError(f"호출 한도 {self.max_calls}회를 다 썼습니다.")
        worst = self.worst_case_usd(estimated_input_tokens, max_output_tokens)
        if self.committed_usd + worst > self.budget_usd:
            raise BudgetExceededError(
                f"누적 ${self.spent_usd:.4f} + 미측정 예약 ${self.reserved_usd:.4f} + 이번 최악 ${worst:.4f}"
                f" > 예산 ${self.budget_usd:.2f}")
        self.attempts += 1
        return worst

    def record_unmeasured(self, worst_case_usd: float, reason: str) -> None:
        """응답을 받지 못해 사용량을 모르는 시도. 최악 비용을 예약해 둔다 (실제 과금은 사용량 화면에서 확인)."""
        self.unmeasured.append({"worst_case_usd": round(worst_case_usd, 6), "reason": reason})

    def record_usage(self, usage: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
        usage = usage or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        reasoning = int((usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
        cost = ((input_tokens - cached) * self.prices["input"] + cached * self.prices["cached_input"]
                + output_tokens * self.prices["output"]) / 1_000_000
        record = {"input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": output_tokens,
                  "reasoning_tokens": reasoning, "cost_usd": round(cost, 6), "usage_reported": bool(usage), **extra}
        self.records.append(record)
        return record

    def summary(self) -> dict[str, Any]:
        return {"attempts": self.attempts, "calls_with_usage": len(self.records), "spent_usd": self.spent_usd,
                "unmeasured_attempts": len(self.unmeasured), "reserved_usd": self.reserved_usd,
                "committed_usd": self.committed_usd,
                "budget_usd": self.budget_usd, "max_calls": self.max_calls, "prices_per_1m": self.prices,
                "input_tokens": sum(r["input_tokens"] for r in self.records),
                "output_tokens": sum(r["output_tokens"] for r in self.records),
                "reasoning_tokens": sum(r["reasoning_tokens"] for r in self.records)}


def _openai_settings(api_key: str | None, model: str | None, base_url: str | None) -> tuple[str, str, str]:
    """키·모델·주소를 정한다. 키는 반환만 하고 어디에도 출력하지 않는다."""
    import os
    em.load_env_file()
    key = (api_key or os.environ.get(OPENAI_API_KEY_ENV, "")).strip()
    name = (model or os.environ.get(OPENAI_MODEL_ENV, "")).strip()
    if not key:
        raise ValueError(f"{OPENAI_API_KEY_ENV}가 설정되지 않았습니다.")
    if not name:
        raise ValueError(f"모델 이름이 필요합니다 (인자 또는 {OPENAI_MODEL_ENV}).")
    return key, name, (base_url or OPENAI_DEFAULT_BASE_URL).rstrip("/")


def _openai_error(response) -> OpenAIRequestError:
    """오류 응답에서 상태 코드와 오류 type/code만 꺼낸다 (본문 메시지 전체는 넣지 않는다)."""
    kind = None
    try:
        error = (response.json() or {}).get("error") or {}
        kind = error.get("code") or error.get("type")
    except Exception:   # noqa: BLE001 - 본문이 JSON이 아닐 수 있다
        pass
    return OpenAIRequestError(f"OpenAI HTTP {response.status_code} ({kind})", response.status_code, kind)


def check_openai_model_access(
    model: str | None = None, *, api_key: str | None = None, base_url: str | None = None,
    timeout: float = 30, http_get: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    """GET /models/{model} 로 이 키가 모델에 접근할 수 있는지 확인한다 (생성 호출 아님, 토큰 과금 없음)."""
    key, name, url = _openai_settings(api_key, model, base_url)
    response = http_get(f"{url}/models/{name}", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    if response.status_code != 200:
        error = _openai_error(response)
        return {"model": name, "accessible": False, "status": error.status, "kind": error.kind}
    data = response.json()
    return {"model": name, "accessible": True, "status": 200, "id": data.get("id"), "owned_by": data.get("owned_by")}


def estimate_input_tokens(prompt: str) -> int:
    """
    호출 전 입력 토큰 추정(예산 검사용 상한). 한국어는 보통 글자 수보다 토큰이 적으므로 "글자 수 + 지시 여유 200"을 상한으로 쓴다.
    실제 값은 응답 usage.input_tokens로 기록한다.
    """
    return len(prompt) + 200


def make_openai_section_proposer(
    model: str | None = None,
    *,
    tracker: CostTracker | None = None,
    budget_usd: float = 3.0,
    max_calls: int = 1,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 120,
    max_output_tokens: int = 4000,
    reasoning_effort: str | None = "low",
    max_retries: int = 1,
    retry_wait_seconds: float = 5.0,
    prices: dict[str, float] | None = None,
    http_post: Callable[..., Any] = requests.post,
    sleep: Callable[[float], None] = time.sleep,
    call_log: list[dict[str, Any]] | None = None,
) -> Callable[[str], str]:
    """
    OpenAI Responses API(POST /responses)로 섹션 제안을 받는 proposer. 인터페이스는 Ollama proposer와 같다(프롬프트 → JSON 문자열).
    - Structured Outputs: text.format = json_schema(strict) → {"sections": [{title, start_text, page}]}만 받는다.
    - 안전장치: 호출 전 예산·호출 수 검사(CostTracker), 타임아웃, max_output_tokens(추론 토큰 포함 상한),
      재시도는 429·5xx·연결 오류에만 최대 max_retries번 (재시도도 호출 수·예산에 포함), store=false.
    - 응답이 incomplete(출력 한도 도달 등)·refusal이면 실패로 올린다 → chunk_document_llm이 B3로 대체한다.
    - call_log에는 호출별 상태·토큰·비용·응답 id만 남긴다 (키·프롬프트·원문 없음).
    """
    key, name, url = _openai_settings(api_key, model, base_url)
    if tracker is None:
        price = prices or OPENAI_PRICES_PER_1M.get(name)
        if price is None:
            raise ValueError(f"{name}의 단가를 알 수 없어 비용을 계산할 수 없습니다 (prices= 로 지정).")
        tracker = CostTracker(price, budget_usd, max_calls)

    def propose(prompt: str) -> str:
        estimated = estimate_input_tokens(prompt)
        if estimated > OPENAI_LONG_CONTEXT_INPUT_TOKENS:
            raise BudgetExceededError("입력이 장문 단가 구간(272K 토큰) 이상으로 추정되어 호출하지 않음")
        body: dict[str, Any] = {
            "model": name,
            "input": [{"role": "user", "content": prompt}],
            "text": {"format": {"type": "json_schema", "name": "section_list", "schema": OPENAI_SECTION_SCHEMA,
                                "strict": True}},
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        if reasoning_effort is not None:
            body["reasoning"] = {"effort": reasoning_effort}
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            worst = tracker.check_before_call(estimated, max_output_tokens)
            started = time.perf_counter()
            entry: dict[str, Any] = {"model": name, "attempt": attempt, "estimated_input_tokens": estimated,
                                     "worst_case_usd": round(worst, 6)}
            try:
                response = http_post(f"{url}/responses", json=body, timeout=timeout,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            except (requests.ConnectionError, requests.Timeout) as error:
                tracker.record_unmeasured(worst, type(error).__name__)   # 과금됐을 수 있어 최악 비용을 예약
                entry.update({"status": None, "error": type(error).__name__, "usage_unknown": True,
                              "seconds": round(time.perf_counter() - started, 3)})
                if call_log is not None:
                    call_log.append(entry)
                last_error = OpenAIRequestError(f"OpenAI 연결 실패: {type(error).__name__}", None, "connection")
                if attempt < max_retries:
                    sleep(retry_wait_seconds * (attempt + 1))
                    continue
                raise last_error from error
            entry["seconds"] = round(time.perf_counter() - started, 3)
            entry["status"] = response.status_code
            if response.status_code != 200:
                error = _openai_error(response)
                entry["error"] = error.kind
                if call_log is not None:
                    call_log.append(entry)
                if response.status_code in RETRYABLE_STATUS and attempt < max_retries:
                    sleep(retry_wait_seconds * (attempt + 1))
                    last_error = error
                    continue
                raise error
            data = response.json()
            usage_record = tracker.record_usage(data.get("usage"), response_id=data.get("id"), model=data.get("model"))
            entry.update({"response_id": data.get("id"), "response_model": data.get("model"),
                          "response_status": data.get("status"), **{k: usage_record[k] for k in
                          ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "cost_usd")}})
            if call_log is not None:
                call_log.append(entry)
            if data.get("status") != "completed":
                reason = (data.get("incomplete_details") or {}).get("reason")
                raise OpenAIRequestError(f"응답 미완료: status={data.get('status')} reason={reason}", 200, "incomplete")
            texts, refusals = [], []
            for item in data.get("output") or []:
                if item.get("type") != "message":
                    continue
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        texts.append(part.get("text") or "")
                    elif part.get("type") == "refusal":
                        refusals.append(part.get("refusal") or "")
            if refusals:
                raise OpenAIRequestError("모델이 응답을 거절함 (refusal)", 200, "refusal")
            if not texts:
                raise OpenAIRequestError("응답에 output_text가 없음", 200, "empty_output")
            return "".join(texts)
        raise last_error or OpenAIRequestError("알 수 없는 실패")

    propose.tracker = tracker          # type: ignore[attr-defined]  호출 후 비용 요약 확인용
    propose.model = name               # type: ignore[attr-defined]
    return propose
