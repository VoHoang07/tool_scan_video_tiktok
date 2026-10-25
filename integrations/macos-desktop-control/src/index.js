#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { execFile, exec } from "child_process";
import { promisify } from "util";
import { readFile, writeFile, unlink, mkdir, readdir, rm } from "fs/promises";
import { tmpdir } from "os";
import { join } from "path";
import { existsSync } from "fs";

const execFileAsync = promisify(execFile);
const execAsync = promisify(exec);

// ============================================================
// Helpers: Core
// ============================================================

async function runCliclick(...args) {
  const { stdout } = await execFileAsync("cliclick", args);
  return stdout.trim();
}

async function runOsascript(script) {
  const { stdout } = await execFileAsync("osascript", ["-e", script]);
  return stdout.trim();
}

async function runJxa(script) {
  const { stdout } = await execFileAsync("osascript", ["-l", "JavaScript", "-e", script]);
  return stdout.trim();
}

function tempPath(ext = "png") {
  return join(tmpdir(), `mcp-screenshot-${Date.now()}.${ext}`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ============================================================
// Helpers: Image Compression & Tiling
// ============================================================

const COMPRESSION_PRESETS = {
  none:   { maxWidth: null, quality: 100, format: "png" },
  low:    { maxWidth: 2048, quality: 85,  format: "jpeg" },
  medium: { maxWidth: 1280, quality: 70,  format: "jpeg" },
  high:   { maxWidth: 800,  quality: 50,  format: "jpeg" },
};

const compressionSchema = z.enum(["none", "low", "medium", "high"]).default("medium")
  .describe("Compression preset: none (raw PNG), low (2048px/q85), medium (1280px/q70, default), high (800px/q50)");
const maxWidthSchema = z.number().min(100).optional()
  .describe("Override preset max width (px). Image is scaled down if wider, never upscaled.");
const qualitySchema = z.number().min(1).max(100).optional()
  .describe("Override preset JPEG/WebP quality (1-100).");
const formatSchema = z.enum(["jpeg", "png", "webp"]).optional()
  .describe("Override preset output format.");

// In-memory tile store: { [tileId]: { dir, tiles, timer } }
const tileStore = {};
const TILE_TTL_MS = 120_000; // 2 minutes

function resolveCompression({ compression = "medium", maxWidth, quality, format }) {
  const preset = COMPRESSION_PRESETS[compression] || COMPRESSION_PRESETS.medium;
  return {
    maxWidth: maxWidth ?? preset.maxWidth,
    quality: quality ?? preset.quality,
    format: format ?? preset.format,
  };
}

async function getImageWidth(filePath) {
  const { stdout } = await execFileAsync("sips", ["-g", "pixelWidth", filePath]);
  const match = stdout.match(/pixelWidth:\s*(\d+)/);
  return match ? parseInt(match[1], 10) : null;
}

async function compressImage(inputPath, opts) {
  const { maxWidth, quality, format } = opts;

  // If format is png and no resizing needed, return as-is
  if (format === "png" && !maxWidth) {
    const data = await readFile(inputPath);
    return { data, mimeType: "image/png" };
  }

  const ext = format === "jpeg" ? "jpg" : format;
  const outPath = inputPath.replace(/\.[^.]+$/, `_compressed.${ext}`);

  // Build sips command
  const args = [];

  // Resize if needed (never upscale)
  if (maxWidth) {
    const currentWidth = await getImageWidth(inputPath);
    if (currentWidth && currentWidth > maxWidth) {
      args.push("--resampleWidth", String(maxWidth));
    }
  }

  // Format conversion
  const sipsFormat = format === "jpeg" ? "jpeg" : format === "webp" ? "com.google.webp" : "png";
  args.push("-s", "format", sipsFormat);

  // Quality (for jpeg/webp)
  if (format !== "png") {
    args.push("-s", "formatOptions", String(quality));
  }

  args.push(inputPath, "--out", outPath);
  await execFileAsync("sips", args);

  const data = await readFile(outPath);
  await unlink(outPath).catch(() => {});

  const mimeMap = { jpeg: "image/jpeg", png: "image/png", webp: "image/webp" };
  return { data, mimeType: mimeMap[format] || "image/png" };
}

async function createTiles(inputPath, rows, cols) {
  const tileId = `tiles-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const tileDir = join(tmpdir(), `mcp-${tileId}`);
  await mkdir(tileDir, { recursive: true });

  // Get image dimensions
  const { stdout } = await execFileAsync("sips", ["-g", "pixelWidth", "-g", "pixelHeight", inputPath]);
  const wMatch = stdout.match(/pixelWidth:\s*(\d+)/);
  const hMatch = stdout.match(/pixelHeight:\s*(\d+)/);
  const imgW = wMatch ? parseInt(wMatch[1], 10) : 1920;
  const imgH = hMatch ? parseInt(hMatch[1], 10) : 1080;

  const tileW = Math.ceil(imgW / cols);
  const tileH = Math.ceil(imgH / rows);

  const tiles = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const index = r * cols + c;
      const offsetX = c * tileW;
      const offsetY = r * tileH;
      const actualW = Math.min(tileW, imgW - offsetX);
      const actualH = Math.min(tileH, imgH - offsetY);
      const tilePath = join(tileDir, `tile_${index}.png`);

      // sips crop: first pad/crop to size, then offset
      // Strategy: copy full image, then crop with extract
      await execFileAsync("sips", [
        "-c", String(imgH), String(imgW),  // ensure no padding issues
        "--cropOffset", String(offsetY), String(offsetX),
        "-c", String(actualH), String(actualW),
        inputPath, "--out", tilePath,
      ]);

      tiles.push({ index, row: r, col: c, width: actualW, height: actualH, path: tilePath });
    }
  }

  // Schedule cleanup
  const timer = setTimeout(async () => {
    await rm(tileDir, { recursive: true, force: true }).catch(() => {});
    delete tileStore[tileId];
  }, TILE_TTL_MS);

  tileStore[tileId] = { dir: tileDir, tiles, timer, originalSize: { width: imgW, height: imgH }, tileSize: { width: tileW, height: tileH } };

  return {
    tileId,
    rows,
    cols,
    totalTiles: tiles.length,
    originalSize: { width: imgW, height: imgH },
    tileSize: { width: tileW, height: tileH },
    tiles: tiles.map(t => ({ index: t.index, row: t.row, col: t.col, width: t.width, height: t.height })),
  };
}

// ============================================================
// Helpers: Focus Management (Foreground Mode)
// ============================================================

async function ensureAppFocus(app, delayMs = 300) {
  await runOsascript(`
    tell application "${app}" to activate
    delay 0.2
    tell application "System Events"
      tell process "${app}"
        set frontmost to true
        try
          perform action "AXRaise" of window 1
        end try
      end tell
    end tell
  `);
  if (delayMs > 0) await sleep(delayMs);
}

async function switchToEnglishInput() {
  try {
    await runOsascript(`
      tell application "System Events"
        tell process "SystemUIServer"
          key code 57
        end tell
      end tell
    `);
    await sleep(200);
  } catch {
    // Silently ignore
  }
}

function buildModifierClause(modifiers) {
  if (!modifiers?.length) return "";
  const modMap = {
    cmd: "command down",
    alt: "option down",
    ctrl: "control down",
    shift: "shift down",
  };
  const parts = modifiers.map((m) => modMap[m]).filter(Boolean);
  if (parts.length === 1) return ` using ${parts[0]}`;
  return ` using {${parts.join(", ")}}`;
}

// ============================================================
// Helpers: Background Mode — CGEvent via JXA
// ============================================================

/**
 * Get window info (CGWindowID, PID, bounds) for a target app via CGWindowListCopyWindowInfo.
 * Returns { windowId, pid, name, bounds: {x,y,w,h} } or throws if not found.
 */
async function getWindowInfo(app, title) {
  const titleFilter = title
    ? `if (wName.indexOf(${JSON.stringify(title)}) === -1) continue;`
    : "";
  const result = await runJxa(`
    ObjC.import("CoreGraphics");
    var opts = $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements;
    var cfList = $.CGWindowListCopyWindowInfo(opts, 0);
    var bridged = ObjC.castRefToObject(cfList);
    var match = null;
    for (var i = 0; i < bridged.count; i++) {
      var d = bridged.objectAtIndex(i);
      var wApp = ObjC.unwrap(d.objectForKey("kCGWindowOwnerName"));
      var layer = ObjC.unwrap(d.objectForKey("kCGWindowLayer"));
      if (layer !== 0) continue;
      if (wApp !== ${JSON.stringify(app)}) continue;
      var wName = d.objectForKey("kCGWindowName") ? ObjC.unwrap(d.objectForKey("kCGWindowName")) : "";
      ${titleFilter}
      var bounds = d.objectForKey("kCGWindowBounds");
      match = {
        windowId: ObjC.unwrap(d.objectForKey("kCGWindowNumber")),
        pid: ObjC.unwrap(d.objectForKey("kCGWindowOwnerPID")),
        name: wName,
        bounds: {
          x: ObjC.unwrap(bounds.objectForKey("X")),
          y: ObjC.unwrap(bounds.objectForKey("Y")),
          w: ObjC.unwrap(bounds.objectForKey("Width")),
          h: ObjC.unwrap(bounds.objectForKey("Height"))
        }
      };
      break;
    }
    JSON.stringify(match);
  `);
  const parsed = JSON.parse(result);
  if (!parsed) {
    throw new Error(`Window not found: "${app}"${title ? ` title="${title}"` : ""}`);
  }
  return parsed;
}

/**
 * Convert window-relative coords to screen-absolute coords.
 */
function toScreenCoords(bounds, relX, relY) {
  return { x: bounds.x + relX, y: bounds.y + relY };
}

/**
 * Background click via CGEvent → CGEventPostToPid. No focus steal.
 */
async function bgClick(pid, screenX, screenY, button = "left", clicks = 1) {
  const evtTypes = {
    left:   { down: 1,  up: 2 },
    right:  { down: 3,  up: 4 },
    middle: { down: 25, up: 26 },
  };
  const btnNum = { left: 0, right: 1, middle: 2 };
  const evts = evtTypes[button] || evtTypes.left;
  const btn = btnNum[button] || 0;

  await runJxa(`
    ObjC.import("CoreGraphics");
    var src = $.CGEventSourceCreate($.kCGEventSourceStateCombinedSessionState);
    var point = $.CGPointMake(${screenX}, ${screenY});
    for (var c = 0; c < ${clicks}; c++) {
      var down = $.CGEventCreateMouseEvent(src, ${evts.down}, point, ${btn});
      if (c > 0) $.CGEventSetIntegerValueField(down, 1, c + 1);
      $.CGEventPostToPid(${pid}, down);
      delay(0.03);
      var up = $.CGEventCreateMouseEvent(src, ${evts.up}, point, ${btn});
      if (c > 0) $.CGEventSetIntegerValueField(up, 1, c + 1);
      $.CGEventPostToPid(${pid}, up);
      delay(0.03);
    }
    "ok";
  `);
}

/**
 * Background key press via CGEvent → CGEventPostToPid. No focus steal.
 */
async function bgKeyPress(pid, keyCode, modifiers) {
  const modBits = { cmd: 1 << 20, alt: 1 << 19, ctrl: 1 << 18, shift: 1 << 17 };
  let flags = 0;
  if (modifiers?.length) {
    for (const m of modifiers) flags |= (modBits[m] || 0);
  }

  await runJxa(`
    ObjC.import("CoreGraphics");
    var src = $.CGEventSourceCreate($.kCGEventSourceStateCombinedSessionState);
    var down = $.CGEventCreateKeyboardEvent(src, ${keyCode}, true);
    ${flags ? `$.CGEventSetFlags(down, ${flags});` : ""}
    $.CGEventPostToPid(${pid}, down);
    delay(0.02);
    var up = $.CGEventCreateKeyboardEvent(src, ${keyCode}, false);
    ${flags ? `$.CGEventSetFlags(up, ${flags});` : ""}
    $.CGEventPostToPid(${pid}, up);
    "ok";
  `);
}

/**
 * Background text typing via clipboard paste (Cmd+V) to target PID.
 * Saves/restores clipboard. Handles Unicode.
 */
async function bgTypeText(pid, text) {
  // Save current clipboard
  let savedClip = "";
  try { savedClip = (await execAsync("pbpaste 2>/dev/null")).stdout; } catch {}

  // Set clipboard to our text
  const tmpFile = tempPath("txt");
  await writeFile(tmpFile, text, "utf8");
  await execAsync(`pbcopy < "${tmpFile}"`);
  await unlink(tmpFile).catch(() => {});

  // Cmd+V via CGEvent to target PID (keycode 9 = V)
  await bgKeyPress(pid, 9, ["cmd"]);
  await sleep(100);

  // Restore clipboard
  const restoreFile = tempPath("txt");
  await writeFile(restoreFile, savedClip, "utf8");
  await execAsync(`pbcopy < "${restoreFile}"`);
  await unlink(restoreFile).catch(() => {});
}

/**
 * Background scroll via CGEvent → CGEventPostToPid.
 */
async function bgScroll(pid, dy, dx = 0) {
  await runJxa(`
    ObjC.import("CoreGraphics");
    var src = $.CGEventSourceCreate($.kCGEventSourceStateCombinedSessionState);
    var evt = $.CGEventCreateScrollWheelEvent(src, 0, 2, ${dy}, ${dx});
    $.CGEventPostToPid(${pid}, evt);
    "ok";
  `);
}

/**
 * Flash focus: briefly activate target → execute action → restore foreground app.
 * Used for operations that need momentary focus (e.g. drag).
 */
async function flashFocus(targetApp, actionFn) {
  const frontApp = await runOsascript(`
    tell application "System Events"
      name of first process whose frontmost is true
    end tell
  `);
  try {
    await ensureAppFocus(targetApp, 100);
    await actionFn();
  } finally {
    if (frontApp && frontApp !== targetApp) {
      await ensureAppFocus(frontApp, 0);
    }
  }
}

// ============================================================
// Helpers: Simulator Detection
// ============================================================

async function detectCommand(cmd) {
  try {
    await execAsync(`which ${cmd} 2>/dev/null`);
    return true;
  } catch { return false; }
}

async function detectSimctl() {
  try {
    await execAsync("xcrun simctl help 2>/dev/null");
    return true;
  } catch { return false; }
}

// ============================================================
// Shared Schemas & Constants
// ============================================================

const targetSchema = z.object({
  app: z.string().describe("Target app name, e.g. 'Safari'"),
  title: z.string().optional().describe("Window title substring to match"),
}).optional().describe(
  "Background mode: target a specific window without stealing focus. " +
  "When set, x/y coordinates become window-relative (origin = window top-left)."
);

const keyCodeMap = {
  return: 36, enter: 36, escape: 53, esc: 53, tab: 48, space: 49,
  delete: 51, "fwd-delete": 117,
  "arrow-up": 126, "arrow-down": 125, "arrow-left": 123, "arrow-right": 124,
  home: 115, end: 119, "page-up": 116, "page-down": 121,
  f1: 122, f2: 120, f3: 99, f4: 118, f5: 96, f6: 97,
  f7: 98, f8: 100, f9: 101, f10: 109, f11: 103, f12: 111,
  capslock: 57,
};

const charToKeyCode = {
  a: 0, b: 11, c: 8, d: 2, e: 14, f: 3, g: 5, h: 4, i: 34, j: 38,
  k: 40, l: 37, m: 46, n: 45, o: 31, p: 35, q: 12, r: 15, s: 1,
  t: 17, u: 32, v: 9, w: 13, x: 7, y: 16, z: 6,
  "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23,
  "6": 22, "7": 26, "8": 28, "9": 25,
  " ": 49, "-": 27, "=": 24, "[": 33, "]": 30, "\\": 42,
  ";": 41, "'": 39, ",": 43, ".": 47, "/": 44, "`": 50,
};

// ============================================================
// MCP Server
// ============================================================

const server = new McpServer({
  name: "macos-desktop-control",
  version: "3.1.0",
});

// ============================================================
// Tool: screenshot
// ============================================================
server.tool(
  "screenshot",
  "Capture a screenshot of the entire screen, a region, or a specific window (background mode).\n" +
    "Use 'target' to capture a specific app's window without stealing focus.\n" +
    "Compression presets: none (raw PNG), low (2048px/q85), medium (1280px/q70, DEFAULT), high (800px/q50).\n" +
    "Use 'tile' to split a high-res screenshot into a grid; fetch individual tiles with screenshot_tile.",
  {
    region: z.object({
      x: z.number().describe("Top-left x coordinate"),
      y: z.number().describe("Top-left y coordinate"),
      w: z.number().describe("Width"),
      h: z.number().describe("Height"),
    }).optional().describe("Optional region to capture. Omit for full screen."),
    display: z.number().optional().describe("Display number (1 = main). Omit for main display."),
    target: targetSchema,
    compression: compressionSchema,
    maxWidth: maxWidthSchema,
    quality: qualitySchema,
    format: formatSchema,
    tile: z.object({
      rows: z.number().min(1).max(4).describe("Number of rows (1-4)"),
      cols: z.number().min(1).max(4).describe("Number of columns (1-4)"),
    }).optional().describe("Enable tile mode: splits screenshot into a grid. Returns a manifest; use screenshot_tile to fetch individual tiles."),
  },
  async ({ region, display, target, compression, maxWidth, quality, format, tile }) => {
    const filePath = tempPath("png");
    try {
      // Step 1: Capture raw screenshot
      if (target) {
        const win = await getWindowInfo(target.app, target.title);
        await execFileAsync("screencapture", ["-x", "-o", `-l${win.windowId}`, filePath]);
      } else {
        const args = ["-x"];
        if (region) args.push("-R", `${region.x},${region.y},${region.w},${region.h}`);
        if (display) args.push("-D", String(display));
        args.push(filePath);
        await execFileAsync("screencapture", args);
      }

      // Step 2: Tile mode — return manifest, no image data
      if (tile) {
        const manifest = await createTiles(filePath, tile.rows, tile.cols);
        await unlink(filePath).catch(() => {});
        return {
          content: [{ type: "text", text: JSON.stringify(manifest, null, 2) }],
        };
      }

      // Step 3: Compress and return
      const opts = resolveCompression({ compression, maxWidth, quality, format });
      const { data, mimeType } = await compressImage(filePath, opts);
      const base64 = data.toString("base64");
      await unlink(filePath).catch(() => {});
      return {
        content: [{ type: "image", data: base64, mimeType }],
      };
    } catch (err) {
      await unlink(filePath).catch(() => {});
      return { content: [{ type: "text", text: `Screenshot failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: screenshot_tile
// ============================================================
server.tool(
  "screenshot_tile",
  "Fetch a single tile from a tiled screenshot. Use after calling screenshot with tile mode.\n" +
    "Each tile is compressed individually before returning.",
  {
    id: z.string().describe("Tile manifest ID returned by screenshot with tile mode"),
    index: z.number().min(0).describe("Tile index (0-based, row-major order)"),
    compression: compressionSchema,
    maxWidth: maxWidthSchema,
    quality: qualitySchema,
    format: formatSchema,
  },
  async ({ id, index, compression, maxWidth, quality, format }) => {
    try {
      const entry = tileStore[id];
      if (!entry) {
        return { content: [{ type: "text", text: `Tile set "${id}" not found or expired (tiles expire after ${TILE_TTL_MS / 1000}s).` }] };
      }
      const tileInfo = entry.tiles[index];
      if (!tileInfo) {
        return { content: [{ type: "text", text: `Tile index ${index} out of range (0-${entry.tiles.length - 1}).` }] };
      }

      const opts = resolveCompression({ compression, maxWidth, quality, format });
      const { data, mimeType } = await compressImage(tileInfo.path, opts);
      const base64 = data.toString("base64");
      return {
        content: [{ type: "image", data: base64, mimeType }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `screenshot_tile failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: click
// ============================================================
server.tool(
  "click",
  "Click at coordinates. Supports left/right/double/triple click.\n" +
    "Foreground: use 'app' to focus before clicking.\n" +
    "Background: use 'target' to click in a window without stealing focus (coords become window-relative).",
  {
    x: z.number().describe("X coordinate"),
    y: z.number().describe("Y coordinate"),
    button: z.enum(["left", "right", "middle"]).default("left").describe("Mouse button"),
    clicks: z.number().min(1).max(3).default(1).describe("Number of clicks (1=single, 2=double, 3=triple)"),
    modifiers: z.array(z.enum(["cmd", "alt", "ctrl", "shift"])).optional().describe("Modifier keys to hold during click"),
    app: z.string().optional().describe("App name to focus before clicking (e.g. 'Safari'). Recommended to prevent focus-stealing."),
    target: targetSchema,
  },
  async ({ x, y, button, clicks, modifiers, app, target }) => {
    try {
      if (target) {
        // Background mode: CGEvent click to target PID
        const win = await getWindowInfo(target.app, target.title);
        const screen = toScreenCoords(win.bounds, x, y);
        // TODO: modifiers in bg mode for future enhancement
        await bgClick(win.pid, screen.x, screen.y, button, clicks);
        return {
          content: [{
            type: "text",
            text: `[bg] Clicked ${button}(x${clicks}) at window(${x},${y}) → screen(${screen.x},${screen.y}) in "${target.app}"`,
          }],
        };
      }

      // Foreground mode: existing behavior
      if (app) await ensureAppFocus(app);

      const actions = [];
      if (modifiers?.length) {
        for (const m of modifiers) actions.push(`kd:${m}`);
      }
      const clickCmds = {
        left: { 1: "c", 2: "dc", 3: "tc" },
        right: { 1: "rc", 2: "rc", 3: "rc" },
        middle: { 1: "mc", 2: "mc", 3: "mc" },
      };
      const cmd = clickCmds[button]?.[clicks] || "c";
      actions.push(`${cmd}:${x},${y}`);
      if (modifiers?.length) {
        for (const m of [...modifiers].reverse()) actions.push(`ku:${m}`);
      }

      const result = await runCliclick(...actions);
      return {
        content: [{
          type: "text",
          text: `Clicked ${button}(x${clicks}) at (${x}, ${y})${modifiers?.length ? ` with [${modifiers.join("+")}]` : ""}${app ? ` in ${app}` : ""}. ${result}`,
        }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Click failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: move_mouse (foreground only — background mode N/A)
// ============================================================
server.tool(
  "move_mouse",
  "Move the mouse cursor to the specified coordinates.",
  {
    x: z.number().describe("X coordinate"),
    y: z.number().describe("Y coordinate"),
  },
  async ({ x, y }) => {
    try {
      await runCliclick(`m:${x},${y}`);
      return { content: [{ type: "text", text: `Mouse moved to (${x}, ${y})` }] };
    } catch (err) {
      return { content: [{ type: "text", text: `Move failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: drag
// ============================================================
server.tool(
  "drag",
  "Drag from one position to another.\n" +
    "Background: uses flash technique (briefly activates window, drags, restores focus).",
  {
    fromX: z.number().describe("Start X"),
    fromY: z.number().describe("Start Y"),
    toX: z.number().describe("End X"),
    toY: z.number().describe("End Y"),
    target: targetSchema,
  },
  async ({ fromX, fromY, toX, toY, target }) => {
    try {
      if (target) {
        // Background mode: flash focus + drag + restore
        const win = await getWindowInfo(target.app, target.title);
        const from = toScreenCoords(win.bounds, fromX, fromY);
        const to = toScreenCoords(win.bounds, toX, toY);
        await flashFocus(target.app, async () => {
          await runCliclick(`dd:${from.x},${from.y}`, `du:${to.x},${to.y}`);
        });
        return {
          content: [{
            type: "text",
            text: `[bg/flash] Dragged (${fromX},${fromY})→(${toX},${toY}) in "${target.app}"`,
          }],
        };
      }

      // Foreground mode
      await runCliclick(`dd:${fromX},${fromY}`, `du:${toX},${toY}`);
      return {
        content: [{ type: "text", text: `Dragged from (${fromX},${fromY}) to (${toX},${toY})` }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Drag failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: type_text
// ============================================================
server.tool(
  "type_text",
  "Type text into an application. Three modes available:\n" +
    "- 'keystroke' (default): Types via System Events keystroke. Works with any app but affected by input method.\n" +
    "- 'cliclick': Types via cliclick. Fast but also affected by input method.\n" +
    "- 'direct': Sets text directly via AppleScript (TextEdit, Notes, etc.). Bypasses input method completely.\n" +
    "Use 'target' for background typing (uses clipboard paste via CGEvent, no focus steal).",
  {
    text: z.string().describe("The text to type"),
    app: z.string().optional().describe("App name to focus before typing (e.g. 'TextEdit'). Highly recommended."),
    mode: z.enum(["keystroke", "cliclick", "direct"]).default("keystroke").describe(
      "'keystroke': via System Events (default). 'cliclick': via cliclick. 'direct': set text via AppleScript."
    ),
    switchToEnglish: z.boolean().default(false).describe("If true, press Caps Lock before typing to switch to English input."),
    target: targetSchema,
  },
  async ({ text, app, mode, switchToEnglish, target }) => {
    try {
      if (target) {
        // Background mode: paste via CGEvent Cmd+V
        const win = await getWindowInfo(target.app, target.title);
        await bgTypeText(win.pid, text);
        return {
          content: [{ type: "text", text: `[bg] Typed "${text.substring(0, 50)}${text.length > 50 ? "..." : ""}" in "${target.app}"` }],
        };
      }

      // Foreground mode: existing behavior
      if (app) await ensureAppFocus(app);
      if (switchToEnglish) await switchToEnglishInput();

      if (mode === "direct") {
        if (!app) {
          return { content: [{ type: "text", text: 'Direct mode requires "app" parameter.' }] };
        }
        const escaped = text.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        await runOsascript(`
          tell application "${app}"
            if (count of documents) > 0 then
              set text of document 1 to "${escaped}"
            end if
          end tell
        `);
        return { content: [{ type: "text", text: `Set text directly in ${app}: "${text}"` }] };
      } else if (mode === "cliclick") {
        await runCliclick(`t:${text}`);
        return { content: [{ type: "text", text: `Typed via cliclick: "${text}"` }] };
      } else {
        const escaped = text.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        await runOsascript(`
          tell application "System Events"
            keystroke "${escaped}"
          end tell
        `);
        return { content: [{ type: "text", text: `Typed via keystroke: "${text}"` }] };
      }
    } catch (err) {
      return { content: [{ type: "text", text: `Type failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: key_press
// ============================================================
server.tool(
  "key_press",
  "Press a key or key combination.\n" +
    "For special keys: return, escape, tab, space, delete, arrow-up/down/left/right, f1-f12, home, end, page-up, page-down.\n" +
    "For letters/numbers: 'a', '1', etc.\n" +
    "Supports modifier combos: key='c', modifiers=['cmd'] for Cmd+C.\n" +
    "Use 'target' for background key press (no focus steal).",
  {
    key: z.string().describe('Key to press. Special keys: "return", "escape", "tab", "space", "delete", "arrow-up", etc. Letters: "a", "b", "1".'),
    modifiers: z.array(z.enum(["cmd", "alt", "ctrl", "shift"])).optional().describe("Modifier keys to hold"),
    app: z.string().optional().describe("App name to focus before pressing key"),
    target: targetSchema,
  },
  async ({ key, modifiers, app, target }) => {
    try {
      const combo = modifiers?.length ? `${modifiers.join("+")}+${key}` : key;

      if (target) {
        // Background mode: CGEvent key press to target PID
        const win = await getWindowInfo(target.app, target.title);
        let kc = keyCodeMap[key];
        if (kc === undefined && key.length === 1) {
          kc = charToKeyCode[key.toLowerCase()];
        }
        if (kc === undefined) {
          throw new Error(`Unknown key "${key}" for background mode`);
        }
        await bgKeyPress(win.pid, kc, modifiers);
        return {
          content: [{ type: "text", text: `[bg] Pressed: ${combo} in "${target.app}"` }],
        };
      }

      // Foreground mode: existing behavior
      if (app) await ensureAppFocus(app);
      const modClause = buildModifierClause(modifiers);

      if (keyCodeMap[key] !== undefined) {
        await runOsascript(`
          tell application "System Events"
            key code ${keyCodeMap[key]}${modClause}
          end tell
        `);
      } else if (key.length === 1) {
        const escaped = key.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        await runOsascript(`
          tell application "System Events"
            keystroke "${escaped}"${modClause}
          end tell
        `);
      } else {
        const actions = [];
        if (modifiers?.length) {
          for (const m of modifiers) actions.push(`kd:${m}`);
        }
        actions.push(`kp:${key}`);
        if (modifiers?.length) {
          for (const m of [...modifiers].reverse()) actions.push(`ku:${m}`);
        }
        await runCliclick(...actions);
      }

      return { content: [{ type: "text", text: `Pressed: ${combo}` }] };
    } catch (err) {
      return { content: [{ type: "text", text: `Key press failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: scroll
// ============================================================
server.tool(
  "scroll",
  "Scroll at coordinates or in a target window.\n" +
    "Use 'target' for background scrolling (no focus steal, coords become window-relative).",
  {
    direction: z.enum(["up", "down", "left", "right"]).describe("Scroll direction"),
    amount: z.number().min(1).max(50).default(3).describe("Scroll amount (clicks of scroll wheel)"),
    x: z.number().optional().describe("X coordinate to move mouse to before scrolling"),
    y: z.number().optional().describe("Y coordinate to move mouse to before scrolling"),
    target: targetSchema,
  },
  async ({ direction, amount, x, y, target }) => {
    try {
      if (target) {
        // Background mode: CGEvent scroll to target PID
        const win = await getWindowInfo(target.app, target.title);
        const scrollMap = {
          up: { dy: -amount, dx: 0 },
          down: { dy: amount, dx: 0 },
          left: { dy: 0, dx: -amount },
          right: { dy: 0, dx: amount },
        };
        const { dy, dx } = scrollMap[direction];
        await bgScroll(win.pid, dy, dx);
        return {
          content: [{ type: "text", text: `[bg] Scrolled ${direction} by ${amount} in "${target.app}"` }],
        };
      }

      // Foreground mode: existing behavior
      if (x !== undefined && y !== undefined) {
        await runCliclick(`m:${x},${y}`);
      }
      const scrollVec = {
        up: `0,-${amount}`,
        down: `0,${amount}`,
        left: `-${amount},0`,
        right: `${amount},0`,
      };
      await runCliclick(`sc:${scrollVec[direction]}`);
      return { content: [{ type: "text", text: `Scrolled ${direction} by ${amount}` }] };
    } catch (err) {
      return { content: [{ type: "text", text: `Scroll failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: get_mouse_position (foreground only)
// ============================================================
server.tool(
  "get_mouse_position",
  "Get the current mouse cursor position.",
  {},
  async () => {
    try {
      const result = await runCliclick("p:");
      return { content: [{ type: "text", text: `Mouse position: ${result}` }] };
    } catch (err) {
      return { content: [{ type: "text", text: `Get position failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: get_screen_size (foreground only)
// ============================================================
server.tool(
  "get_screen_size",
  "Get the screen dimensions of all connected displays.",
  {},
  async () => {
    try {
      const script = `
        tell application "Finder"
          set _bounds to bounds of window of desktop
          set _w to item 3 of _bounds
          set _h to item 4 of _bounds
          return ((_w as text) & "x" & (_h as text))
        end tell
      `;
      const finderSize = await runOsascript(script).catch(() => "unknown");
      const { stdout } = await execAsync(
        "system_profiler SPDisplaysDataType 2>/dev/null | grep -E 'Resolution|Display Type'"
      );
      return {
        content: [{ type: "text", text: `Desktop: ${finderSize}\nDisplays:\n${stdout.trim()}` }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Get screen size failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: open_app
// ============================================================
server.tool(
  "open_app",
  "Open or activate a macOS application.\n" +
    "Set background=true to launch without bringing to front (uses 'open -g').",
  {
    app: z.string().describe('Application name, e.g. "Safari", "Finder", "Terminal"'),
    background: z.boolean().default(false).describe("If true, launch app in background without stealing focus."),
  },
  async ({ app, background }) => {
    try {
      if (background) {
        await execAsync(`open -g -a "${app}"`);
        return { content: [{ type: "text", text: `[bg] Launched: ${app} (background)` }] };
      }
      await ensureAppFocus(app);
      return { content: [{ type: "text", text: `Activated: ${app}` }] };
    } catch (err) {
      return { content: [{ type: "text", text: `Open app failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: list_windows (enhanced with CGWindowID + PID)
// ============================================================
server.tool(
  "list_windows",
  "List all visible windows with their titles, positions, sizes, CGWindowIDs, and PIDs.\n" +
    "CGWindowIDs can be used with 'target' parameter for background operations.",
  {},
  async () => {
    try {
      const result = await runJxa(`
        ObjC.import("CoreGraphics");
        var opts = $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements;
        var cfList = $.CGWindowListCopyWindowInfo(opts, 0);
        var bridged = ObjC.castRefToObject(cfList);
        var lines = [];
        for (var i = 0; i < bridged.count; i++) {
          var d = bridged.objectAtIndex(i);
          var layer = ObjC.unwrap(d.objectForKey("kCGWindowLayer"));
          if (layer !== 0) continue;
          var app = ObjC.unwrap(d.objectForKey("kCGWindowOwnerName"));
          var wid = ObjC.unwrap(d.objectForKey("kCGWindowNumber"));
          var pid = ObjC.unwrap(d.objectForKey("kCGWindowOwnerPID"));
          var wName = d.objectForKey("kCGWindowName") ? ObjC.unwrap(d.objectForKey("kCGWindowName")) : "";
          var bounds = d.objectForKey("kCGWindowBounds");
          var bx = ObjC.unwrap(bounds.objectForKey("X"));
          var by = ObjC.unwrap(bounds.objectForKey("Y"));
          var bw = ObjC.unwrap(bounds.objectForKey("Width"));
          var bh = ObjC.unwrap(bounds.objectForKey("Height"));
          lines.push(app + " | " + wName + " | wid:" + wid + " | pid:" + pid + " | pos:" + bx + "," + by + " | size:" + bw + "," + bh);
        }
        lines.join("\\n");
      `);
      return {
        content: [{ type: "text", text: result || "No visible windows found." }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `List windows failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: focus_window (foreground only)
// ============================================================
server.tool(
  "focus_window",
  "Focus a specific window by application name and optional window title.",
  {
    app: z.string().describe("Application name"),
    title: z.string().optional().describe("Window title substring to match"),
  },
  async ({ app, title }) => {
    try {
      if (title) {
        await runOsascript(`
          tell application "System Events"
            tell process "${app}"
              set frontmost to true
              set wins to (every window whose name contains "${title}")
              if (count of wins) > 0 then
                perform action "AXRaise" of item 1 of wins
              end if
            end tell
          end tell
        `);
      } else {
        await ensureAppFocus(app);
      }
      return {
        content: [{ type: "text", text: `Focused: ${app}${title ? ` (window: "${title}")` : ""}` }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Focus window failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Tool: run_shortcut (unchanged)
// ============================================================
server.tool(
  "run_shortcut",
  "Run a macOS Shortcuts app shortcut by name.",
  {
    name: z.string().describe("Name of the shortcut to run"),
    input: z.string().optional().describe("Optional input text for the shortcut"),
  },
  async ({ name, input }) => {
    try {
      const args = ["shortcuts", "run", name];
      if (input) args.push("-i", input);
      const { stdout } = await execFileAsync(args[0], args.slice(1), { timeout: 30000 });
      return {
        content: [{
          type: "text",
          text: `Shortcut "${name}" executed.${stdout ? ` Output: ${stdout.trim()}` : ""}`,
        }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Shortcut failed: ${err.message}` }] };
    }
  }
);

// ============================================================
// Simulator Tools — iOS (conditional: requires Xcode + simctl)
// ============================================================
async function registerSimulatorTools() {
  const hasSimctl = await detectSimctl();
  if (!hasSimctl) return;

  server.tool(
    "sim_list_devices",
    "List all iOS simulator devices with their status.",
    {},
    async () => {
      try {
        const { stdout } = await execAsync("xcrun simctl list devices -j");
        const data = JSON.parse(stdout);
        const lines = [];
        for (const [runtime, devices] of Object.entries(data.devices)) {
          for (const d of devices) {
            lines.push(`${d.name} | ${d.udid} | ${d.state} | ${runtime.split(".").pop()}`);
          }
        }
        return { content: [{ type: "text", text: lines.join("\n") || "No simulators found." }] };
      } catch (err) {
        return { content: [{ type: "text", text: `sim_list_devices failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "sim_boot",
    "Boot an iOS simulator device.",
    { deviceId: z.string().describe("Device UDID from sim_list_devices") },
    async ({ deviceId }) => {
      try {
        await execAsync(`xcrun simctl boot "${deviceId}"`);
        // Also open Simulator.app to show the device
        await execAsync("open -a Simulator");
        await sleep(2000);
        return { content: [{ type: "text", text: `Booted simulator: ${deviceId}` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `sim_boot failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "sim_shutdown",
    "Shutdown an iOS simulator device.",
    { deviceId: z.string().describe("Device UDID") },
    async ({ deviceId }) => {
      try {
        await execAsync(`xcrun simctl shutdown "${deviceId}"`);
        return { content: [{ type: "text", text: `Shutdown simulator: ${deviceId}` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `sim_shutdown failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "sim_screenshot",
    "Take a screenshot of an iOS simulator.\n" +
      "Compression presets: none (raw PNG), low (2048px/q85), medium (1280px/q70, DEFAULT), high (800px/q50).",
    {
      deviceId: z.string().describe("Device UDID (use 'booted' for the active simulator)"),
      compression: compressionSchema,
      maxWidth: maxWidthSchema,
      quality: qualitySchema,
      format: formatSchema,
    },
    async ({ deviceId, compression, maxWidth, quality, format }) => {
      const filePath = tempPath("png");
      try {
        await execAsync(`xcrun simctl io "${deviceId}" screenshot "${filePath}"`);
        const opts = resolveCompression({ compression, maxWidth, quality, format });
        const { data, mimeType } = await compressImage(filePath, opts);
        const base64 = data.toString("base64");
        await unlink(filePath).catch(() => {});
        return { content: [{ type: "image", data: base64, mimeType }] };
      } catch (err) {
        await unlink(filePath).catch(() => {});
        return { content: [{ type: "text", text: `sim_screenshot failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "sim_tap",
    "Tap at coordinates on an iOS simulator screen.\n" +
      "Coordinates are in the iOS device's point space (e.g. 0-390 x 0-844 for iPhone 14).\n" +
      "Internally maps to the Simulator.app window position.",
    {
      x: z.number().describe("X coordinate in iOS device points"),
      y: z.number().describe("Y coordinate in iOS device points"),
      deviceId: z.string().default("booted").describe("Device UDID (default: 'booted')"),
    },
    async ({ x, y, deviceId }) => {
      try {
        // Get device screen size
        const { stdout: deviceJson } = await execAsync("xcrun simctl list devices booted -j");
        const bootedDevices = JSON.parse(deviceJson);
        let deviceName = "Simulator";
        for (const devices of Object.values(bootedDevices.devices)) {
          for (const d of devices) {
            if (d.state === "Booted" && (deviceId === "booted" || d.udid === deviceId)) {
              deviceName = d.name;
              break;
            }
          }
        }

        // Find the Simulator window
        const win = await getWindowInfo("Simulator", deviceName);
        // Simulator window chrome: ~52px for title bar + status bar area
        const chromeTop = 52;
        const contentH = win.bounds.h - chromeTop;
        const contentW = win.bounds.w;

        // Get device logical resolution via simctl (approximate from window size)
        // Map iOS points to window pixels
        const screenX = win.bounds.x + (x / contentW) * contentW;
        const screenY = win.bounds.y + chromeTop + (y / contentH) * contentH;

        // Use CGEvent click for background operation
        await bgClick(win.pid, Math.round(screenX), Math.round(screenY));
        return {
          content: [{ type: "text", text: `[sim] Tapped at iOS(${x},${y}) → screen(${Math.round(screenX)},${Math.round(screenY)})` }],
        };
      } catch (err) {
        return { content: [{ type: "text", text: `sim_tap failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "sim_swipe",
    "Swipe on an iOS simulator screen.\n" +
      "Coordinates are in iOS device point space.",
    {
      fromX: z.number().describe("Start X in iOS points"),
      fromY: z.number().describe("Start Y in iOS points"),
      toX: z.number().describe("End X in iOS points"),
      toY: z.number().describe("End Y in iOS points"),
      duration: z.number().default(300).describe("Swipe duration in ms"),
      deviceId: z.string().default("booted").describe("Device UDID"),
    },
    async ({ fromX, fromY, toX, toY, duration, deviceId }) => {
      try {
        const win = await getWindowInfo("Simulator");
        const chromeTop = 52;
        const contentH = win.bounds.h - chromeTop;
        const contentW = win.bounds.w;

        const from = {
          x: Math.round(win.bounds.x + (fromX / contentW) * contentW),
          y: Math.round(win.bounds.y + chromeTop + (fromY / contentH) * contentH),
        };
        const to = {
          x: Math.round(win.bounds.x + (toX / contentW) * contentW),
          y: Math.round(win.bounds.y + chromeTop + (toY / contentH) * contentH),
        };

        // Flash focus for drag operation
        await flashFocus("Simulator", async () => {
          await runCliclick(`dd:${from.x},${from.y}`, `du:${to.x},${to.y}`);
        });
        return {
          content: [{ type: "text", text: `[sim] Swiped (${fromX},${fromY})→(${toX},${toY})` }],
        };
      } catch (err) {
        return { content: [{ type: "text", text: `sim_swipe failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "sim_type",
    "Type text into an iOS simulator. Sends keystroke events to the Simulator app.",
    {
      text: z.string().describe("Text to type"),
      deviceId: z.string().default("booted").describe("Device UDID"),
    },
    async ({ text, deviceId }) => {
      try {
        const win = await getWindowInfo("Simulator");
        await bgTypeText(win.pid, text);
        return { content: [{ type: "text", text: `[sim] Typed: "${text}"` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `sim_type failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "sim_open_url",
    "Open a URL in an iOS simulator.",
    {
      url: z.string().describe("URL to open"),
      deviceId: z.string().default("booted").describe("Device UDID"),
    },
    async ({ url, deviceId }) => {
      try {
        await execAsync(`xcrun simctl openurl "${deviceId}" "${url}"`);
        return { content: [{ type: "text", text: `[sim] Opened URL: ${url}` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `sim_open_url failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "sim_install_app",
    "Install an app on an iOS simulator.",
    {
      appPath: z.string().describe("Path to .app bundle or .ipa file"),
      deviceId: z.string().default("booted").describe("Device UDID"),
    },
    async ({ appPath, deviceId }) => {
      try {
        await execAsync(`xcrun simctl install "${deviceId}" "${appPath}"`);
        return { content: [{ type: "text", text: `[sim] Installed: ${appPath}` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `sim_install_app failed: ${err.message}` }] };
      }
    }
  );
}

// ============================================================
// Emulator Tools — Android (conditional: requires adb)
// ============================================================
async function registerEmulatorTools() {
  const hasAdb = await detectCommand("adb");
  if (!hasAdb) return;

  server.tool(
    "emu_list_devices",
    "List connected Android emulators/devices.",
    {},
    async () => {
      try {
        const { stdout } = await execAsync("adb devices -l");
        return { content: [{ type: "text", text: stdout.trim() }] };
      } catch (err) {
        return { content: [{ type: "text", text: `emu_list_devices failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "emu_screenshot",
    "Take a screenshot of an Android emulator.\n" +
      "Compression presets: none (raw PNG), low (2048px/q85), medium (1280px/q70, DEFAULT), high (800px/q50).",
    {
      serial: z.string().optional().describe("Device serial (omit if only one device connected)"),
      compression: compressionSchema,
      maxWidth: maxWidthSchema,
      quality: qualitySchema,
      format: formatSchema,
    },
    async ({ serial, compression, maxWidth, quality, format }) => {
      const filePath = tempPath("png");
      try {
        const s = serial ? `-s "${serial}" ` : "";
        await execAsync(`adb ${s}exec-out screencap -p > "${filePath}"`);
        const opts = resolveCompression({ compression, maxWidth, quality, format });
        const { data, mimeType } = await compressImage(filePath, opts);
        const base64 = data.toString("base64");
        await unlink(filePath).catch(() => {});
        return { content: [{ type: "image", data: base64, mimeType }] };
      } catch (err) {
        await unlink(filePath).catch(() => {});
        return { content: [{ type: "text", text: `emu_screenshot failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "emu_tap",
    "Tap at coordinates on an Android emulator. Fully background — no focus steal.",
    {
      x: z.number().describe("X coordinate in device pixels"),
      y: z.number().describe("Y coordinate in device pixels"),
      serial: z.string().optional().describe("Device serial"),
    },
    async ({ x, y, serial }) => {
      try {
        const s = serial ? `-s "${serial}" ` : "";
        await execAsync(`adb ${s}shell input tap ${x} ${y}`);
        return { content: [{ type: "text", text: `[emu] Tapped at (${x}, ${y})` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `emu_tap failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "emu_swipe",
    "Swipe on an Android emulator. Fully background.",
    {
      fromX: z.number().describe("Start X"),
      fromY: z.number().describe("Start Y"),
      toX: z.number().describe("End X"),
      toY: z.number().describe("End Y"),
      duration: z.number().default(300).describe("Swipe duration in ms"),
      serial: z.string().optional().describe("Device serial"),
    },
    async ({ fromX, fromY, toX, toY, duration, serial }) => {
      try {
        const s = serial ? `-s "${serial}" ` : "";
        await execAsync(`adb ${s}shell input swipe ${fromX} ${fromY} ${toX} ${toY} ${duration}`);
        return { content: [{ type: "text", text: `[emu] Swiped (${fromX},${fromY})→(${toX},${toY})` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `emu_swipe failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "emu_type",
    "Type text on an Android emulator. Fully background.",
    {
      text: z.string().describe("Text to type"),
      serial: z.string().optional().describe("Device serial"),
    },
    async ({ text, serial }) => {
      try {
        const s = serial ? `-s "${serial}" ` : "";
        // Escape for adb shell
        const escaped = text.replace(/'/g, "'\\''");
        await execAsync(`adb ${s}shell input text '${escaped}'`);
        return { content: [{ type: "text", text: `[emu] Typed: "${text}"` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `emu_type failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "emu_key",
    "Send a key event to an Android emulator. Common keycodes: KEYCODE_HOME (3), KEYCODE_BACK (4), KEYCODE_ENTER (66).",
    {
      keycode: z.union([z.string(), z.number()]).describe("Android keycode name or number"),
      serial: z.string().optional().describe("Device serial"),
    },
    async ({ keycode, serial }) => {
      try {
        const s = serial ? `-s "${serial}" ` : "";
        await execAsync(`adb ${s}shell input keyevent ${keycode}`);
        return { content: [{ type: "text", text: `[emu] Key event: ${keycode}` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `emu_key failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "emu_open_url",
    "Open a URL on an Android emulator.",
    {
      url: z.string().describe("URL to open"),
      serial: z.string().optional().describe("Device serial"),
    },
    async ({ url, serial }) => {
      try {
        const s = serial ? `-s "${serial}" ` : "";
        await execAsync(`adb ${s}shell am start -a android.intent.action.VIEW -d "${url}"`);
        return { content: [{ type: "text", text: `[emu] Opened URL: ${url}` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `emu_open_url failed: ${err.message}` }] };
      }
    }
  );

  server.tool(
    "emu_install_app",
    "Install an APK on an Android emulator.",
    {
      apkPath: z.string().describe("Path to .apk file"),
      serial: z.string().optional().describe("Device serial"),
    },
    async ({ apkPath, serial }) => {
      try {
        const s = serial ? `-s "${serial}" ` : "";
        await execAsync(`adb ${s}install "${apkPath}"`);
        return { content: [{ type: "text", text: `[emu] Installed: ${apkPath}` }] };
      } catch (err) {
        return { content: [{ type: "text", text: `emu_install_app failed: ${err.message}` }] };
      }
    }
  );
}

// ============================================================
// Start
// ============================================================
await registerSimulatorTools();
await registerEmulatorTools();

const transport = new StdioServerTransport();
await server.connect(transport);
