# DG-MCP

Model Context Protocol server for DG-Lab Coyote 2.0 / 3.0.

Exposes the device as a set of MCP tools (`scan`, `connect`, `start`, `stop`, `adjust_strength`, `change_wave`, `burst`, `design_wave`, `emergency_stop`, ...) so any MCP-compatible LLM client (Claude Desktop, Continue, etc.) can drive a real Coyote over Bluetooth Low Energy from Node.js.

Built on `@dg-kit/*` (the shared TypeScript runtime that DG-Agent and DG-Chat also use).

## Status

`1.0.0-rc.0` — the v0.1.x Python implementation has been **archived** to the [`archive/0.x-py`](https://github.com/0xNullAI/DG-MCP/tree/archive/0.x-py) branch and remains installable from PyPI but no longer receives updates.

## Install

```bash
npm install -g dg-mcp
```

## Usage

```bash
dg-mcp [options]

Options:
  --waveforms <path>      Add a .pulse or .zip file at startup (repeatable)
  --waveforms-dir <dir>   Add every .pulse / .zip file in a directory
  --library-dir <dir>     Persist user-imported / AI-designed waveforms here
  --help, -h              Show help
  --version, -v           Show version

Environment:
  DG_MCP_WAVEFORMS        Colon-separated list of waveform paths
  DG_MCP_WAVEFORMS_DIR    Same as --waveforms-dir
  DG_MCP_LIBRARY_DIR      Same as --library-dir
```

## Claude Desktop config

```json
{
  "mcpServers": {
    "dg-lab": {
      "command": "npx",
      "args": ["-y", "dg-mcp", "--waveforms-dir", "/Users/you/wave-pack"]
    }
  }
}
```

## Tools

### Device control (from `@dg-kit/tools`)

| Tool | Purpose |
|---|---|
| `start` | Cold-start a channel: set initial strength + waveform in one go |
| `stop` | Stop one channel, or both if no `channel` argument |
| `adjust_strength` | Relative strength change, ±10 per step (rate-limited 2× / 5s) |
| `change_wave` | Swap the waveform without changing strength |
| `burst` | Briefly spike a channel to a target strength, then auto-restore (rate-limited 1× / 5s) |
| `design_wave` | Compose a new waveform from `ramp` / `hold` / `pulse` / `silence` segments and save it to the library |

### MCP-only

| Tool | Purpose |
|---|---|
| `scan` | Discover nearby Coyote devices |
| `connect` / `disconnect` | Manage the BLE link |
| `get_status` | Current connection / strength / wave / battery snapshot |
| `list_waveforms` | All available waveforms (built-in + imported) |
| `load_waveforms` | Import a `.pulse` file or `.zip` of `.pulse` files at runtime |
| `emergency_stop` | Strength → 0, all waves stopped, immediate |

## Safety notes

- Strength scale is **0-200**. Cold-start tools cap initial strength at 10. Always start low.
- Strength soft-limits default to 200 on connect; constrain via your own LLM client policy if needed.
- The device's physical wheel can override strength upward. Treat MCP as an _input_, not a hard ceiling.

## Requirements

- Node.js ≥ 20
- BLE-capable host (macOS, Linux/BlueZ, or Windows with a noble-supported adapter)
- DG-Lab Coyote 2.0 (D-LAB ESTIM…) or 3.0 (47L121…) device

## License

MIT — see [LICENSE](./LICENSE).

## Architecture

```
        ┌─────────────────────┐
        │ MCP-capable client  │  (Claude Desktop / Continue / ...)
        └──────────┬──────────┘
                   │ stdio (MCP)
        ┌──────────▼──────────┐
        │      dg-mcp         │  this repo
        ├─────────────────────┤
        │  src/server.ts      │  → translates tools → DeviceCommand
        │  src/coyote-device  │  → DeviceClient via @dg-kit/protocol + noble
        │  src/waveform-lib   │  → built-ins + .pulse/.zip + JSON persist
        │  src/noble-shim     │  → noble Characteristic → CharacteristicLike
        └──────────┬──────────┘
                   │ shared
        ┌──────────▼──────────┐
        │     @dg-kit/*       │  (core, protocol, tools, waveforms)
        └─────────────────────┘
                   │ BLE
        ┌──────────▼──────────┐
        │  Coyote 2.0 / 3.0   │
        └─────────────────────┘
```
