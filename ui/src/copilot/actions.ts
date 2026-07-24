import type { ConnectionMode } from '../types';
import type { CopilotBoardView } from './context';

export const COPILOT_SEED_LABELS = ['test_dict_order.py', 'test_first_key.py'] as const;
export type CopilotSeedLabel = (typeof COPILOT_SEED_LABELS)[number];
export type BoardPanel = 'evidence' | 'observatory' | 'runs';

interface CopilotUiActionOptions {
  mode: ConnectionMode;
  treeAvailable: boolean;
  treeLocked: boolean;
  runInProgress: boolean;
  promotionPending: boolean;
  setView: (view: CopilotBoardView) => void;
  setSeedLabel: (seed: string) => void;
  setObservatoryOpen: (open: boolean) => void;
  setRunsOpen: (open: boolean) => void;
  focusGo: () => void;
  focusPromotion: () => void;
  focusPanel: (panel: BoardPanel) => void;
}

export function isCopilotSeedLabel(value: string): value is CopilotSeedLabel {
  return COPILOT_SEED_LABELS.some((seed) => seed === value);
}

export function createCopilotUiActions({
  mode,
  treeAvailable,
  treeLocked,
  runInProgress,
  promotionPending,
  setView,
  setSeedLabel,
  setObservatoryOpen,
  setRunsOpen,
  focusGo,
  focusPromotion,
  focusPanel,
}: CopilotUiActionOptions) {
  return {
    openBoardPanel(panel: BoardPanel) {
      if (panel === 'observatory') {
        setObservatoryOpen(true);
        setRunsOpen(false);
      } else if (panel === 'runs') {
        setObservatoryOpen(false);
        setRunsOpen(true);
      } else {
        setObservatoryOpen(false);
        setRunsOpen(false);
      }
      focusPanel(panel);
      return { ok: true, panel };
    },

    setEvidenceView(nextView: CopilotBoardView) {
      if (treeLocked && nextView !== 'tree') {
        return {
          ok: false,
          reason: 'Tree view is locked while bisection evidence is active.',
        };
      }
      if (nextView === 'tree' && !treeAvailable) {
        return {
          ok: false,
          reason: 'Tree view is only available for a bisection or when ?tree=1 is enabled.',
        };
      }
      setView(nextView);
      focusPanel('evidence');
      return { ok: true, view: nextView };
    },

    prepareTournament(test: string) {
      if (runInProgress) {
        return { ok: false, reason: 'A run is already in progress.' };
      }
      if (!isCopilotSeedLabel(test)) {
        return { ok: false, reason: 'That test is not in the calibrated seed allowlist.' };
      }
      setSeedLabel(test);
      setObservatoryOpen(false);
      setRunsOpen(false);
      focusGo();
      return {
        ok: true,
        test,
        readyToStart: mode === 'live',
        instruction:
          mode === 'live'
            ? 'The seed is selected. The human can press GO.'
            : 'The seed is selected, but GO remains unavailable outside live mode.',
      };
    },

    focusPromotionReview() {
      if (!promotionPending) {
        return { ok: false, reason: 'There is no promotion awaiting human review.' };
      }
      focusPromotion();
      return {
        ok: true,
        instruction: 'Promotion review focused. Only the human can approve or reject it.',
      };
    },
  };
}
