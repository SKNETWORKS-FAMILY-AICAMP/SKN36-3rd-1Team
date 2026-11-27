"""Read-only release/DB evidence. Generator is a deterministic test double.

Run pytest for release regression; run this file with --live --output PATH
for real Neo4j/Qdrant evidence using R3D-only environment variables.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from pr140_verification_oracles import (
    assert_age_outcomes, assert_replay_sources, bind_saved_completion, boundary_values,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.api.main import create_app
from backend.api.schemas import AskRequest
from backend.eligibility.checker import _condition, check_eligibility
from backend.rag.pipeline import run_ask_pipeline
from database.hybrid_search import (
    _build_condition_tree, _tree_groups,
    combine_policy_evidence, get_policy_graph_context, ReadOnlyTimedRunner,
    default_profile, resolve_search_settings,
)
from fastapi.testclient import TestClient

PACKAGE = ROOT / 'data/releases/v1.4.0-candidate'


def rows(name):
    return [json.loads(s) for s in (PACKAGE / name).read_text(encoding='utf-8').splitlines() if s.strip()]


POLICIES = {r['policy_id']: r for r in rows('neo4j/nodes_policy.jsonl')}
DOCS = {r['document_id']: r for r in rows('neo4j/nodes_document.jsonl')}
CONDITIONS = {r['condition_id']: r for r in rows('neo4j/nodes_eligibility_condition.jsonl')}
GROUPS = {r['group_id']: r for r in rows('neo4j/nodes_condition_group.jsonl')}
RELS = rows('neo4j/relationships.jsonl')
CHUNKS = rows('qdrant/payload_chunks.jsonl')
CORRECTIONS = json.loads((ROOT / 'data/metadata/data_corrections_v1.4.0.json').read_text(encoding='utf-8'))
AGE_RULES = json.loads((ROOT / 'tests/fixtures/pr140_age_expectations.json').read_text(encoding='utf-8'))['policies']


def release_graph(pid):
    """Build the real search tree shape, retaining every relationship review."""
    group_rows = {}
    for gid, group in GROUPS.items():
        if group['policy_id'] != pid:
            continue
        links = [r for r in RELS if r['source_id'] == gid]
        group_rows[gid] = {'group': group,
            'conditions': [{'condition': CONDITIONS[r['target_id']], 'relationship': r}
                           for r in links if r['relation'] == 'CONTAINS_CONDITION'],
            'child_groups': [{'group_id': r['target_id'], 'relationship': r}
                             for r in links if r['relation'] == 'CONTAINS_GROUP']}
    roots = [r for r in RELS if r['source_id'] == pid and r['relation'] == 'HAS_ROOT_CONDITION_GROUP']
    issues = []
    trees = [_build_condition_tree(pid, r['target_id'], group_rows, r, issues) for r in roots]
    docs = [{**DOCS[r['target_id']], 'relationship': r} for r in RELS
            if r['source_id'] == pid and r['relation'] == 'SUPPORTED_BY']
    return {'policy_id': pid, 'policy': POLICIES[pid], 'documents': docs,
            'eligibility': {'condition_tree': trees, 'structure_issues': issues,
                'unknown_logic_groups': [g['group_id'] for t in trees for g in _tree_groups(t) if g['logic'] == 'UNKNOWN']}}


def item(pid, graph=None, chunks=None):
    graph = graph or release_graph(pid)
    hits = [{**c, 'rank': i, 'score': 1.0} for i, c in enumerate(
        chunks if chunks is not None else [c for c in CHUNKS if c['policy_id'] == pid], 1)]
    return combine_policy_evidence({'hits': hits}, {'policies': {pid: graph}})['policies'][0]


def generate(question, contexts, checks, history, profile=None):
    return {'status': 'answered', 'answer': '공식 원문과 추가 확인 사항을 확인하세요. ' +
            ' '.join(f"[{c['source_id']}]" for c in contexts),
            'used_source_ids': [c['source_id'] for c in contexts], 'follow_up_questions': []}


def exercise(policy, age, *, check_fn=check_eligibility):
    profile = {'age': age, 'region': '', 'income': None}
    capture = Mock(side_effect=generate)
    session = Mock()
    session.search_policy_context.return_value = {'policies': [copy.deepcopy(policy)]}
    request = {'question': policy['policy_name'] + ' 지원 요건과 공식 출처',
               'profile': profile, 'history': [], 'selected_policy_id': policy['policy_id']}
    raw = check_fn(profile, policy)
    with TestClient(create_app(session_factory=lambda: session, check_fn=check_fn,
                              generate_fn=capture)) as client:
        response = client.post('/api/ask', json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    selected = body['policy_checks'][0]
    assert selected['policy_id'] == policy['policy_id']
    args = capture.call_args.args
    assert args[2][0]['conditions'] == selected['conditions']
    contexts = args[1]
    assert all(c['policy_id'] == policy['policy_id'] for c in contexts)
    assert all(1 <= int(n) <= len(body['citations']) for n in re.findall(r'\[(\d+)\]', body['answer']))
    by_doc = {d['document_id']: d for d in policy['graph']['documents']}
    for context in contexts:
        assert context['document_id'] in by_doc
        assert context['url'] == by_doc[context['document_id']]['source_url']
        hit = next(h for h in policy['evidence'] if h['chunk_id'] == context['chunk_id'])
        assert context['text'] == hit['text'].strip()
    assert selected['official_apply_url'] == (policy['graph']['policy'].get('official_apply_url') or None)
    return {'age': age, 'checker': raw, 'api': body, 'http_status': response.status_code,
            'generator_contexts': contexts, 'generator_checks': args[2],
            'quality': policy['data_quality'], 'evidence_document_check': policy['evidence_document_check']}


def age_conditions(graph):
    return [c for t in graph['eligibility']['condition_tree'] for g in _tree_groups(t)
            for c in g['conditions'] if c['type'] == 'AGE']


def test_incheon_boundaries_and_hold():
    p = item('POL_STG_ICN_001')
    for age in (18, 19, 39, 40, None):
        result = exercise(p, age)
        assert all(c['status'] == 'needs_check' for c in result['api']['policy_checks'][0]['conditions'])
    # Synthetic safety probe: never change frozen relationship review flags.
    held = copy.deepcopy(p)
    held['data_quality'] = {'hold_auto_eligibility': True, 'affected_attributes': ['age'], 'warnings': []}
    assert all(c['status'] != 'eligible' for c in exercise(held, 19)['generator_checks'][0]['conditions'])


@pytest.mark.parametrize('rule', AGE_RULES, ids=[r['policy_id'] for r in AGE_RULES])
def test_corrected_applicant_boundaries_through_api(rule):
    p = item(rule['policy_id'])
    condition = CONDITIONS[rule['condition_id']]
    assert condition['type'] == 'AGE'
    assert condition['operator'] == rule['operator']
    assert condition['unit'] == rule['unit']
    assert condition['value'] == f"{rule['lower']}~{rule['stored_upper']}"
    actual_groups = [{'group_id': g['group_id'], 'logic': g['logic'],
                      'condition_ids': [c['condition_id'] for c in g['conditions']]}
                     for t in p['graph']['eligibility']['condition_tree'] for g in _tree_groups(t)]
    assert actual_groups == rule['group_snapshot']
    assert p['data_quality']['hold_auto_eligibility']
    assert rule['evidence_document_id'] in DOCS
    assert DOCS[rule['evidence_document_id']]['policy_id'] == rule['policy_id']
    links = [r for r in RELS if r['source_id'] == rule['policy_id']
             and r['relation'] == 'REQUIRES_CONDITION' and r['target_id'] == rule['condition_id']]
    assert len(links) == 1
    assert links[0]['evidence_document_id'] == rule['evidence_document_id']
    for age in boundary_values(rule):
        direct = _condition(condition, {'age': age})
        if age is not None and not 0 <= age <= 120:
            assert direct['status'] == 'needs_check'
            continue
        result = exercise(p, age)
        assert_age_outcomes(rule, age, direct, result['checker'], result['api']['policy_checks'][0])


def test_multi_policy_sources_and_null_url():
    policies = [item(pid) for pid in ('POL_STG_ICN_001', 'POL_YC_007', 'POL_BOK_004')]
    response = run_ask_pipeline(AskRequest(question='공식 지원 요건', profile={'age': 25, 'region': '', 'income': None}, history=[]),
        search_fn=lambda *a, **kw: {'policies': policies}, check_fn=check_eligibility, generate_fn=generate)
    assert {p.policy_id for p in response.policy_checks} == {p['policy_id'] for p in policies}
    for citation in response.citations:
        assert citation.document_id in DOCS


def test_beneficiary_and_guardian_age_is_not_applicant_age():
    special = [c for c in CONDITIONS.values() if c['condition_id'].startswith(('COND_CHILD_AGE_', 'COND_TARGET_AGE_'))]
    assert special
    for condition in special:
        assert condition['type'] == 'SPECIAL_TARGET'
        for age in (0, 25, 65, None):
            assert _condition(condition, {'age': age})['status'] == 'needs_check'


def test_saved_live_llm_contract_replay():
    """Replay seven recorded real completions without another external call.

    This checks transport/source contracts, not semantic correctness of prose.
    The report records the observed age-conflict and URL prose failures.
    """
    evidence = json.loads((ROOT / 'docs/reports/issue79-18-90-e2e-20261126.json').read_text(encoding='utf-8'))
    by_pid = {h['target']: h['result'] for h in evidence['hybrid']}
    for run in evidence['llm']:
        saved = run['response']
        session = Mock()
        session.search_policy_context.return_value = by_pid[run['policy_id']]
        with TestClient(create_app(session_factory=lambda: session, check_fn=check_eligibility,
                                  generate_fn=lambda question, contexts, *a, **kw:
                                  bind_saved_completion(saved, contexts, run['policy_id']))) as client:
            response = client.post('/api/ask', json={'question': '공식 신청 URL과 연령 근거를 알려주세요.',
                'profile': {'age': run['age'], 'region': '', 'income': None},
                'selected_policy_id': run['policy_id'], 'history': []})
        assert response.status_code == 200
        assert {p['policy_id'] for p in response.json()['policy_checks']} == {run['policy_id']}
        assert_replay_sources(saved, response.json(), run['policy_id'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', required=True)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--hybrid', action='store_true')
    parser.add_argument('--llm', action='store_true')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    result = {'mode': 'real DB + TestClient + mock Generator' if args.live else 'release + TestClient + mock Generator',
              'llm_calls': 0, 'manifest_sha256': hashlib.sha256((PACKAGE / 'manifest.json').read_bytes()).hexdigest(),
              'search_settings': resolve_search_settings(profile=default_profile()), 'policies': {}}
    driver = client = None
    try:
        if args.live:
            from neo4j import GraphDatabase
            from urllib.request import Request, build_opener, ProxyHandler
            opener = build_opener(ProxyHandler({}))
            def qdrant(path, body=None):
                request = Request('http://127.0.0.1:56756/' + path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={'api-key': os.environ['ISSUE105_R3D_VERIFY_QDRANT_API_KEY'],
                             'Content-Type': 'application/json'})
                with opener.open(request, timeout=10) as stream:
                    return json.load(stream)['result']
            driver = GraphDatabase.driver('bolt://127.0.0.1:56787', auth=('neo4j', os.environ['ISSUE105_R3D_VERIFY_NEO4J_PASSWORD']), connection_timeout=5)
            runner = ReadOnlyTimedRunner(driver)
            result['neo4j_counts'] = runner.read_rows('MATCH (p:Policy) RETURN count(p) AS policies')
            result['qdrant_counts'] = {name: qdrant('collections/' + name)['points_count'] for name in
                ('exp_b2_qwen3emb06b_v140_v1', 'exp_b3_qwen3emb06b_v140_v1')}
        source_chunks = None
        if args.source_root:
            from database.release_adapter import load_release_documents
            from database.chunking import chunk_documents
            documents = load_release_documents(PACKAGE, args.source_root)
            source_chunks = {c['chunk_id']: c for c in chunk_documents(documents, strategy='B2')[0]}
            result['source_documents_hash_verified'] = len(documents)
        ids = ['POL_STG_ICN_001', 'POL_010', 'POL_YC_024', 'POL_YC_007', 'POL_BOK_004',
               'POL_STG_MOIS_001', 'POL_BOK_001', 'POL_BOK_043', 'POL_GOV_054', 'POL_GOV_065']
        guardian = next(r['source_id'] for r in RELS if r['relation'] == 'REQUIRES_CONDITION'
                        and r['target_id'].startswith('COND_TARGET_AGE_'))
        ids.append(guardian)
        for pid in ids:
            if pid not in POLICIES:
                continue
            graph = get_policy_graph_context([pid], runner=runner)['policies'][pid] if args.live else release_graph(pid)
            chunks = None
            if args.live:
                found = qdrant('collections/exp_b2_qwen3emb06b_v140_v1/points/scroll', {
                    'filter': {'must': [{'key': 'policy_id', 'match': {'value': pid}}]},
                    'limit': 200, 'with_payload': True, 'with_vector': False})
                assert found.get('next_page_offset') is None, 'Increase pagination coverage'
                chunks = [point['payload'] for point in found['points']]
            p = item(pid, graph, chunks)
            audit = []
            if source_chunks is not None and args.live:
                for hit in p['evidence']:
                    original = source_chunks.get(hit['chunk_id'])
                    audit.append({'chunk_id': hit['chunk_id'], 'document_id': hit['document_id'],
                        'same_b2_content': original is not None and original['text'] == hit['text'],
                        'same_document_url': DOCS[hit['document_id']]['source_url'] == hit['source_url'],
                        'payload_hash_fields': {k: v for k, v in hit.items() if 'hash' in k or k in ('retrieved_at', 'collected_at')}})
            ages = age_conditions(graph)
            values = [18, 19, 39, 40, None] if pid == 'POL_STG_ICN_001' else [None, 25, 65]
            if pid != 'POL_STG_ICN_001' and ages and '~' in str(ages[0].get('value')):
                lo, hi = map(int, ages[0]['value'].split('~'))
                values = [a for a in (lo - 1, lo, hi, hi + 1, None) if a is None or 0 <= a <= 120]
            result['policies'][pid] = {'graph': graph, 'age_conditions': ages, 'source_audit': audit,
                'runs': [exercise(p, age) for age in values]}
        with TestClient(create_app(session_factory=Mock)) as catalog_client:
            catalog_response = catalog_client.get('/api/policies')
            result['catalog'] = {'http_status': catalog_response.status_code, 'body': catalog_response.json()}
        if args.hybrid and args.live:
            from types import SimpleNamespace as NS
            from database.hybrid_search import HybridSearchSession
            class HTTPReadClient:
                def get_collection(self, name):
                    size = qdrant('collections/' + name)['config']['params']['vectors']['size']
                    return NS(config=NS(params=NS(vectors=NS(size=size))))
                def query_points(self, collection_name, query, limit, query_filter, with_payload):
                    body = {'query': query, 'limit': limit, 'with_payload': with_payload}
                    if query_filter:
                        body['filter'] = query_filter.model_dump(exclude_none=True)
                    found = qdrant('collections/' + collection_name + '/points/query', body)
                    return NS(points=[NS(**p) for p in found['points']])
            session = HybridSearchSession(client=HTTPReadClient(), runner=runner)
            result['hybrid'] = []
            for pid in ('POL_STG_ICN_001', 'POL_YC_024', 'POL_BOK_004'):
                found = session.search_policy_context(POLICIES[pid]['policy_name'], filters={'policy_id': pid}, k=10)
                result['hybrid'].append({'target': pid, 'result': found})
                if args.llm:
                    from backend.llm.generator import generate_answer
                    if os.environ.get('OPENAI_API_KEY'):
                        for age in ((18, 19, 39, 40, None) if pid == 'POL_STG_ICN_001' else (None,)):
                            result['llm_calls'] += 1
                            request = AskRequest(question=POLICIES[pid]['policy_name'] + ' 지원할 수 있나요? 공식 신청 URL과 연령 근거를 알려주세요.',
                                profile={'age': age, 'region': '', 'income': None}, history=[], selected_policy_id=pid)
                            try:
                                response = run_ask_pipeline(request, search_fn=lambda *a, **kw: found,
                                    check_fn=check_eligibility, generate_fn=generate_answer)
                                result.setdefault('llm', []).append({'policy_id': pid, 'age': age, 'response': response.model_dump()})
                            except Exception as exc:
                                result.setdefault('llm', []).append({'policy_id': pid, 'age': age, 'error_type': type(exc).__name__})
                    else:
                        result['llm_status'] = 'BLOCKED: OPENAI_API_KEY unavailable'
            session.close()
        result['execution_status'] = 'COMPLETED'
        result['status'] = 'REQUIRES_REVIEW' if result['llm_calls'] else 'PASS'
    except Exception as exc:
        # Never serialize driver/auth exception messages.
        import traceback
        result.update(status='BLOCKED', error_type=type(exc).__name__,
            error_frames=[{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                          for f in traceback.extract_tb(exc.__traceback__)])
    finally:
        if driver:
            driver.close()
        if client:
            client.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'policies': len(result['policies']), 'error_type': result.get('error_type')}))
    if result['status'] == 'BLOCKED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
