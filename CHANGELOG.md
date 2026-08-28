# Changelog

All notable changes to this project are documented in this file.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions match
the `version` field in `pyproject.toml` (Comfy Registry releases).

## [1.0.5] - 2026-08-28

### Fixed
- The bundled example workflows were serialized in the pre-1.0.1 widget layout and blanked
  `filename_prefix` when opened from the template browser; trimmed to the current format.

### Changed
- Example workflows polished: the separation example ships with the prompt
  `layerize objects` filled in, and the load example taps `layer_9_image` into its own
  PreviewImage to demonstrate per-layer wiring.
- Docs: full example-workflow screenshots in both READMEs, Save-first ordering,
  stronger emphasis that saved PSDs keep every layer as an embedded smart object,
  and links to the companion fal.ai project's TECH.md (reverse-engineering write-up)
  instead of local paths.

## [1.0.4] - 2026-08-25

### Fixed
- Dependency is now `psd-tools[composite]` — the extra (aggdraw, scipy, scikit-image) is
  required by the psd-tools compositing engine for vector/shape rasterization and richer
  rendering; without it shape/fill layers could fail to render.

## [1.0.3] - 2026-08-25

### Changed
- Registry description rewritten with the phrases people actually search for
  ("Load PSD", "save PSD", "Seedream layer PSD") — the registry search matches phrases.
- `SaveSeedreamLayersPSD` gained node-search aliases (psd, save psd, seedream,
  photoshop, layers, smart object); `LoadLayersPSD` already had its set.

## [1.0.2] - 2026-08-25

### Changed
- **Save panel**: `base_image` now appears in the layer list as a fixed bottom row —
  selectable (full-canvas bounding box, picked when clicking where no upper layer covers),
  hideable via the eye toggle, but not draggable. Layer count / caption now match the Load
  panel for the same file.
- The bottom PSD layer is named `base_image` (matching the node input) instead of the
  arbitrary `background`.

### Added
- Hiding `base_image` saves it into the PSD with `visible=false` and renders the preview
  PNG on a transparent canvas.

## [1.0.1] - 2026-08-25

### Fixed
- Widget values (`filename_prefix`, `psd`, `psd_path`, `layer_state`) were reset after a
  page reload: the DOM panel widget leaked an empty entry into `widgets_values`
  (the frontend serializer only honors `widget.serialize === false` on the widget object,
  not the `addDOMWidget` option), which desynced value restoration. Workflows saved with
  the old layout are healed automatically from `widgets_values_named`.

## [1.0.0] - 2026-08-25

Initial release.

- **Load Layers PSD** — generic PSD/PSB loader: composite output (embedded Photoshop
  render when unedited), full-canvas layer/mask batches, up to 32 per-layer tight-crop
  image/mask output pairs (unused slots collapse in the UI). Group flattening with
  layer/group/clipping masks pre-applied, text/shape layer rasterization, all Photoshop
  blend modes (psd-tools engine). Interactive panel: alpha hit-test selection,
  drag-to-reorder z order, visibility toggles, status bar with warnings
  (adjustment layers / layer styles are not applied when recompositing), Reload button +
  mtime-based auto reload, drag & drop upload, `psd_path` server-path override.
- **Save Seedream Layers PSD** — saves ByteDance Seedream layer-separation results as an
  embedded smart-object PSD + composite PNG (SaveImage-style filename prefix templates).
  Interactive panel with re-save (order/visibility) without re-running the graph.
- Localized UI and node tooltips: en, ko, ja, zh, es, fr, ru — follows the ComfyUI locale
  setting, switches without a page reload.
- Example workflows, MIT license.
