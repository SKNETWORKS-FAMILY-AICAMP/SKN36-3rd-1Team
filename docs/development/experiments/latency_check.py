"""
localhost vs 127.0.0.1 요청 지연 측정 (2026-11-05 노트북 실행 스크립트 보존본).

DB 쓰기 없음: Qdrant 컬렉션 조회(get_collection) + Ollama 가상 문장 임베딩 + TCP 연결 시도만 한다.
전제: exp_b2_qwen3emb06b_v1 존재. 프로젝트 루트에서 .venv로 실행. API 키는 환경 변수에서 읽기만 하고 출력하지 않는다.
노트북 결과: localhost 요청마다 약 2.05초 추가(::1 연결 거부까지 대기), 127.0.0.1은 0.18초(Ollama 1건)/0.007초(Qdrant).
"""
import os
import socket
import statistics
import sys
import time
import warnings

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore", message="Api key is used with an insecure connection.")
from database import embedding as em  # noqa: E402
from database.qdrant import store  # noqa: E402

store.load_project_env()
api_key = os.environ["QDRANT_API_KEY"]
REPEAT = 5
TEXT = "가상 지연 측정 문장 (sample_only)"


def timed(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def summary(values):
    return {"median": round(statistics.median(values), 4), "min": round(min(values), 4), "max": round(max(values), 4),
            "runs": [round(v, 4) for v in values]}


print("resolve_ollama_base_url() host:", em.resolve_ollama_base_url().split("//")[1])
print("getaddrinfo('localhost') order:",
      [info[4][0] for info in socket.getaddrinfo("localhost", 6333, type=socket.SOCK_STREAM)])

for port in (6333, 11434):
    for family, address in ((socket.AF_INET6, "::1"), (socket.AF_INET, "127.0.0.1")):
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(5)
        start = time.perf_counter()
        try:
            sock.connect((address, port))
            result = "connected"
        except OSError as error:
            result = f"{type(error).__name__}"
        finally:
            sock.close()
        print(f"raw TCP connect {address}:{port} -> {result} in {time.perf_counter() - start:.3f}s")

em.make_ollama_embedder(base_url="http://127.0.0.1:11434")(["가상 준비 문장 (sample_only)"])  # 모델 로딩 분리
for host in ("localhost", "127.0.0.1"):
    embed = em.make_ollama_embedder(base_url=f"http://{host}:11434")
    ollama_times = [timed(lambda: embed([TEXT])) for _ in range(REPEAT)]
    client = store.get_client(url=f"http://{host}:6333", api_key=api_key)
    qdrant_times = [timed(lambda: client.get_collection("exp_b2_qwen3emb06b_v1")) for _ in range(REPEAT)]
    print(f"[{host}] Ollama embed 1건:", summary(ollama_times))
    print(f"[{host}] Qdrant get_collection:", summary(qdrant_times))
