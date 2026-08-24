# psd_builder 스모크 테스트: 합성 이미지로 PSD 생성 후 재파싱 검증
import importlib.util
import os
import tempfile

from PIL import Image

spec = importlib.util.spec_from_file_location(
    "psd_builder", os.path.join(os.path.dirname(__file__), "psd_builder.py")
)
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

base = Image.new("RGBA", (400, 300), (200, 220, 240, 255))
red = Image.new("RGBA", (100, 80), (255, 0, 0, 200))
green = Image.new("RGBA", (60, 60), (0, 255, 0, 255))

entries = [
    {"name": "background", "image": base, "rect": (0, 0, 400, 300), "visible": True},
    {"name": "red box", "image": red, "rect": (50, 40, 100, 80), "visible": True},
    {"name": "green/box:*?", "image": green, "rect": (200, 100, 60, 60), "visible": False},
]

out = os.path.join(tempfile.gettempdir(), "ssl_smoke.psd")
pb.build_psd(entries, (400, 300), out)
print("saved:", out, os.path.getsize(out), "bytes")

from psd_tools import PSDImage
from psd_tools.api.layers import SmartObjectLayer

psd = PSDImage.open(out)
assert psd.size == (400, 300), psd.size
assert len(psd) == 3, len(psd)
for lyr, exp_vis in zip(psd, (True, True, False)):
    assert isinstance(lyr, SmartObjectLayer), (lyr.name, type(lyr))
    assert lyr.visible == exp_vis, (lyr.name, lyr.visible)
    so = lyr.smart_object
    print(f"layer {lyr.name!r}: smart_object filename={so.filename!r} "
          f"visible={lyr.visible} bbox={lyr.bbox}")
comp = psd.composite()
px = comp.getpixel((100, 80))  # red box 위 (알파 200 합성)
assert px[0] > 200 and px[1] < 100, px
px2 = comp.getpixel((230, 130))  # 숨긴 green 자리 → 배경색이어야 함... (composite가 visible 반영하는지)
print("composite px(red area):", px, "px(hidden green area):", px2)
print("OK")
