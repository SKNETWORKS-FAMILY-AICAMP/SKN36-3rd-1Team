"""#76 official_requirements를 실제 R3D DB(Neo4j·Qdrant)로 읽기 전용 확인한다. pytest 수집 대상이 아니다.

- DB에는 검색(Qdrant query, Neo4j READ 세션)만 한다. 적재·삭제·컬렉션 변경을 하지 않는다.
- LLM은 호출하지 않는다. Generator는 첫 근거를 인용하는 고정 응답 대역이다. Checker는 실제 Checker다.
- 프로필은 가상값이다. 인증값은 이 스크립트가 만들거나 복호화하지 않는다. 실행 전 현재 PowerShell 프로세스의
  환경 변수로만 넣는다(ISSUE105_R3_RUNBOOK §5.7 Enter-R3VerifySecrets 방식). 변수 이름·주소·포트만 출력한다.
- --env-file(선택)은 이미 있는 환경 변수를 덮어쓰지 않고 나머지 설정(예: OLLAMA_BASE_URL)만 채운다.
- 필수 변수가 없거나 검색 세션을 만들지 못하면 API를 호출하지 않고 보고서도 쓰지 않은 채 끝낸다.
- 기대값을 하드코딩하지 않는다. 응답의 각 기준을 이번 검색이 DB에서 실제로 돌려준 같은 정책의 청크(B2 컬렉션
  chunk_id·document_id·본문)와 대조한다. 릴리스 payload_chunks.jsonl은 청크 경계·ID가 달라 참고값으로만 남긴다.

실행 예(PowerShell, 이 저장소 루트):
    ..\\SKN36-3rd-1Team\\.venv\\Scripts\\python.exe tests\\manual\\verify_issue76_r3d_readonly.py `
        --env-file ..\\SKN36-3rd-1Team\\.env --out ..\\SKN36-3rd-1Team\\.cache\\issue76-r3d-ask-2.json
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

RELEASE = ROOT / "data" / "releases" / "v1.4.0-candidate"
# 유형별 대표 정책(릴리스 원문·품질 플래그에서 확인해 고름). 기대값은 응답·원문 대조로 판정한다.
POLICIES = {
    "POL_GOV_046": "나이 기준",
    "POL_BOK_045": "소득 기준",
    "POL_BOK_004": "여러 기준",
    "POL_BOK_007": "조건 UNKNOWN(S1) + 원문 기준",
    "POL_009": "제외 조건(EXCLUSION_NOT_MODELED)",
    "POL_BOK_061": "이용료 구간 소득 문구(PR #133 리뷰)", "POL_GOV_061": "다음 줄 단서(PR #133 리뷰)",
    "POL_YC_029": "출처 검토 필요(SOURCE_AGE_ERROR_MARK)",
    "POL_BOK_099": "신규 복지로", "POL_BOK_101": "신규 복지로", "POL_BOK_104": "신규 복지로",
    "POL_003": "신청기간 미확인 대출", "POL_004": "신청기간 미확인 대출",
    "POL_006": "신청기간 미확인 대출", "POL_007": "신청기간 미확인 대출",
}
PROFILE = {"age": 27, "region": "서울", "income": None}  # 가상 프로필
QUESTION = "이 정책의 신청 자격 기준을 알려줘"
# 검색 세션에 필요한 변수(값은 출력하지 않는다).
REQUIRED_ENV = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "QDRANT_URL", "QDRANT_API_KEY")


def _endpoint(name: str) -> str:
    value = os.environ.get(name) or ""
    parts = urlsplit(value)
    return f"{parts.scheme}://{parts.hostname}:{parts.port}" if parts.hostname else "(unset)"


def _flat(text: str) -> str:
    # 단서 줄을 이어 붙인 값은 원문 줄바꿈이 공백 하나가 된다. 공백·줄바꿈 차이만 없애고 대조한다.
    return re.sub(r"\s+", " ", text).strip()


ADOPTED: list[list[dict]] = []  # 호출마다 Generator가 받은 채택 근거(chunk_id 등, 본문 제외)


def _mock_generator(question, contexts, checks, history, profile=None):
    ADOPTED.append([{key: context.get(key) for key in ("source_id", "policy_id", "document_id", "chunk_id")}
                    for context in contexts])
    first = contexts[0]["source_id"] if contexts else None
    if first is None:
        return {"status": "no_evidence", "answer": "근거가 없습니다.", "used_source_ids": [], "follow_up_questions": []}
    return {"status": "answered", "answer": f"검증용 고정 답변 [{first}]", "used_source_ids": [first],
            "follow_up_questions": []}


def _release_texts_by_policy() -> dict[str, list[str]]:
    texts = {}
    with (RELEASE / "qdrant" / "payload_chunks.jsonl").open(encoding="utf-8") as handle:
        for row in map(json.loads, handle):
            texts.setdefault(row["policy_id"], []).append(row["text"])
    return texts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True, help="새 JSON 보고서 경로(이미 있으면 거부)")
    parser.add_argument("--env-file", type=Path, help="나머지 설정을 읽을 .env(이미 있는 환경 변수는 덮어쓰지 않음)")
    args = parser.parse_args()
    if args.out.exists():
        print(f"보고서가 이미 있습니다. 다른 이름을 쓰세요: {args.out}", file=sys.stderr)
        return 2
    # DB 주소·인증값은 이 PowerShell 프로세스에 직접 넣은 값만 쓴다. .env로 채우면 다른 DB의 인증값이 섞일 수 있어
    # --env-file을 읽기 전에 확인한다(PowerShell에서 빈 값을 대입하면 변수 자체가 사라진다).
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    print(json.dumps({"neo4j": _endpoint("NEO4J_URI"), "qdrant": _endpoint("QDRANT_URL"),
                      "missing_env": missing}, ensure_ascii=False))
    if missing:
        print("필수 환경 변수가 이 PowerShell 창에 없어 실행하지 않았습니다(보고서 미작성): " + ", ".join(missing)
              + " — .env 값으로 대신 채우지 않습니다. R3D 인증값을 먼저 넣었는지 확인하세요.", file=sys.stderr)
        return 2
    from database import embedding

    if args.env_file is not None and not embedding.load_env_file(args.env_file):
        print(f".env 파일을 읽지 못했습니다: {args.env_file}", file=sys.stderr)
        return 2

    from fastapi.testclient import TestClient
    from backend.api.main import create_app

    manifest_sha = hashlib.sha256((RELEASE / "manifest.json").read_bytes()).hexdigest()
    release_texts = _release_texts_by_policy()
    app = create_app(generate_fn=_mock_generator)
    report = {"neo4j": _endpoint("NEO4J_URI"), "qdrant": _endpoint("QDRANT_URL"),
              "release_manifest_sha256": manifest_sha, "profile": PROFILE, "llm_calls": 0, "results": []}
    failures = 0
    with TestClient(app) as client:
        if app.state.session is None:
            print("검색 세션을 만들지 못해 실행하지 않았습니다(보고서 미작성). 주소·포트·인증값·Ollama를 확인하세요.",
                  file=sys.stderr)
            return 2
        report["search_profile"] = {"profile": str(app.state.session.profile),
                                    "collection": str(app.state.session.collection)}
        # 읽기 전용 검색 1회로 연결을 먼저 확인한다. 실패하면 어느 백엔드·어떤 종류인지만 출력한다(메시지·값 미출력).
        try:
            app.state.session.search_policy_context(QUESTION, filters={"policy_id": next(iter(POLICIES))})
        except Exception as exc:  # noqa: BLE001 - 진단 출력 후 종료
            print(json.dumps({"probe": "failed", "error": type(exc).__name__,
                              "backend": getattr(exc, "backend", None), "kind": getattr(exc, "kind", None)},
                             ensure_ascii=False))
            print("검색 연결 확인에 실패해 실행하지 않았습니다(보고서 미작성). kind=auth면 인증값, unavailable이면 "
                  "주소·컨테이너, backend=ollama면 Ollama를 확인하세요.", file=sys.stderr)
            return 2
        print(json.dumps({"probe": "ok", **report["search_profile"]}, ensure_ascii=False))
        # 검색 결과(읽기 전용)를 그대로 기록만 하는 감싸기. 결과는 바꾸지 않는다.
        searched = []
        original_search = app.state.session.search_policy_context

        def recording_search(*search_args, **search_kwargs):
            result = original_search(*search_args, **search_kwargs)
            searched.append(result)
            return result

        app.state.session.search_policy_context = recording_search
        report["health"] = client.get("/health").json()
        for pid, kind in POLICIES.items():
            body = {"question": QUESTION, "profile": PROFILE, "history": [], "selected_policy_id": pid}
            searched.clear()
            ADOPTED.clear()
            response = client.post("/api/ask", json=body)
            row = {"policy_id": pid, "kind": kind, "http": response.status_code}
            if response.status_code != 200:
                row["error"] = response.json()
                failures += 1
                report["results"].append(row)
                continue
            data = response.json()
            checks = data["policy_checks"]
            row["policy_ids"] = [check["policy_id"] for check in checks]
            cited = {citation["document_id"] for citation in data["citations"]}
            # 이번 검색이 DB에서 돌려준 청크(정책·문서·본문). 같은 chunk_id만 대조한다.
            hits = {hit.get("chunk_id"): (item.get("policy_id"), hit.get("document_id"), hit.get("text") or "")
                    for result in searched for item in result.get("policies", []) for hit in item.get("evidence", [])}
            adopted = [context for call in ADOPTED for context in call]
            adopted_ids = {context["chunk_id"] for context in adopted}
            for check in checks:
                problems = []
                for requirement in check.get("official_requirements") or []:
                    hit = hits.get(requirement["chunk_id"])
                    if hit is None or hit[0] != check["policy_id"]:
                        problems.append(f"검색 결과에 없거나 다른 정책의 청크: {requirement['chunk_id']}")
                    elif hit[1] != requirement["document_id"]:
                        problems.append(f"문서 불일치: {requirement['chunk_id']}")
                    elif _flat(requirement["value"]) not in _flat(hit[2]):
                        problems.append(f"DB 청크 원문에 없는 값: {requirement['label']}")
                    if requirement["chunk_id"] not in adopted_ids:
                        problems.append(f"채택되지 않은 청크: {requirement['chunk_id']}")
                    if requirement["document_id"] not in cited:
                        problems.append(f"citations에 없는 문서: {requirement['document_id']}")
                    # 참고: 청크 경계가 다른 릴리스 파일 본문에도 같은 원문 값이 있는가.
                    requirement["in_release_text"] = any(_flat(requirement["value"]) in _flat(text)
                                                         for text in release_texts.get(check["policy_id"], []))
                failures += bool(problems) or check["policy_id"] != pid
                row.update({
                    "official_requirements": check.get("official_requirements"),
                    "requirement_excerpt_labels": [e["label"] for e in check.get("requirement_excerpts") or []],
                    # 개인 판정은 별도 필드로 그대로 둔다(공식 기준과 섞지 않음).
                    "conditions": [(c["field"], c["status"]) for c in check["conditions"]],
                    "reception_status": check["reception_status"],
                    "exclusion_reasons": check.get("exclusion_reasons"),
                    "contacts": len(check.get("contacts") or []),
                    "adopted_chunks": [context["chunk_id"] for context in adopted
                                       if context["policy_id"] == check["policy_id"]],
                    "searched_chunks": sorted(chunk_id for chunk_id, hit in hits.items()
                                              if hit[0] == check["policy_id"]),
                    "problems": problems,
                })
            report["results"].append(row)
    report["failures"] = failures
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"neo4j": report["neo4j"], "qdrant": report["qdrant"], "failures": failures,
                      "checked": len(POLICIES), "out": str(args.out)}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
