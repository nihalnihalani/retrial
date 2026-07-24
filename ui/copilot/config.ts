import path from 'node:path';
import { loadEnvFile } from 'node:process';

export const BASE_PATH = '/api/copilotkit';
export const DEFAULT_COPILOT_MODEL = 'accounts/fireworks/models/glm-5p2';
export const FIREWORKS_BASE_URL = 'https://api.fireworks.ai/inference/v1';

export interface CopilotRuntimeConfig {
  port: number;
  engineUrl: string;
  fireworksApiKey?: string;
  model: string;
  maxOutputTokens: number;
  configured: boolean;
}

export interface RuntimeEnvironment {
  COPILOT_RUNTIME_PORT?: string;
  RETRIAL_ENGINE_URL?: string;
  FIREWORKS_API_KEY?: string;
  FIREWORKS_MODELS?: string;
  COPILOT_MODEL?: string;
  COPILOT_MAX_OUTPUT_TOKENS?: string;
}

function integer(
  raw: string | undefined,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  if (!raw?.trim()) return fallback;
  const value = Number(raw);
  return Number.isSafeInteger(value)
    ? Math.min(Math.max(value, minimum), maximum)
    : fallback;
}

function engineUrl(raw: string | undefined): string {
  if (!raw?.trim()) return 'http://127.0.0.1:8000';
  try {
    const url = new URL(raw);
    if (!['http:', 'https:'].includes(url.protocol)) {
      return 'http://127.0.0.1:8000';
    }
    return url.toString().replace(/\/$/, '');
  } catch {
    return 'http://127.0.0.1:8000';
  }
}

export function resolveCopilotConfig(
  env: RuntimeEnvironment = process.env,
): CopilotRuntimeConfig {
  const fireworksApiKey = env.FIREWORKS_API_KEY?.trim() || undefined;
  const listedModel = env.FIREWORKS_MODELS
    ?.split(',')
    .map((value) => value.trim())
    .find(Boolean);
  return {
    port: integer(env.COPILOT_RUNTIME_PORT, 4000, 1, 65_535),
    engineUrl: engineUrl(env.RETRIAL_ENGINE_URL),
    fireworksApiKey,
    model:
      env.COPILOT_MODEL?.trim() ||
      listedModel ||
      DEFAULT_COPILOT_MODEL,
    maxOutputTokens: integer(
      env.COPILOT_MAX_OUTPUT_TOKENS,
      800,
      128,
      4_096,
    ),
    configured: Boolean(fireworksApiKey),
  };
}

export function loadRootEnvironment(fromDir = import.meta.dirname): string | null {
  const envPath = path.resolve(fromDir, '../../.env');
  try {
    loadEnvFile(envPath);
    return envPath;
  } catch (error) {
    const code =
      error !== null && typeof error === 'object' && 'code' in error
        ? error.code
        : undefined;
    if (code === 'ENOENT') return null;
    throw error;
  }
}
