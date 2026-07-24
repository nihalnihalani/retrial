import { once } from 'node:events';
import type { AddressInfo } from 'node:net';

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_COPILOT_MODEL,
  FIREWORKS_BASE_URL,
  createRuntime,
  createRuntimeServer,
  getEngineStatus,
  getRecentRuns,
  resolveCopilotConfig,
  summarizeRuns,
  type FetchLike,
} from './server.js';

const servers: ReturnType<typeof createRuntimeServer>[] = [];

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) =>
        new Promise<void>((resolve) => {
          server.close(() => resolve());
        }),
    ),
  );
});

describe('resolveCopilotConfig', () => {
  it('uses explicit Copilot values before the Fireworks model list', () => {
    expect(
      resolveCopilotConfig({
        FIREWORKS_API_KEY: '  secret ',
        FIREWORKS_MODELS: ' accounts/fireworks/models/first, second ',
        COPILOT_MODEL: 'accounts/fireworks/models/copilot',
        COPILOT_RUNTIME_PORT: '4100',
        COPILOT_MAX_OUTPUT_TOKENS: '1200',
        RETRIAL_ENGINE_URL: 'http://localhost:9000/',
      }),
    ).toEqual({
      port: 4100,
      engineUrl: 'http://localhost:9000',
      fireworksApiKey: 'secret',
      model: 'accounts/fireworks/models/copilot',
      maxOutputTokens: 1200,
      configured: true,
    });
  });

  it('falls back safely for absent or malformed values', () => {
    const config = resolveCopilotConfig({
      COPILOT_RUNTIME_PORT: 'not-a-number',
      COPILOT_MAX_OUTPUT_TOKENS: '999999',
      RETRIAL_ENGINE_URL: 'file:///tmp/engine',
    });

    expect(config).toMatchObject({
      port: 4000,
      engineUrl: 'http://127.0.0.1:8000',
      model: DEFAULT_COPILOT_MODEL,
      maxOutputTokens: 4096,
      configured: false,
    });
    expect(FIREWORKS_BASE_URL).toBe(
      'https://api.fireworks.ai/inference/v1',
    );
  });

  it('uses the first configured Fireworks model when no Copilot override exists', () => {
    expect(
      resolveCopilotConfig({
        FIREWORKS_MODELS: ' , accounts/fireworks/models/glm-5p1, other',
      }).model,
    ).toBe('accounts/fireworks/models/glm-5p1');
  });
});

describe('read-only engine helpers', () => {
  it('summarizes only bounded, valid run receipts', () => {
    const long = 'x'.repeat(300);
    const runs = summarizeRuns({
      runs: [
        {
          id: 'run-1',
          kind: 'tournament',
          test_name: long,
          verdict: 'FIXED',
          orig_flake_rate: 0.48,
          final_flake_rate: 0,
          winner_model: 'glm',
          braintrust_url: 'https://example.test/run-1',
          started_at: 1,
          finished_at: 2,
          ignored_secret: 'do-not-forward',
        },
        null,
        { no_id: true },
      ],
    });

    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({
      id: 'run-1',
      testName: 'x'.repeat(240),
      verdict: 'FIXED',
      originalFlakeRate: 0.48,
      finalFlakeRate: 0,
    });
    expect(runs[0]).not.toHaveProperty('ignored_secret');
  });

  it('clamps recent-run limits and performs GET only', async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify({ runs: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ) as unknown as FetchLike;

    await getRecentRuns('http://engine.test', 999, fetchImpl);

    expect(fetchImpl).toHaveBeenCalledOnce();
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://engine.test/runs?limit=10',
      expect.objectContaining({ method: 'GET' }),
    );
  });

  it('returns partial status evidence when preflight is unavailable', async () => {
    const fetchImpl: FetchLike = async (input, init) => {
      expect(init?.method).toBe('GET');
      if (String(input).endsWith('/health')) {
        return new Response(
          JSON.stringify({
            status: 'ok',
            running: false,
            test_name: null,
            pool: { available: 2, live: 1, prewarming: false, error: null },
            config: {
              threshold: 0.05,
              isolation: 'sandbox',
              preflight_ok: true,
              pool_backend: 'fork',
              promote_gate: true,
            },
          }),
          { status: 200 },
        );
      }
      return new Response('no', { status: 503 });
    };

    const result = await getEngineStatus('http://engine.test', fetchImpl);

    expect(result.reachable).toBe(true);
    expect(result.health).toMatchObject({
      status: 'ok',
      pool: { available: 2, live: 1 },
      config: { preflightOk: true, poolBackend: 'fork' },
    });
    expect(result.preflight).toBeNull();
    expect(result.errors).toEqual(['preflight: engine returned HTTP 503']);
  });
});

describe('degraded runtime server', () => {
  it('disables CopilotKit telemetry by default before runtime construction', () => {
    const previous = process.env.COPILOTKIT_TELEMETRY_DISABLED;
    delete process.env.COPILOTKIT_TELEMETRY_DISABLED;
    try {
      expect(
        createRuntime(
          resolveCopilotConfig({ FIREWORKS_API_KEY: 'test-placeholder' }),
        ),
      ).not.toBeNull();
      expect(process.env.COPILOTKIT_TELEMETRY_DISABLED).toBe('true');
    } finally {
      if (previous === undefined) {
        delete process.env.COPILOTKIT_TELEMETRY_DISABLED;
      } else {
        process.env.COPILOTKIT_TELEMETRY_DISABLED = previous;
      }
    }
  });

  it('stays healthy and rejects Copilot requests without exposing config', async () => {
    const server = createRuntimeServer(resolveCopilotConfig({}));
    servers.push(server);
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const { port } = server.address() as AddressInfo;

    const health = await fetch(`http://127.0.0.1:${port}/healthz`);
    expect(health.status).toBe(200);
    expect(await health.json()).toEqual({
      status: 'degraded',
      configured: false,
      agent: 'retrial',
      model: DEFAULT_COPILOT_MODEL,
    });

    const proxiedHealth = await fetch(
      `http://127.0.0.1:${port}/api/copilotkit/healthz`,
    );
    expect(proxiedHealth.status).toBe(200);
    expect(await proxiedHealth.json()).toMatchObject({
      status: 'degraded',
      configured: false,
      agent: 'retrial',
    });

    const runtime = await fetch(`http://127.0.0.1:${port}/api/copilotkit`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    });
    expect(runtime.status).toBe(503);
    expect(await runtime.json()).toEqual({
      error: 'copilot_unavailable',
      message: 'Copilot runtime is not configured.',
    });
  });
});
