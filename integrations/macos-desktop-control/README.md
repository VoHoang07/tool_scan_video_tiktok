# macos-desktop-control

[中文文档](./README.zh-CN.md)

MCP server for native macOS desktop automation — screen, mouse, keyboard, window management, and mobile simulators.

No Docker. No virtual display. Controls your actual Mac desktop. AI operates in the background or the foreground — you choose.

## What's New in v3.1

**Smart screenshot compression** — screenshots are now compressed by default to prevent API "Input too long" errors on high-DPI displays (Retina, 4K).

| Preset | Max Width | Quality | Format | Typical Size |
|--------|-----------|---------|--------|-------------|
| `none` | original | 100 | PNG | 4-15 MB |
| `low` | 2048 px | 85 | JPEG | 300-500 KB |
| `medium` | 1280 px | 70 | JPEG | 100-400 KB |
| `high` | 800 px | 50 | JPEG | 30-150 KB |

Default is `medium`. Agent picks the level based on the task — or uses `none` for pixel-perfect work.

**Tile mode** — when full resolution is needed, split a screenshot into a grid. Agent fetches tiles one at a time, each small enough for the API.

New tool: `screenshot_tile` — fetch individual tiles from a tiled screenshot.

Compression also works on `sim_screenshot` and `emu_screenshot`.

### v3.0

Two operation modes. 30 tools (up from 13). Optional iOS/Android simulator control.

| Mode | How It Works | User Experience |
|------|-------------|-----------------|
| **Foreground** | cliclick + AppleScript (same as v2) | You watch the AI operate your screen |
| **Background** | CGEvent API via `CGEventPostToPid` | AI works in a target window — your focus stays untouched |

Add `target: { app: "Safari" }` to any supported tool. Coordinates become window-relative. The AI never steals your foreground.

## Quick Start

```bash
# 1. Install cliclick
brew install cliclick

# 2. Clone and install
git clone https://github.com/d-wwei/macos-desktop-control.git
cd macos-desktop-control
npm install

# 3. Add to your MCP client (example: Claude Code)
claude mcp add macos-desktop-control -- node /path/to/macos-desktop-control/src/index.js
```

Grant **Accessibility permission** to your terminal: System Settings → Privacy & Security → Accessibility.

## Features

### Foreground Mode (default)

All original v2 capabilities, unchanged.

- **Screen capture** — full screen, region, or specific display; with compression presets and tile mode
- **Mouse** — click (left/right/double/triple), move, drag, scroll, with modifier keys
- **Keyboard** — three typing modes (keystroke, cliclick, direct IME bypass), any key combo via AppleScript key codes
- **Window management** — list windows, focus by app/title, open apps
- **System** — run macOS Shortcuts workflows
- **Focus protection** — `app` parameter auto-refocuses the target before each action

### Background Mode (`target` parameter)

Add `target: { app: "AppName", title?: "WindowTitle" }` to operate without stealing focus.

| Tool | Background Behavior |
|------|-------------------|
| `screenshot` | Captures the target window via `screencapture -l<windowId>` |
| `click` | Sends CGEvent mouse events directly to the target PID |
| `type_text` | Pastes text via CGEvent Cmd+V to the target PID (saves/restores clipboard) |
| `key_press` | Sends CGEvent keyboard events to the target PID |
| `scroll` | Sends CGEvent scroll wheel events to the target PID |
| `drag` | Flash technique: briefly activates target → drags → restores your foreground app |
| `open_app` | Launches via `open -g` (background, no focus steal) |
| `list_windows` | Returns CGWindowID + PID for each window (used internally for targeting) |

When `target` is set, x/y coordinates are **window-relative** — (0,0) is the top-left corner of the target window. The server converts to screen-absolute coordinates internally.

### iOS Simulator (requires Xcode)

Tools register automatically when `xcrun simctl` is detected.

| Tool | Function |
|------|----------|
| `sim_list_devices` | List simulators and their status |
| `sim_boot` / `sim_shutdown` | Start or stop a simulator |
| `sim_screenshot` | Capture at native device resolution |
| `sim_tap` | Tap at iOS-space coordinates (auto-mapped to Simulator window) |
| `sim_swipe` | Swipe gesture with duration control |
| `sim_type` | Type text into the simulator |
| `sim_open_url` | Open a URL on the simulator |
| `sim_install_app` | Install a .app bundle |

### Android Emulator (requires adb)

Tools register automatically when `adb` is detected. All operations are fully background — adb never steals focus.

| Tool | Function |
|------|----------|
| `emu_list_devices` | List connected devices/emulators |
| `emu_screenshot` | Capture via `adb exec-out screencap` |
| `emu_tap` | Tap at device coordinates |
| `emu_swipe` | Swipe with duration control |
| `emu_type` | Type text |
| `emu_key` | Send keyevent (HOME, BACK, ENTER, etc.) |
| `emu_open_url` | Open a URL via intent |
| `emu_install_app` | Install an APK |

## Usage Examples

### Background screenshot of a specific app

```json
{ "target": { "app": "Safari" } }
```

Captures Safari's window even if it's behind other windows. Your foreground stays untouched.

### Background click in a window

```json
{ "x": 100, "y": 200, "target": { "app": "Safari", "title": "GitHub" } }
```

Clicks at position (100, 200) relative to the Safari window titled "GitHub". No focus change.

### Background text input

```json
{ "text": "hello world", "target": { "app": "Notes" } }
```

Types into Notes via clipboard paste (CGEvent Cmd+V). Clipboard is saved and restored.

### Compressed screenshot (default behavior in v3.1)

```json
{ "target": { "app": "Chrome" } }
```

Returns a 1280px-wide JPEG (~150KB) instead of a raw PNG (~5MB). Works out of the box.

### High-res screenshot with no compression

```json
{ "target": { "app": "Chrome" }, "compression": "none" }
```

Returns the raw PNG — same as v3.0 behavior.

### Custom compression

```json
{ "target": { "app": "Chrome" }, "compression": "low", "maxWidth": 1920, "quality": 90 }
```

Explicit `maxWidth`/`quality`/`format` override the preset values.

### Tile mode for full-resolution inspection

```json
{ "target": { "app": "Chrome" }, "tile": { "rows": 2, "cols": 2 } }
```

Returns a manifest with tile metadata. Then fetch individual tiles:

```json
{ "id": "tiles-1711929600000-abc123", "index": 0, "compression": "medium" }
```

### Focus-safe foreground operation

```json
{ "text": "hello", "app": "TextEdit", "mode": "direct" }
```

Writes text directly via AppleScript — bypasses input method entirely.

## Prerequisites

- macOS (tested on Sequoia 15.x and Tahoe 26.x)
- Node.js 18+
- [cliclick](https://github.com/BlueM/cliclick): `brew install cliclick`
- **Accessibility permission** for your terminal app
- Optional: Xcode (for iOS simulator tools)
- Optional: Android SDK with adb (for Android emulator tools)

## Client Configuration

Uses **stdio transport**. Configuration is the same across all MCP clients.

<details>
<summary><b>Claude Code</b></summary>

```bash
# Project scope
claude mcp add macos-desktop-control -- node /path/to/macos-desktop-control/src/index.js

# Global scope
claude mcp add macos-desktop-control -s user -- node /path/to/macos-desktop-control/src/index.js
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "macos-desktop-control": {
      "command": "node",
      "args": ["/path/to/macos-desktop-control/src/index.js"]
    }
  }
}
```
</details>

<details>
<summary><b>OpenAI Codex CLI</b></summary>

`.codex/mcp.json`:

```json
{
  "mcpServers": {
    "macos-desktop-control": {
      "command": "node",
      "args": ["/path/to/macos-desktop-control/src/index.js"]
    }
  }
}
```
</details>

<details>
<summary><b>Gemini CLI</b></summary>

`~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "macos-desktop-control": {
      "command": "node",
      "args": ["/path/to/macos-desktop-control/src/index.js"]
    }
  }
}
```
</details>

<details>
<summary><b>Cursor</b></summary>

`.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "macos-desktop-control": {
      "command": "node",
      "args": ["/path/to/macos-desktop-control/src/index.js"]
    }
  }
}
```
</details>

<details>
<summary><b>VS Code (GitHub Copilot)</b></summary>

`.vscode/mcp.json`:

```json
{
  "servers": {
    "macos-desktop-control": {
      "command": "node",
      "args": ["/path/to/macos-desktop-control/src/index.js"]
    }
  }
}
```
</details>

## Architecture

```
                          ┌─────────────────────────────────┐
                          │   MCP Server (stdio transport)  │
                          └──────────┬──────────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
     Foreground Mode          Background Mode        Simulator Mode
              │                      │                      │
   ┌──────────┴──────────┐  ┌───────┴────────┐   ┌────────┴────────┐
   │ cliclick (mouse)    │  │ CGEvent API    │   │ xcrun simctl    │
   │ osascript (keyboard)│  │ via JXA bridge │   │ (iOS)           │
   │ screencapture       │  │ CGEventPost-   │   │                 │
   │ shortcuts CLI       │  │   ToPid(pid)   │   │ adb             │
   └─────────────────────┘  │ screencapture  │   │ (Android)       │
                             │   -l<windowId> │   └─────────────────┘
                             └────────────────┘
```

**Background mode internals:**
1. `CGWindowListCopyWindowInfo` via JXA enumerates windows with CGWindowID, PID, and bounds
2. Window-relative coordinates are converted to screen-absolute using bounds
3. `CGEventPostToPid` sends mouse/keyboard/scroll events directly to the target process
4. `screencapture -l<windowId>` captures a specific window without requiring focus

## Compared to Alternatives

| Solution | Platform | Background Mode | Simulator Support | Real Desktop |
|----------|----------|----------------|-------------------|-------------|
| **This project** | **macOS** | **Yes (CGEvent)** | **iOS + Android** | **Yes** |
| Anthropic Computer Use | Linux | No | No | No (virtual) |
| MCPControl | Windows | No | No | Yes |
| Playwright MCP | Cross-platform | Partial | No | Browser only |
| PyAutoGUI MCP servers | Cross-platform | No | No | Yes |

### Why macOS-native

- **Background operation** — CGEvent API posts events to a target PID without touching focus. PyAutoGUI and cliclick both require the window to be foreground.
- **Focus-stealing prevention** — `app` parameter + `ensureAppFocus()` handles the approval-dialog problem that all MCP clients share.
- **IME bypass** — `direct` mode writes text through AppleScript, skipping the input method entirely. PyAutoGUI's `typewrite` only handles ASCII.
- **Simulator integration** — iOS and Android simulators controlled through the same MCP interface. No separate tools needed.
- **Lightweight** — cliclick (one brew package) + built-in macOS tools. No Python runtime, no ONNX, no heavy dependencies.

### When to choose a cross-platform solution

- You need Windows or Linux support
- You need OCR-based element detection
- Background operation is not a requirement for your workflow

## Update Management

This project integrates [update-kit](https://github.com/d-wwei/update-kit) for update orchestration with policy control, verification, and rollback.

Check for updates:

```bash
npx update-kit check --cwd /path/to/macos-desktop-control --json
```

Apply an update (git pull + syntax verification):

```bash
npx update-kit apply --cwd /path/to/macos-desktop-control
```

Rollback if something goes wrong:

```bash
npx update-kit rollback --cwd /path/to/macos-desktop-control
```

Configuration lives in `update.config.json`. State and audit logs are stored in `.update-kit/` (gitignored).

## License

MIT
