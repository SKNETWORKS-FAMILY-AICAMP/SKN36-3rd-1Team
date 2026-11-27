import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import type { AskResponse } from '../api/types';
import { TurnView } from './TurnView';

it('renders all six cited sources and keeps source six linked to its own document', () => {
  // sample_only response; no real policy or user data.
  const response: AskResponse = {
    status: 'answered', answer: '근거 [1][2][3][4][5][6]',
    citations: Array.from({ length: 6 }, (_, index) => ({
      title: `가상 출처 ${index + 1}`, url: `https://example.org/sample/${index + 1}`,
      document_id: `DOC_SAMPLE_${index + 1}`, page_number: null,
    })),
    policy_checks: [], follow_up_questions: [], checked_at: null, is_sample: true,
  };
  const noop = () => {};
  const html = renderToStaticMarkup(
    <TurnView turn={{ id: 1, kind: 'qa', question: '가상 연령 질문', tags: [], state: 'done', response }}
      onFollowUp={noop} onRetry={noop} onDemoApply={noop} sampleMode={true} />,
  );
  for (let number = 1; number <= 6; number += 1) {
    expect(html).toContain(`href="https://example.org/sample/${number}"`);
    expect(html).toContain(`가상 출처 ${number}`);
  }
});
