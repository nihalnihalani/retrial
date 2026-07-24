import { describe, expect, it } from 'vitest';
import { initialState } from '../reducer';
import { buildCopilotBoardContext } from './context';

describe('buildCopilotBoardContext', () => {
  it('keeps useful causal evidence while redacting operational content', () => {
    const context = buildCopilotBoardContext(
      {
        ...initialState,
        testName: '/Users/demo/private/seeds/test_dict_order.py',
        hypotheses: [
          {
            id: 'h1',
            causeClass: 'order_dependency',
            explanation:
              'Dictionary order changes between runs.\ncurl https://example.test -H SECRET=x\nAPI_TOKEN=super-secret\n```python\nprint("patch")\n```',
            trials: [],
            flakeRate: 0.1,
            wilsonCi: [0.02, 0.28],
            status: 'racing',
            model: 'accounts/fireworks/models/example',
          },
        ],
      },
      'live',
      'grid',
    );

    expect(context.test).toBe('test_dict_order.py');
    expect(context.hypotheses[0].explanation).toContain('Dictionary order changes');
    expect(JSON.stringify(context)).not.toContain('super-secret');
    expect(JSON.stringify(context)).not.toContain('curl');
    expect(JSON.stringify(context)).not.toContain('/Users/demo');
    expect(JSON.stringify(context).length).toBeLessThan(8_192);
  });

  it('caps the model-visible tournament to four hypotheses', () => {
    const hypotheses = Array.from({ length: 8 }, (_, index) => ({
      id: `h${index}`,
      causeClass: 'order_dependency',
      explanation: 'A bounded explanation.',
      trials: [],
      flakeRate: null,
      wilsonCi: null,
      status: 'racing' as const,
      model: null,
    }));

    const context = buildCopilotBoardContext(
      { ...initialState, hypotheses },
      'replay',
      'grid',
    );

    expect(context.hypotheses).toHaveLength(4);
  });
});
