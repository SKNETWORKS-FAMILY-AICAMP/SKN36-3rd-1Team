"""Frozen-response and citation punctuation regression; no external LLM calls."""
import copy
import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FROZEN = json.loads((ROOT / 'docs/reports/assets/issue79-18-90-e2e-20261127/llm-live-frozen.json').read_text(encoding='utf-8'))['records']


@pytest.fixture(scope='module')
def verifier():
    return runpy.run_path(str(ROOT / 'tests/manual/verify_issue79_18_90_llm_frozen_release.py'))


@pytest.mark.parametrize('record', FROZEN, ids=[r['id'] for r in FROZEN])
def test_frozen_automatic_results_are_reproducible(verifier, record):
    # Independent expectations: the original, unchanged frozen JSON.
    assert verifier['automatic_checks'](record, record['response']) == record['automatic']


@pytest.mark.parametrize('suffix', ['.', '[1].', ' [1].', '[1][2].', ''])
def test_both_agreed_phrases_allow_terminal_citations(verifier, suffix):
    record = next(r for r in FROZEN if r['id'] == 'sejong-committee-link')
    payload = copy.deepcopy(record['response'])
    payload['answer'] = '\n'.join(verifier[name].rstrip('.') + suffix for name in ('MISSING_LINK', 'UNKNOWN_RECEPTION'))
    result = verifier['automatic_checks'](record, payload)
    assert result['agreed_missing_link_phrase'] is True
    assert result['agreed_reception_phrase'] is True


@pytest.mark.parametrize('phrase', ['', '[1].', '공식 홈페이지를 확인하세요.'])
def test_absent_agreed_phrases_are_not_accepted(verifier, phrase):
    record = next(r for r in FROZEN if r['id'] == 'sejong-committee-link')
    payload = copy.deepcopy(record['response'])
    payload['answer'] = phrase
    result = verifier['automatic_checks'](record, payload)
    assert result['agreed_missing_link_phrase'] is False
    assert result['agreed_reception_phrase'] is False
