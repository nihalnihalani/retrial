import { defineTool } from '@copilotkit/runtime/v2';
import { z } from 'zod';

const MAX_RUNS = 10;
export type FetchLike = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}
function text(value: unknown, limit = 240): string | null {
  return typeof value === 'string' ? value.slice(0, limit) : null;
}
function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
function bool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}
async function getJson(url: string, fetchImpl: FetchLike): Promise<unknown> {
  const response = await fetchImpl(url, {
    method: 'GET',
    headers: { accept: 'application/json' },
    signal: AbortSignal.timeout(2_500),
  });
  if (!response.ok) throw new Error(`engine returned HTTP ${response.status}`);
  return response.json() as Promise<unknown>;
}
function safeError(error: unknown): string {
  if (error instanceof DOMException && error.name === 'TimeoutError') {
    return 'engine request timed out';
  }
  if (error instanceof Error && /^engine returned HTTP \d{3}$/.test(error.message)) {
    return error.message;
  }
  return 'engine is unreachable';
}
function health(value: unknown) {
  const body = record(value);
  if (!body) return null;
  const pool = record(body.pool) ?? {};
  const config = record(body.config) ?? {};
  return {
    status: text(body.status, 40),
    running: bool(body.running),
    testName: text(body.test_name),
    pool: {
      available: number(pool.available),
      live: number(pool.live),
      prewarming: bool(pool.prewarming),
      error: text(pool.error),
    },
    config: {
      threshold: number(config.threshold),
      isolation: text(config.isolation, 40),
      preflightOk: bool(config.preflight_ok),
      poolBackend: text(config.pool_backend, 80),
      promoteGate: bool(config.promote_gate),
    },
  };
}
function preflight(value: unknown) {
  const body = record(value);
  if (!body) return null;
  if (body.status === 'pending') {
    return { status: 'pending', ok: null, liveChecked: null, checks: [] };
  }
  const checks = Array.isArray(body.checks)
    ? body.checks.slice(0, 20).flatMap((value) => {
        const item = record(value);
        return item ? [{
          name: text(item.name, 80),
          status: text(item.status, 20),
          detail: text(item.detail),
        }] : [];
      })
    : [];
  return {
    status: 'complete',
    ok: bool(body.ok),
    liveChecked: bool(body.live_checked),
    poolDegradedSeen: body.pool_degraded_seen != null,
    checks,
  };
}

export async function getEngineStatus(
  engineUrl: string,
  fetchImpl: FetchLike = fetch,
) {
  const [healthResult, preflightResult] = await Promise.allSettled([
    getJson(`${engineUrl}/health`, fetchImpl),
    getJson(`${engineUrl}/preflight`, fetchImpl),
  ]);
  const errors: string[] = [];
  if (healthResult.status === 'rejected') errors.push(`health: ${safeError(healthResult.reason)}`);
  if (preflightResult.status === 'rejected') errors.push(`preflight: ${safeError(preflightResult.reason)}`);
  return {
    reachable: healthResult.status === 'fulfilled',
    health: healthResult.status === 'fulfilled' ? health(healthResult.value) : null,
    preflight: preflightResult.status === 'fulfilled' ? preflight(preflightResult.value) : null,
    errors,
  };
}

export function summarizeRuns(value: unknown) {
  const body = record(value);
  if (!body || !Array.isArray(body.runs)) return [];
  return body.runs.slice(0, MAX_RUNS).flatMap((value) => {
    const run = record(value);
    if (!run || typeof run.id !== 'string') return [];
    return [{
      id: run.id.slice(0, 128),
      kind: text(run.kind, 40),
      testName: text(run.test_name),
      verdict: text(run.verdict, 120),
      originalFlakeRate: number(run.orig_flake_rate),
      finalFlakeRate: number(run.final_flake_rate),
      winnerModel: text(run.winner_model, 160),
      braintrustUrl: text(run.braintrust_url, 500),
      startedAt: number(run.started_at),
      finishedAt: number(run.finished_at),
    }];
  });
}

export async function getRecentRuns(
  engineUrl: string,
  limit = 5,
  fetchImpl: FetchLike = fetch,
) {
  const safeLimit = Math.min(
    Math.max(Number.isSafeInteger(limit) ? limit : 5, 1),
    MAX_RUNS,
  );
  try {
    const body = await getJson(`${engineUrl}/runs?limit=${safeLimit}`, fetchImpl);
    return { reachable: true, runs: summarizeRuns(body), error: null };
  } catch (error) {
    return { reachable: false, runs: [], error: safeError(error) };
  }
}

export function createEngineTools(engineUrl: string) {
  return [
    defineTool({
      name: 'get_engine_status',
      description: 'Read Retrial engine health and preflight. Never changes a run.',
      parameters: z.object({}),
      execute: async () => getEngineStatus(engineUrl),
    }),
    defineTool({
      name: 'get_recent_runs',
      description: 'Read up to 10 recent completed run receipts. Never changes history.',
      parameters: z.object({
        limit: z.number().int().min(1).max(MAX_RUNS).default(5),
      }),
      execute: async ({ limit }) => getRecentRuns(engineUrl, limit),
    }),
  ];
}
