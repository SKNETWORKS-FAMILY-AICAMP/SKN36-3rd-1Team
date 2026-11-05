"""
구조 기반 청킹(B안) 모듈.

무엇을 하는가?
- 데이터 담당이 만든 문서 단위 정제본(data/processed/documents.jsonl, 246건)을
  "원문에 실제로 있는 제목·필드 경계"를 기준으로 다시 자른다.
- 기존 청크(A안, 750자 고정 + 80자 overlap)는 그대로 두고, 비교 실험용 새 청크를 만든다.
- 두 가지 버전을 제공한다.
    B1: 필드·제목 경계(API는 의미 그룹 단위)로 자른 결과           → chunk_documents(docs)
    B2: B1에서 100자 미만 청크를 "관련 있는 이웃"과만 합친 결과   → chunk_documents(docs, strategy="B2")

지켜야 할 원칙
1. 원문을 바꾸지 않는다. 모든 청크의 text는 원문 본문[char_start:char_end] 와 정확히 같다.
2. 원문에서 뺀 부분(PDF 페이지 표지, 출처 URL·최종수정일시 필드)은 "제외 구간"으로 따로 기록한다.
3. 페이지는 PDF 추출 단계가 실제 페이지 번호로 넣은 표지(--- [Page N] ---)에서만 가져온다.
4. 구조를 찾지 못한 문서는 길이 기반으로 자르고, 그 사유를 기록한다.

처리 흐름 (chunk_document 함수 기준)
    문서 1건
      → (1) 경계 찾기: 문서 유형별로 "구간(segment)" 목록과 "제외 구간" 목록을 만든다
      → (2) 길이 맞추기: 구간이 CHUNK_SIZE보다 길면 split_long_span()으로 더 자른다
      → (3) 청크 dict 만들기: ID·원문 위치·페이지·출처를 붙인다
      → 결과: (청크 리스트, 문서별 처리 기록)

강의 노트북과의 관계
- 강의(Advanced RAG, 1. Create VectorDB.ipynb 셀 32·34)처럼 "구간 시작 위치를 찾고 → 다음 시작 위치까지 자른다".
- 강의(셀 39)의 RecursiveCharacterTextSplitter처럼 "구분자 우선순위 + 최대 길이 + overlap"으로 긴 구간을 자른다.
  다만 langchain_text_splitters가 설치되어 있지 않고(패키지 설치 금지 단계),
  원문 위치(char_start/char_end)를 정확히 남겨야 해서 같은 원리를 표준 라이브러리로 직접 구현했다.
- 이 모듈의 함수들(split_long_span, _segments_from_starts 등)은 다음 단계 C안(LLM 섹션)에서도 재사용한다.
"""

import re
import statistics
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# 1. 설정값 (한 곳에서 관리)
# ---------------------------------------------------------------------------
# 청킹 버전. 청크 ID와 payload에 남겨서, 기존 557건(A안)과 섞이지 않게 한다.
CHUNKING_VERSION = "B1"

# A안(기존)의 창 크기·overlap과 같은 값으로 맞춘다.
# 이유: A/B 비교에서 "청크 크기"는 그대로 두고 "경계를 정하는 방법"만 바꾸기 위해서다.
CHUNK_SIZE = 750
CHUNK_OVERLAP = 80

# 구분자를 찾을 때, 청크가 너무 짧게 잘리지 않도록 창의 앞 1/3 안에서는 자르지 않는다.
MIN_CUT_RATIO = 3

# ---------------------------------------------------------------------------
# 2. 문서 구조를 알아보는 패턴
# ---------------------------------------------------------------------------
# API 문서: 줄 맨 앞의 [필드명]   예) [지원대상 및 자격요건]
API_FIELD_PATTERN = re.compile(r"^\[([^\]\n]{2,25})\]", re.MULTILINE)

# PDF 페이지 표지. collectors/preprocessing/normalize.py 의 extract_pdf_text()가
# 실제 PDF 페이지 번호로 넣는다. → 페이지 추적의 유일한 근거.
PAGE_MARKER_PATTERN = re.compile(r"^--- \[Page (\d+)\] ---[ \t]*$", re.MULTILINE)

# PDF 장(章) 제목: "1 공급개요", "2 입주자격 및 입주자 선정기준" (숫자 + 공백 + 한글로 시작하는 짧은 줄)
# DOC_LH_001에서 정확히 6개(1~6장)가 잡히는 것을 확인했다. → 상위 제목(level 1)
PDF_CHAPTER_PATTERN = re.compile(r"^[ \t]*\d{1,2}[ \t]+[가-힣].{0,40}$", re.MULTILINE)

# PDF 절 제목: ■(LH 공고문의 섹션 제목), ◆(LH 유의사항) → 하위 제목(level 2)
PDF_HEADING_PATTERN = re.compile(r"^[ \t]*[■◆].*$", re.MULTILINE)

# 제목 앞에 남은 조각이 이보다 짧으면(예: 인쇄 쪽번호 "- 4 -") 따로 청크로 만들지 않고 다음 구간 앞에 붙인다.
MIN_PIECE_CHARS = 30

# 번호 항목: "1. 신혼부부의 정의는…"  "0. (중복대출 금지) …"
# 숫자 1~2자리 + 점 + 공백. "2026.10.16" 같은 날짜는 공백이 없어서 걸리지 않는다.
NUMBERED_ITEM_PATTERN = re.compile(r"^[ \t]*(\d{1,2})\.\s.*$", re.MULTILINE)

# HTML 제목: □ (주택도시기금 안내문의 소제목)
HTML_BOX_HEADING_PATTERN = re.compile(r"^[ \t]*□.*$", re.MULTILINE)

# ---------------------------------------------------------------------------
# 3. API 필드 → 의미 그룹
# ---------------------------------------------------------------------------
# 이웃한 필드가 같은 그룹이면 한 구간으로 묶는다. (필드 하나하나는 평균 50자 정도로 너무 짧다)
# 표에 없는 필드(예: [우대형], [일반형])는 바로 앞 필드의 그룹을 이어받는다.
#   → [지원대상] 아래의 하위 제목이기 때문이다.
GROUP_BASIC = "기본정보"
GROUP_ELIGIBILITY = "지원대상·자격"
GROUP_BENEFIT = "지원내용"
GROUP_APPLY = "신청·서류"
GROUP_CONTACT = "문의·근거"
GROUP_NOTICE = "유의사항"
GROUP_UNKNOWN = "기타"

API_FIELD_GROUPS: dict[str, str] = {
    "서비스명": GROUP_BASIC,
    "정책명": GROUP_BASIC,
    "소관부처": GROUP_BASIC,
    "소관기관 / 서비스분야": GROUP_BASIC,
    "주관/운영기관": GROUP_BASIC,
    "정책분류": GROUP_BASIC,
    "생애주기 / 대상특성 / 관심주제": GROUP_BASIC,
    "제공유형 / 지원주기 / 신청방법": GROUP_BASIC,
    "사업개요": GROUP_BASIC,
    "서비스목적": GROUP_BASIC,
    "정책개요(설명)": GROUP_BASIC,
    "정책개요": GROUP_BASIC,
    "지원대상": GROUP_ELIGIBILITY,
    "지원대상 상세": GROUP_ELIGIBILITY,
    "선정기준": GROUP_ELIGIBILITY,
    "지원대상 및 자격요건": GROUP_ELIGIBILITY,
    "추가 신청자격조건": GROUP_ELIGIBILITY,
    "참여 제한대상": GROUP_ELIGIBILITY,
    "지원내용": GROUP_BENEFIT,
    "지원내용(급여서비스)": GROUP_BENEFIT,
    "신청기간 및 신청방법": GROUP_APPLY,
    "신청기간 및 방법": GROUP_APPLY,
    "신청 URL": GROUP_APPLY,
    "구비서류": GROUP_APPLY,
    "제출서류": GROUP_APPLY,
    "문의처": GROUP_CONTACT,
    "근거법령": GROUP_CONTACT,
    "기타 유의사항": GROUP_NOTICE,
}

# 청크로 만들지 않고 "제외 구간"으로 기록하는 필드.
# - [출처 URL]: payload의 source_url 필드에 이미 있다. (기존 청크 27건이 이 URL 꼬리만 남은 경우였다)
# - [최종수정일시]: 정책 내용이 아니라 수정 시각이다.
METADATA_FIELDS = frozenset({"출처 URL", "최종수정일시"})

# ---------------------------------------------------------------------------
# 3-1. B2 전용 설정: 짧은 청크 병합
# ---------------------------------------------------------------------------
# B2는 B1 결과를 받아, 너무 짧은 청크만 "관련 있는" 이웃 청크와 합친다. 경계는 여전히 원문의 필드·제목이다.
B2_VERSION = "B2"

# 이보다 짧은 청크를 병합 대상으로 본다. 100자로 정한 근거 (B1 1,300개 실측, 2026-11-05):
# - B1 길이 25% 분위가 약 90자, 100자 미만이 364개(28%)다.
# - 100자 미만의 대부분은 [문의처]+[근거법령] 묶음(168개 중 139개, 중앙값 60자)처럼 전화번호·법령명만 있는 부가 정보다.
# - 150자로 올리면 [지원내용] 245개 중 130개(중앙값 131자)처럼 그 자체로 완결된 섹션까지 병합 대상이 되어
#   구조 기반 분할의 의미가 약해진다.
# - 실험에서 바꿔 볼 수 있도록 merge_short_chunks()의 매개변수로도 받는다.
MIN_CHUNK_CHARS = 100

# 서로 다른 그룹끼리 병합을 허용하는 짝. 여기에 없는 조합은 병합하지 않는다. (같은 그룹끼리는 항상 허용)
# - 기본정보 + 지원내용 : "무슨 정책이고 무엇을 주는가"는 함께 읽히는 개요다.
# - 기본정보 + 지원대상·자격 : 정책명이 붙으면 "누가 대상인가" 조건의 문맥이 분명해진다. 조건 문장은 그대로다.
# - 신청·서류 + 문의·근거 / 유의사항 : 신청 방법·서류와 문의처·근거법령은 신청 단계의 부가 정보다.
# 일부러 허용하지 않은 조합:
# - 지원내용 + 신청·서류 : 혜택과 신청 절차는 다른 질문에 쓰인다. (승인 조건: 무조건 병합하지 않음)
# - 지원대상·자격 + (지원내용·신청·서류·문의·근거) : 자격·제외 조건 청크에 다른 주제가 섞이지 않게 한다.
MERGE_PARTNER_GROUPS = frozenset({
    frozenset({GROUP_BASIC, GROUP_BENEFIT}),
    frozenset({GROUP_BASIC, GROUP_ELIGIBILITY}),
    frozenset({GROUP_APPLY, GROUP_CONTACT}),
    frozenset({GROUP_APPLY, GROUP_NOTICE}),
    frozenset({GROUP_CONTACT, GROUP_NOTICE}),
})

# 자격·예외 조건 필드 (A/B 비교 측정용)
ELIGIBILITY_FIELDS = frozenset(
    {"지원대상", "지원대상 상세", "선정기준", "지원대상 및 자격요건", "추가 신청자격조건", "참여 제한대상"}
)

# 예외·제외 조건 문장을 찾는 패턴 (A/B 비교 측정용)
EXCEPTION_LINE_PATTERN = re.compile(r"제외|불가|제한|단,|다만|※")

# ---------------------------------------------------------------------------
# 4. 긴 구간을 자를 때 쓰는 구분자 (앞에 있을수록 우선)
# ---------------------------------------------------------------------------
# 강의 노트북 셀 39의 separators 목록(문단 → 줄 → 문장 → 단어 → 글자)과 같은 순서이고,
# 맨 앞에 "항목·글머리표가 시작되는 줄 앞"을 추가했다. 조건 목록의 항목 하나가 반으로 잘리는 것을 줄이기 위해서다.
# 각 패턴이 찾은 위치의 "끝"에서 자른다. 즉 다음 청크는 항목 기호/문장 시작부터 시작한다.
#
# 줄바꿈을 "\n" 하나로만 보지 않는 이유:
# 복지로·정부24 원문은 줄바꿈으로 "\r" 단독을 쓰는 곳이 많다(전체 문서에서 1,140개, 2026-11-05 측정).
# 원문은 그대로 두고, 자를 위치를 찾을 때만 "\r\n", "\r", "\n"을 모두 줄바꿈으로 인정한다.
LINE_BREAK = r"(?:\r\n|\r|\n)"
SEPARATOR_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("항목 시작 줄", re.compile(LINE_BREAK + r"(?=[ \t]*(?:\d{1,2}\.\s|[■□◆○●▶※ㅇ•\-]|\[))")),
    ("빈 줄(문단)", re.compile(LINE_BREAK + r"[ \t]*" + LINE_BREAK)),
    ("줄바꿈", re.compile(LINE_BREAK)),
    ("문장 끝", re.compile(r"[.?!](?=\s)")),
    ("공백", re.compile(r"[ \t]")),
]


# ---------------------------------------------------------------------------
# 5. 작은 도우미 함수
# ---------------------------------------------------------------------------
def _skip_space_forward(text: str, pos: int, end: int) -> int:
    """pos부터 앞으로 가며 공백을 건너뛴 위치. 청크가 공백으로 시작하지 않게 한다."""
    while pos < end and text[pos].isspace():
        pos += 1
    return pos


def _skip_space_backward(text: str, start: int, end: int) -> int:
    """end부터 뒤로 가며 공백을 건너뛴 위치. 청크가 공백으로 끝나지 않게 한다."""
    while end > start and text[end - 1].isspace():
        end -= 1
    return end


def make_chunk_id(document_id: str, seq: int, version: str = CHUNKING_VERSION) -> str:
    """새 청크 ID. 계약(data_contract.md)의 CHK_ 네임스페이스를 쓴다. 예) CHK_B1_DOC_BOK_001_C003"""
    return f"CHK_{version}_{document_id}_C{seq:03d}"


def make_point_id(chunk_id: str) -> str:
    """Qdrant Point ID. 기존 인계 규칙과 같은 UUIDv5(NAMESPACE_DNS, chunk_id)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))


# ---------------------------------------------------------------------------
# 6. 긴 구간 자르기 (강의의 RecursiveCharacterTextSplitter와 같은 원리)
# ---------------------------------------------------------------------------
def _find_cut(text: str, window_start: int, window_end: int) -> int:
    """
    [window_start, window_end] 안에서 자를 위치를 고른다.
    우선순위가 높은 구분자부터 찾아보고, 찾으면 그 구분자 중 "가장 뒤쪽"에서 자른다.
    (뒤쪽에서 자를수록 청크가 CHUNK_SIZE에 가깝게 꽉 찬다)
    아무 구분자도 없으면 window_end에서 그냥 자른다(최후의 수단: 글자 단위).
    """
    region = text[window_start:window_end]
    for _name, pattern in SEPARATOR_PATTERNS:
        matches = list(pattern.finditer(region))
        if matches:
            return window_start + matches[-1].end()
    return window_end


def _overlap_start(text: str, cut: int, overlap: int, lower: int) -> int:
    """
    다음 청크의 시작 위치. cut보다 overlap 글자만큼 앞에서 시작하되,
    단어 중간에서 시작하지 않도록 다음 공백 뒤로 옮긴다. lower보다 앞으로는 가지 않는다.
    """
    start = max(cut - overlap, lower)
    if start <= 0 or text[start - 1].isspace():
        return start
    next_space = start
    while next_space < cut and not text[next_space].isspace():
        next_space += 1
    return next_space if next_space < cut else cut


def split_long_span(
    text: str,
    start: int,
    end: int,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> list[tuple[int, int, int]]:
    """
    text[start:end] 구간을 chunk_size 이하 조각으로 자른다.

    입력: 원문 전체 text, 자를 구간의 시작·끝 위치, 최대 길이, overlap 길이
    반환: [(조각 시작, 조각 끝, 앞 조각과 겹친 글자 수), ...]
         - 위치는 모두 "원문 전체" 기준이다. 그래서 text[조각 시작:조각 끝] 이 곧 청크 본문이다.
         - 구간이 chunk_size 이하이면 조각 1개(공백만 앞뒤로 정리)를 돌려준다.
    주의: overlap은 같은 구간 안에서 잘린 조각끼리만 생긴다. 서로 다른 필드·제목 사이에는 생기지 않는다.
    """
    pieces: list[tuple[int, int, int]] = []
    pos = start
    previous_end = None

    while True:
        pos = _skip_space_forward(text, pos, end)
        if pos >= end:
            break

        if end - pos <= chunk_size:
            # 남은 부분이 한 청크에 들어가면 그대로 마지막 조각이 된다.
            piece_end = _skip_space_backward(text, pos, end)
            cut = end
        else:
            window_end = pos + chunk_size
            cut = _find_cut(text, pos + chunk_size // MIN_CUT_RATIO, window_end)
            piece_end = _skip_space_backward(text, pos, cut)

        overlap_chars = max(0, previous_end - pos) if previous_end is not None else 0
        pieces.append((pos, piece_end, overlap_chars))
        previous_end = piece_end

        if cut >= end:
            break
        # 다음 조각은 cut보다 약간 앞(overlap)에서 시작한다. pos+1 보다는 항상 뒤라서 무한 반복하지 않는다.
        pos = _overlap_start(text, cut, chunk_overlap, lower=pos + 1)

    return pieces


# ---------------------------------------------------------------------------
# 7. 문서 유형별 경계 찾기
# ---------------------------------------------------------------------------
# "구간(segment)" 하나는 dict로 표현한다.
#   {"start": int, "end": int, "title": str, "group": str | None, "page": int | None,
#    "field_spans": [(start, end), ...]  # API 그룹에서 필드 단위로 먼저 나눌 때만 사용
#   }
# "제외 구간"은 {"start": int, "end": int, "reason": str}


def _api_segments(text: str) -> tuple[list[dict], list[dict]]:
    """API 문서: [필드명] 경계로 나누고, 이웃한 같은 그룹 필드를 하나의 구간으로 묶는다."""
    fields = list(API_FIELD_PATTERN.finditer(text))
    segments: list[dict] = []
    excluded: list[dict] = []

    # 첫 필드보다 앞에 글이 있으면 버리지 않고 구간으로 남긴다. (강의 코드는 이 부분이 빠진다)
    if fields and fields[0].start() > 0 and text[: fields[0].start()].strip():
        segments.append({"start": 0, "end": fields[0].start(), "title": "(첫 필드 이전)",
                         "group": GROUP_UNKNOWN, "page": None, "field_spans": [(0, fields[0].start())]})

    current_group = GROUP_UNKNOWN
    for index, match in enumerate(fields):
        name = match.group(1)
        field_start = match.start()
        field_end = fields[index + 1].start() if index + 1 < len(fields) else len(text)

        if name in METADATA_FIELDS:
            excluded.append({"start": field_start, "end": field_end, "reason": f"metadata_field:{name}"})
            continue

        # 표에 없는 필드는 앞 필드 그룹을 이어받는다.
        current_group = API_FIELD_GROUPS.get(name, current_group)

        # 바로 앞 구간과 그룹이 같고, 사이에 제외 구간이 끼어 있지 않으면 이어 붙인다.
        previous = segments[-1] if segments else None
        if previous and previous["group"] == current_group and previous["end"] == field_start:
            previous["end"] = field_end
            previous["field_spans"].append((field_start, field_end))
            previous["title"] = f"{previous['title']} + [{name}]"
        else:
            segments.append({"start": field_start, "end": field_end, "title": f"[{name}]",
                             "group": current_group, "page": None,
                             "field_spans": [(field_start, field_end)]})
    return segments, excluded


# 제목 시작 위치는 (위치, 제목 글자, 수준) 으로 표현한다. 수준 1 = 장(상위), 2 = 절·항목(하위)


def _heading_starts_pdf(text: str) -> list[tuple[int, str, int]]:
    """
    PDF 제목 위치.
    - 장 제목("1 공급개요")은 수준 1, ■·◆ 줄은 수준 2.
    - 둘 다 없으면 번호 항목(FAQ의 '1. 질문')을 수준 2 제목으로 본다.
    """
    chapters = [(m.start(), m.group(0).strip()[:60], 1) for m in PDF_CHAPTER_PATTERN.finditer(text)]
    headings = [(m.start(), m.group(0).strip()[:60], 2) for m in PDF_HEADING_PATTERN.finditer(text)]
    if chapters or headings:
        return sorted(chapters + headings)
    return [(m.start(), m.group(0).strip()[:60], 2) for m in NUMBERED_ITEM_PATTERN.finditer(text)]


def _heading_starts_html(text: str) -> list[tuple[int, str, int]]:
    """
    HTML 제목 위치: □ 줄 + "번호 목록이 새로 시작되는 줄".
    번호 목록(예: 0. (중복대출 금지) … 8. (신혼가구))은 대출 자격 조건들이 이어진 하나의 목록이므로
    항목마다 자르지 않고, 번호가 이어지지 않는 지점(목록이 새로 시작되는 곳)에서만 자른다.
    예) "8." 다음에 "1. 임차 전용면적"이 나오면 새 목록 → 새 구간.
    """
    starts = [(m.start(), m.group(0).strip()[:60], 2) for m in HTML_BOX_HEADING_PATTERN.finditer(text)]
    previous_number = None
    for match in NUMBERED_ITEM_PATTERN.finditer(text):
        number = int(match.group(1))
        if previous_number is None or number != previous_number + 1:
            starts.append((match.start(), match.group(0).strip()[:60], 2))
        previous_number = number
    return sorted(starts)


def _segments_from_starts(
    text: str, starts: list[tuple[int, str, int]], page_markers: list[tuple[int, int, int]]
) -> tuple[list[dict], list[dict]]:
    """
    제목 시작 위치(starts)와 페이지 표지로 구간을 만든다. (강의 셀 34의 '다음 시작 위치까지 자르기'와 같은 원리)

    - 페이지 표지 줄은 제외 구간으로 기록하고, 표지에서도 반드시 자른다.
      → 한 청크는 항상 한 페이지 안에 있으므로 페이지 번호가 정확해진다.
    - 페이지가 바뀌어 제목 없이 이어지는 부분은 앞 제목을 이어받고 "(이어짐)"을 붙인다.
    - 구간 제목은 "장 제목 > 절 제목" 경로로 만든다. 예) "2 입주자격 및 입주자 선정기준 > ■ 입주자격"
    - 제목 앞에 남은 MIN_PIECE_CHARS 미만 조각(예: 쪽번호 "- 4 -")은 다음 구간 앞에 붙인다. 원문은 버리지 않는다.
    """
    excluded = [{"start": s, "end": e, "reason": "page_marker"} for s, e, _page in page_markers]

    # 원문을 "페이지 표지를 뺀 연속 영역"들로 나눈다. 각 영역은 한 페이지(또는 페이지 없음)다.
    regions = []
    cursor, current_page = 0, None
    for marker_start, marker_end, page in page_markers:
        if marker_start > cursor:
            regions.append((cursor, marker_start, current_page))
        cursor, current_page = marker_end, page
    regions.append((cursor, len(text), current_page))

    segments: list[dict] = []
    first_title = "(첫 제목 이전)"
    current_title = first_title
    current_chapter = None
    for region_start, region_end, page in regions:
        if not text[region_start:region_end].strip():
            continue

        # 이 영역(페이지) 안의 제목들로 자를 위치를 정한다. 영역 시작이 제목이 아니면 "이어지는 조각"부터 시작.
        inside = [start for start in starts if region_start <= start[0] < region_end]
        cut_points: list[tuple[int, str | None, int]] = [(region_start, None, 0)]
        for position, title, level in inside:
            if position == region_start:
                cut_points[0] = (position, title, level)
            else:
                cut_points.append((position, title, level))

        region_pieces: list[dict] = []
        for index, (piece_start, title, level) in enumerate(cut_points):
            piece_end = cut_points[index + 1][0] if index + 1 < len(cut_points) else region_end
            if title is None:
                # 제목 없이 시작하는 조각 = 앞 페이지에서 이어지는 내용
                piece_title = current_title if current_title == first_title else f"{current_title} (이어짐)"
            else:
                if level == 1:
                    current_chapter = title
                    current_title = title
                else:
                    current_title = f"{current_chapter} > {title}" if current_chapter else title
                piece_title = current_title
            region_pieces.append({"start": piece_start, "end": piece_end, "title": piece_title,
                                  "group": None, "page": page})

        # 짧은 조각은 같은 영역의 다음 조각 앞에 붙인다. (마지막 조각이면 그대로 둔다)
        for index, piece in enumerate(region_pieces):
            is_short = len(text[piece["start"]:piece["end"]].strip()) < MIN_PIECE_CHARS
            if is_short and index + 1 < len(region_pieces):
                region_pieces[index + 1]["start"] = piece["start"]
                piece["end"] = piece["start"]  # 빈 조각으로 만들어 아래에서 버린다.

        segments.extend(piece for piece in region_pieces if text[piece["start"]:piece["end"]].strip())
    return segments, excluded


def find_page_markers(text: str) -> list[tuple[int, int, int]]:
    """페이지 표지 줄의 (시작, 끝, 페이지 번호). 끝은 줄바꿈까지 포함한다."""
    markers = []
    for match in PAGE_MARKER_PATTERN.finditer(text):
        end = match.end()
        if end < len(text) and text[end] == "\n":
            end += 1
        markers.append((match.start(), end, int(match.group(1))))
    return markers


# ---------------------------------------------------------------------------
# 8. 문서 1건 청킹 (B안의 진입점)
# ---------------------------------------------------------------------------
def chunk_document(document: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    문서 1건을 구조 기반으로 청킹한다.

    입력: documents.jsonl의 문서 dict (document_id, policy_id, source_type, source_url, text 필요)
    반환: (청크 리스트, 처리 기록)
      청크 dict 필드: chunk_id, point_id, chunking_version, document_id, policy_id, source_type,
                     source_url, section_title, section_group, boundary_method, char_start, char_end,
                     text, page_start, page_end, overlap_prev_chars, chunk_seq
      처리 기록 필드: document_id, source_type, boundary_method, fallback_reason,
                     chunk_count, excluded_spans
    """
    text = document["text"]
    source_type = document["source_type"]
    page_markers = find_page_markers(text) if source_type == "pdf" else []
    fallback_reason = None

    # (1) 경계 찾기
    if source_type == "api" and API_FIELD_PATTERN.search(text):
        boundary_method = "api_field"
        segments, excluded = _api_segments(text)
    else:
        starts = _heading_starts_pdf(text) if source_type == "pdf" else _heading_starts_html(text)
        if starts:
            boundary_method = "pdf_heading" if source_type == "pdf" else "html_heading"
        else:
            boundary_method = "length_fallback"
            fallback_reason = "명시적 제목·필드를 찾지 못함 → 길이 기반 분할"
        segments, excluded = _segments_from_starts(text, starts, page_markers)
        if boundary_method == "length_fallback":
            # 제목이 없으므로 구간 제목 자리에 문서 제목을 쓴다.
            for segment in segments:
                segment["title"] = document.get("title", "(제목 없음)")

    # (2) 길이 맞추기 + (3) 청크 만들기
    chunks: list[dict[str, Any]] = []
    for segment in segments:
        # API 그룹은 먼저 "필드 단위"로 최대한 묶고, 그래도 긴 필드만 문장 단위로 자른다.
        # 다른 문서는 구간 전체를 split_long_span에 맡긴다.
        if segment.get("field_spans"):
            ranges = _pack_field_spans(text, segment["field_spans"])
        else:
            ranges = [(segment["start"], segment["end"])]

        for range_start, range_end in ranges:
            for piece_start, piece_end, overlap_chars in split_long_span(text, range_start, range_end):
                chunks.append(build_chunk(
                    document, seq=len(chunks) + 1, version=CHUNKING_VERSION,
                    char_start=piece_start, char_end=piece_end,
                    section_title=segment["title"], section_group=segment["group"],
                    boundary_method=boundary_method,
                    page_start=segment["page"], page_end=segment["page"],
                    overlap_prev_chars=overlap_chars,
                ))

    report = {
        "document_id": document["document_id"],
        "source_type": source_type,
        "boundary_method": boundary_method,
        "fallback_reason": fallback_reason,
        "chunk_count": len(chunks),
        "excluded_spans": excluded,
    }
    return chunks, report


def build_chunk(
    document: dict[str, Any],
    *,
    seq: int,
    version: str,
    char_start: int,
    char_end: int,
    section_title: str,
    section_group: str | None,
    boundary_method: str,
    page_start: int | None,
    page_end: int | None,
    overlap_prev_chars: int,
) -> dict[str, Any]:
    """
    청크 dict 하나를 만든다. B1·B2(그리고 다음 단계 C안)가 모두 이 함수로 청크를 만든다.
    text는 항상 원문[char_start:char_end] 를 그대로 잘라 쓴다. → 원문이 바뀌거나 요약되지 않는다.
    """
    chunk_id = make_chunk_id(document["document_id"], seq, version)
    return {
        "chunk_id": chunk_id,
        "point_id": make_point_id(chunk_id),
        "chunking_version": version,
        "document_id": document["document_id"],
        "policy_id": document["policy_id"],
        "source_type": document["source_type"],
        "source_url": document["source_url"],
        "section_title": section_title,
        "section_group": section_group,
        "boundary_method": boundary_method,
        "char_start": char_start,
        "char_end": char_end,
        "text": document["text"][char_start:char_end],
        "page_start": page_start,
        "page_end": page_end,
        "overlap_prev_chars": overlap_prev_chars,
        "chunk_seq": seq,
    }


# ---------------------------------------------------------------------------
# 8-1. B2: 짧은 청크 병합
# ---------------------------------------------------------------------------
def _title_root(title: str) -> str:
    """PDF·HTML 제목의 상위 부분. "2 입주자격 > ■ 입주순위 (이어짐)" → "2 입주자격" """
    return title.split(" > ")[0].replace(" (이어짐)", "")


def _can_merge(text: str, left: dict[str, Any], right: dict[str, Any], max_chars: int) -> bool:
    """
    원문에서 바로 이웃한 두 조각(left가 앞, right가 뒤)을 하나로 합쳐도 되는지 판단한다.
    1) 붙어 있어야 한다: 사이에 공백만 있거나 overlap으로 겹쳐 있음. (제외 구간이 끼어 있으면 불가)
    2) 합친 길이가 max_chars 이하
    3) 같은 페이지 (PDF 페이지 번호를 정확히 유지하기 위해)
    4) 주제가 관련 있어야 한다: API는 같은 그룹 또는 MERGE_PARTNER_GROUPS, PDF·HTML은 같은 상위 제목
    """
    between = text[left["end"]:right["start"]] if right["start"] > left["end"] else ""
    if between.strip():
        return False
    if max(left["end"], right["end"]) - left["start"] > max_chars:
        return False
    if left["page"] != right["page"]:
        return False
    if left["groups"] and right["groups"]:
        return all(
            a == b or frozenset({a, b}) in MERGE_PARTNER_GROUPS
            for a in left["groups"] for b in right["groups"]
        )
    return _title_root(left["titles"][-1]) == _title_root(right["titles"][0])


def merge_short_chunks(
    document: dict[str, Any],
    chunks: list[dict[str, Any]],
    min_chars: int = MIN_CHUNK_CHARS,
    max_chars: int = CHUNK_SIZE,
) -> list[dict[str, Any]]:
    """
    B1 청크(한 문서분)를 받아 min_chars보다 짧은 청크를 관련 있는 이웃과 합친 B2 청크를 돌려준다.

    입력: 문서 dict, 그 문서의 B1 청크 리스트(순서대로), 최소 길이, 최대 길이
    반환: B2 청크 리스트. 각 청크에 merged_from(합쳐진 B1 chunk_id 목록)과 section_groups가 추가된다.

    병합 순서
    - 짧은 청크마다 이웃 후보(앞·뒤)를 본다. 같은 그룹 이웃을 먼저, 그다음 허용된 짝 그룹을 고르고,
      둘 다 가능하면 합친 길이가 더 짧아지는 쪽을 고른다. (한쪽으로 몰려 커지는 것을 줄이기 위해)
    - 합친 뒤에도 여전히 짧으면 다시 시도한다. 더 이상 합칠 수 없으면 짧은 채로 남긴다.
    """
    text = document["text"]
    units = [
        {
            "start": c["char_start"], "end": c["char_end"], "page": c["page_start"],
            "titles": [c["section_title"]], "groups": [c["section_group"]] if c["section_group"] else [],
            "method": c["boundary_method"], "merged_from": [c["chunk_id"]],
        }
        for c in chunks
    ]

    def length(unit: dict) -> int:
        return unit["end"] - unit["start"]

    def same_group(left: dict, right: dict) -> bool:
        return bool(left["groups"]) and set(left["groups"]) == set(right["groups"])

    changed = True
    while changed:
        changed = False
        for index, unit in enumerate(units):
            if length(unit) >= min_chars:
                continue
            candidates = []
            if index > 0 and _can_merge(text, units[index - 1], unit, max_chars):
                candidates.append(index - 1)
            if index + 1 < len(units) and _can_merge(text, unit, units[index + 1], max_chars):
                candidates.append(index + 1)
            if not candidates:
                continue
            # 같은 그룹 우선 → 합친 길이가 짧은 쪽 우선 → 앞쪽 우선
            candidates.sort(key=lambda j: (not same_group(units[j], unit), length(units[j]), j))
            neighbor_index = candidates[0]
            left_index, right_index = sorted((index, neighbor_index))
            left, right = units[left_index], units[right_index]
            merged = {
                "start": left["start"],
                "end": max(left["end"], right["end"]),
                "page": left["page"],
                "titles": left["titles"] + [t for t in right["titles"] if t not in left["titles"]],
                "groups": left["groups"] + [g for g in right["groups"] if g not in left["groups"]],
                "method": left["method"],
                "merged_from": left["merged_from"] + right["merged_from"],
            }
            units[left_index:right_index + 1] = [merged]
            changed = True
            break

    merged_chunks = []
    previous_end = None
    for unit in units:
        overlap = max(0, previous_end - unit["start"]) if previous_end is not None else 0
        chunk = build_chunk(
            document, seq=len(merged_chunks) + 1, version=B2_VERSION,
            char_start=unit["start"], char_end=unit["end"],
            section_title=" + ".join(unit["titles"]),
            section_group="+".join(unit["groups"]) if unit["groups"] else None,
            boundary_method=unit["method"],
            page_start=unit["page"], page_end=unit["page"],
            overlap_prev_chars=overlap,
        )
        chunk["section_groups"] = list(unit["groups"])
        chunk["merged_from"] = list(unit["merged_from"])
        merged_chunks.append(chunk)
        previous_end = unit["end"]
    return merged_chunks


def get_excluded_metadata(document: dict[str, Any], report: dict[str, Any]) -> dict[str, list[str]]:
    """
    청크 본문에서 뺀 메타 필드의 실제 값을 원문 위치로 되찾는다. 예) {"출처 URL": ["https://..."]}
    출처 URL·최종수정일시를 검색 본문에서 빼더라도, document_id + 이 함수로 언제든 추적할 수 있다.
    (수집 시각 collected_at·retrieved_at은 documents.jsonl의 문서 필드에 그대로 있다.)
    """
    values: dict[str, list[str]] = {}
    for span in report["excluded_spans"]:
        if not span["reason"].startswith("metadata_field:"):
            continue
        field_name = span["reason"].split(":", 1)[1]
        raw = document["text"][span["start"]:span["end"]]
        values.setdefault(field_name, []).append(raw.replace(f"[{field_name}]", "", 1).strip())
    return values


def _pack_field_spans(text: str, field_spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """
    이웃한 필드들을 CHUNK_SIZE를 넘지 않는 한 하나로 묶는다. 필드 경계에서만 나눈다.
    (필드 하나가 CHUNK_SIZE보다 길면 그 필드 혼자 한 범위가 되고, 이후 split_long_span이 자른다.)
    """
    packed: list[tuple[int, int]] = []
    for field_start, field_end in field_spans:
        if packed:
            group_start, _ = packed[-1]
            trimmed_end = _skip_space_backward(text, group_start, field_end)
            if trimmed_end - group_start <= CHUNK_SIZE:
                packed[-1] = (group_start, field_end)
                continue
        packed.append((field_start, field_end))
    return packed


def chunk_documents(
    documents: list[dict[str, Any]], strategy: str = "B1"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    문서 여러 건을 청킹한다. 반환: (전체 청크, 문서별 처리 기록)

    strategy="B1"(기본값): 기존 B1 결과 그대로
    strategy="B2": B1 결과에 merge_short_chunks()를 적용. 처리 기록에 chunking_version·b1_chunk_count가 추가된다.
    """
    if strategy not in ("B1", "B2"):
        raise ValueError(f"알 수 없는 청킹 전략: {strategy} (B1 또는 B2)")

    all_chunks, reports = [], []
    for document in documents:
        chunks, report = chunk_document(document)
        if strategy == "B2":
            b1_count = len(chunks)
            chunks = merge_short_chunks(document, chunks)
            report = {**report, "chunking_version": B2_VERSION, "b1_chunk_count": b1_count,
                      "chunk_count": len(chunks)}
        all_chunks.extend(chunks)
        reports.append(report)
    return all_chunks, reports


# ---------------------------------------------------------------------------
# 9. 검증 함수 (테스트와 비교 실험에서 사용)
# ---------------------------------------------------------------------------
def verify_coverage(
    document: dict[str, Any], chunks: list[dict[str, Any]], excluded_spans: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    한 문서에 대해 "원문 누락"과 "중복"을 센다.

    - missing_chars: 어떤 청크에도, 어떤 제외 구간에도 속하지 않는 글자 수 (공백 문자는 세지 않음) → 0이어야 정상
    - intentional_overlap_chars: split_long_span이 일부러 겹치게 만든 글자 수 (overlap_prev_chars 합)
    - unintended_overlap_chars: 그 밖의 이유로 두 번 이상 들어간 글자 수 → 0이어야 정상
    - excluded_in_chunks: 제외 구간 글자가 청크에 들어간 수 → 0이어야 정상
    """
    text = document["text"]
    cover_count = [0] * len(text)
    for chunk in chunks:
        for index in range(chunk["char_start"], chunk["char_end"]):
            cover_count[index] += 1

    excluded_mask = [False] * len(text)
    for span in excluded_spans:
        for index in range(span["start"], span["end"]):
            excluded_mask[index] = True

    missing_positions = [
        index for index, char in enumerate(text)
        if cover_count[index] == 0 and not excluded_mask[index] and not char.isspace()
    ]
    total_overlap = sum(count - 1 for count in cover_count if count > 1)
    intentional_overlap = sum(chunk["overlap_prev_chars"] for chunk in chunks)
    excluded_in_chunks = sum(1 for index in range(len(text)) if excluded_mask[index] and cover_count[index] > 0)

    return {
        "document_id": document["document_id"],
        "missing_chars": len(missing_positions),
        "missing_sample": text[missing_positions[0]:missing_positions[0] + 40] if missing_positions else "",
        "intentional_overlap_chars": intentional_overlap,
        "unintended_overlap_chars": total_overlap - intentional_overlap,
        "excluded_in_chunks": excluded_in_chunks,
    }


def locate_chunks_in_document(document_text: str, chunk_texts: list[str]) -> list[tuple[int, int] | None]:
    """
    원문 위치가 없는 청크(A안 기존 557건)의 위치를 원문에서 찾는다. 순서대로 앞에서부터 찾는다.
    찾지 못하면 None. (기존 청크는 모두 원문의 부분 문자열이므로 정상이라면 None이 없다)
    """
    positions: list[tuple[int, int] | None] = []
    search_from = 0
    for chunk_text in chunk_texts:
        found = document_text.find(chunk_text, search_from)
        if found == -1:
            found = document_text.find(chunk_text)
        if found == -1:
            positions.append(None)
            continue
        positions.append((found, found + len(chunk_text)))
        search_from = found + 1
    return positions


def find_api_field_spans(text: str, field_names: frozenset[str]) -> list[tuple[int, int]]:
    """API 문서에서 지정한 필드들의 (시작, 끝) 위치. 끝의 공백은 뺀다."""
    fields = list(API_FIELD_PATTERN.finditer(text))
    spans = []
    for index, match in enumerate(fields):
        if match.group(1) not in field_names:
            continue
        end = fields[index + 1].start() if index + 1 < len(fields) else len(text)
        spans.append((match.start(), _skip_space_backward(text, match.start(), end)))
    return spans


def find_line_spans(text: str, pattern: re.Pattern) -> list[tuple[int, int]]:
    """
    pattern이 들어 있는 줄들의 (시작, 끝) 위치. 줄 앞뒤 공백은 뺀다.
    줄은 "\r\n", "\r", "\n" 어느 것으로 끝나도 한 줄로 본다. (SEPARATOR_PATTERNS와 같은 기준)
    """
    spans = []
    for line in re.finditer(r"[^\r\n]+", text):
        if pattern.search(line.group(0)):
            start = _skip_space_forward(text, line.start(), line.end())
            end = _skip_space_backward(text, start, line.end())
            if end > start:
                spans.append((start, end))
    return spans


def count_preserved_spans(spans: list[tuple[int, int]], chunk_ranges: list[tuple[int, int]]) -> int:
    """spans 중에서 "어느 한 청크 안에 통째로 들어간" 것의 개수."""
    return sum(
        1 for span_start, span_end in spans
        if any(chunk_start <= span_start and span_end <= chunk_end for chunk_start, chunk_end in chunk_ranges)
    )


def length_summary(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """청크 길이 분포 요약 (비교표용)."""
    lengths = [len(chunk["text"]) for chunk in chunks]
    return {
        "count": len(lengths),
        "min": min(lengths),
        "median": statistics.median(lengths),
        "mean": round(statistics.mean(lengths), 1),
        "max": max(lengths),
        "under_100": sum(1 for length in lengths if length < 100),
        "over_chunk_size": sum(1 for length in lengths if length > CHUNK_SIZE),
    }


# ---------------------------------------------------------------------------
# 10. 문서 구조 인식 (청킹 "전에" 실행): analyze_structure
# ---------------------------------------------------------------------------
# 목적
# - 청크를 만들기 전에, 문서에 어떤 제목·섹션 구조가 있는지 찾고 그 결과를 믿을 수 있는지 판정한다.
# - 판정 결과로 "규칙 기반 청킹이면 충분한지 / 일부 구간만 LLM이 필요한지 / 문서 전체에 LLM이 필요한지"를 정한다.
#
# B1/B2와의 관계
# - B1/B2 청킹 함수(chunk_document, merge_short_chunks, chunk_documents)는 이 절의 함수를 쓰지 않는다.
#   → B1/B2 결과는 바뀌지 않는다. (경계 → 구간 단계는 기존 _api_segments, _segments_from_starts를 재사용한다)
# - 이 단계에서는 LLM을 호출하지 않는다. 반환값의 headings / sections / llm_candidate_regions 는
#   다음 단계(C안 LLM 섹션)의 입력으로 그대로 쓴다.
#
# 처리 흐름 (텍스트 패턴 문서 기준)
#   (1) 줄마다 "제목 후보 규칙"(HEADING_RULES)을 적용해 후보를 찾는다.
#   (2) 줄 단위 검사: 주의 문구(※), 빈 제목, 너무 긴 제목, 문장형, 본문이 붙은 대괄호, 목차 점선 등은 제외
#   (3) 순서 검사: 본문 없이 바로 다음 후보가 이어지는 줄(번호 목록·목차·메뉴)은 제외
#   (4) 남은 제목으로 섹션을 만들고, 원문 누락·중복을 검사한다.
#   (5) 상태 판정: structured / partial / unstructured  (새 숫자 임계값 없이 논리 조건과 기존 설정값만 사용)

STRUCTURE_RULE_SET_VERSION = "S1"   # 규칙·사전이 바뀌면 올린다. (재청킹 여부 판단 키에 들어갈 값)

STATUS_STRUCTURED = "structured"
STATUS_PARTIAL = "partial"
STATUS_UNSTRUCTURED = "unstructured"

# 제목 한 줄의 최대 글자 수(기호·번호를 뗀 뒤).
# 새로 정한 값이 아니라 기존 PDF_CHAPTER_PATTERN(장 제목 뒤 40자)과 같은 값이다. (API 필드명은 25자 이내)
HEADING_MAX_CHARS = 40

# 주의·참고 문구: 제목처럼 줄 맨 앞에 오지만 섹션 제목이 아니다.
NOTE_LINE_PATTERN = re.compile(r"^(?:※|\*|☞|주\)|참고\))")

# 인쇄 쪽번호 줄 "- 4 -" : 제목도 본문도 아닌 줄로 본다.
PAGE_NUMBER_LINE_PATTERN = re.compile(r"^-\s*\d{1,3}\s*-$")

# 목차 줄: "지원대상 ........ 3" 처럼 점선·말줄임 뒤에 쪽번호가 붙는다.
TOC_LEADER_PATTERN = re.compile(r"(?:\.{3,}|·{3,}|…+|-{3,})\s*\d{1,3}$")

# 문장으로 끝나는 줄은 제목이 아니라 본문 항목이다.  예) "1. 신청은 불가합니다."
SENTENCE_END_PATTERN = re.compile(r"(?:니다|습니다)\s*\.?$|다\.$|[.。]$")

# 웹 화면의 버튼·링크 이름. 예) "[구직신청 바로가기]"
UI_LABEL_WORDS = ("바로가기", "다운로드")


def _normalize_title(title: str) -> str:
    """제목 비교용 정규화: 공백·가운뎃점·괄호·콜론을 지운다. 예) '지원대상 · 자격' → '지원대상자격'"""
    return re.sub(r"[\s·ㆍ・/:：()（）\[\]<>〈〉《》【】]", "", title)


# 섹션 이름 사전 (정규화된 제목 → 의미 그룹).
# - API 필드명(API_FIELD_GROUPS)을 그대로 쓰고,
# - 기호 없이 제목만 한 줄로 있는 HTML 화면(주택도시기금·고용24)에서 실제로 확인한 섹션 이름을 더했다. (2026-11-05)
# 정책 내용이 아니라 "제목으로 쓰이는 낱말" 목록이다. 새 동의어는 여기에 한 줄 추가하면 된다(코드 수정 불필요).
_EXTRA_SECTION_TERMS: dict[str, str] = {
    "주요내용": GROUP_BASIC,
    "대출대상": GROUP_ELIGIBILITY,
    "지원자격": GROUP_ELIGIBILITY,
    "신청자격": GROUP_ELIGIBILITY,
    "자격요건": GROUP_ELIGIBILITY,
    "대상주택": GROUP_ELIGIBILITY,
    "대출금리": GROUP_BENEFIT,
    "대출한도": GROUP_BENEFIT,
    "대출기간": GROUP_BENEFIT,
    "지원금액": GROUP_BENEFIT,
    "신청방법": GROUP_APPLY,
    "신청기간": GROUP_APPLY,
    "신청시기": GROUP_APPLY,
    "대출신청": GROUP_APPLY,
    "이용절차": GROUP_APPLY,
    "지원절차": GROUP_APPLY,
    "준비서류": GROUP_APPLY,
    "상담안내": GROUP_CONTACT,
}
SECTION_VOCABULARY: dict[str, str] = {
    _normalize_title(term): group for term, group in {**API_FIELD_GROUPS, **_EXTRA_SECTION_TERMS}.items()
}

# 제목 후보 규칙. 줄(앞뒤 공백 제거) 하나에 위에서부터 차례로 맞춰 보고, 처음 맞는 규칙 하나만 쓴다.
# - level: 1 = 장(가장 상위), 2 = 절, 3 = 항목. 숫자가 작을수록 상위 제목이다.
# - 정규식의 (?P<title>…) 부분이 기호·번호를 뗀 제목이고, (?P<num>…)은 번호, (?P<rest>…)는 괄호 뒤에 붙은 글이다.
# 새 기호·번호 체계가 나오면 이 목록에 규칙을 추가한다(설정 변경).
HEADING_RULES: list[dict[str, Any]] = [
    # [지원대상]  — 대괄호 단독 줄
    {"name": "bracket_square", "level": 2,
     "pattern": re.compile(r"^\[(?P<title>[^\]]{1,40})\](?P<rest>.*)$")},
    # <지원대상>  〈 〉 《 》 【 】
    {"name": "bracket_angle", "level": 2,
     "pattern": re.compile(r"^[<〈《【](?P<title>[^>〉》】]{1,40})[>〉》】](?P<rest>.*)$")},
    # Ⅰ. 개요  /  제1장 총칙
    {"name": "chapter_roman", "level": 1,
     "pattern": re.compile(r"^(?P<num>[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])[.\s]\s*(?P<title>.*)$")},
    {"name": "chapter_jang", "level": 1,
     "pattern": re.compile(r"^제\s*(?P<num>\d{1,2})\s*[장편]\s*(?P<title>.*)$")},
    # 1 공급개요  — 번호 + 공백 + 한글 (점 없음). LH 공고문의 장 제목 형식
    {"name": "chapter_line", "level": 1,
     "pattern": re.compile(r"^(?P<num>\d{1,2})[ \t]+(?P<title>[가-힣].*)$")},
    # ■ 지원대상  ◆ □ ▣ ◈
    {"name": "symbol_strong", "level": 2,
     "pattern": re.compile(r"^[■◆□▣◈]\s*(?P<title>.*)$")},
    # ○ ● ◎ ◇ ▶ ▷ ►  — 하위 항목에 자주 쓰여 수준을 한 단계 낮게 본다.
    {"name": "symbol_weak", "level": 3,
     "pattern": re.compile(r"^[○●◎◇▶▷►]\s*(?P<title>.*)$")},
    # 1. 지원대상  /  1) 지원대상   — 점·괄호 뒤가 공백이거나 줄 끝이어야 한다. ("3.45억원" 같은 숫자는 제외)
    {"name": "numbered", "level": 3,
     "pattern": re.compile(r"^(?P<num>\d{1,2})[.)](?:\s+|$)(?P<title>.*)$")},
    # (1) 지원대상
    {"name": "numbered_paren", "level": 3,
     "pattern": re.compile(r"^\((?P<num>\d{1,2})\)\s*(?P<title>.*)$")},
    # 가. 지원대상
    {"name": "numbered_hangul", "level": 3,
     "pattern": re.compile(r"^(?P<num>[가나다라마바사아자차카타파하])[.)]\s+(?P<title>.*)$")},
    # ① 지원대상
    {"name": "numbered_circled", "level": 3,
     "pattern": re.compile(r"^(?P<num>[①-⑳])\s*(?P<title>.*)$")},
]
# 기호 없이 제목만 있는 줄(예: "지원자격")은 SECTION_VOCABULARY에 있을 때만 후보로 본다.
PLAIN_VOCABULARY_RULE = {"name": "plain_vocabulary", "level": 2}

_HANGUL_ORDER = "가나다라마바사아자차카타파하"
_ROMAN_ORDER = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"


def _item_number(token: str | None) -> int | None:
    """번호 표기를 정수로 바꾼다. '3'→3, '다'→3, '③'→3, 'Ⅲ'→3. 번호가 없으면 None."""
    if not token:
        return None
    if "①" <= token <= "⑳":            # 주의: '①'.isdigit()은 True지만 int('①')는 오류가 난다. 먼저 검사한다.
        return ord(token) - ord("①") + 1
    if token.isdecimal():
        return int(token)
    if token in _HANGUL_ORDER:
        return _HANGUL_ORDER.index(token) + 1
    if token in _ROMAN_ORDER:
        return _ROMAN_ORDER.index(token) + 1
    return None


def _iter_lines(text: str):
    """원문의 줄을 (내용 시작 위치, 내용 끝 위치, 앞뒤 공백을 뺀 줄) 로 하나씩 돌려준다. \\r 단독 줄바꿈도 인정한다."""
    for match in re.finditer(r"[^\r\n]+", text):
        start = _skip_space_forward(text, match.start(), match.end())
        end = _skip_space_backward(text, start, match.end())
        if end > start:
            yield start, end, text[start:end]


def _match_heading_line(line: str) -> dict[str, Any] | None:
    """
    줄 하나가 제목 후보인지 본다.
    반환: 후보 dict(rule, level, title, rest, number) 또는 None(후보 아님).
    주의 문구(※ 등)는 rule="note"인 후보로 돌려준다. → 나중에 "주의 문구라서 제목이 아님"으로 기록하기 위해서다.
    """
    if NOTE_LINE_PATTERN.match(line):
        return {"rule": "note", "level": 9, "title": line, "rest": "", "number": None}
    for rule in HEADING_RULES:
        match = rule["pattern"].match(line)
        if match:
            groups = match.groupdict()
            return {"rule": rule["name"], "level": rule["level"],
                    "title": (groups.get("title") or "").strip(),
                    "rest": (groups.get("rest") or "").strip(),
                    "number": _item_number(groups.get("num"))}
    if _normalize_title(line) in SECTION_VOCABULARY and len(line) <= HEADING_MAX_CHARS:
        return {"rule": PLAIN_VOCABULARY_RULE["name"], "level": PLAIN_VOCABULARY_RULE["level"],
                "title": line, "rest": "", "number": None}
    return None


def _line_reject_reason(candidate: dict[str, Any]) -> str | None:
    """
    줄 하나만 보고 판단할 수 있는 "제목이 아닌 이유". 제목으로 볼 수 있으면 None.
      note_marker      : ※·*·☞ 로 시작하는 주의·참고 문구
      empty_title      : 기호·번호만 있고 제목 글자가 없음 (예: "□", "1.")
      label_with_body  : [필수] 본인은 … 처럼 괄호 뒤에 본문이 같은 줄에 붙어 있음
      toc_entry        : "지원대상 ...... 3" 처럼 목차 점선 + 쪽번호
      too_long         : 기호를 뗀 제목이 HEADING_MAX_CHARS보다 김 → 본문 문장
      sentence         : "…합니다." 처럼 문장으로 끝남
      ui_label         : "바로가기" 같은 화면 버튼 이름
    """
    title = candidate["title"]
    if candidate["rule"] == "note":
        return "note_marker"
    if not re.sub(r"[\W_]", "", title):
        return "empty_title"
    if candidate["rest"]:
        return "label_with_body"
    if TOC_LEADER_PATTERN.search(title):
        return "toc_entry"
    if len(title) > HEADING_MAX_CHARS:
        return "too_long"
    if SENTENCE_END_PATTERN.search(title):
        return "sentence"
    if any(word in title for word in UI_LABEL_WORDS):
        return "ui_label"
    return None


def _has_rule_split_point(segment_text: str) -> bool:
    """
    긴 구간을 규칙으로 자를 수 있는 지점(항목 시작 줄, 빈 줄, 줄바꿈)이 있는가.
    있으면 split_long_span()이 항목·문단·줄 경계에서 자르므로 LLM이 꼭 필요하지 않다.
    없으면 문장 끝·공백에서만 자를 수 있다 → 이런 구간만 LLM 후보로 본다.
    (SEPARATOR_PATTERNS의 앞 세 개와 같은 기준)
    제목 줄 바로 뒤의 줄바꿈은 분할 지점으로 치지 않는다. 그래서 첫 줄(제목 줄)을 뺀 본문에서만 찾는다.
    """
    parts = re.split(LINE_BREAK, segment_text.strip(), maxsplit=1)
    body = parts[1] if len(parts) == 2 else parts[0]
    return any(pattern.search(body) for _name, pattern in SEPARATOR_PATTERNS[:3])


def _detect_text_headings(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    텍스트 패턴으로 제목을 찾는다. 반환: (인정된 제목 목록, 제외된 후보 목록)
    각 항목: {start, end, line, title, level, rule, number, group, body_lines_after, evidence, reject_reason}
    """
    # (1)·(2) 줄마다 후보를 찾고, 줄 단위 검사를 한다.
    entries = []      # 줄 순서대로: ("candidate", dict) 또는 ("body", None)
    rejected: list[dict[str, Any]] = []
    for start, end, line in _iter_lines(text):
        if PAGE_MARKER_PATTERN.match(line) or PAGE_NUMBER_LINE_PATTERN.match(line):
            continue                                  # 페이지 표지·쪽번호 줄은 제목도 본문도 아니다.
        candidate = _match_heading_line(line)
        if candidate is None:
            entries.append(("body", None))
            continue
        candidate.update({"start": start, "end": end, "line": line,
                          "group": SECTION_VOCABULARY.get(_normalize_title(candidate["title"]))})
        reason = _line_reject_reason(candidate)
        if reason:
            candidate["reject_reason"] = reason
            rejected.append(candidate)
            entries.append(("body", None))            # 제목이 아닌 후보는 본문 줄로 센다.
        else:
            entries.append(("candidate", candidate))

    # 각 후보 뒤에 다음 후보가 나오기 전까지 본문 줄이 몇 개 있는지 센다.
    candidates = []
    for kind, candidate in entries:
        if kind == "candidate":
            candidate["body_lines_after"] = 0
            candidates.append(candidate)
        elif candidates:
            candidates[-1]["body_lines_after"] += 1

    # (3) 순서 검사: 번호 목록·목차·메뉴 구분
    accepted: list[dict[str, Any]] = []
    run_titles: set[str] = set()                      # 지금 이어지는 "목록 묶음"에 나온 제목들
    for index, candidate in enumerate(candidates):
        following = candidates[index + 1] if index + 1 < len(candidates) else None
        previous = candidates[index - 1] if index > 0 else None
        reason = None

        # 규칙 A: 본문 없이 같은 수준(또는 더 상위) 후보가 바로 이어지면 → 목록·목차·메뉴의 한 줄
        #   예) "1. 신분증\n2. 주민등록등본"  /  목차 "지원대상\n지원내용"
        #   단, 바로 아래에 더 하위 제목이 오는 경우(장 제목 → ■ 절 제목)는 상위 제목이므로 인정한다.
        if following is not None and candidate["body_lines_after"] == 0 and following["level"] <= candidate["level"]:
            reason = "list_or_toc"

        # 규칙 B: 바로 앞 후보가 목록으로 판정됐고, 본문 없이 이어지는 같은 규칙의 마지막 줄 → 목록의 끝
        #   번호가 있으면 앞 번호 + 1 이어야 하고(번호가 1로 돌아가면 새 목록·본문 시작),
        #   번호가 없으면 같은 묶음에 이미 나온 제목이 아니어야 한다(목차 뒤 본문 첫 제목은 목차 항목과 같은 이름).
        elif (previous is not None and previous.get("reject_reason") == "list_or_toc"
              and previous["body_lines_after"] == 0 and previous["rule"] == candidate["rule"]):
            if candidate["number"] is not None and previous["number"] is not None:
                if candidate["number"] == previous["number"] + 1:
                    reason = "list_end"
            elif _normalize_title(candidate["title"]) not in run_titles:
                reason = "list_end"

        if reason:
            candidate["reject_reason"] = reason
            rejected.append(candidate)
            run_titles.add(_normalize_title(candidate["title"]))
        else:
            run_titles = set()
            accepted.append(candidate)

    # 규칙 C: 상위 제목(수준 1·2)이 있는 문서에서 수준 3 항목(1. / 1) / 가. / ① / ○)은
    #   섹션 제목이 아니라 그 제목 아래의 하위 항목으로 본다. 사전에 있는 섹션 이름(예: "1. 지원대상")만 예외.
    #   상위 제목이 하나도 없는 문서(예: 번호만 있는 FAQ)에서는 번호 항목이 곧 가장 상위 구조이므로 인정한다.
    has_upper_heading = any(candidate["level"] <= 2 for candidate in accepted)
    if has_upper_heading:
        kept = []
        for candidate in accepted:
            if candidate["level"] >= 3 and not candidate["group"]:
                candidate["reject_reason"] = "sub_item"
                rejected.append(candidate)
            else:
                kept.append(candidate)
        accepted = kept

    # 규칙 D: 같은 규칙·같은 이름의 제목이 여러 번 인정되면(화면 상단 메뉴·요약 상자 + 본문),
    #   다음 제목까지의 본문이 가장 긴 것 하나만 남긴다. 나머지는 duplicate_title.
    #   예) 주택도시기금 화면 상단 "대출한도"(요약) 와 본문 "대출한도"(상세)
    def section_length(index: int) -> int:
        next_start = accepted[index + 1]["start"] if index + 1 < len(accepted) else len(text)
        return len(text[accepted[index]["start"]:next_start].strip())

    best_by_title: dict[tuple[str, str], int] = {}
    for index, candidate in enumerate(accepted):
        key = (candidate["rule"], _normalize_title(candidate["title"]))
        if key not in best_by_title or section_length(index) > section_length(best_by_title[key]):
            best_by_title[key] = index
    kept = []
    for index, candidate in enumerate(accepted):
        if best_by_title[(candidate["rule"], _normalize_title(candidate["title"]))] != index:
            candidate["reject_reason"] = "duplicate_title"
            rejected.append(candidate)
        else:
            kept.append(candidate)
    accepted = kept

    for candidate in accepted:
        evidence = [f"rule:{candidate['rule']}", f"body_lines_after:{candidate['body_lines_after']}"]
        if candidate["group"]:
            evidence.append(f"vocabulary:{candidate['group']}")
        candidate["evidence"] = evidence
        candidate["reject_reason"] = None

    rejected.sort(key=lambda item: item["start"])
    return accepted, rejected


def _decide_structure_status(
    text: str, heading_count: int, unit_spans: list[tuple[int, int, str]]
) -> dict[str, Any]:
    """
    구조 인식 상태를 정한다. 새 숫자 임계값을 만들지 않고, 아래 논리 조건과 기존 설정값(CHUNK_SIZE)만 쓴다.

    - unstructured : 인정된 제목이 2개 미만 → 섹션을 나눌 근거가 없다. (제목 1개 = 경계가 없는 것과 같다)
        · 문서 전체가 CHUNK_SIZE 이하면 청크 1개로 충분하므로 LLM이 필요 없다(single_chunk).
        · 더 길면 LLM 섹션 추출 대상(llm_sections).
    - partial      : 제목은 충분하지만, CHUNK_SIZE보다 긴 구간 중에 항목 시작 줄·빈 줄 같은
                     규칙 분할 지점이 하나도 없는 구간이 있다 → 그 구간만 LLM 후보.
    - structured   : 위에 해당하지 않음 → 규칙 기반 청킹으로 충분하다.
    unit_spans: 길이를 검사할 단위 구간 [(시작, 끝, 제목)]. (API는 필드 하나, 그 밖에는 섹션 하나)
    """
    reasons: list[str] = []
    regions: list[dict[str, Any]] = []
    body_length = len(text.strip())

    if heading_count < 2:
        reasons.append(f"인정된 제목 {heading_count}개: 2개 미만이면 섹션 경계를 정할 근거가 없음")
        if body_length <= CHUNK_SIZE:
            method = "single_chunk"
            reasons.append(f"문서 길이 {body_length}자 ≤ CHUNK_SIZE {CHUNK_SIZE}자 → 청크 1개로 충분, LLM 불필요")
        else:
            method = "llm_sections"
            regions.append({"start": 0, "end": len(text), "title": "(문서 전체)",
                            "reason": "제목을 인정할 수 없는 긴 문서"})
            reasons.append(f"문서 길이 {body_length}자 > CHUNK_SIZE {CHUNK_SIZE}자 → LLM 섹션 추출 후보")
        return {"status": STATUS_UNSTRUCTURED, "recommended_method": method, "llm_needed": method == "llm_sections",
                "status_reasons": reasons, "llm_candidate_regions": regions}

    for start, end, title in unit_spans:
        span_text = text[start:end].strip()
        if len(span_text) > CHUNK_SIZE and not _has_rule_split_point(span_text):
            regions.append({"start": start, "end": end, "title": title,
                            "reason": f"{len(span_text)}자 구간에 항목·문단 분할 지점이 없음"})
    if regions:
        reasons.append(f"제목 {heading_count}개 인정, 규칙으로 나눌 수 없는 긴 구간 {len(regions)}개 → 해당 구간만 LLM 후보")
        return {"status": STATUS_PARTIAL, "recommended_method": "rule_based_with_llm_regions", "llm_needed": True,
                "status_reasons": reasons, "llm_candidate_regions": regions}

    reasons.append(f"제목 {heading_count}개 인정, 긴 구간도 규칙으로 분할 가능 → 규칙 기반 청킹")
    return {"status": STATUS_STRUCTURED, "recommended_method": "rule_based", "llm_needed": False,
            "status_reasons": reasons, "llm_candidate_regions": []}


def analyze_structure(document: dict[str, Any]) -> dict[str, Any]:
    """
    문서 1건의 구조를 인식하고 판정한다. 청크는 만들지 않고, 원문·문서 dict도 바꾸지 않는다.

    입력: documents.jsonl의 문서 dict (document_id, source_type, text 필요)
    반환: dict
      document_id, source_type, rule_set_version
      structure_source   : "api_field"(반석님 정규화 형식의 [필드명]) 또는 "text_pattern"(줄 단위 제목 규칙)
      status             : structured / partial / unstructured
      recommended_method : rule_based / rule_based_with_llm_regions / llm_sections / single_chunk
      llm_needed         : LLM 섹션 추출이 필요한가 (이 함수는 LLM을 호출하지 않는다)
      status_reasons     : 판정 이유 (사람이 읽는 문장)
      headings           : 인정된 제목 [{start, end, line, title, level, rule, group, evidence, ...}]
      rejected_candidates: 제목처럼 보였지만 제외한 줄 [{..., reject_reason}]
      sections           : 제목으로 나눈 구간 [{start, end, title, group, page}]
      excluded_spans     : 의도적으로 뺀 구간 (페이지 표지, 출처 URL·최종수정일시 필드)
      llm_candidate_regions: LLM에 보낼 후보 구간 [{start, end, title, reason}]
      coverage           : 원문 보존 검사 결과 (verify_coverage 결과 + preserved_ratio)
      metrics            : 판정에 쓰지 않는 참고 지표 (제목 수, 규칙별 개수, 사전 일치 수, 첫 제목 위치 등)
    """
    text = document["text"]
    source_type = document["source_type"]

    if source_type == "api" and API_FIELD_PATTERN.search(text):
        # API 문서: [필드명] 줄이 곧 구조다. (B1의 _api_segments를 그대로 쓴다)
        structure_source = "api_field"
        sections, excluded = _api_segments(text)
        headings: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for match in API_FIELD_PATTERN.finditer(text):
            name = match.group(1)
            if name in METADATA_FIELDS:
                continue
            line_end = re.compile(r"[\r\n]|$").search(text, match.start()).start()
            group = API_FIELD_GROUPS.get(name)
            headings.append({"start": match.start(), "end": line_end, "line": text[match.start():line_end],
                             "title": name, "level": 2, "rule": "api_field", "number": None, "group": group,
                             "evidence": ["rule:api_field"] + ([f"vocabulary:{group}"] if group else []),
                             "reject_reason": None})
        unit_spans = [(start, end, section["title"]) for section in sections for start, end in section["field_spans"]]
    else:
        structure_source = "text_pattern"
        page_markers = find_page_markers(text) if source_type == "pdf" else []
        headings, rejected = _detect_text_headings(text)
        starts = [(heading["start"], heading["line"][:60], heading["level"]) for heading in headings]
        sections, excluded = _segments_from_starts(text, starts, page_markers)
        unit_spans = [(section["start"], section["end"], section["title"]) for section in sections]

    decision = _decide_structure_status(text, len(headings), unit_spans)

    # 원문 보존 검사: 섹션을 청크처럼 보고 기존 verify_coverage로 누락·중복을 센다.
    pseudo_chunks = [{"char_start": s["start"], "char_end": s["end"], "overlap_prev_chars": 0} for s in sections]
    coverage = verify_coverage(document, pseudo_chunks, excluded)
    excluded_mask = [False] * len(text)
    for span in excluded:
        for index in range(span["start"], span["end"]):
            excluded_mask[index] = True
    countable = sum(1 for index, char in enumerate(text) if not char.isspace() and not excluded_mask[index])
    coverage["countable_chars"] = countable
    coverage["preserved_ratio"] = round(1 - coverage["missing_chars"] / countable, 6) if countable else 1.0

    if coverage["missing_chars"] or coverage["unintended_overlap_chars"]:
        # 구조 인식 결과로 원문을 온전히 나눌 수 없으면 그 결과를 쓰지 않는다. (정상이라면 일어나지 않는다)
        decision = {**decision, "status": STATUS_UNSTRUCTURED, "recommended_method": "llm_sections",
                    "llm_needed": True,
                    "status_reasons": decision["status_reasons"] + ["섹션이 원문을 빠짐없이·겹침없이 덮지 못함"]}

    section_lengths = [len(text[s["start"]:s["end"]].strip()) for s in sections]
    rules_used: dict[str, int] = {}
    for heading in headings:
        rules_used[heading["rule"]] = rules_used.get(heading["rule"], 0) + 1
    rejected_by_reason: dict[str, int] = {}
    for candidate in rejected:
        rejected_by_reason[candidate["reject_reason"]] = rejected_by_reason.get(candidate["reject_reason"], 0) + 1
    metrics = {
        "heading_count": len(headings),
        "rejected_count": len(rejected),
        "rejected_by_reason": rejected_by_reason,
        "rules_used": rules_used,
        "vocabulary_matches": sum(1 for heading in headings if heading["group"]),
        "first_heading_ratio": round(headings[0]["start"] / len(text), 3) if headings and text else None,
        "section_count": len(sections),
        "max_section_chars": max(section_lengths) if section_lengths else 0,
        "sections_over_chunk_size": sum(1 for length in section_lengths if length > CHUNK_SIZE),
    }

    return {
        "document_id": document["document_id"],
        "source_type": source_type,
        "rule_set_version": STRUCTURE_RULE_SET_VERSION,
        "structure_source": structure_source,
        **decision,
        "headings": headings,
        "rejected_candidates": rejected,
        "sections": sections,
        "excluded_spans": excluded,
        "coverage": coverage,
        "metrics": metrics,
    }


def score_heading_detection(analysis: dict[str, Any], expected_lines: list[str]) -> dict[str, Any]:
    """
    정답 제목 줄이 있는 문서(가상 예제 등)에서만 쓰는 제목 인식 정확도.
    precision = 찾은 제목 중 정답 비율, recall = 정답 중 찾은 비율.
    정답 경계가 없는 실제 문서에는 쓰지 않는다. (정확도를 지어내지 않기 위해)
    """
    found = [heading["line"] for heading in analysis["headings"]]
    remaining = list(expected_lines)
    true_positive = 0
    for line in found:
        if line in remaining:
            remaining.remove(line)
            true_positive += 1
    return {
        "precision": true_positive / len(found) if found else 0.0,
        "recall": true_positive / len(expected_lines) if expected_lines else 0.0,
        "found": len(found),
        "expected": len(expected_lines),
    }
