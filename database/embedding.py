"""
임베딩 모듈 — Ollama /api/embed 호출과 "임베딩 입력 문자열" 만들기.

용어 (예시는 모두 가상)
- 청크 text      : DB에 저장해 사람에게 보여 줄 원문 조각. 예) "[지원내용] 인턴 채용시 … 지급"
- 임베딩 입력     : 벡터로 바꿀 때 모델에 넣는 문자열. 원문 text에 정책명·섹션명을 덧붙일 수 있다.
                   예) "2026 우수인재 연계 기업인턴십\n[지원내용]\n[지원내용] 인턴 채용시 … 지급"
                   → 저장되는 text는 바뀌지 않는다. 임베딩 입력은 검색 정확도를 위한 별도 문자열이다.
- 임베딩 벡터     : 임베딩 입력을 숫자 1024개로 바꾼 것. 예) [0.012, -0.034, …] (1024개)
                   뜻이 비슷한 문장끼리 벡터 방향이 비슷해져서, 질문 벡터와 가까운 청크를 찾을 수 있다.

이 모듈은 외부 패키지로 requests만 쓴다. (이미 설치되어 있음)
"""

import math
import os
import re
from pathlib import Path
from typing import Any, Callable

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

# ---------------------------------------------------------------------------
# 1. 설정값
# ---------------------------------------------------------------------------
# 기준선(baseline) 모델. 최종 모델이 아니며, 비교 실험 후 바뀔 수 있다.
# 강의 노트북에는 'qwen3-embedding:0.6b ' 처럼 뒤에 공백이 붙어 있었다 → 이름은 항상 strip()해서 쓴다.
DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
# `ollama show qwen3-embedding:0.6b` 의 embedding length (2026-11-05 확인)
DEFAULT_EMBEDDING_DIM = 1024
# 설정(인자·환경 변수·.env)이 하나도 없을 때만 쓰는 기본 주소. 기존 값 그대로 둔다.
# Windows에서는 localhost 요청이 127.0.0.1보다 느릴 수 있으므로(2026-11-05 실측) .env에 OLLAMA_BASE_URL을 두는 것을 권장한다.
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_BATCH_SIZE = 16
DEFAULT_TIMEOUT_SECONDS = 60

# 임베딩 입력 규칙. 실험에서 한 번에 하나만 바꿔 비교한다. 규칙 이름은 payload의 embedding_input_rule에 남는다.
#   policy_section_body    : (v1) 정책명 + 섹션명 + 본문을 그대로 이어 붙인다. 결과 재현을 위해 그대로 둔다.
#   policy_section_body_v2 : v1에서 "본문에 이미 있는 정책명·섹션명 줄"만 덧붙이지 않는다. (권장)
#   body_only              : 본문만
# 규칙의 동작을 바꿀 때는 기존 이름을 고치지 않고 _v3 처럼 새 이름을 만든다. → 이미 적재한 벡터와 섞이지 않는다.
RULE_POLICY_SECTION_BODY = "policy_section_body"
RULE_POLICY_SECTION_BODY_V2 = "policy_section_body_v2"
RULE_BODY_ONLY = "body_only"
EMBEDDING_INPUT_RULES = (RULE_POLICY_SECTION_BODY, RULE_POLICY_SECTION_BODY_V2, RULE_BODY_ONLY)

# v2 중복 판단에 쓰는 값
# - 정책명을 "선언"하는 필드: 이 필드 줄에 정책명이 들어 있으면 본문이 이미 정책명을 밝힌 것으로 본다.
#   (2026-11-05 B2 실측: 정책명이 본문에 있는 293개 중 229개가 이 형태, 64개는 문장 속 언급 → 정책명 유지)
POLICY_NAME_FIELDS = ("[서비스명]", "[정책명]")
# - 구조 인식 단계가 붙인 자리표시 제목: 실제 원문 제목이 아니므로 임베딩 입력에 넣지 않는다.
PLACEHOLDER_TITLES = ("(첫 제목 이전)", "(첫 필드 이전)")
CONTINUATION_SUFFIX = " (이어짐)"
# - section_title의 구분자: B1 API 그룹·B2 병합은 " + ", PDF·HTML 제목 경로는 " > "
TITLE_GROUP_SEPARATOR = " + "
TITLE_PATH_SEPARATOR = " > "
_LINE_SPLIT = re.compile(r"\r\n|\r|\n")

# 함수 타입: 문자열 목록 → 벡터 목록. 실제 Ollama 함수와 테스트용 가짜 함수가 같은 모양을 따른다.
Embedder = Callable[[list[str]], list[list[float]]]


class EmbeddingError(RuntimeError):
    """임베딩 요청 실패(연결·시간 초과·응답 형식 오류). 메시지에 키·비밀값을 넣지 않는다."""


# ---------------------------------------------------------------------------
# 2. 임베딩 입력 문자열
# ---------------------------------------------------------------------------
def _text_lines(text: str) -> list[str]:
    """본문의 줄 목록(앞뒤 공백 제거, 빈 줄 제외). \\r 단독 줄바꿈도 인정한다."""
    return [line.strip() for line in _LINE_SPLIT.split(text) if line.strip()]


def policy_name_declared(text: str, policy_name: str) -> bool:
    """
    본문이 정책명을 "명확히" 밝히고 있는가.
    - [서비스명]·[정책명] 줄에 정책명 전체가 들어 있거나,
    - 줄 하나가 정책명과 정확히 같을 때(제목 줄, 예: LH 웹 공고의 공고명 줄)만 True.
    문장 속에 정책명이 언급된 경우(예: "아이돌봄서비스 이용 시 …")는 False → 정책명을 덧붙여 문맥을 지킨다.
    """
    if not policy_name:
        return False
    for line in _text_lines(text):
        if line.startswith(POLICY_NAME_FIELDS) and policy_name in line:
            return True
        if line == policy_name:
            return True
    return False


def starts_a_line(text: str, element: str) -> bool:
    """
    element가 원문 어딘가의 "줄 시작"(문서 맨 앞 또는 줄바꿈 뒤, 앞 공백 무시)에서 시작하는가.
    줄바꿈이 들어 있는 제목(예: "1.\\n혼인 요건")도 원문을 직접 찾으므로 판단할 수 있다.
    문장 중간에서 시작하는 경우는 False.
    """
    if not element:
        return False
    position = text.find(element)
    while position != -1:
        before = position
        while before > 0 and text[before - 1] in " \t":
            before -= 1
        if before == 0 or text[before - 1] in "\r\n":
            return True
        position = text.find(element, position + 1)
    return False


def section_header_for_embedding(section_title: str | None, text: str) -> tuple[str, list[str]]:
    """
    섹션 제목 중에서 "본문에 아직 없는 부분"만 남긴 머리말과, 생략한 요소 목록을 돌려준다.

    1) 제목을 요소로 나눈다.  "[구비서류] + [근거법령]+[문의처]" → 묶음 2개
                              "1 공급개요 > ■ 공급대상"          → 경로 요소 2개
    2) 요소마다 판단한다.
       - "(첫 제목 이전)" 같은 자리표시 → 생략 (원문 제목이 아님)
       - " (이어짐)" 꼬리는 떼고 비교한다.
       - 본문의 어떤 줄이 그 요소로 "시작"하면 → 이미 본문에 제목 줄이 있으므로 생략
       - 그 밖에는 유지 (예: 청크가 "■ 공급대상"부터 시작해도 상위 장 제목 "1 공급개요"는 본문에 없으므로 유지)
    3) 남은 요소를 원래 구분자로 다시 잇는다.
    본문 내용을 지우는 함수가 아니다. "덧붙일 머리말"을 줄이는 것뿐이다.
    """
    if not section_title:
        return "", []
    kept_groups, omitted = [], []
    for group in section_title.split(TITLE_GROUP_SEPARATOR):
        kept_path = []
        for element in group.split(TITLE_PATH_SEPARATOR):
            element = element.strip()
            clean = element[:-len(CONTINUATION_SUFFIX)] if element.endswith(CONTINUATION_SUFFIX) else element
            if not clean or clean in PLACEHOLDER_TITLES:
                omitted.append(element)
            elif starts_a_line(text, clean):
                omitted.append(element)
            else:
                kept_path.append(clean)
        if kept_path:
            kept_groups.append(TITLE_PATH_SEPARATOR.join(kept_path))
    return TITLE_GROUP_SEPARATOR.join(kept_groups), omitted


def describe_embedding_input(chunk: dict[str, Any], policy_meta: dict[str, Any] | None, rule: str) -> dict[str, Any]:
    """
    임베딩 입력이 어떻게 만들어졌는지 설명하는 dict (보고·테스트용). 청크 dict는 바꾸지 않는다.
    반환: {"rule", "policy_name_line", "section_line", "omitted_title_elements", "policy_name_omitted", "text"}
      text가 실제 임베딩 입력 문자열이다. (build_embedding_text의 반환값과 같다)
    """
    if rule not in EMBEDDING_INPUT_RULES:
        raise ValueError(f"알 수 없는 임베딩 입력 규칙: {rule} {EMBEDDING_INPUT_RULES}")
    body = chunk["text"]
    result = {"rule": rule, "policy_name_line": "", "section_line": "", "omitted_title_elements": [],
              "policy_name_omitted": False}
    if rule == RULE_BODY_ONLY:
        return {**result, "text": body}
    if policy_meta is None:
        raise ValueError(f"{rule} 규칙에는 정책 메타데이터가 필요합니다: {chunk.get('chunk_id')}")

    policy_name = (policy_meta.get("policy_name") or "").strip()
    section_title = (chunk.get("section_title") or "").strip()
    if rule == RULE_POLICY_SECTION_BODY:
        # v1: 그대로 이어 붙인다. (기존 결과 재현용 — 동작을 바꾸지 않는다)
        policy_line, section_line, omitted, policy_omitted = policy_name, section_title, [], False
    else:
        # v2: 본문에 이미 있는 정책명·제목 줄은 덧붙이지 않는다.
        policy_omitted = policy_name_declared(body, policy_name)
        policy_line = "" if policy_omitted else policy_name
        section_line, omitted = section_header_for_embedding(section_title, body)

    text = "\n".join(part for part in (policy_line, section_line, body) if part)
    return {**result, "policy_name_line": policy_line, "section_line": section_line,
            "omitted_title_elements": omitted, "policy_name_omitted": policy_omitted, "text": text}


def build_embedding_text(chunk: dict[str, Any], policy_meta: dict[str, Any] | None, rule: str) -> str:
    """
    청크 1개의 임베딩 입력 문자열을 만든다. 청크 dict와 그 text는 바꾸지 않는다.

    입력: chunk(text·section_title 필요), policy_meta(policy_name 필요, body_only면 None 가능), rule
    반환: 문자열
      policy_section_body    → "정책명\n섹션명\n본문"  (비어 있는 줄은 넣지 않는다)
      policy_section_body_v2 → 위와 같되, 본문에 이미 있는 정책명·섹션명 줄은 넣지 않는다
      body_only              → "본문"
    """
    return describe_embedding_input(chunk, policy_meta, rule)["text"]


def build_query_text(question: str, query_template: str | None) -> str:
    """
    질문의 임베딩 입력 문자열.
    query_template=None 이면 질문 그대로, 문자열이면 template.format(question=질문).
    질의 지시문(Instruct 형식)을 쓸지는 모델 카드 확인 후 정한다. 그래서 이 함수에는 기본 지시문이 없고,
    호출하는 쪽이 None 또는 템플릿을 반드시 직접 넘긴다.
    """
    if query_template is None:
        return question
    if "{question}" not in query_template:
        raise ValueError("query_template에는 {question} 자리가 있어야 합니다.")
    return query_template.format(question=question)


# ---------------------------------------------------------------------------
# 3. 응답 검증
# ---------------------------------------------------------------------------
def validate_vectors(vectors: Any, expected_count: int, expected_dim: int) -> list[list[float]]:
    """
    임베딩 결과가 "입력 수만큼, 정해진 차원의, 유한한 숫자"인지 확인한다. 아니면 EmbeddingError.
    반환: float 리스트의 리스트
    """
    if not isinstance(vectors, list):
        raise EmbeddingError(f"임베딩 응답이 리스트가 아님: {type(vectors).__name__}")
    if len(vectors) != expected_count:
        raise EmbeddingError(f"임베딩 개수 불일치: 입력 {expected_count}개, 응답 {len(vectors)}개")
    checked: list[list[float]] = []
    for index, vector in enumerate(vectors):
        if not isinstance(vector, list) or len(vector) != expected_dim:
            size = len(vector) if isinstance(vector, list) else type(vector).__name__
            raise EmbeddingError(f"{index}번째 벡터 차원 불일치: 기대 {expected_dim}, 실제 {size}")
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
            raise EmbeddingError(f"{index}번째 벡터에 숫자가 아니거나 무한대·NaN인 값이 있음")
        checked.append([float(value) for value in vector])
    return checked


# ---------------------------------------------------------------------------
# 4. Ollama 호출
# ---------------------------------------------------------------------------
def load_env_file(env_path: Path | str | None = None) -> bool:
    """
    프로젝트 루트 .env를 python-dotenv로 읽어 환경 변수에 넣는다. 이미 있는 환경 변수는 덮어쓰지 않는다.
    python-dotenv가 없거나 파일이 없으면 아무것도 하지 않고 False. (값은 출력하지 않는다)
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    path = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    if not path.is_file():
        return False
    load_dotenv(dotenv_path=path, override=False)
    return True


def resolve_ollama_base_url(base_url: str | None = None, env_path: Path | str | None = None) -> str:
    """
    Ollama 주소를 정한다. 우선순위: ① 함수 인자 → ② 환경 변수 OLLAMA_BASE_URL → ③ .env → ④ DEFAULT_OLLAMA_BASE_URL
    (② 와 ③: .env는 이미 설정된 환경 변수를 덮어쓰지 않으므로, 시스템 환경 변수가 있으면 그 값이 쓰인다)
    """
    if base_url and base_url.strip():
        return base_url.strip().rstrip("/")
    load_env_file(env_path)
    value = os.environ.get("OLLAMA_BASE_URL", "").strip()
    return (value or DEFAULT_OLLAMA_BASE_URL).rstrip("/")


def ollama_embed_batch(
    texts: list[str],
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    base_url: str | None = None,
    expected_dim: int = DEFAULT_EMBEDDING_DIM,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    http_post: Callable[..., Any] = requests.post,
    metrics_sink: list[dict[str, Any]] | None = None,
) -> list[list[float]]:
    """
    문자열 목록을 한 번의 요청으로 임베딩한다.

    요청: POST {base_url}/api/embed  본문 {"model": 모델, "input": [문자열, ...], "truncate": false}
      truncate=false: 문맥 길이를 넘는 입력을 몰래 자르지 않고 오류로 돌려받는다. (Ollama 기본값은 true)
    응답: {"embeddings": [[숫자 ...], ...], "total_duration": ns, "load_duration": ns, "prompt_eval_count": n, ...}
    http_post: 테스트에서 가짜 함수를 넣을 수 있게 인자로 받는다. (기본값은 requests.post)
    metrics_sink: 리스트를 넘기면 요청마다 Ollama 측정값 dict를 하나씩 추가한다. (반환값은 그대로 벡터 목록)
    """
    if not texts:
        return []
    base_url = resolve_ollama_base_url(base_url)
    model = model.strip()
    request_body = {"model": model, "input": texts, "truncate": False}
    try:
        response = http_post(f"{base_url}/api/embed", json=request_body, timeout=timeout)
    except requests.Timeout as error:
        raise EmbeddingError(f"Ollama 임베딩 시간 초과({timeout}초): {base_url}") from error
    except requests.RequestException as error:
        raise EmbeddingError(f"Ollama 연결 실패: {base_url} ({type(error).__name__})") from error

    if getattr(response, "status_code", 200) != 200:
        detail = (getattr(response, "text", "") or "")[:200]
        raise EmbeddingError(f"Ollama 응답 오류 {response.status_code}: {detail}")
    try:
        body = response.json()
    except ValueError as error:
        raise EmbeddingError("Ollama 응답이 JSON이 아님") from error
    if not isinstance(body, dict) or "embeddings" not in body:
        raise EmbeddingError("Ollama 응답에 embeddings 필드가 없음")
    vectors = validate_vectors(body["embeddings"], expected_count=len(texts), expected_dim=expected_dim)
    if metrics_sink is not None:
        metrics_sink.append(extract_ollama_metrics(body, input_count=len(texts)))
    return vectors


# Ollama /api/embed 응답의 측정값 (docs.ollama.com/api/embed 기준). 시간 단위는 나노초.
OLLAMA_METRIC_FIELDS = ("total_duration", "load_duration", "prompt_eval_count")


def extract_ollama_metrics(body: dict[str, Any], input_count: int) -> dict[str, Any]:
    """응답에서 측정값만 꺼낸다. 없는 값은 None (모델·버전에 따라 빠질 수 있으므로 추측하지 않는다)."""
    metrics = {"model": body.get("model"), "input_count": input_count}
    for field in OLLAMA_METRIC_FIELDS:
        value = body.get(field)
        metrics[field] = value if isinstance(value, (int, float)) else None
    return metrics


def summarize_ollama_metrics(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """요청별 측정값을 합쳐 실험 보고용 요약을 만든다. 값이 하나라도 없으면 그 합계는 None."""
    def total(field):
        values = [item[field] for item in metrics]
        return sum(values) if values and all(value is not None for value in values) else None
    total_ns = total("total_duration")
    return {"requests": len(metrics), "inputs": sum(item["input_count"] for item in metrics),
            "total_duration_seconds": round(total_ns / 1e9, 3) if total_ns is not None else None,
            "load_duration_seconds": round(total("load_duration") / 1e9, 3) if total("load_duration") is not None else None,
            "prompt_eval_count": total("prompt_eval_count")}


def make_ollama_embedder(
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    base_url: str | None = None,
    expected_dim: int = DEFAULT_EMBEDDING_DIM,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    http_post: Callable[..., Any] = requests.post,
    metrics_sink: list[dict[str, Any]] | None = None,
) -> Embedder:
    """
    Ollama 임베딩 함수를 만들어 돌려준다. 반환된 함수는 문자열 목록을 batch_size개씩 나눠 요청한다.
    예) metrics = []
        embed = make_ollama_embedder(metrics_sink=metrics)
        vectors = embed(["문장1", "문장2"])          # 반환값은 기존과 같은 "벡터 목록"
        summarize_ollama_metrics(metrics)             # Ollama가 잰 시간·토큰 수 요약
    이 함수 자체는 서버에 접속하지 않는다. 반환된 함수를 호출할 때 접속한다.
    """
    if batch_size < 1:
        raise ValueError("batch_size는 1 이상이어야 합니다.")

    def embed(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(ollama_embed_batch(
                texts[start:start + batch_size], model=model, base_url=base_url,
                expected_dim=expected_dim, timeout=timeout, http_post=http_post, metrics_sink=metrics_sink,
            ))
        return vectors

    return embed


def vector_norm(vector: list[float]) -> float:
    """벡터 길이(L2 노름). Ollama 결과가 정규화되어 있는지(≈1.0) 실제 실행에서 확인할 때 쓴다."""
    return math.sqrt(sum(value * value for value in vector))
