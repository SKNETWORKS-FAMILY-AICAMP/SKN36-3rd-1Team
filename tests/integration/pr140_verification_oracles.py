"""Independent test oracles. No Checker or Pipeline implementation imports."""

import re

CITATION = re.compile(r"\[(\d+)\]")


def citation_identity(source):
    # A missing page is different from an explicitly unknown (None) page.
    return tuple(source[key] for key in ('document_id', 'url', 'page_number'))


def bind_saved_completion(saved, contexts, policy_id):
    """Translate display numbers back to current Context source_ids by identity.

    Saved API responses do not contain chunk_id. Multiple matching chunks of the
    same document/URL/page are document-equivalent, not proof of original chunk.
    Missing/mismatched identities fail closed; never fall back to the number.
    """
    mapping = {}
    assigned = set()
    numbers = {int(n) for n in CITATION.findall(saved['answer'])}
    for number in sorted(numbers):
        assert 1 <= number <= len(saved['citations']), 'saved citation missing'
        source = saved['citations'][number - 1]
        identity = citation_identity(source)
        matches = [c for c in contexts if c['policy_id'] == policy_id
                   and citation_identity(c) == identity and c['source_id'] not in assigned]
        assert matches, f'original citation identity unavailable: {identity}'
        mapping[number] = matches[0]['source_id']
        assigned.add(mapping[number])
    return {'status': saved['status'],
            'answer': CITATION.sub(lambda m: f'[{mapping[int(m[1])]}]', saved['answer']),
            'used_source_ids': sorted(set(mapping.values())),
            'follow_up_questions': saved['follow_up_questions']}


def assert_replay_sources(saved, replayed, policy_id):
    """Compare each textual citation occurrence to the independently saved API."""
    assert {p['policy_id'] for p in replayed['policy_checks']} == {policy_id}
    def linked_sources(response):
        linked = []
        for number in map(int, CITATION.findall(response['answer'])):
            assert 1 <= number <= len(response['citations']), 'citation missing'
            linked.append(citation_identity(response['citations'][number - 1]))
        return linked
    assert linked_sources(saved) == linked_sources(replayed), 'citation binding changed'
    assert sorted(map(citation_identity, saved['citations']), key=repr) == sorted(
        map(citation_identity, replayed['citations']), key=repr), 'citation metadata changed'


def expected_numeric_condition(rule, value):
    """Evaluate approved numeric bounds, independently of production functions."""
    if type(value) is not int or value < 0:
        return 'needs_check'
    if rule['subject'] == 'applicant' and value > 120:
        return 'needs_check'
    op = rule['operator']
    low, high = rule.get('lower'), rule.get('upper')
    if op == 'BETWEEN':
        okay = (low is None or low <= value) and (high is None or value <= high)
    elif op == 'LTE':
        okay = value <= high
    elif op == 'LT':
        okay = value < high
    elif op == 'GTE':
        okay = value >= low
    elif op == 'GT':
        okay = value > low
    else:
        raise AssertionError(f'unreviewed operator {op}')
    return 'eligible' if okay else 'ineligible'


def boundary_values(rule):
    values = [None]
    if rule['lower'] is not None:
        values += [rule['lower'] + d for d in (-1, 0, 1)]
    if rule['upper'] is not None:
        values += [rule['upper'] + d for d in (-1, 0, 1)]
    return list(dict.fromkeys(values))


def assert_age_outcomes(rule, value, direct, checker, api):
    assert direct['status'] == expected_numeric_condition(rule, value), 'individual age mismatch'
    def age_and_root(result):
        conditions = result['conditions']
        ages = [c['status'] for c in conditions if c['field'] == 'age']
        root = [c['status'] for c in conditions if c['field'] == 'eligibility'][-1]
        return ages, root
    for result, age_rule in ((checker, rule['checker_age_expected']), (api, rule['api_age_expected'])):
        ages, root = age_and_root(result)
        expected = expected_numeric_condition(rule, value) if age_rule == 'numeric' else age_rule
        assert ages == [expected], 'public age expectation mismatch'
        assert root == rule['root_expected'], 'root expectation mismatch'
        if root == 'needs_check':
            assert result.get('exclusion_reasons') is None
