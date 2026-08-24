# 노드 실행 경로 스모크 테스트 (ComfyUI 루트에서 실행)
import importlib.util
import json
import os
import sys

sys.path.insert(0, "D:/ComfyUI")
import torch  # noqa: E402

pkg_dir = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "ssl_pkg", os.path.join(pkg_dir, "__init__.py"),
    submodule_search_locations=[pkg_dir],
)
mod = importlib.util.module_from_spec(spec)
sys.modules["ssl_pkg"] = mod
spec.loader.exec_module(mod)

W, H = 320, 240
base = torch.rand(1, H, W, 3)
layers = torch.zeros(2, H, W, 3)
masks = torch.ones(2, H, W)
# 레이어 0: (40,30)-(140,110) 영역에 빨강
layers[0, 30:110, 40:140] = torch.tensor([1.0, 0.0, 0.0])
masks[0, 30:110, 40:140] = 0.0
# 레이어 1: (200,100)-(260,160) 영역에 초록
layers[1, 100:160, 200:260] = torch.tensor([0.0, 1.0, 0.0])
masks[1, 100:160, 200:260] = 0.0

bboxes = [[
    {"x": 0, "y": 0, "width": W, "height": H,
     "metadata": {"name": "Red thing", "z_index": 2,
                  "content_rect": [40, 30, 100, 80], "flags": []}},
    {"x": 0, "y": 0, "width": W, "height": H,
     "metadata": {"name": "Green thing", "z_index": 1,
                  "content_rect": [200, 100, 60, 60], "flags": []}},
]]

node = mod.SaveSeedreamLayersPSD()
base_mask = torch.zeros(1, H, W)
res = node.save_layers(base, base_mask, layers, masks, bboxes,
                       filename_prefix="ssl_test/smoke", unique_id="42")
payload = res["ui"]["seedream_psd"][0]

# composite_image 출력 확인: (1,H,W,3), 빨간 박스가 초록 위(z 큼)라 중첩부는 빨강
comp = res["result"][0]
assert tuple(comp.shape) == (1, H, W, 3), comp.shape
assert comp[0, 70, 90, 0] > 0.9 and comp[0, 70, 90, 1] < 0.1  # 빨간 박스 내부
assert comp[0, 130, 230, 1] > 0.9  # 초록 박스 내부
print(json.dumps(payload, indent=1, ensure_ascii=False))

# z_index 순 정렬 확인: Green(z=1)이 먼저(아래) 와야 함
assert [l["index"] for l in payload["layers"]] == [1, 0]
assert payload["layers"][0]["rect"] == [200, 100, 60, 60]

# temp 에셋 존재 확인
import folder_paths  # noqa: E402
tf = os.path.join(folder_paths.get_temp_directory(), payload["base"]["subfolder"])
assert os.path.isfile(os.path.join(tf, "base.png"))
assert os.path.isfile(os.path.join(tf, "layer_00.png"))

# output 파일 존재 확인
of = os.path.join(folder_paths.get_output_directory(), payload["saved"]["psd"]["subfolder"])
psd_file = os.path.join(of, payload["saved"]["psd"]["filename"])
assert os.path.isfile(psd_file), psd_file
assert os.path.isfile(os.path.join(of, payload["saved"]["png"]["filename"]))

# Save 버튼 경로: 순서 뒤집기 + Green 숨김으로 재저장
session = mod._SESSIONS["42"]
saved2, _comp2 = mod._save_outputs(session, [0, 1], {1}, "ssl_test/smoke_btn")
of2 = os.path.join(folder_paths.get_output_directory(), saved2["psd"]["subfolder"])
assert os.path.isfile(os.path.join(of2, saved2["psd"]["filename"]))

from psd_tools import PSDImage  # noqa: E402
psd = PSDImage.open(os.path.join(of2, saved2["psd"]["filename"]))
names = [(l.name, l.visible) for l in psd]
print("psd layers:", names)
assert names == [("background", True), ("Red thing", True), ("Green thing", False)]
print("OK")
