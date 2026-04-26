# CLAUDE.md

Guidance for Claude Code working in **DG-MCP** — the Model Context Protocol server for DG-Lab Coyote 2.0 / 3.0.

## Project Overview

DG-MCP is a single-package Node.js CLI published to npm as `dg-mcp`. It speaks MCP over stdio so any MCP-compatible LLM client (Claude Desktop, Continue, etc.) can drive a Coyote via Bluetooth Low Energy.

The v0.1.x implementation (Python + bleak + FastMCP) is **archived** on the [`archive/0.x-py`](https://github.com/0xNullAI/DG-MCP/tree/archive/0.x-py) branch. The current v1.x rewrite is TypeScript on top of [`@dg-kit/*`](https://github.com/0xNullAI/DG-Kit) and `@stoprocent/noble`.

## Repo Layout

```
src/
  cli.ts                 entry; --waveforms / --waveforms-dir / --library-dir, env vars, runs stdio server
  server.ts              MCP server: @dg-kit/tools defs → MCP tool schema, plus device-management tools
  coyote-device.ts       DeviceClient impl: scans noble, finds device by address, drives @dg-kit/protocol
  noble-shim.ts          @stoprocent/noble Characteristic → BluetoothRemoteGATTCharacteristicLike
  waveform-library.ts    fs-backed WaveformLibrary (built-ins + .pulse / .zip + JSON persist)
.github/workflows/
  ci.yml                 typecheck + build on PR
  publish.yml            npm publish on git tag (`v*`)
```

## Branch & PR Convention

- Default branch: `main`
- All changes go directly on `main` (small project, single-user surface)
- Use `archive/0.x-py` for any Python-version maintenance only
- Releases: tag a version on `main` with `git tag v1.0.x && git push --tags` → `publish.yml` pushes to npm using the `NPM_TOKEN` repo secret

## Commands

```bash
npm install
npm run build        # tsc -p tsconfig.json
npm run dev          # tsx src/cli.ts (hot reload during dev)
npm run typecheck    # tsc --noEmit
npm run start        # node dist/cli.js (after build)
node dist/cli.js --version
node dist/cli.js --help
```

## Test & Commit Workflow

Before commits:

1. `npm run typecheck` — clean
2. `npm run build` — `dist/` produced, shebang preserved on `cli.js`
3. Smoke test the CLI: `node dist/cli.js --version` and `--help` (sanity that stdio server boots)

> No vitest suite. The MCP surface is small enough that real-device testing via Claude Desktop covers it; the @dg-kit stack is already covered upstream.

Commit message style — conventional commits. PR description follows the same template as other DG repos.

## Releasing

```bash
# 1. Bump version in package.json (and update src/cli.ts and src/server.ts version strings)
# 2. Commit, push to main
# 3. Tag and push:
git tag v1.0.x
git push origin v1.0.x
# 4. .github/workflows/publish.yml runs npm publish --access public
```

Make sure `NPM_TOKEN` is configured under repo Settings → Secrets → Actions.

## Architecture Notes

- **Protocol code is `@dg-kit/protocol`**; this project only writes the noble shim that satisfies `BluetoothRemoteGATTCharacteristicLike`. The same V2 / V3 logic that DG-Agent and DG-Chat use runs unchanged.
- **Rate-limit policy**: `createSlidingWindowRateLimitPolicy({ windowMs: 5000, caps: { adjust_strength: 2, burst: 1, design_wave: 1 } })`. MCP has no notion of "turns" so a time window is the right model.
- **Tool list** = registry tools (`start` / `stop` / `adjust_strength` / `change_wave` / `burst` / `design_wave`) + MCP-only tools (`scan` / `connect` / `disconnect` / `get_status` / `list_waveforms` / `load_waveforms` / `emergency_stop`). The `timer` tool is registered but returns a "not supported in MCP" hint when invoked.
- **noble version**: `@stoprocent/noble` (active fork). If swapping to another noble fork, verify the async API (`writeAsync`, `subscribeAsync`, etc.) is preserved — the shim relies on it.

## Platform Notes

### macOS

First run triggers a Bluetooth permission prompt. Allow it.

### Linux

Noble needs raw BLE permission:

```bash
sudo setcap cap_net_raw+eip $(eval readlink -f $(which node))
```

### Windows

Use a noble-supported BLE adapter. WSL2 doesn't expose Bluetooth — run the CLI in native Windows Node.

## Sister Projects

| Project | Purpose |
|---|---|
| [DG-Kit](https://github.com/0xNullAI/DG-Kit) | Shared TypeScript runtime (consumed by this project) |
| [DG-Agent](https://github.com/0xNullAI/DG-Agent) | Browser AI controller |
| [DG-Chat](https://github.com/0xNullAI/DG-Chat) | Multi-user P2P room |

## Code Conventions

- TypeScript with `strict: true`, `noUncheckedIndexedAccess: true`
- ESM only (`"type": "module"`)
- `import type` for type-only imports
- No emojis in code or comments unless explicitly requested
