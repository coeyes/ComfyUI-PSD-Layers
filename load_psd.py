"""LoadLayersPSD — PSD를 레이어별로 읽어 composite + 레이어/마스크 쌍으로 출력하는 노드.

- 그룹 평탄화: 리프 레이어만 아래→위 순서로 추출
- 레이어 마스크/그룹 래스터 마스크/클리핑 마스크는 알파에 사전 적용
- 텍스트 레이어는 임베디드 래스터, 셰이프 레이어는 psd-tools 벡터 래스터라이즈 사용
- 블렌드 모드는 psd-tools 합성 엔진(BLEND_FUNC) 재사용
- 조정 레이어/레이어 스타일(이펙트)은 재합성 시 미반영 → 경고로 보고
- 편집(순서/숨김) 전 composite 는 PSD 임베디드 프리뷰(포토샵 렌더) 사용 = 픽셀 퍼펙트
"""

import json
import logging
import os
import uuid as uuidlib

import numpy as np
import torch
from PIL import Image

import folder_paths

MAX_LAYERS = 32  # layer_N_image/mask 쌍의 최대 개수 (초과분은 batch 출력으로만 제공)
PREVIEW_MAX = 2048  # UI 미리보기 에셋의 최대 변 길이 (출력 텐서는 항상 원본 해상도)

# 파싱 결과 캐시: (path, mtime, size) -> doc
_DOC_CACHE: dict[tuple, dict] = {}
# UI 에셋 캐시: doc_key -> {"temp_sub", "scale", "layers"} (레이어 PNG 재인코딩 방지)
_ASSET_CACHE: dict[tuple, dict] = {}
# UI 세션: node_id -> {"doc_key": ..., "temp_sub": ...}
_UI_SESSIONS: dict[str, dict] = {}

_ADJUSTMENT_KINDS = {
    "brightnesscontrast", "curves", "levels", "exposure", "vibrance", "huesaturation",
    "colorbalance", "blackandwhite", "photofilter", "channelmixer", "colorlookup",
    "invert", "posterize", "threshold", "selectivecolor", "gradientmap",
}


def _psd_path(psd: str) -> str:
    return os.path.join(folder_paths.get_input_directory(), psd)


def _resolve_path(psd: str, psd_path: str = "") -> str:
    """psd_path(서버 로컬 절대 경로)가 있으면 그것을, 없으면 input 폴더의 psd를 쓴다."""
    p = (psd_path or "").strip().strip('"').strip("'")
    if p:
        return os.path.abspath(os.path.expanduser(p))
    return _psd_path(psd)


def _doc_key(path: str) -> tuple:
    st = os.stat(path)
    return (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size)


def _mask_to_canvas(mask, W: int, H: int) -> np.ndarray | None:
    """레이어/그룹의 래스터 마스크를 캔버스 크기 float(0..1) 배열로 편다."""
    try:
        mp = mask.topil()
    except Exception:
        return None
    if mp is None:
        return None
    bg = (mask.background_color if mask.background_color is not None else 0) / 255.0
    arr = np.full((H, W), bg, dtype=np.float32)
    a = np.asarray(mp.convert("L"), dtype=np.float32) / 255.0
    left, top = mask.left, mask.top
    x0, y0 = max(left, 0), max(top, 0)
    x1, y1 = min(left + a.shape[1], W), min(top + a.shape[0], H)
    if x0 < x1 and y0 < y1:
        arr[y0:y1, x0:x1] = a[y0 - top : y1 - top, x0 - left : x1 - left]
    return arr


def _resize_rgba(rgba: np.ndarray, tw: int, th: int) -> np.ndarray:
    """프리멀티플라이드 LANCZOS 축소 (straight-alpha 리사이즈의 가장자리 색 번짐 방지)."""
    a = rgba[..., 3:].astype(np.float32) / 255.0
    pm = np.concatenate([rgba[..., :3].astype(np.float32) * a, rgba[..., 3:]], axis=-1)
    small = np.asarray(
        Image.fromarray(pm.clip(0, 255).astype(np.uint8), "RGBA").resize((tw, th), Image.Resampling.LANCZOS),
        dtype=np.float32,
    )
    oa = small[..., 3:] / 255.0
    rgb = (small[..., :3] / np.maximum(oa, 1e-4)).clip(0, 255)
    return np.concatenate([rgb, small[..., 3:]], axis=-1).astype(np.uint8)


def _save_preview(rgba: np.ndarray, scale: float, path: str) -> None:
    if scale < 1.0:
        tw = max(1, round(rgba.shape[1] * scale))
        th = max(1, round(rgba.shape[0] * scale))
        rgba = _resize_rgba(rgba, tw, th)
    Image.fromarray(rgba, "RGBA").save(path, compress_level=1)


def _layer_pixels_fast(layer) -> np.ndarray | None:
    """벡터 요소가 없는 레이어의 래스터 직독 (합성 엔진의 ~10배 속도).

    자기 래스터 마스크는 적용하지 않은 상태로 반환한다 (호출측에서 곱한다).
    """
    if getattr(layer, "vector_mask", None) is not None:
        return None
    if layer.kind not in ("pixel", "smartobject", "type"):
        return None
    try:
        arr = layer.numpy()  # float32 0..1, (h, w, 3|4), bbox 크기
    except Exception:
        return None
    if arr is None or arr.size == 0 or arr.ndim != 3:
        return None
    if arr.shape[-1] == 3:
        arr = np.concatenate([arr, np.ones_like(arr[..., :1])], axis=-1)
    elif arr.shape[-1] != 4:
        return None
    return (arr * 255.0 + 0.5).clip(0, 255).astype(np.uint8)


def _fill_opacity(layer) -> float:
    try:
        from psd_tools.constants import Tag
        key = getattr(Tag, "BLEND_FILL_OPACITY", None)
        if key is not None and layer.tagged_blocks is not None:
            v = layer.tagged_blocks.get_data(key, 255)
            return float(v) / 255.0
    except Exception:
        pass
    return 1.0


def _has_effects(layer) -> bool:
    try:
        fx = layer.effects
        return fx is not None and fx.enabled and len(list(fx)) > 0
    except Exception:
        return False


def extract_document(path: str) -> dict:
    """PSD를 파싱해 리프 레이어들(아래→위)과 문서 정보를 반환한다. 결과는 캐시된다."""
    key = _doc_key(path)
    cached = _DOC_CACHE.get(key)
    if cached is not None:
        return cached

    from psd_tools import PSDImage
    from psd_tools.constants import BlendMode

    psd = PSDImage.open(path)
    W, H = psd.size
    entries: list[dict] = []
    warnings: list[dict] = []  # {"code", "name", "detail"?} — 프론트가 로케일에 맞게 렌더

    def walk(container, anc_masks: list[np.ndarray], anc_opacity: float, anc_visible: bool):
        clip_base: dict | None = None  # 이 컨테이너 레벨의 클리핑 베이스 엔트리
        for layer in container:  # psd-tools 순회 = 아래→위
            if layer.is_group():
                gmasks = list(anc_masks)
                if layer.mask is not None and not getattr(layer.mask, "disabled", False):
                    cm = _mask_to_canvas(layer.mask, W, H)
                    if cm is not None:
                        gmasks.append(cm)
                if getattr(layer, "vector_mask", None) is not None:
                    warnings.append({"code": "group_vector_mask", "name": str(layer.name)})
                if layer.blend_mode not in (BlendMode.PASS_THROUGH, BlendMode.NORMAL):
                    warnings.append({"code": "group_blend", "name": str(layer.name),
                                     "detail": layer.blend_mode.name})
                walk(layer, gmasks, anc_opacity * layer.opacity / 255.0,
                     anc_visible and layer.visible)
                continue

            kind = layer.kind
            if kind in _ADJUSTMENT_KINDS:
                warnings.append({"code": "adjustment_layer", "name": str(layer.name)})
                continue
            if _has_effects(layer):
                warnings.append({"code": "layer_style", "name": str(layer.name)})

            bbox = layer.bbox
            if bbox is None or bbox == (0, 0, 0, 0):
                bbox = (0, 0, W, H)  # 전체 캔버스 필 레이어 등
            # 빠른 경로: 래스터 직독 (자기 마스크는 아래에서 곱한다)
            rgba = _layer_pixels_fast(layer)
            if rgba is not None and rgba.shape[:2] != (bbox[3] - bbox[1], bbox[2] - bbox[0]):
                rgba = None  # bbox와 어긋나면 엔진으로
            own_mask_pending = rgba is not None
            if rgba is None:  # 셰이프/필/벡터 마스크 → 합성 엔진 (자기 마스크 적용됨)
                try:
                    img = layer.composite(viewport=bbox, alpha=0.0, apply_icc=False)
                except Exception as exc:
                    warnings.append({"code": "render_failed", "name": str(layer.name),
                                     "detail": str(exc)})
                    continue
                if img is None:
                    continue
                rgba = np.asarray(img.convert("RGBA"), dtype=np.uint8)
            left, top = bbox[0], bbox[1]
            x0, y0 = max(left, 0), max(top, 0)
            x1 = min(left + rgba.shape[1], W)
            y1 = min(top + rgba.shape[0], H)
            if x0 >= x1 or y0 >= y1:
                continue
            rgba = rgba[y0 - top : y1 - top, x0 - left : x1 - left].copy()

            alpha = rgba[..., 3].astype(np.float32) / 255.0
            if own_mask_pending and layer.mask is not None and not getattr(layer.mask, "disabled", False):
                m = _mask_to_canvas(layer.mask, W, H)
                if m is not None:
                    alpha *= m[y0:y1, x0:x1]
            for m in anc_masks:
                alpha *= m[y0:y1, x0:x1]
            if layer.clipping:
                if clip_base is not None:
                    alpha *= _entry_alpha_region(clip_base, x0, y0, x1, y1)
                else:
                    warnings.append({"code": "clip_base_missing", "name": str(layer.name)})
            rgba[..., 3] = (alpha * 255.0 + 0.5).clip(0, 255).astype(np.uint8)

            entry = {
                "name": str(layer.name or f"Layer {len(entries) + 1}"),
                "rect": (x0, y0, x1 - x0, y1 - y0),
                "rgba": rgba,
                "blend": layer.blend_mode,
                "opacity": (layer.opacity / 255.0) * _fill_opacity(layer) * anc_opacity,
                "visible": bool(layer.visible and anc_visible),
                "kind": kind,
            }
            entries.append(entry)
            if not layer.clipping:
                clip_base = entry

    walk(psd, [], 1.0, True)

    embedded = None
    try:
        pil = psd.topil()
        if pil is not None and pil.size == (W, H):
            embedded = pil.convert("RGBA")
    except Exception:
        pass

    doc = {"canvas": (W, H), "entries": entries, "warnings": warnings, "embedded": embedded}
    _DOC_CACHE.clear()  # 최신 문서 1개만 유지 (대용량 배열 메모리 관리)
    _DOC_CACHE[key] = doc
    return doc


def _entry_alpha_region(entry: dict, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    """엔트리의 알파를 캔버스 좌표 (x0,y0)-(x1,y1) 영역으로 잘라 반환 (영역 밖은 0)."""
    out = np.zeros((y1 - y0, x1 - x0), dtype=np.float32)
    ex, ey, ew, eh = entry["rect"]
    ix0, iy0 = max(x0, ex), max(y0, ey)
    ix1, iy1 = min(x1, ex + ew), min(y1, ey + eh)
    if ix0 < ix1 and iy0 < iy1:
        out[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0] = (
            entry["rgba"][iy0 - ey : iy1 - ey, ix0 - ex : ix1 - ex, 3].astype(np.float32) / 255.0
        )
    return out


def default_state(doc: dict) -> tuple[list[int], set[int]]:
    order = list(range(len(doc["entries"])))
    hidden = {i for i, e in enumerate(doc["entries"]) if not e["visible"]}
    return order, hidden


def parse_state(doc: dict, layer_state: str) -> tuple[list[int], set[int], bool]:
    """위젯의 상태 JSON을 (order, hidden, edited)로 해석한다. 무효하면 기본값."""
    d_order, d_hidden = default_state(doc)
    try:
        st = json.loads(layer_state) if layer_state else {}
        order = [int(i) for i in st.get("order", [])]
        hidden = {int(i) for i in st.get("hidden", [])}
        if sorted(order) != d_order or not hidden.issubset(set(d_order)):
            return d_order, d_hidden, False
        return order, hidden, (order != d_order or hidden != d_hidden)
    except Exception:
        return d_order, d_hidden, False


def composite_entries(doc: dict, order: list[int], hidden: set[int]) -> Image.Image:
    """psd-tools 블렌드 함수로 현재 순서/가시성 기준 캔버스 합성 (RGBA)."""
    from psd_tools.composite.blend import BLEND_FUNC, normal

    W, H = doc["canvas"]
    C = np.zeros((H, W, 3), dtype=np.float32)
    A = np.zeros((H, W, 1), dtype=np.float32)
    for idx in order:
        if idx in hidden:
            continue
        e = doc["entries"][idx]
        x, y, w, h = e["rect"]
        src = e["rgba"].astype(np.float32) / 255.0
        Cs = src[..., :3]
        As = src[..., 3:4] * e["opacity"]
        Cd = C[y : y + h, x : x + w]
        Ad = A[y : y + h, x : x + w]
        B = np.clip(BLEND_FUNC.get(e["blend"], normal)(Cd, Cs), 0.0, 1.0)
        Ao = As + Ad * (1.0 - As)
        Co = (As * (1.0 - Ad) * Cs + As * Ad * B + (1.0 - As) * Ad * Cd) / np.maximum(Ao, 1e-6)
        C[y : y + h, x : x + w] = Co
        A[y : y + h, x : x + w] = Ao
    out = np.concatenate([C, A], axis=-1)
    return Image.fromarray((out * 255.0 + 0.5).clip(0, 255).astype(np.uint8), "RGBA")


def compose_for_state(doc: dict, order: list[int], hidden: set[int], edited: bool) -> Image.Image:
    """편집 전이면 임베디드 포토샵 렌더(픽셀 퍼펙트), 편집 후면 엔진 재합성."""
    if not edited and doc["embedded"] is not None:
        return doc["embedded"]
    return composite_entries(doc, order, hidden)


def _ensure_assets(doc: dict, key: tuple) -> dict:
    """레이어 미리보기 PNG들을 temp에 준비 (문서 단위 캐시 + 병렬 인코딩 + 다운스케일)."""
    cached = _ASSET_CACHE.get(key)
    if cached is not None:
        full = os.path.join(folder_paths.get_temp_directory(), *cached["temp_sub"].split("/"))
        if os.path.isdir(full):
            return cached

    W, H = doc["canvas"]
    scale = min(1.0, PREVIEW_MAX / max(W, H))
    token = uuidlib.uuid4().hex[:8]
    temp_sub = f"load_layers_psd/{token}"
    temp_full = os.path.join(folder_paths.get_temp_directory(), "load_layers_psd", token)
    os.makedirs(temp_full, exist_ok=True)

    from concurrent.futures import ThreadPoolExecutor

    def save_layer(item):
        i, e = item
        fname = f"layer_{i:02}.png"
        _save_preview(e["rgba"], scale, os.path.join(temp_full, fname))
        return fname

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
        files = list(ex.map(save_layer, enumerate(doc["entries"])))

    assets = {"temp_sub": temp_sub, "temp_full": temp_full, "scale": scale, "files": files}
    _ASSET_CACHE.clear()  # 최신 문서 1개만 유지
    _ASSET_CACHE[key] = assets
    return assets


def build_ui_payload(path: str, psd_name: str, node_id: str, layer_state: str) -> dict:
    """프론트 패널용 payload 생성: temp 에셋 준비 + 세션 등록."""
    doc = extract_document(path)
    key = _doc_key(path)
    order, hidden, edited = parse_state(doc, layer_state)
    assets = _ensure_assets(doc, key)
    temp_sub = assets["temp_sub"]

    layers = []
    for i, e in enumerate(doc["entries"]):
        layers.append({
            "index": i,
            "name": e["name"],
            "rect": list(e["rect"]),
            "blend": e["blend"].name,
            "kind": e["kind"],
            "visible_default": e["visible"],
            "image": {"filename": assets["files"][i], "subfolder": temp_sub, "type": "temp"},
        })
    comp = compose_for_state(doc, order, hidden, edited)
    comp_name = f"composite_{uuidlib.uuid4().hex[:6]}.png"
    _save_preview(np.asarray(comp), assets["scale"], os.path.join(assets["temp_full"], comp_name))

    _UI_SESSIONS[str(node_id)] = {
        "path": path, "doc_key": key, "temp_sub": temp_sub,
        "temp_full": assets["temp_full"], "scale": assets["scale"],
    }
    return {
        "node_id": str(node_id),
        "psd": psd_name,
        "canvas": list(doc["canvas"]),
        "layers": layers,
        "order": order,
        "hidden": sorted(hidden),
        "edited": edited,
        "warnings": doc["warnings"],
        "max_layers": MAX_LAYERS,
        "composite": {"filename": comp_name, "subfolder": temp_sub, "type": "temp"},
    }


class LoadLayersPSD:
    @classmethod
    def INPUT_TYPES(cls):
        input_dir = folder_paths.get_input_directory()
        try:
            files = sorted(
                f for f in os.listdir(input_dir)
                if f.lower().endswith((".psd", ".psb")) and os.path.isfile(os.path.join(input_dir, f))
            )
        except FileNotFoundError:
            files = []
        return {
            "required": {
                "psd": (files or [""], {"tooltip": "PSD file in the input folder. You can also drag & drop a file onto the node."}),
                "layer_state": ("STRING", {"default": "", "tooltip": "Layer order/visibility state edited in the panel (managed automatically)."}),
            },
            "optional": {
                "psd_path": ("STRING", {
                    "default": "",
                    "tooltip": "Absolute path on the server (.psd/.psb). When set, this file is read instead of "
                               "the psd combo above. Useful for the edit-in-Photoshop-then-Reload workflow. "
                               "For remote access the path must be readable by the server.",
                }),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "MASK") + ("IMAGE", "MASK") * MAX_LAYERS
    RETURN_NAMES = ("composite_image", "layers_batch", "masks_batch") + tuple(
        n for i in range(1, MAX_LAYERS + 1) for n in (f"layer_{i}_image", f"layer_{i}_mask")
    )
    FUNCTION = "load"
    CATEGORY = "image"
    DESCRIPTION = (
        "Load a PSD layer by layer: composite image + layer batches + per-layer image/mask pairs. "
        "Reorder/hide layers in the panel to affect the composite (masks, clipping and blend modes "
        "supported; adjustment layers and layer styles are not applied when recompositing)."
    )
    SEARCH_ALIASES = ["psd", "photoshop", "load psd", "layers"]

    @classmethod
    def IS_CHANGED(cls, psd, layer_state="", psd_path="", **kwargs):
        path = _resolve_path(psd, psd_path)
        try:
            st = os.stat(path)
            return f"{path}:{st.st_mtime_ns}:{st.st_size}:{layer_state}"
        except OSError:
            return float("nan")

    @classmethod
    def VALIDATE_INPUTS(cls, psd, psd_path="", **kwargs):
        path = _resolve_path(psd, psd_path)
        if (psd_path or "").strip():
            if not path.lower().endswith((".psd", ".psb")):
                return f"psd_path must be a .psd/.psb file: {path}"
            if not os.path.isfile(path):
                return f"psd_path file not found (path on the server): {path}"
            return True
        if not psd or not os.path.isfile(path):
            return f"PSD file not found: {psd}"
        return True

    def load(self, psd, layer_state="", psd_path="", unique_id=None):
        path = _resolve_path(psd, psd_path)
        doc = extract_document(path)
        order, hidden, edited = parse_state(doc, layer_state)
        W, H = doc["canvas"]
        entries = doc["entries"]
        n = len(entries)
        if n > MAX_LAYERS:
            logging.warning(
                "LoadLayersPSD: only %d of %d layers get individual outputs (batch outputs include all).",
                MAX_LAYERS, n,
            )

        comp = compose_for_state(doc, order, hidden, edited)
        composite = torch.from_numpy(
            np.asarray(comp.convert("RGB"), dtype=np.float32) / 255.0
        ).unsqueeze(0)

        # 배치: 풀캔버스 배치 (아래→위 원본 순서, 전 레이어)
        layers_batch = torch.zeros((max(n, 1), H, W, 3))
        masks_batch = torch.ones((max(n, 1), H, W))
        outputs: list[torch.Tensor] = []
        blank_img = torch.zeros((1, 1, 1, 3))
        blank_mask = torch.ones((1, 1, 1))
        for i in range(MAX_LAYERS):
            if i < n:
                e = entries[i]
                x, y, w, h = e["rect"]
                rgba = torch.from_numpy(e["rgba"].astype(np.float32) / 255.0)
                layers_batch[i, y : y + h, x : x + w] = rgba[..., :3]
                masks_batch[i, y : y + h, x : x + w] = 1.0 - rgba[..., 3]
                outputs.append(rgba[..., :3].unsqueeze(0).contiguous())
                outputs.append((1.0 - rgba[..., 3]).unsqueeze(0).contiguous())
            else:
                outputs.append(blank_img)
                outputs.append(blank_mask)
        # MAX 초과 레이어는 batch에만 채운다
        for i in range(MAX_LAYERS, n):
            e = entries[i]
            x, y, w, h = e["rect"]
            rgba = torch.from_numpy(e["rgba"].astype(np.float32) / 255.0)
            layers_batch[i, y : y + h, x : x + w] = rgba[..., :3]
            masks_batch[i, y : y + h, x : x + w] = 1.0 - rgba[..., 3]

        result = (composite, layers_batch, masks_batch, *outputs)
        ui = {"load_layers_psd": [build_ui_payload(
            path, (psd_path or "").strip() or psd, str(unique_id), layer_state)]}
        return {"ui": ui, "result": result}


def register_routes():
    """PromptServer 라우트 등록 (__init__에서 서버 존재 시 호출)."""
    import asyncio

    from aiohttp import web
    from server import PromptServer

    routes = PromptServer.instance.routes

    async def _json_body(request):
        try:
            return await request.json()
        except Exception:
            return None

    @routes.post("/load_layers_psd/inspect")
    async def llp_inspect(request):
        data = await _json_body(request)
        if data is None:
            return web.json_response({"error": "Invalid JSON body", "code": "bad_json"}, status=400)
        psd = str(data.get("psd") or "")
        psd_path = str(data.get("psd_path") or "")
        path = _resolve_path(psd, psd_path)
        if not path.lower().endswith((".psd", ".psb")):
            return web.json_response(
                {"error": f"Not a PSD/PSB file: {path}", "code": "not_psd", "detail": path},
                status=400)
        if not os.path.isfile(path):
            return web.json_response(
                {"error": f"PSD file not found (path on the server): {path}",
                 "code": "file_not_found", "detail": path},
                status=404)
        try:
            payload = await asyncio.to_thread(
                build_ui_payload, path, psd_path.strip() or psd,
                str(data.get("node_id")), str(data.get("layer_state") or "")
            )
        except Exception as exc:
            logging.exception("LoadLayersPSD: inspect failed")
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response(payload)

    @routes.post("/load_layers_psd/stat")
    async def llp_stat(request):
        """auto reload 폴링용: 현재 소스 PSD의 mtime/size 만 가볍게 반환."""
        data = await _json_body(request)
        if data is None:
            return web.json_response({"error": "Invalid JSON body", "code": "bad_json"}, status=400)
        path = _resolve_path(str(data.get("psd") or ""), str(data.get("psd_path") or ""))
        try:
            st = os.stat(path)
        except OSError:
            return web.json_response({"error": "file not found", "code": "file_not_found"}, status=404)
        return web.json_response({"stat": f"{st.st_mtime_ns}:{st.st_size}"})

    @routes.post("/load_layers_psd/recomposite")
    async def llp_recomposite(request):
        data = await _json_body(request)
        if data is None:
            return web.json_response({"error": "Invalid JSON body", "code": "bad_json"}, status=400)
        session = _UI_SESSIONS.get(str(data.get("node_id")))
        if session is None:
            return web.json_response(
                {"error": "No session. Reload the PSD.", "code": "session_missing"}, status=404)
        doc = _DOC_CACHE.get(session["doc_key"])
        if doc is None:
            try:
                doc = await asyncio.to_thread(extract_document, session["path"])
            except Exception as exc:
                return web.json_response({"error": str(exc)}, status=500)
        n = len(doc["entries"])
        d_order, d_hidden = default_state(doc)
        order = [int(i) for i in data.get("order", []) if 0 <= int(i) < n]
        order += [i for i in d_order if i not in order]
        hidden = {int(i) for i in data.get("hidden", []) if 0 <= int(i) < n}
        edited = order != d_order or hidden != d_hidden

        def render() -> dict:
            comp = compose_for_state(doc, order, hidden, edited)
            fname = f"composite_{uuidlib.uuid4().hex[:6]}.png"
            full = session.get("temp_full") or os.path.join(
                folder_paths.get_temp_directory(), session["temp_sub"])
            os.makedirs(full, exist_ok=True)
            _save_preview(np.asarray(comp), session.get("scale", 1.0), os.path.join(full, fname))
            return {"filename": fname, "subfolder": session["temp_sub"], "type": "temp"}

        try:
            file_info = await asyncio.to_thread(render)
        except Exception as exc:
            logging.exception("LoadLayersPSD: recomposite failed")
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response({"composite": file_info, "edited": edited})

    @routes.post("/load_layers_psd/upload")
    async def llp_upload(request):
        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "file":
            return web.json_response({"error": "Missing 'file' field", "code": "bad_upload"}, status=400)
        name = os.path.basename(field.filename or "upload.psd")
        if not name.lower().endswith((".psd", ".psb")):
            return web.json_response(
                {"error": "Only PSD/PSB files can be uploaded", "code": "bad_type"}, status=400)
        input_dir = folder_paths.get_input_directory()
        os.makedirs(input_dir, exist_ok=True)
        base, ext = os.path.splitext(name)
        final = name
        k = 1
        while os.path.exists(os.path.join(input_dir, final)):
            final = f"{base}_{k}{ext}"
            k += 1
        with open(os.path.join(input_dir, final), "wb") as f:
            while True:
                chunk = await field.read_chunk(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        return web.json_response({"name": final})
