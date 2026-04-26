/**
 * MCP server: turns `@dg-kit/tools` tool definitions into MCP tools and
 * routes their execution plans through `NobleCoyoteDevice`.
 */

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  type Tool,
} from '@modelcontextprotocol/sdk/types.js';
import {
  createDefaultToolRegistry,
  createSlidingWindowRateLimitPolicy,
  type ToolRegistry,
} from '@dg-kit/tools';
import type { ToolCall, ToolExecutionPlan } from '@dg-kit/core';
import type { NobleCoyoteDevice } from './coyote-device.js';
import type { NodeWaveformLibrary } from './waveform-library.js';

export interface DgMcpServerOptions {
  device: NobleCoyoteDevice;
  waveformLibrary: NodeWaveformLibrary;
  /** Sliding-window cap (ms) for per-tool rate limits. Default 5000. */
  rateLimitWindowMs?: number;
}

export function createDgMcpServer(options: DgMcpServerOptions): Server {
  const policy = createSlidingWindowRateLimitPolicy({
    windowMs: options.rateLimitWindowMs ?? 5_000,
    caps: {
      adjust_strength: 2,
      burst: 1,
      design_wave: 1,
    },
  });

  const registry: ToolRegistry = createDefaultToolRegistry({
    waveformLibrary: options.waveformLibrary,
    rateLimitPolicy: policy,
  });

  const server = new Server(
    {
      name: 'dg-mcp',
      version: '1.0.0-rc.0',
    },
    {
      capabilities: {
        tools: {},
      },
    },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => {
    const definitions = await registry.listDefinitions();
    const builtIn: Tool[] = definitions.map((def) => ({
      name: def.name,
      description: def.description,
      inputSchema: def.parameters as Tool['inputSchema'],
    }));

    // Plus MCP-specific tools that don't go through the registry:
    builtIn.push(
      {
        name: 'scan',
        description: '扫描附近的 Coyote 设备。返回包含 address / name / version 的列表。',
        inputSchema: {
          type: 'object',
          properties: {
            timeoutMs: {
              type: 'integer',
              minimum: 500,
              maximum: 30_000,
              description: '扫描时长（毫秒），默认 5000。',
            },
          },
        },
      },
      {
        name: 'connect',
        description: '连接到指定的 Coyote 设备。需先调用 scan 获取地址。',
        inputSchema: {
          type: 'object',
          properties: {
            address: {
              type: 'string',
              description: 'BLE 地址（aa:bb:cc:dd:ee:ff）',
            },
          },
          required: ['address'],
        },
      },
      {
        name: 'disconnect',
        description: '断开当前 Coyote 连接。',
        inputSchema: { type: 'object', properties: {} },
      },
      {
        name: 'get_status',
        description: '查询当前设备状态：连接状态 / 强度 / 波形 / 电池。',
        inputSchema: { type: 'object', properties: {} },
      },
      {
        name: 'list_waveforms',
        description: '列出当前波形库中的所有波形（内置 + 已导入）。',
        inputSchema: { type: 'object', properties: {} },
      },
      {
        name: 'load_waveforms',
        description: '从指定路径加载 .pulse 文件或包含 .pulse 的 .zip。',
        inputSchema: {
          type: 'object',
          properties: {
            path: { type: 'string', description: '绝对文件路径' },
          },
          required: ['path'],
        },
      },
      {
        name: 'emergency_stop',
        description: '紧急停止：强度归零，所有波形停止。',
        inputSchema: { type: 'object', properties: {} },
      },
    );

    return { tools: builtIn };
  });

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    const safeArgs = (args ?? {}) as Record<string, unknown>;

    try {
      // MCP-specific tools handled here directly.
      switch (name) {
        case 'scan': {
          const timeoutMs = typeof safeArgs.timeoutMs === 'number' ? safeArgs.timeoutMs : 5_000;
          const results = await options.device.scan(timeoutMs);
          return jsonResult({ devices: results });
        }
        case 'connect': {
          const address = String(safeArgs.address ?? '');
          if (!address) throw new Error('connect 需要 address 参数');
          await options.device.connect(address);
          const state = await options.device.getState();
          return jsonResult({ ok: true, state });
        }
        case 'disconnect': {
          await options.device.disconnect();
          return jsonResult({ ok: true });
        }
        case 'get_status': {
          const state = await options.device.getState();
          return jsonResult(state);
        }
        case 'list_waveforms': {
          const waveforms = await options.waveformLibrary.list();
          return jsonResult({
            waveforms: waveforms.map((w) => ({
              id: w.id,
              name: w.name,
              description: w.description,
              frameCount: w.frames.length,
            })),
          });
        }
        case 'load_waveforms': {
          const path = String(safeArgs.path ?? '');
          if (!path) throw new Error('load_waveforms 需要 path 参数');
          const result = await options.waveformLibrary.importPath(path);
          return jsonResult(result);
        }
        case 'emergency_stop': {
          await options.device.emergencyStop();
          return jsonResult({ ok: true });
        }
      }

      // Registry-backed tools (start / stop / adjust_strength / change_wave / burst / design_wave / timer).
      const toolCall: ToolCall = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        name,
        args: safeArgs,
      };
      const plan: ToolExecutionPlan = await registry.resolve(toolCall);

      if (plan.type === 'device') {
        const result = await options.device.execute(plan.command);
        return jsonResult({ ok: true, state: result.state });
      }
      if (plan.type === 'inline') {
        return { content: [{ type: 'text', text: plan.output }] };
      }
      // 'timer' isn't applicable to a stateless MCP server — surface a hint.
      return jsonResult({
        ok: false,
        reason: 'timer 工具在 MCP 模式下不受支持，请改用客户端侧的延时机制',
      });
    } catch (e) {
      const reason = e instanceof Error ? e.message : String(e);
      return {
        content: [{ type: 'text', text: JSON.stringify({ ok: false, reason }) }],
        isError: true,
      };
    }
  });

  return server;
}

function jsonResult(payload: unknown): { content: Array<{ type: 'text'; text: string }> } {
  return {
    content: [{ type: 'text', text: JSON.stringify(payload) }],
  };
}

export async function runStdioServer(options: DgMcpServerOptions): Promise<void> {
  const server = createDgMcpServer(options);
  const transport = new StdioServerTransport();
  await server.connect(transport);
}
