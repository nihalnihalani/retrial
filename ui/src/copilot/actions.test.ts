import { describe, expect, it, vi } from 'vitest';
import { createCopilotUiActions } from './actions';

function setup(overrides: Partial<Parameters<typeof createCopilotUiActions>[0]> = {}) {
  const options: Parameters<typeof createCopilotUiActions>[0] = {
    mode: 'live',
    treeAvailable: false,
    treeLocked: false,
    runInProgress: false,
    promotionPending: false,
    setView: vi.fn(),
    setSeedLabel: vi.fn(),
    setObservatoryOpen: vi.fn(),
    setRunsOpen: vi.fn(),
    focusGo: vi.fn(),
    focusPromotion: vi.fn(),
    focusPanel: vi.fn(),
    ...overrides,
  };
  return { options, actions: createCopilotUiActions(options) };
}

describe('createCopilotUiActions', () => {
  it('prepares only allowlisted seeds and never receives a run callback', () => {
    const { options, actions } = setup();

    expect(actions.prepareTournament('../../private.py').ok).toBe(false);
    expect(options.setSeedLabel).not.toHaveBeenCalled();

    expect(actions.prepareTournament('test_dict_order.py')).toMatchObject({
      ok: true,
      readyToStart: true,
    });
    expect(options.setSeedLabel).toHaveBeenCalledWith('test_dict_order.py');
    expect(options.focusGo).toHaveBeenCalledOnce();
  });

  it('rejects unavailable tree navigation', () => {
    const { options, actions } = setup({ treeAvailable: false });

    expect(actions.setEvidenceView('tree').ok).toBe(false);
    expect(options.setView).not.toHaveBeenCalled();
  });

  it('cannot change the forced bisection view or prepare during a run', () => {
    const { options, actions } = setup({
      treeAvailable: true,
      treeLocked: true,
      runInProgress: true,
    });

    expect(actions.setEvidenceView('grid').ok).toBe(false);
    expect(actions.prepareTournament('test_dict_order.py').ok).toBe(false);
    expect(options.setView).not.toHaveBeenCalled();
    expect(options.setSeedLabel).not.toHaveBeenCalled();
  });

  it('focuses promotion without making a decision', () => {
    const blocked = setup({ promotionPending: false });
    expect(blocked.actions.focusPromotionReview().ok).toBe(false);
    expect(blocked.options.focusPromotion).not.toHaveBeenCalled();

    const pending = setup({ promotionPending: true });
    expect(pending.actions.focusPromotionReview().ok).toBe(true);
    expect(pending.options.focusPromotion).toHaveBeenCalledOnce();
  });
});
