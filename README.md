# ComfyUI-PSD-Layers

PSD layer workflow nodes for ComfyUI. No Photoshop installation required.

[한국어 문서 (Korean)](README.ko.md)

- **Save Seedream Layers PSD** — save ByteDance Seedream layer-separation results as a PSD
  where **every layer is preserved as an embedded smart object**: nothing gets flattened,
  so each element stays individually movable, transformable and re-editable in Photoshop
- **Load Layers PSD** — load any PSD layer by layer with a composite output and per-layer
  image/mask outputs

Both nodes ship an interactive panel: stacked preview, alpha-accurate object picking with
bounding-box highlight, drag-to-reorder z order, per-layer visibility toggles, and a status bar.

<p align="center">
<img src="https://raw.githubusercontent.com/coeyes/ComfyUI-PSD-Layers/main/docs/screenshot_save.png" alt="Save Seedream Layers PSD" height="560">
<img src="https://raw.githubusercontent.com/coeyes/ComfyUI-PSD-Layers/main/docs/screenshot_load.png" alt="Load Layers PSD" height="560">
</p>

Unlike canvas-editor nodes (e.g. XISER Canvas) that pack images into their own state format,
this pack is a **native PSD round-trip**: it reads real PSDs (masks, clipping, blend modes),
writes real smart-object PSDs that Photoshop opens losslessly, and wires everything into the
graph as per-layer outputs.

## Node: Save Seedream Layers PSD

Connect the outputs of `ByteDanceSeedreamLayerSeparationNode` (everything except
`layer_stack`) straight into the node, top to bottom.

### Inputs

| Input | Type | Connect to |
|---|---|---|
| `base_image` | IMAGE | base_image (background plate) |
| `base_mask` | MASK | base_mask (1 = transparent) |
| `layers` | IMAGE | layers batch (full canvas / minimal size both supported) |
| `masks` | MASK | masks (1 = transparent) |
| `bboxes` | BOUNDING_BOX | bboxes (uses name/z_index/content_rect from metadata) |
| `filename_prefix` | STRING | same prefix templates as SaveImage (`subfolder/name`, `%date:...%`, …) |

### Outputs

| Output | Type | Description |
|---|---|---|
| `composite_image` | IMAGE | composite of all layers (identical to the saved PNG) |

### Behavior

- On execution, saves `<prefix>_00001_.psd` + `<prefix>_00001_.png` (composite preview) to the
  `output` folder.
- The PSD stacks the background plus each layer as an **embedded smart object** in z-index
  order — the AI-separated elements are not rasterized into flat layers, so in Photoshop each
  one keeps its own pixels and can be repositioned, scaled or swapped without quality loss.
- Panel: click objects in the preview (alpha hit test) or rows in the list to select,
  **drag rows to change z order**, toggle visibility with the eye icon.
- **Re-save PSD button**: saves again with the adjusted order/visibility without re-running the
  graph (counter increments). Hidden layers are included in the PSD with `visible=false` and
  excluded from the preview PNG.

Note: the Save button session lives in server memory — after a server restart, run the node once
before using the button.

## Node: Load Layers PSD

Pick a PSD from the `input` folder, or drag & drop a file onto the node (general purpose — not
Seedream-specific). PSB is supported too.

Set `psd_path` to an **absolute path on the server** (pasted quotes are fine) to read that file
directly instead of the input-folder combo — ideal for the edit-in-Photoshop-then-Reload loop.
For remote access the path must be readable by the server; otherwise use drag & drop upload.

### Outputs

| Output | Type | Description |
|---|---|---|
| `composite_image` | IMAGE | full composite. Before any edit: the PSD's embedded Photoshop render (pixel-perfect). After editing: recomposited with the psd-tools blend engine |
| `layers_batch` / `masks_batch` | IMAGE / MASK | all layers on full-size canvases (no layer-count limit) |
| `layer_N_image` / `layer_N_mask` | IMAGE / MASK | per-layer tight-crop pairs (up to 32, fixed bottom-to-top PSD order). Unused slots collapse automatically in the UI |

### Behavior

- **Group flattening**: only leaf layers are extracted. Layer masks, group raster masks and
  clipping masks are pre-applied to the alpha.
- **Text layers** use the embedded raster (no fonts needed); **shape layers** are rasterized via
  psd-tools vector masks.
- **All Photoshop blend modes** supported (reusing psd-tools `BLEND_FUNC`; measured mean error
  vs. the Photoshop render ≈ 0.03/255).
- **Adjustment layers / layer styles (effects)**: not applied when recompositing — a persistent
  warning appears in the status bar. They are present in the unedited composite (embedded
  render).
- Reordering / hiding layers in the panel is serialized into the `layer_state` widget →
  invalidates the cache and affects the composite output. Per-layer output slots keep the
  original PSD order regardless of edits.
- **Reload button**, plus automatic reload on the next run when the source file changes
  (mtime-based `IS_CHANGED`).

## Installation

Copy this folder into `ComfyUI/custom_nodes/`, then:

```
pip install -r requirements.txt   # psd-tools[composite]>=1.18.0, pillow, numpy
```

## Example workflows

`example_workflows/` ships ready-made templates (also exposed in ComfyUI's workflow
template browser).

**`seedream_separation_to_psd`** — image → Seedream layer separation → smart-object PSD:

<img src="https://raw.githubusercontent.com/coeyes/ComfyUI-PSD-Layers/main/docs/workflow_save.png" alt="Seedream separation to PSD workflow">

**`load_psd_layers`** — PSD → composite / per-layer outputs:

<img src="https://raw.githubusercontent.com/coeyes/ComfyUI-PSD-Layers/main/docs/workflow_load.png" alt="Load PSD layers workflow">

## Localization

Node tooltips/descriptions and the panel UI follow the ComfyUI language setting
(`Settings → Locale`). Included: English, 한국어, 日本語, 中文, Español, Français, Русский.
Panel texts switch immediately; translations live in `locales/` and `web/js/psdI18n.js`.

## Implementation notes

psd-tools does not officially support *writing* smart objects. The Save node assembles the
`SoLd`/`PlLd`/`lnk2` binary blocks from base64 templates extracted from a real Photoshop file,
patching only uuid/transform/size at runtime, and works around a psd-tools bug (missing
`contentID` in LinkedLayer v8 that makes Photoshop reject the file) via a `LinkedLayerV8`
subclass. The full reverse-engineering write-up lives in
[TECH.md](https://github.com/coeyes/Fal.ai-Seedream5-Layers-To-Save-PSD/blob/master/TECH.md)
of the companion [fal.ai CLI/GUI project](https://github.com/coeyes/Fal.ai-Seedream5-Layers-To-Save-PSD).

The Load node reads raster layers directly (`layer.numpy()`, ~10x faster than the compositing
engine) and falls back to the engine only for vector content. UI preview assets are
LANCZOS-downscaled (2048 px cap) with premultiplied alpha; graph-side rendering uses
high-quality `createImageBitmap` caching with a zoom-settle redraw.

`_smoke_test*.py` provide offline verification (builder, save node, load node).

## License / Author

MIT License — see [LICENSE](LICENSE).

Author: **Hyeongjik Song** <coeyes@gmail.com>
