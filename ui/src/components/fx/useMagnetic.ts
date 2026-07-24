// Distilled from Originkit "Magnetic Hover Button" (https://originkit.dev),
// same feel without the framer-motion dependency: the element leans toward a
// nearby pointer with a critically-damped spring and snaps home on leave.
// No-op under prefers-reduced-motion and on touch-only devices.
import { useEffect, useRef } from 'react';

const RANGE_PER_POINT = 18;
const MAX_PULL = 0.5;

export function useMagnetic<T extends HTMLElement>(magnet = 8) {
  const ref = useRef<T>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    if (window.matchMedia('(pointer: coarse)').matches) return;

    const pull = (magnet / 20) * MAX_PULL;
    const reach = magnet * RANGE_PER_POINT;
    let tx = 0, ty = 0, cx = 0, cy = 0;
    let raf: number | null = null;

    const step = () => {
      cx += (tx - cx) * 0.18;
      cy += (ty - cy) * 0.18;
      el.style.transform = Math.abs(cx) + Math.abs(cy) < 0.05 && tx === 0 && ty === 0
        ? ''
        : `translate(${cx.toFixed(2)}px, ${cy.toFixed(2)}px)`;
      if (Math.abs(tx - cx) > 0.05 || Math.abs(ty - cy) > 0.05) {
        raf = requestAnimationFrame(step);
      } else {
        raf = null;
      }
    };
    const kick = () => { if (raf == null) raf = requestAnimationFrame(step); };

    const onMove = (e: PointerEvent) => {
      const rect = el.getBoundingClientRect();
      const dx = e.clientX - (rect.left + rect.width / 2 - cx);
      const dy = e.clientY - (rect.top + rect.height / 2 - cy);
      const gapX = Math.max(0, Math.abs(dx) - rect.width / 2);
      const gapY = Math.max(0, Math.abs(dy) - rect.height / 2);
      const gap = Math.hypot(gapX, gapY);
      if (gap > reach) {
        tx = 0; ty = 0;
      } else {
        const falloff = 1 - gap / reach;
        tx = dx * pull * falloff;
        ty = dy * pull * falloff;
      }
      kick();
    };
    const onLeave = () => { tx = 0; ty = 0; kick(); };

    window.addEventListener('pointermove', onMove, { passive: true });
    document.addEventListener('pointerleave', onLeave);
    return () => {
      window.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerleave', onLeave);
      if (raf != null) cancelAnimationFrame(raf);
      el.style.transform = '';
    };
  }, [magnet]);

  return ref;
}
