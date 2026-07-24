import { createServer, type Server, type ServerResponse } from 'node:http';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createOpenAI } from '@ai-sdk/openai';
import {
  BuiltInAgent,
  CopilotRuntime,
  createCopilotRuntimeHandler,
} from '@copilotkit/runtime/v2';
import { createCopilotNodeHandler } from '@copilotkit/runtime/v2/node';
import {
  BASE_PATH,
  DEFAULT_COPILOT_MODEL,
  FIREWORKS_BASE_URL,
  loadRootEnvironment,
  resolveCopilotConfig,
  type CopilotRuntimeConfig,
} from './config.js';
import {
  createEngineTools,
  getEngineStatus,
  getRecentRuns,
  summarizeRuns,
  type FetchLike,
} from './engineTools.js';
import { SYSTEM_PROMPT } from './prompt.js';

export {
  DEFAULT_COPILOT_MODEL,
  FIREWORKS_BASE_URL,
  getEngineStatus,
  getRecentRuns,
  resolveCopilotConfig,
  summarizeRuns,
};
export type { CopilotRuntimeConfig, FetchLike };

export function createRuntime(config: CopilotRuntimeConfig) {
  if (!config.fireworksApiKey) return null;

  // CopilotKit telemetry is opt-in for this local evidence navigator. Preserve
  // an explicit operator override, but otherwise disable it before runtime
  // construction so no board metadata leaves the machine unexpectedly.
  process.env.COPILOTKIT_TELEMETRY_DISABLED ??= 'true';

  const fireworks = createOpenAI({
    name: 'fireworks',
    apiKey: config.fireworksApiKey,
    baseURL: FIREWORKS_BASE_URL,
  });
  return new CopilotRuntime({
    agents: {
      retrial: new BuiltInAgent({
        model: fireworks.chat(config.model),
        prompt: SYSTEM_PROMPT,
        tools: createEngineTools(config.engineUrl),
        maxSteps: 3,
        temperature: 0.2,
        maxOutputTokens: config.maxOutputTokens,
      }),
    },
  });
}

function json(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  });
  response.end(JSON.stringify(body));
}

/** Construct without listening so importing this module never binds a port. */
export function createRuntimeServer(config: CopilotRuntimeConfig): Server {
  const runtime = createRuntime(config);
  const handler = runtime
    ? createCopilotNodeHandler(
        createCopilotRuntimeHandler({
          runtime,
          basePath: BASE_PATH,
          mode: 'single-route',
          cors: false,
          activateChannels: false,
        }),
      )
    : null;

  return createServer(async (request, response) => {
    const url = new URL(
      request.url ?? '/',
      `http://${request.headers.host ?? 'localhost'}`,
    );
    if (
      request.method === 'GET' &&
      (
        url.pathname === '/healthz' ||
        url.pathname === '/health' ||
        url.pathname === `${BASE_PATH}/healthz`
      )
    ) {
      json(response, 200, {
        status: config.configured ? 'ok' : 'degraded',
        configured: config.configured,
        agent: 'retrial',
        model: config.model,
      });
      return;
    }
    if (url.pathname === BASE_PATH && !handler) {
      json(response, 503, {
        error: 'copilot_unavailable',
        message: 'Copilot runtime is not configured.',
      });
      return;
    }
    if (url.pathname === BASE_PATH && handler) {
      try {
        await handler(request, response);
      } catch {
        if (!response.headersSent) {
          json(response, 502, {
            error: 'copilot_runtime_error',
            message: 'Copilot runtime request failed.',
          });
        } else if (!response.writableEnded) {
          response.end();
        }
      }
      return;
    }
    json(response, 404, { error: 'not_found' });
  });
}

export function startRuntimeServer(
  config = resolveCopilotConfig(),
): Promise<Server> {
  const server = createRuntimeServer(config);
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(config.port, '127.0.0.1', () => {
      server.off('error', reject);
      const state = config.configured
        ? 'ready'
        : 'degraded (missing FIREWORKS_API_KEY)';
      console.log(
        `[retrial-copilot] ${state} on http://127.0.0.1:${config.port}`,
      );
      resolve(server);
    });
  });
}

const entrypoint = process.argv[1]
  ? pathToFileURL(path.resolve(process.argv[1])).href
  : undefined;
if (entrypoint === import.meta.url) {
  loadRootEnvironment();
  void startRuntimeServer(resolveCopilotConfig()).catch(() => {
    console.error('[retrial-copilot] failed to start');
    process.exitCode = 1;
  });
}
