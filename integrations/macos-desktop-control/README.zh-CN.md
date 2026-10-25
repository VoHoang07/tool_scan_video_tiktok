# macos-desktop-control

[English](./README.md)

macOS 原生桌面控制 MCP Server — 屏幕、鼠标、键盘、窗口管理，以及移动端模拟器操控。

无需 Docker，无需虚拟桌面，直接控制你的 Mac 桌面。AI 可以在台前操作，也可以在台后悄悄干活。

## v3.1 更新

**智能截图压缩** — 截图默认自动压缩，彻底解决高分屏（Retina / 4K）下 API "Input too long" 报错。

| 级别 | 最大宽度 | 质量 | 格式 | 典型大小 |
|------|---------|------|------|---------|
| `none` | 原始 | 100 | PNG | 4-15 MB |
| `low` | 2048 px | 85 | JPEG | 300-500 KB |
| `medium` | 1280 px | 70 | JPEG | 100-400 KB |
| `high` | 800 px | 50 | JPEG | 30-150 KB |

默认 `medium`。Agent 根据任务需要自行选择压缩级别，也可以用 `none` 获取原始图。

**切图模式** — 需要高清全图时，把截图切成网格，Agent 按块获取，每块都不超限。

新工具：`screenshot_tile` — 按索引获取单块切片。

`sim_screenshot` 和 `emu_screenshot` 同样支持压缩。

### v3.0

两种操作模式。30 个工具（v2 是 13 个）。可选的 iOS / Android 模拟器支持。

| 模式 | 原理 | 体验 |
|------|------|------|
| **台前** | cliclick + AppleScript（同 v2） | 你看着 AI 操作你眼前的页面 |
| **台后** | CGEvent API，通过 `CGEventPostToPid` 直接向目标进程发事件 | AI 在后台干活，你的焦点纹丝不动 |

给任何支持的工具加上 `target: { app: "Safari" }`，坐标自动变成窗口相对坐标，AI 操作全程不碰你的前台窗口。

## 快速开始

```bash
# 1. 安装 cliclick
brew install cliclick

# 2. 克隆并安装
git clone https://github.com/d-wwei/macos-desktop-control.git
cd macos-desktop-control
npm install

# 3. 添加到 MCP 客户端（以 Claude Code 为例）
claude mcp add macos-desktop-control -- node /path/to/macos-desktop-control/src/index.js
```

记得给终端授权**辅助功能权限**：系统设置 → 隐私与安全性 → 辅助功能。

## 功能

### 台前模式（默认）

所有 v2 功能，完全不变。

- **屏幕截图** — 全屏、区域、指定显示器；支持压缩预设和切图模式
- **鼠标操作** — 点击（左/右/双击/三击）、移动、拖拽、滚动，支持修饰键
- **键盘输入** — 三种输入模式（keystroke / cliclick / 直接写入绕过输入法）、任意按键+修饰键组合
- **窗口管理** — 列出窗口、按应用/标题聚焦、打开应用
- **系统集成** — 运行 macOS 快捷指令
- **焦点保护** — `app` 参数在每次操作前自动切回目标应用

### 台后模式（`target` 参数）

加上 `target: { app: "应用名", title?: "窗口标题" }`，AI 就在后台操作，不抢焦点。

| 工具 | 台后行为 |
|------|---------|
| `screenshot` | 通过 `screencapture -l<windowId>` 截取目标窗口 |
| `click` | 通过 CGEvent 向目标 PID 发送鼠标事件 |
| `type_text` | 通过 CGEvent Cmd+V 粘贴文字到目标 PID（自动保存/恢复剪贴板） |
| `key_press` | 通过 CGEvent 向目标 PID 发送键盘事件 |
| `scroll` | 通过 CGEvent 向目标 PID 发送滚轮事件 |
| `drag` | Flash 技术：短暂激活目标窗口 → 拖拽 → 立刻恢复你的前台应用 |
| `open_app` | 通过 `open -g` 后台启动，不抢焦点 |
| `list_windows` | 返回每个窗口的 CGWindowID + PID（台后模式的寻址基础） |

设了 `target` 后，x/y 坐标是**窗口相对坐标** —— (0,0) 是目标窗口的左上角。服务器内部自动转换成屏幕绝对坐标。

### iOS 模拟器（需要 Xcode）

检测到 `xcrun simctl` 时自动注册。

| 工具 | 功能 |
|------|------|
| `sim_list_devices` | 列出所有模拟器及状态 |
| `sim_boot` / `sim_shutdown` | 启动 / 关闭模拟器 |
| `sim_screenshot` | 按设备原生分辨率截图 |
| `sim_tap` | 在 iOS 坐标空间点击（自动映射到 Simulator 窗口） |
| `sim_swipe` | 滑动手势，可控制时长 |
| `sim_type` | 向模拟器输入文字 |
| `sim_open_url` | 在模拟器中打开 URL |
| `sim_install_app` | 安装 .app 包 |

### Android 模拟器（需要 adb）

检测到 `adb` 时自动注册。adb 操作天然后台，不需要焦点。

| 工具 | 功能 |
|------|------|
| `emu_list_devices` | 列出已连接设备 / 模拟器 |
| `emu_screenshot` | 通过 `adb exec-out screencap` 截图 |
| `emu_tap` | 按设备坐标点击 |
| `emu_swipe` | 滑动，可控制时长 |
| `emu_type` | 输入文字 |
| `emu_key` | 发送按键事件（HOME / BACK / ENTER 等） |
| `emu_open_url` | 通过 intent 打开 URL |
| `emu_install_app` | 安装 APK |

## 用法示例

### 台后截取指定应用窗口

```json
{ "target": { "app": "Safari" } }
```

即使 Safari 被其他窗口遮住，也能截到。你的前台窗口不受影响。

### 台后点击窗口内某个位置

```json
{ "x": 100, "y": 200, "target": { "app": "Safari", "title": "GitHub" } }
```

在标题含 "GitHub" 的 Safari 窗口中，点击相对坐标 (100, 200)。不切焦点。

### 台后输入文字

```json
{ "text": "hello world", "target": { "app": "备忘录" } }
```

通过剪贴板粘贴（CGEvent Cmd+V）输入到备忘录。剪贴板会自动保存和恢复。

### 压缩截图（v3.1 默认行为）

```json
{ "target": { "app": "Chrome" } }
```

返回 1280px 宽的 JPEG（~150KB），而不是原始 PNG（~5MB）。开箱即用。

### 不压缩的高清截图

```json
{ "target": { "app": "Chrome" }, "compression": "none" }
```

返回原始 PNG — 和 v3.0 行为一样。

### 自定义压缩参数

```json
{ "target": { "app": "Chrome" }, "compression": "low", "maxWidth": 1920, "quality": 90 }
```

显式的 `maxWidth`/`quality`/`format` 会覆盖预设值。

### 切图模式：高清分块检查

```json
{ "target": { "app": "Chrome" }, "tile": { "rows": 2, "cols": 2 } }
```

返回切片清单（manifest）。然后按需获取单块：

```json
{ "id": "tiles-1711929600000-abc123", "index": 0, "compression": "medium" }
```

### 台前操作 + 输入法绕过

```json
{ "text": "你好", "app": "TextEdit", "mode": "direct" }
```

通过 AppleScript 直接写入，完全绕过输入法。

## 前置条件

- macOS（已测试 Sequoia 15.x、Tahoe 26.x）
- Node.js 18+
- [cliclick](https://github.com/BlueM/cliclick)：`brew install cliclick`
- 终端应用需要**辅助功能权限**
- 可选：Xcode（用于 iOS 模拟器工具）
- 可选：Android SDK + adb（用于 Android 模拟器工具）

## 客户端配置

使用 **stdio 传输**，各客户端配置格式一致。

<details>
<summary><b>Claude Code</b></summary>

```bash
# 项目级
claude mcp add macos-desktop-control -- node /path/to/macos-desktop-control/src/index.js

# 全局
claude mcp add macos-desktop-control -s user -- node /path/to/macos-desktop-control/src/index.js
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

`~/Library/Application Support/Claude/claude_desktop_config.json`：

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

`.codex/mcp.json`：

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

`~/.gemini/settings.json`：

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

`.cursor/mcp.json`：

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

`.vscode/mcp.json`：

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

## 架构

```
                          ┌──────────────────────────────────┐
                          │   MCP Server（stdio 传输）        │
                          └──────────┬───────────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
          台前模式                台后模式              模拟器模式
              │                      │                      │
   ┌──────────┴──────────┐  ┌───────┴────────┐   ┌────────┴────────┐
   │ cliclick（鼠标）     │  │ CGEvent API    │   │ xcrun simctl    │
   │ osascript（键盘）    │  │ 通过 JXA 桥接   │   │ （iOS）          │
   │ screencapture       │  │ CGEventPost-   │   │                 │
   │ shortcuts CLI       │  │   ToPid(pid)   │   │ adb             │
   └─────────────────────┘  │ screencapture  │   │ （Android）      │
                             │   -l<windowId> │   └─────────────────┘
                             └────────────────┘
```

**台后模式原理：**
1. 通过 JXA 调用 `CGWindowListCopyWindowInfo`，枚举所有窗口，获取 CGWindowID、PID、窗口边界
2. 将窗口相对坐标转换成屏幕绝对坐标
3. 通过 `CGEventPostToPid` 将鼠标/键盘/滚轮事件直接发到目标进程
4. 通过 `screencapture -l<windowId>` 截取指定窗口，不需要焦点

## 方案对比

| 方案 | 平台 | 台后模式 | 模拟器支持 | 真实桌面 |
|------|------|---------|-----------|---------|
| **本项目** | **macOS** | **支持（CGEvent）** | **iOS + Android** | **是** |
| Anthropic Computer Use | Linux | 不支持 | 不支持 | 否（虚拟桌面） |
| MCPControl | Windows | 不支持 | 不支持 | 是 |
| Playwright MCP | 跨平台 | 部分 | 不支持 | 仅浏览器 |
| PyAutoGUI MCP 方案 | 跨平台 | 不支持 | 不支持 | 是 |

### 为什么做 macOS 原生方案

- **台后操作** — CGEvent API 直接向目标 PID 发事件，完全不碰焦点。PyAutoGUI 和 cliclick 都需要窗口在前台。
- **防止焦点被抢** — `app` 参数 + `ensureAppFocus()` 解决了所有 MCP 客户端共有的审批弹窗抢焦点问题。
- **输入法绕过** — `direct` 模式通过 AppleScript 直接写入文本，跳过输入法。PyAutoGUI 的 `typewrite` 只能处理 ASCII。
- **模拟器集成** — iOS 和 Android 模拟器通过同一个 MCP 接口控制，无需单独的工具。
- **轻量** — cliclick（一个 brew 包）+ macOS 内置工具。无需 Python、无需 ONNX、无需重量级依赖。

### 什么时候该选跨平台方案

- 你需要 Windows 或 Linux 支持
- 你需要 OCR 识别屏幕文字来定位元素
- 台后操作不是你的刚需

## 更新管理

本项目集成了 [update-kit](https://github.com/d-wwei/update-kit)，提供策略控制、验证和回滚能力的更新编排。

检查更新：

```bash
npx update-kit check --cwd /path/to/macos-desktop-control --json
```

执行更新（git pull + 语法验证）：

```bash
npx update-kit apply --cwd /path/to/macos-desktop-control
```

出问题时回滚：

```bash
npx update-kit rollback --cwd /path/to/macos-desktop-control
```

配置文件为 `update.config.json`。状态和审计日志存储在 `.update-kit/`（已加入 gitignore）。

## 许可证

MIT
