# LoadLayersPSD 백엔드 스모크 테스트 (ComfyUI 루트에서 실행)
import importlib.util
import os
import sys

sys.path.insert(0, "D:/ComfyUI")

pkg_dir = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "llp", os.path.join(pkg_dir, "load_psd.py"), submodule_search_locations=[pkg_dir]
)
llp = importlib.util.module_from_spec(spec)
sys.modules["llp"] = llp
spec.loader.exec_module(llp)

import numpy as np  # noqa: E402
from psd_tools import PSDImage  # noqa: E402

if len(sys.argv) < 2:
    print("usage: python _smoke_test_load.py <test.psd>")
    raise SystemExit(1)
TEST_PSD = sys.argv[1]

doc = llp.extract_document(TEST_PSD)
W, H = doc["canvas"]
print(f"canvas: {W}x{H}, entries: {len(doc['entries'])}, warnings: {doc['warnings']}")
for i, e in enumerate(doc["entries"]):
    print(f"  [{i}] {e['name']!r} kind={e['kind']} rect={e['rect']} blend={e['blend'].name} "
          f"opacity={e['opacity']:.2f} visible={e['visible']}")

order, hidden = llp.default_state(doc)

# 1) 우리 합성기 vs psd-tools 엔진 합성 (블렌드 모드 포함 기준)
ours = llp.composite_entries(doc, order, hidden).convert("RGB")
psd = PSDImage.open(TEST_PSD)
engine = psd.composite(force=True).convert("RGB")
a, b = np.asarray(ours, np.float32), np.asarray(engine, np.float32)
print(f"ours vs psd-tools engine: mean diff {np.abs(a - b).mean():.3f}/255, max {np.abs(a - b).max():.0f}")

# 2) 임베디드 포토샵 렌더와 비교 (있으면)
if doc["embedded"] is not None:
    c = np.asarray(doc["embedded"].convert("RGB"), np.float32)
    print(f"ours vs embedded(PS render): mean diff {np.abs(a - c).mean():.3f}/255")
else:
    print("embedded preview 없음")

# 3) 편집 상태 합성 (역순 + 첫 레이어 숨김)
edited = llp.composite_entries(doc, list(reversed(order)), {order[0]} if order else set())
print("edited composite:", edited.size)

# 4) 노드 실행 경로
node = llp.LoadLayersPSD()
import folder_paths  # noqa: E402
import shutil  # noqa: E402
dst = os.path.join(folder_paths.get_input_directory(), "_llp_smoke.psd")
shutil.copy(TEST_PSD, dst)
try:
    res = node.load("_llp_smoke.psd", "", unique_id="99")
    r = res["result"]
    n = len(doc["entries"])
    print(f"composite: {tuple(r[0].shape)}, layers_batch: {tuple(r[1].shape)}, masks_batch: {tuple(r[2].shape)}")
    assert tuple(r[0].shape) == (1, H, W, 3)
    assert r[1].shape[0] == max(n, 1) and r[1].shape[1:] == (H, W, 3)
    for i in range(min(n, llp.MAX_LAYERS)):
        img, msk = r[3 + 2 * i], r[4 + 2 * i]
        x, y, w, h = doc["entries"][i]["rect"]
        assert tuple(img.shape) == (1, h, w, 3), (i, img.shape)
        assert tuple(msk.shape) == (1, h, w), (i, msk.shape)
    # 미사용 슬롯은 1x1
    if n < llp.MAX_LAYERS:
        assert tuple(r[3 + 2 * n].shape) == (1, 1, 1, 3)
    payload = res["ui"]["load_layers_psd"][0]
    print(f"ui payload: layers={len(payload['layers'])}, composite={payload['composite']['filename']}, "
          f"edited={payload['edited']}, warnings={payload['warnings']}")
    # IS_CHANGED / VALIDATE
    print("IS_CHANGED:", llp.LoadLayersPSD.IS_CHANGED("_llp_smoke.psd", "")[:24], "...")
    assert llp.LoadLayersPSD.VALIDATE_INPUTS("_llp_smoke.psd") is True
    assert llp.LoadLayersPSD.VALIDATE_INPUTS("no_such.psd") is not True
finally:
    os.remove(dst)
print("OK")
