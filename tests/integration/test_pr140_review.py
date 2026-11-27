"""PR #140: source identity and independent age-oracle regression tests.

All malformed values below are local test mutations; no production/DB writes.
"""
import copy
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import test_issue79_18_90_verification as verification
from pr140_verification_oracles import (
    assert_age_outcomes, assert_replay_sources, bind_saved_completion,
    boundary_values, expected_numeric_condition,
)

EVIDENCE = json.loads((verification.ROOT / 'docs/reports/issue79-18-90-e2e-20261126.json').read_text(encoding='utf-8'))
RUNS = EVIDENCE['llm']
SEARCH = {h['target']: h['result'] for h in EVIDENCE['hybrid']}


def replay(run, *, reverse=False):
    session = Mock()
    found = copy.deepcopy(SEARCH[run['policy_id']])
    if reverse:
        for p in found['policies']:
            p['evidence'].reverse()
            for rank, hit in enumerate(p['evidence'], 1):
                hit['rank'] = rank
    session.search_policy_context.return_value = found
    captured = []
    def generate(question, contexts, *args, **kwargs):
        captured.extend(contexts)
        return bind_saved_completion(run['response'], contexts, run['policy_id'])
    with TestClient(verification.create_app(session_factory=lambda: session,
            check_fn=verification.check_eligibility, generate_fn=generate)) as client:
        response = client.post('/api/ask', json={
            'question': '공식 신청 URL과 연령 근거를 알려주세요.',
            'profile': {'age': run['age'], 'region': '', 'income': None},
            'selected_policy_id': run['policy_id'], 'history': []})
    assert response.status_code == 200, response.text
    assert_replay_sources(run['response'], response.json(), run['policy_id'])
    return response.json(), captured


@pytest.mark.parametrize('run', RUNS, ids=[f"{r['policy_id']}-{r['age']}" for r in RUNS])
def test_saved_seven_original_sources(run):
    replay(run)


def sample():
    """sample_only: two documents of one fictitious policy, reversed source IDs."""
    contexts = [
        {'policy_id': 'sample_only_P', 'document_id': 'sample_only_A',
         'url': 'https://example.org/a', 'page_number': 2, 'chunk_id': 'sample_only_CA', 'source_id': 2},
        {'policy_id': 'sample_only_P', 'document_id': 'sample_only_B',
         'url': 'https://example.org/b', 'page_number': None, 'chunk_id': 'sample_only_CB', 'source_id': 1},
    ]
    saved = {'status': 'answered', 'answer': 'A [1], B [2].', 'follow_up_questions': [],
             'citations': [{k: c[k] for k in ('document_id', 'url', 'page_number')} for c in contexts]}
    return saved, contexts


@pytest.mark.parametrize('order', [False, True])
def test_display_renumbering_and_search_reordering(order):
    saved, contexts = sample()
    if order:
        contexts.reverse()
    completion = bind_saved_completion(saved, contexts, 'sample_only_P')
    assert completion['answer'] == 'A [2], B [1].'
    # Simulate valid API display renumbering, preserving occurrence identities.
    replayed = {'answer': completion['answer'], 'citations': list(reversed(saved['citations'])),
                'policy_checks': [{'policy_id': 'sample_only_P'}]}
    assert_replay_sources(saved, replayed, 'sample_only_P')


def test_real_search_order_change_preserves_original_citation():
    # Single-document welfare example remains available even after Top-5 reorder.
    replay(RUNS[-1], reverse=True)


@pytest.mark.parametrize('field,value', [
    ('document_id', 'sample_only_wrong_document'),
    ('url', 'https://example.org/wrong'),
    ('page_number', 99),
    ('policy_id', 'sample_only_other_policy'),
])
def test_binding_rejects_wrong_document_url_page_or_policy(field, value):
    saved, contexts = sample()
    contexts[0][field] = value
    with pytest.raises(AssertionError, match='identity unavailable'):
        bind_saved_completion(saved, contexts, 'sample_only_P')


@pytest.mark.parametrize('field,value', [
    ('document_id', 'sample_only_wrong_document'),
    ('url', 'https://example.org/wrong'),
    ('page_number', 99),
])
def test_unchanged_number_with_corrupt_metadata_is_detected(field, value):
    saved, _ = sample()
    replayed = {**copy.deepcopy(saved), 'policy_checks': [{'policy_id': 'sample_only_P'}]}
    replayed['citations'][0][field] = value
    with pytest.raises(AssertionError):
        assert_replay_sources(saved, replayed, 'sample_only_P')


def test_same_policy_document_swap_is_detected():
    saved, _ = sample()
    replayed = {**copy.deepcopy(saved), 'policy_checks': [{'policy_id': 'sample_only_P'}]}
    replayed['citations'].reverse()
    with pytest.raises(AssertionError, match='binding changed'):
        assert_replay_sources(saved, replayed, 'sample_only_P')


def test_mixed_policy_is_detected():
    saved, _ = sample()
    replayed = {**copy.deepcopy(saved), 'policy_checks': [{'policy_id': 'sample_only_other'}]}
    with pytest.raises(AssertionError):
        assert_replay_sources(saved, replayed, 'sample_only_P')


def test_missing_citation_and_missing_page_metadata_are_detected():
    saved, contexts = sample()
    broken = copy.deepcopy(saved)
    broken['citations'].pop()
    with pytest.raises(AssertionError, match='missing'):
        bind_saved_completion(broken, contexts, 'sample_only_P')
    del contexts[0]['page_number']
    with pytest.raises(KeyError):
        bind_saved_completion(saved, contexts, 'sample_only_P')


def test_53_expected_policies_and_actual_operators():
    rules = verification.AGE_RULES
    actual_ids = {r['policy_id'] for r in verification.CORRECTIONS['requires_condition_additions']
                  if verification.CONDITIONS[r['condition_id']]['type'] == 'AGE'}
    assert len(rules) == 53
    assert {r['policy_id'] for r in rules} == actual_ids
    assert {r['operator'] for r in rules} == {'BETWEEN'}
    assert {r['subject'] for r in rules} == {'applicant'}
    assert sum(r['checker_age_expected'] == 'numeric' for r in rules) == 7
    # Sentinel 120 is not a policy upper bound, so no synthetic upper+1 case.
    for rule in rules:
        if rule['upper'] is None:
            assert 121 not in boundary_values(rule)


@pytest.mark.parametrize('op,value,expected', [
    ('LTE', 24, 'eligible'), ('LTE', 25, 'ineligible'), ('LTE', None, 'needs_check'),
    ('LT', 24, 'ineligible'), ('GTE', 19, 'eligible'), ('GT', 19, 'ineligible'),
])
def test_independent_operator_semantics(op, value, expected):
    rule = {'operator': op, 'subject': 'child' if op in ('LT', 'LTE') else 'applicant',
            'lower': 19, 'upper': 24}
    assert expected_numeric_condition(rule, value) == expected


def test_actual_pol_gov_050_lte24_and_applicant_separation():
    condition = next(c for c in verification.CONDITIONS.values() if c['condition_id'] == 'COND_CHILD_AGE_LE_24M')
    assert any(r['source_id'] == 'POL_GOV_050' and r['target_id'] == condition['condition_id'] for r in verification.RELS)
    assert condition['operator'] == 'LTE'
    for months, expected in [(24, 'eligible'), (25, 'ineligible'), (None, 'needs_check')]:
        assert verification._condition(condition, {'age': 65, 'child_age_months': months})['status'] == expected
    assert verification._condition(condition, {'age': 24})['status'] == 'needs_check'


@pytest.mark.parametrize('mutation', [
    'below_lower', 'above_upper', 'lte_as_lt', 'gte_as_gt', 'unknown_eligible',
    'applicant_child_confusion', 'checker_and_api_same_wrong',
])
def test_independent_oracle_rejects_injected_wrong_decisions(mutation):
    rule = next(r for r in verification.AGE_RULES if r['policy_id'] == 'POL_STG_ICN_001')
    rule = copy.deepcopy(rule)
    value = rule['lower'] - 1 if mutation == 'below_lower' else rule['upper'] + 1
    if mutation == 'lte_as_lt':
        rule.update(operator='LTE', upper=24, subject='child'); value = 24
    elif mutation == 'gte_as_gt':
        rule.update(operator='GTE', lower=19); value = 19
    elif mutation in ('unknown_eligible', 'applicant_child_confusion'):
        value = None
    right = expected_numeric_condition(rule, value)
    wrong = 'ineligible' if right == 'eligible' else 'eligible'
    direct = {'status': wrong}
    correct_public = {'conditions': [{'field': 'age', 'status': 'needs_check'},
                                    {'field': 'eligibility', 'status': 'needs_check'}], 'exclusion_reasons': None}
    checker, api = copy.deepcopy(correct_public), copy.deepcopy(correct_public)
    if mutation == 'checker_and_api_same_wrong':
        direct['status'] = right
        for result in (checker, api):
            for c in result['conditions']:
                c['status'] = 'eligible'
        assert checker == api  # The old equality-only assertion would pass.
    with pytest.raises(AssertionError):
        assert_age_outcomes(rule, value, direct, checker, api)


def test_real_checker_comparison_mutation_is_detected(monkeypatch):
    from backend.eligibility import checker
    original = checker._condition
    def wrong(condition, profile):
        result = original(condition, profile)
        if condition['type'] == 'AGE' and profile.get('age') is not None:
            result['status'] = 'eligible'
        return result
    monkeypatch.setattr(checker, '_condition', wrong)
    monkeypatch.setattr(verification, '_condition', wrong)
    rule = next(r for r in verification.AGE_RULES if r['policy_id'] == 'POL_STG_ICN_001')
    with pytest.raises(AssertionError, match='individual age mismatch'):
        verification.test_corrected_applicant_boundaries_through_api(rule)
