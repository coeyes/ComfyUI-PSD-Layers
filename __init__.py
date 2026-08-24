"""SaveSeedreamLayersPSD — ByteDance Seedream 레이어 분리 결과를
스마트 오브젝트 PSD로 저장·편집하는 커스텀 노드.

ByteDanceSeedreamLayerSeparationNode 의 출력(base_image, base_mask, layers,
masks, bboxes)을 그대로 받아:
  - 프론트엔드 위젯에서 배경 + 레이어 합성 미리보기, 레이어 리스트,
    바운딩박스 선택, 드래그로 z 순서 변경, 눈 아이콘으로 숨김 토글
  - 실행 시 output 폴더에 PSD(임베디드 스마트 오브젝트) + 미리보기 PNG 저장
  - 위젯의 Save PSD 버튼으로 조정된 순서/가시성으로 재저장
"""

import asyncio
import json
import logging
import os
import uuid as uuidlib

import numpy as np
import torch
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import folder_paths
from comfy.cli_args import args

# 노드 실행 결과 캐시 (node_id -> 세션). Save 버튼이 재실행 없이 PSD를 다시 만들 때 사용.
_SESSIONS: dict[str, dict] = {}


def _to_uint8(t) -> np.ndarray:
    return np.clip(t.cpu().numpy() * 255.0, 0, 255).astype(np.uint8)


def _parse_frame(bboxes) -> list[dict]:
    """BOUNDING_BOX 출력에서 첫 프레임의 박스 리스트를 얻는다."""
    if isinstance(bboxes, (list, tuple)) and bboxes:
        if isinstance(bboxes[0], (list, tuple)):
            return [b for b in bboxes[0] if isinstance(b, dict)]
        if isinstance(bboxes[0], dict):
            return [b for b in bboxes if isinstance(b, dict)]
    return []


def _save_outputs(
    session: dict, order: list[int], hidden: set[int], filename_prefix: str
) -> tuple[dict, Image.Image]:
    """현재 순서/가시성으로 PSD + 미리보기 PNG를 output 폴더에 저장한다.

    (저장 파일 정보, 합성 미리보기 RGBA 이미지)를 반환한다.
    """
    from .psd_builder import build_psd

    W, H = session["canvas"]
    full_output_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
        filename_prefix, folder_paths.get_output_directory(), W, H
    )
    stem = f"{filename}_{counter:05}_"
    by_index = session["by_index"]

    # PSD: base_image(스마트 오브젝트) + 레이어들 아래→위. 숨긴 레이어는 visible=False로 포함.
    # hidden 의 -1 은 base_image 를 뜻한다.
    base_visible = -1 not in hidden
    psd_entries = [{"name": "base_image", "image": session["base"], "rect": (0, 0, W, H), "visible": base_visible}]
    for idx in order:
        e = by_index[idx]
        psd_entries.append({
            "name": e["name"], "image": e["image"], "rect": e["rect"],
            "visible": idx not in hidden,
        })
    psd_path = os.path.join(full_output_folder, stem + ".psd")
    build_psd(psd_entries, (W, H), psd_path)

    # 미리보기 PNG: 숨긴 레이어 제외하고 합성
    comp = session["base"].copy() if base_visible else Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for idx in order:
        if idx in hidden:
            continue
        e = by_index[idx]
        comp.alpha_composite(e["image"], dest=(e["rect"][0], e["rect"][1]))
    metadata = None
    if not args.disable_metadata:
        metadata = PngInfo()
        if session.get("prompt") is not None:
            metadata.add_text("prompt", json.dumps(session["prompt"]))
        for k, v in (session.get("extra_pnginfo") or {}).items():
            metadata.add_text(k, json.dumps(v))
    comp.save(os.path.join(full_output_folder, stem + ".png"), pnginfo=metadata, compress_level=4)

    return {
        "psd": {"filename": stem + ".psd", "subfolder": subfolder, "type": "output"},
        "png": {"filename": stem + ".png", "subfolder": subfolder, "type": "output"},
    }, comp


class SaveSeedreamLayersPSD:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "base_image": ("IMAGE", {"tooltip": "base_image from the layer separation node (background plate)."}),
                "base_mask": ("MASK", {"tooltip": "base_mask from the layer separation node (1 = transparent)."}),
                "layers": ("IMAGE", {"tooltip": "layers batch from the layer separation node (bottom to top)."}),
                "masks": ("MASK", {"tooltip": "masks from the layer separation node (1 = transparent, LoadImage convention)."}),
                "bboxes": ("BOUNDING_BOX", {"forceInput": True, "tooltip": "bboxes from the layer separation node (placement boxes + metadata)."}),
                "filename_prefix": ("STRING", {
                    "default": "seedream_layers",
                    "tooltip": "Prefix for the saved files. Supports the same templates as SaveImage, e.g. %date:...%.",
                }),
            },
            "hidden": {
                "prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("composite_image",)
    OUTPUT_TOOLTIPS = ("Preview image compositing all layers (identical to the saved PNG).",)
    FUNCTION = "save_layers"
    OUTPUT_NODE = True
    CATEGORY = "image"
    DESCRIPTION = (
        "Save Seedream layer separation results as a smart-object PSD + preview PNG in the output "
        "folder. Reorder/hide layers in the panel, then press Re-save PSD to save again."
    )
    SEARCH_ALIASES = ["psd", "save psd", "seedream", "photoshop", "layers", "smart object"]

    def save_layers(self, base_image, base_mask, layers, masks, bboxes, filename_prefix="seedream_layers",
                    prompt=None, extra_pnginfo=None, unique_id=None):
        H, W = int(base_image.shape[1]), int(base_image.shape[2])

        # 배경 RGBA (base_mask: 1 = 투명)
        base_rgb = _to_uint8(base_image[0][..., :3])
        if base_mask is not None and base_mask.shape[-2:] == (H, W):
            base_a = _to_uint8(1.0 - base_mask[0])
        else:
            base_a = np.full((H, W), 255, dtype=np.uint8)
        base_pil = Image.fromarray(np.dstack([base_rgb, base_a]), "RGBA")

        boxes = _parse_frame(bboxes)
        n = int(layers.shape[0])
        lh, lw = int(layers.shape[1]), int(layers.shape[2])
        if len(boxes) != n:
            logging.warning("SaveSeedreamLayersPSD: %d layers vs %d bboxes — matching by index order.", n, len(boxes))

        entries = []
        for i in range(n):
            box = boxes[i] if i < len(boxes) else {}
            md = box.get("metadata") or {}
            cr = md.get("content_rect")
            if not (isinstance(cr, (list, tuple)) and len(cr) == 4):
                cr = [0, 0, lw, lh]
            cx, cy, cw, ch = (int(v) for v in cr)
            # 레이어 프레임 내부 콘텐츠 영역 (프레임 좌표) → 텐서 범위로 클램프
            lx0, ly0 = max(cx, 0), max(cy, 0)
            lx1, ly1 = min(cx + cw, lw), min(cy + ch, lh)
            # 캔버스 배치 위치 = 박스 위치 + 프레임 내 오프셋
            px = int(box.get("x", 0)) + lx0
            py = int(box.get("y", 0)) + ly0
            if px < 0:
                lx0 -= px
                px = 0
            if py < 0:
                ly0 -= py
                py = 0
            pw = min(lx1 - lx0, W - px)
            ph = min(ly1 - ly0, H - py)
            name = md.get("name") or f"Layer {i + 1}"
            if pw <= 0 or ph <= 0:
                logging.warning("SaveSeedreamLayersPSD: layer %d (%r) has an empty region, skipping.", i, name)
                continue
            rgb = _to_uint8(layers[i, ly0:ly0 + ph, lx0:lx0 + pw, :3])
            a = _to_uint8(1.0 - masks[i, ly0:ly0 + ph, lx0:lx0 + pw])
            zi = md.get("z_index")
            entries.append({
                "index": i,
                "name": str(name),
                "z": int(zi) if isinstance(zi, (int, float)) and not isinstance(zi, bool) else i + 1,
                "rect": (px, py, pw, ph),
                "image": Image.fromarray(np.dstack([rgb, a]), "RGBA"),
            })

        entries.sort(key=lambda e: (e["z"], e["index"]))
        order = [e["index"] for e in entries]

        session = {
            "canvas": (W, H),
            "base": base_pil,
            "by_index": {e["index"]: e for e in entries},
            "default_order": order,
            "prompt": prompt,
            "extra_pnginfo": extra_pnginfo,
            "filename_prefix": filename_prefix,
        }
        _SESSIONS[str(unique_id)] = session

        # 프론트엔드 미리보기용 에셋을 temp에 저장 (다운스케일 + 병렬 인코딩)
        from concurrent.futures import ThreadPoolExecutor

        from .load_psd import PREVIEW_MAX, _save_preview

        token = uuidlib.uuid4().hex[:8]
        temp_sub = f"seedream_psd/{token}"
        temp_full = os.path.join(folder_paths.get_temp_directory(), "seedream_psd", token)
        os.makedirs(temp_full, exist_ok=True)
        scale = min(1.0, PREVIEW_MAX / max(W, H))

        def save_asset(item):
            fname, pil = item
            _save_preview(np.asarray(pil), scale, os.path.join(temp_full, fname))

        jobs = [("base.png", base_pil)] + [(f"layer_{e['index']:02}.png", e["image"]) for e in entries]
        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
            list(ex.map(save_asset, jobs))
        layer_infos = [{
            "index": e["index"],
            "name": e["name"],
            "z_index": e["z"],
            "rect": list(e["rect"]),
            "image": {"filename": f"layer_{e['index']:02}.png", "subfolder": temp_sub, "type": "temp"},
        } for e in entries]

        # 기본 순서(전부 표시)로 1차 저장
        saved, comp = _save_outputs(session, order, set(), filename_prefix)
        composite = torch.from_numpy(
            np.asarray(comp.convert("RGB"), dtype=np.float32) / 255.0
        ).unsqueeze(0)

        payload = {
            "node_id": str(unique_id),
            "canvas": [W, H],
            "base": {"filename": "base.png", "subfolder": temp_sub, "type": "temp"},
            "layers": layer_infos,  # 아래→위 (z_index 오름차순)
            "saved": saved,
        }
        return {"ui": {"seedream_psd": [payload]}, "result": (composite,)}


from .load_psd import LoadLayersPSD  # noqa: E402

NODE_CLASS_MAPPINGS = {
    "SaveSeedreamLayersPSD": SaveSeedreamLayersPSD,
    "LoadLayersPSD": LoadLayersPSD,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "SaveSeedreamLayersPSD": "Save Seedream Layers PSD",
    "LoadLayersPSD": "Load Layers PSD",
}
WEB_DIRECTORY = "./web/js"

try:
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.post("/seedream_psd/save")
    async def seedream_psd_save(request):
        data = await request.json()
        session = _SESSIONS.get(str(data.get("node_id")))
        if session is None:
            return web.json_response(
                {"error": "No session (e.g. server restarted). Run the node again.",
                 "code": "session_missing"},
                status=404,
            )
        valid = set(session["by_index"].keys())
        order = [int(i) for i in data.get("order", []) if int(i) in valid]
        order += [i for i in session["default_order"] if i not in order]
        # -1 = base_image 숨김
        hidden = {int(i) for i in data.get("hidden", []) if int(i) in valid or int(i) == -1}
        prefix = data.get("filename_prefix") or session["filename_prefix"]
        try:
            saved, _ = await asyncio.to_thread(_save_outputs, session, order, hidden, prefix)
        except Exception as exc:
            logging.exception("SaveSeedreamLayersPSD: save failed")
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response(saved)

    from .load_psd import register_routes as _llp_register_routes
    _llp_register_routes()
except Exception:  # 서버 없이 임포트되는 경우 (테스트 등)
    logging.warning("SaveSeedreamLayersPSD: skipping PromptServer route registration", exc_info=True)
