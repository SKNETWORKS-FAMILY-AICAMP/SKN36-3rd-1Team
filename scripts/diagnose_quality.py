import json
from pathlib import Path

base = Path(".")
policies_path = base / "data" / "processed" / "policies.jsonl"
docs_path = base / "data" / "processed" / "documents.jsonl"
chunks_path = base / "data" / "processed" / "chunks.jsonl"
edges_path = base / "data" / "processed" / "graph_edges.jsonl"

with open(policies_path, "r", encoding="utf-8") as f:
    policies = [json.loads(line) for line in f]

with open(docs_path, "r", encoding="utf-8") as f:
    documents = [json.loads(line) for line in f]

with open(chunks_path, "r", encoding="utf-8") as f:
    chunks = [json.loads(line) for line in f]

doc_ids = {d["document_id"] for d in documents}
policy_ids = {p["policy_id"] for p in policies}

print(f"=== Total Records ===")
print(f"Policies: {len(policies)}")
print(f"Documents: {len(documents)}")
print(f"Chunks: {len(chunks)}")

print("\n=== 1. Missing Document References ===")
for p in policies:
    ref_docs = p.get("source_document_ids", [])
    missing = [doc_id for doc_id in ref_docs if doc_id not in doc_ids]
    if missing:
        print(f"Policy {p['policy_id']} ({p['policy_name'][:25]}...) references missing: {missing}")

print("\n=== 2. Document Content Quality & Local Path Check ===")
for d in documents:
    doc_id = d["document_id"]
    local_path_str = d.get("local_raw_path", "")
    local_path = base / local_path_str
    exists = local_path.exists()
    text = d.get("text", "")
    text_len = len(text)
    preview = text[:80].replace("\n", " ") if text else "EMPTY"
    
    # Check for junk / login / logout warning texts
    has_warning = any(w in text for w in ["자동 로그아웃", "이용에 불편을 드려", "로그인", "페이지를 찾을 수 없습니다", "404 Not Found"])
    
    print(f"[{doc_id}] exists={exists} len={text_len} warning={has_warning} | preview: {preview}")

print("\n=== 3. Raw Files in data/raw ===")
raw_dir = base / "data" / "raw"
for f in raw_dir.glob("**/*"):
    if f.is_file():
        print(f"  {f.relative_to(base)} ({f.stat().st_size} bytes)")

