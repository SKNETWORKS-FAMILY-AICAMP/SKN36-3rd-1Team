"""sample_only: 실제 목록 API·Checker·Pipeline, 파일 기반 검색 대역·모의 생성기. DB/유료 호출 없음."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["OPENAI_API_KEY"] = ""
os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:9/v1"
os.environ["LIFEPOLICY_DATASET_DIR"] = str(ROOT / "data/releases/v1.4.0-candidate")

from fastapi.responses import JSONResponse
from backend.api.main import create_app
from backend.api.policy_catalog import PolicyCatalog
from backend.eligibility.checker import check_eligibility
from database.handoff_data import load_payload_chunks
import uvicorn

catalog = PolicyCatalog()
calls = {"list": 0, "search": 0, "generate": 0, "selected_policy_id": None}


class FileSearch:
    def __init__(self):
        self.chunks = load_payload_chunks()

    def search_policy_context(self, question, *, filters, k):
        calls["search"] += 1
        selected = (filters or {}).get("policy_id")
        calls["selected_policy_id"] = selected
        nodes = catalog._snapshot()[2]
        ids = [selected] if selected in nodes else list(nodes)[:1] if not selected else []
        return {"policies": [{"policy_id": pid, "policy_name": nodes[pid]["policy_name"], "best_rank": 1,
            "graph_status": "FOUND", "graph": {"policy": nodes[pid], "eligibility": {}}, "data_quality": {},
            "evidence": [{**chunk, "rank": i + 1} for i, chunk in enumerate([c for c in self.chunks if c["policy_id"] == pid][:k])]} for pid in ids]}

    def close(self):
        pass


def generate(question, contexts, checks, history, profile=None):
    calls["generate"] += 1
    return {"status": "answered", "answer": "화면 검증용 모의 생성기 응답입니다. 선택 정책의 공식 원문을 확인해 주세요. [1]",
        "used_source_ids": [1], "follow_up_questions": [] if (profile or {}).get("region") else
        [{"kind": "ask_info", "field": "region", "text": "거주 지역을 알려 주세요."}]}


app = create_app(session_factory=FileSearch, check_fn=check_eligibility, generate_fn=generate, catalog_factory=lambda: catalog)


@app.middleware("http")
async def mark_validation_response(request, call_next):
    response = await call_next(request)
    if request.url.path == "/api/policies":
        calls["list"] += 1
    if request.method == "POST" and request.url.path == "/api/ask" and response.status_code == 200:
        content = b"".join([chunk async for chunk in response.body_iterator])
        data = json.loads(content); data["is_sample"] = True
        headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
        return JSONResponse(data, headers=headers)
    return response


@app.get("/validation")
def validation():
    result = catalog.list()
    return {"sample_only": True, "calls": calls, "dataset_version": result.dataset_version,
            "manifest_sha256": result.manifest_sha256, "policies": result.total,
            "with_documents": sum(bool(p.required_documents) for p in result.items),
            "with_requirements": sum(bool(p.official_requirements) for p in result.items)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8178)
