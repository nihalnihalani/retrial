// Adapted from Originkit "Blinking Squares" (https://originkit.dev, vite/react
// variant) — trimmed to what this board needs: a quiet canvas grid of
// independently twinkling squares behind the empty-state hero. The motif is
// deliberate: it is the trial grid before any trial exists.
// Purely decorative — pointer-events none, aria-hidden by the caller, single
// static frame under prefers-reduced-motion.
import { useEffect, useRef } from 'react';

type Props = {
  gridSize?: number;
  fillPercent?: number;
  colors?: string[];
  twinkleSpeed?: number;
  opacity?: number;
  className?: string;
};

function parseColor(c: string): [number, number, number] {
  const s = c.trim();
  if (s.startsWith('#')) {
    const hex = s.slice(1);
    if (hex.length === 3) {
      return [
        parseInt(hex[0] + hex[0], 16),
        parseInt(hex[1] + hex[1], 16),
        parseInt(hex[2] + hex[2], 16),
      ];
    }
    return [
      parseInt(hex.slice(0, 2), 16),
      parseInt(hex.slice(2, 4), 16),
      parseInt(hex.slice(4, 6), 16),
    ];
  }
  return [148, 163, 184];
}

// Neutral by default: green and blue carry verdict meaning elsewhere on the
// board, and a decorative field of them dilutes that. The motif is the trial
// grid *before* any trial exists, so these squares are pending-cell greys.
export function BlinkingSquares({
  gridSize = 44,
  fillPercent = 58,
  colors = ['#3f3f46', '#52525b', '#71717a'],
  twinkleSpeed = 22,
  opacity = 0.4,
  className,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const palette = colors.map(parseColor);
    let raf: number | null = null;
    let size = { w: 0, h: 0 };
    let cells: Array<{ phase: number; rate: number; tint: number }> = [];
    const start = performance.now();

    const ensureCells = (n: number) => {
      if (cells.length === n) return;
      cells = Array.from({ length: n }, (_, i) => {
        const r = (k: number, o: number) => {
          const s = Math.sin(i * k + o) * 43758.5453;
          return s - Math.floor(s);
        };
        return { phase: r(12.9898, 78.233) * Math.PI * 2, rate: 0.6 + r(7.137, 33.71) * 0.8, tint: r(3.51, 5.91) };
      });
    };

    const draw = (now: number) => {
      const { w, h } = size;
      if (w <= 0 || h <= 0) return;
      const cellSize = Math.max(w, h) / Math.max(2, gridSize);
      const cols = Math.max(1, Math.ceil(w / cellSize));
      const rows = Math.max(1, Math.ceil(h / cellSize));
      ensureCells(cols * rows);
      ctx.clearRect(0, 0, w, h);
      const t = (now - start) / 1000;
      const speed = twinkleSpeed * 0.05;
      const fill = Math.max(0.1, Math.min(1, fillPercent / 100));
      const inset = (1 - fill) * 0.5;
      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          const cell = cells[y * cols + x];
          // Radial fade: dense at center, empty at edges, so the grid halos
          // the headline instead of wallpapering the card.
          const u = Math.hypot(x / (cols - 1) - 0.5, y / (rows - 1) - 0.5) * 2;
          const envelope = Math.max(0, 1 - u) ** 1.6;
          const osc = 0.5 + 0.5 * Math.sin(t * speed * cell.rate * Math.PI * 2 + cell.phase);
          const alpha = envelope * osc * opacity;
          if (alpha <= 0.004) continue;
          const [r, g, b] = palette[Math.min(palette.length - 1, Math.floor(cell.tint * palette.length))];
          ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
          ctx.fillRect(x * cellSize + cellSize * inset, y * cellSize + cellSize * inset, cellSize * fill, cellSize * fill);
        }
      }
    };

    const loop = (now: number) => {
      draw(now);
      raf = requestAnimationFrame(loop);
    };

    const resize = () => {
      const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
      const w = Math.max(1, container.clientWidth);
      const h = Math.max(1, container.clientHeight);
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      size = { w, h };
      cells = [];
      if (reduced) draw(performance.now());
    };

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(container);
    if (!reduced) raf = requestAnimationFrame(loop);
    return () => {
      ro.disconnect();
      if (raf != null) cancelAnimationFrame(raf);
    };
  }, [gridSize, fillPercent, colors, twinkleSpeed, opacity]);

  return (
    <div ref={containerRef} className={className} style={{ position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none' }}>
      <canvas ref={canvasRef} style={{ display: 'block', width: '100%', height: '100%' }} />
    </div>
  );
}
