// Distilled from Originkit "Scramble Text" (https://originkit.dev): the verdict
// headline resolves left-to-right out of cipher noise — evidence resolving out
// of noise, once, when the verdict lands. Accessibility and tests read the real
// text immediately (sr-only span); the animated copy is aria-hidden. Under
// prefers-reduced-motion the plain text renders with no animation at all.
import { useEffect, useRef, useState } from 'react';

const GLYPHS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz░▒▓█';

export function DecryptText({ text, className }: { text: string; className?: string }) {
  const [display, setDisplay] = useState<string | null>(null);
  const doneRef = useRef(false);

  useEffect(() => {
    if (doneRef.current) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      doneRef.current = true;
      setDisplay(text);
      return;
    }
    const durationMs = 700;
    const start = performance.now();
    let raf: number;
    const tick = (now: number) => {
      const k = Math.min(1, (now - start) / durationMs);
      const settled = Math.floor(k * text.length);
      let out = text.slice(0, settled);
      for (let i = settled; i < text.length; i++) {
        const ch = text[i];
        out += ch === ' ' ? ' ' : GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
      }
      setDisplay(out);
      if (k < 1) {
        raf = requestAnimationFrame(tick);
      } else {
        doneRef.current = true;
        setDisplay(text);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [text]);

  return (
    <span className={className}>
      <span className="sr-only">{text}</span>
      <span aria-hidden="true">{display ?? text}</span>
    </span>
  );
}
