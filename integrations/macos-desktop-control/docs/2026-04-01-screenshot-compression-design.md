# Screenshot Compression & Tiling

## Problem

On high-DPI displays (Retina 3024x1964, 4K 3840x2160), screenshots captured as raw PNG produce 8-15MB base64 payloads. This exceeds API input limits, causing `500 Input is too long` errors.

## Solution

Add built-in compression presets, fine-grained control parameters, and a tile mode to the `screenshot` tool. Apply the same compression support to `sim_screenshot` and `emu_screenshot`.

## Compression Presets

| Level | maxWidth | quality | format | Est. Size | Use Case |
|-------|----------|---------|--------|-----------|----------|
| `none` | original | 100 | png | 8-15MB | Pixel-perfect inspection |
| `low` | 2048 | 85 | jpeg | 500KB-1MB | Reading fine text/details |
| `medium` | 1280 | 70 | jpeg | 150-400KB | General UI understanding |
| `high` | 800 | 50 | jpeg | 50-150KB | Layout overview only |

Default: `medium`.

## Parameter Changes

### `screenshot` — enhanced parameters

New optional parameters (all backward-compatible):

```
compression: enum["none", "low", "medium", "high"]  // default: "medium"
maxWidth:    number                                   // override preset maxWidth
quality:     number (1-100)                           // override preset quality
format:      enum["jpeg", "png", "webp"]              // override preset format
tile:        { rows: number(1-4), cols: number(1-4) } // enable tile mode
```

Priority: explicit `maxWidth`/`quality`/`format` override the preset from `compression`.

### `screenshot_tile` — new tool

```
id:          string                                         // manifest ID from tile-mode screenshot
index:       number                                         // tile index (0-based, row-major)
compression: enum["none", "low", "medium", "high"]          // default: "medium"
maxWidth:    number                                         // optional override
quality:     number (1-100)                                 // optional override
format:      enum["jpeg", "png", "webp"]                    // optional override
```

### `sim_screenshot` / `emu_screenshot` — enhanced parameters

Same compression parameters added (no tile mode needed for simulator/emulator).

```
compression: enum["none", "low", "medium", "high"]  // default: "medium"
maxWidth:    number
quality:     number (1-100)
format:      enum["jpeg", "png", "webp"]
```

## Tile Mode

### Flow

1. Agent calls `screenshot(..., tile: { rows: 2, cols: 2 })`
2. Server captures full-resolution screenshot, splits into `rows * cols` tiles, saves to temp dir
3. Returns JSON manifest (no image data):
   ```json
   {
     "tileId": "ts-1711929600000",
     "rows": 2,
     "cols": 2,
     "totalTiles": 4,
     "originalSize": { "width": 3840, "height": 2160 },
     "tileSize": { "width": 1920, "height": 1080 },
     "tiles": [
       { "index": 0, "row": 0, "col": 0 },
       { "index": 1, "row": 0, "col": 1 },
       { "index": 2, "row": 1, "col": 0 },
       { "index": 3, "row": 1, "col": 1 }
     ]
   }
   ```
4. Agent calls `screenshot_tile(id: "ts-...", index: 0)` to fetch individual tiles
5. Each tile is compressed per the specified compression level before returning

### Cleanup

- Tile temp files are cleaned up after 120 seconds (configurable in code)
- Cleanup is best-effort via `setTimeout`; files in OS tmpdir will also be cleaned by the OS

## Implementation Details

### Compression Engine

Use macOS built-in `sips` (Scriptable Image Processing System):

```bash
# Resize to max width (preserves aspect ratio)
sips --resampleWidth 1280 input.png --out output.jpg

# Set JPEG quality
sips -s format jpeg -s formatOptions 70 input.png --out output.jpg

# Combined
sips --resampleWidth 1280 -s format jpeg -s formatOptions 70 input.png --out output.jpg
```

No additional dependencies required.

### Tile Splitting

Use `sips` crop operations:

```bash
# Crop a tile: --cropToHeightWidth + --cropOffset
sips -c <tileH> <tileW> --cropOffset <offsetY> <offsetX> input.png --out tile_0.png
```

Note: `sips -c` crops from center by default. Use `--cropOffset` to set the crop origin.

### Tile Storage

```
/tmp/mcp-tiles-<tileId>/
  full.png          # original full screenshot
  tile_0.png        # individual tiles (created on demand or all at once)
  tile_1.png
  ...
```

### Backward Compatibility

- All new parameters are optional
- Default `compression: "medium"` changes behavior from current (raw PNG)
- Agents that need the old behavior can use `compression: "none"`

## Files Changed

- `src/index.js` — all changes in this single file:
  - Add compression preset map
  - Add `compressImage()` helper function
  - Add `splitTiles()` helper function
  - Modify `screenshot` tool: new params + compression logic + tile mode
  - Add `screenshot_tile` tool
  - Modify `sim_screenshot` tool: add compression params
  - Modify `emu_screenshot` tool: add compression params
  - Add tile cleanup timer logic

## Testing

Manual verification:
1. `screenshot()` — should return compressed JPEG ~200KB (medium default)
2. `screenshot(compression: "none")` — should return raw PNG (backward compat)
3. `screenshot(compression: "high")` — should return small JPEG ~100KB
4. `screenshot(maxWidth: 640, quality: 30)` — custom override
5. `screenshot(tile: {rows: 2, cols: 2})` — should return manifest JSON
6. `screenshot_tile(id: "...", index: 0)` — should return compressed tile
7. `sim_screenshot(compression: "low")` — should work with simulator
8. Edge case: screenshot of small window (< maxWidth) — should not upscale
