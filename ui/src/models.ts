// Model attribution helpers. The engine round-robins the models from the
// `diagnosing` event across hypotheses IN ORDER (h1→models[0], h2→models[1],…),
// so a hypothesis's model is looked up by its creation index. Names are shown
// ONLY when real slugs arrived — never fabricated.

const KNOWN: Record<string, string> = {
  glm: 'GLM',
  deepseek: 'DeepSeek',
  kimi: 'Kimi',
  minimax: 'MiniMax',
  qwen: 'Qwen',
  llama: 'Llama',
};

// Prettify a real Fireworks slug: strip any path prefix and turn the "p"
// version separator back into a dot ("glm-5p2" -> "GLM 5.2"). Applied ONLY to
// slugs that actually came from the engine.
export function prettyModel(slug: string): string {
  const base = slug.split('/').pop() ?? slug;
  return base
    .split(/[-_]/)
    .filter(Boolean)
    .map((tok) => {
      const dotted = tok.replace(/(\d)p(\d)/g, '$1.$2');
      const lower = dotted.toLowerCase();
      if (KNOWN[lower]) return KNOWN[lower];
      if (/^[a-z]+$/.test(dotted)) return dotted.charAt(0).toUpperCase() + dotted.slice(1);
      return dotted.toUpperCase(); // tokens with digits: v4 -> V4, k2.6 -> K2.6
    })
    .join(' ');
}

// The prettified model for the hypothesis at `index`, or null if no real
// model list was received (so callers omit attribution silently).
export function modelForHypothesis(models: string[] | null, index: number): string | null {
  if (!models || index < 0 || index >= models.length) return null;
  return prettyModel(models[index]);
}
