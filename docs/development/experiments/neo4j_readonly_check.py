"""
Neo4j 읽기 전용 연결 점검 (2026-11-05 노트북 실행 스크립트 보존본).

DB 쓰기 없음: 읽기 세션으로 버전·DB 목록·건수·제약조건·인덱스·메모리 설정, 로더의 count 쿼리 20개를 실행한다.
비밀번호는 출력하지 않는다. 전제: neo4j 드라이버(.venv), .env의 NEO4J_URI·NEO4J_USER·NEO4J_PASSWORD, Neo4j 서버 실행 중.
참고: 빈 DB에서는 UnknownLabelWarning 알림이 대량 출력된다(오류 아님). 드라이버에 notifications_min_severity="OFF"를 주면 사라진다.
"""
import os
import sys
import time
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
import neo4j  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

from database import embedding as em  # noqa: E402
from database.neo4j import load_graph as lg  # noqa: E402

em.load_env_file()   # 프로젝트 .env (python-dotenv, 기존 환경 변수 덮어쓰지 않음)
uri = os.environ["NEO4J_URI"]
auth = (os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
print("driver:", neo4j.__version__, "| uri host:", urlparse(uri).hostname, "port:", urlparse(uri).port)

# 1) 인증·연결 (주소별 시간)
for label, target in (("env(.env)", uri), ("127.0.0.1", uri.replace("localhost", "127.0.0.1"))):
    start = time.perf_counter()
    with GraphDatabase.driver(target, auth=auth) as driver:
        driver.verify_connectivity()
        info = driver.get_server_info()
    print(f"verify_connectivity [{label}]: OK in {time.perf_counter() - start:.3f}s | server agent={info.agent} protocol={info.protocol_version}")

READ = neo4j.READ_ACCESS
with GraphDatabase.driver(uri, auth=auth) as driver:
    def read(query):
        with driver.session(default_access_mode=READ) as session:
            return [record.data() for record in session.run(query)]

    print("components:", read("CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition"))
    print("databases:", read("SHOW DATABASES YIELD name, currentStatus, default RETURN name, currentStatus, default"))
    print("total nodes:", read("MATCH (n) RETURN count(n) AS count")[0]["count"],
          "| total relationships:", read("MATCH ()-[r]->() RETURN count(r) AS count")[0]["count"])
    print("labels:", read("CALL db.labels() YIELD label RETURN collect(label) AS labels")[0]["labels"],
          "| rel types:", read("CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) AS t")[0]["t"])
    print("constraints:", read("SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties RETURN name, type, labelsOrTypes, properties"))
    print("indexes:", read("SHOW INDEXES YIELD name, type, labelsOrTypes, properties RETURN name, type, labelsOrTypes, properties"))
    print("memory settings:", read("SHOW SETTINGS YIELD name, value WHERE name STARTS WITH 'server.memory' "
                                   "OR name = 'server.jvm.additional' RETURN name, value ORDER BY name"))

    # 로더의 레이블 지정 count 쿼리 20개 (읽기 전용)를 그대로 실행해 문법·결과 확인
    class ReadOnlyRunner:
        def read_count(self, query):
            return read(query)[0]["count"]
    counts = lg.snapshot_counts(ReadOnlyRunner())
    print("loader snapshot_counts (20 queries):", "all zero" if not any(counts.values()) else counts, "| keys:", len(counts))
