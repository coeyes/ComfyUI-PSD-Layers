"""Seedream 레이어들을 임베디드 스마트 오브젝트 PSD로 조립한다.

psd-tools 고수준 API는 스마트 오브젝트 생성을 지원하지 않으므로, Photoshop이
실제로 기록한 바이너리 블록(SoLd/PlLd/lnk2)을 base64 템플릿으로 임베드해 두고
uuid·트랜스폼·크기만 런타임에 패치한다. Photoshop 설치 불필요.

역공학 상세와 검증 기록:
https://github.com/coeyes/Fal.ai-Seedream5-Layers-To-Save-PSD (TECH.md)
"""

import base64
import io
import re
import uuid as uuidlib

import psd_tools
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer
from psd_tools.constants import Compression, LinkedLayerType, Tag
from psd_tools.psd.descriptor import DescriptorBlock, Double, List, String
from psd_tools.psd.linked_layer import LinkedLayer, LinkedLayers
from psd_tools.psd.tagged_blocks import (
    PlacedLayerData,
    SmartObjectLayerData,
    TaggedBlock,
    TaggedBlocks,
)

# _build_layer_record_and_channels 는 psd-tools 1.18에서 추가됨
if not hasattr(PixelLayer, "_build_layer_record_and_channels"):
    raise RuntimeError(
        f"psd-tools {psd_tools.__version__} 는 지원하지 않는다. 1.18 이상이 필요하다: "
        "venv 활성화 후 `pip install -U \"psd-tools>=1.18.0\"` 실행"
    )

# Photoshop이 실제로 기록한 스마트 오브젝트 블록의 바이너리 템플릿.
# uuid / 트랜스폼 / 원본 크기만 런타임에 패치해서 재사용한다.
SOLD_TEMPLATE = base64.b64decode(
    'c29MRAAAAAQAAAAQAAAAAQAAAAAAAG51bGwAAAASAAAAAElkbnRURVhUAAAAJQA3ADUAMwAyAGQAOQA3AGMALQAxAGYAYgA4AC0AYwBmADQAMwAtAGEANABmADgALQA1ADgAYgAyAGUAMgAyADkANgBkADQAZQAAAAAABnBsYWNlZFRFWFQAAAAlADcAMwA4ADMAZgA3ADUANQAtADUANwA1ADMALQBjADgANABiAC0AOQBlADcAMAAtADYAMwA1ADAAMABlADQAZAA3ADEANwBlAAAAAAAAUGdObWxvbmcAAAABAAAACnRvdGFsUGFnZXNsb25nAAAAAQAAAABDcm9wbG9uZwAAAAEAAAAJZnJhbWVTdGVwT2JqYwAAAAEAAAAAAABudWxsAAAAAgAAAAludW1lcmF0b3Jsb25nAAAAAAAAAAtkZW5vbWluYXRvcmxvbmcAAAJYAAAACGR1cmF0aW9uT2JqYwAAAAEAAAAAAABudWxsAAAAAgAAAAludW1lcmF0b3Jsb25nAAAAAAAAAAtkZW5vbWluYXRvcmxvbmcAAAJYAAAACmZyYW1lQ291bnRsb25nAAAAAQAAAABBbm50bG9uZwAAABAAAAAAVHlwZWxvbmcAAAACAAAAAFRybmZWbExzAAAACGRvdWJAb3AAAAAAAGRvdWJAVmAAAAAAAGRvdWJAgzQAAAAAAGRvdWJAVmAAAAAAAGRvdWJAgzQAAAAAAGRvdWJAkJoAAAAAAGRvdWJAb3AAAAAAAGRvdWJAkJoAAAAAAAAAABJub25BZmZpbmVUcmFuc2Zvcm1WbExzAAAACGRvdWJAb3AAAAAAAGRvdWJAVmAAAAAAAGRvdWJAgzQAAAAAAGRvdWJAVmAAAAAAAGRvdWJAgzQAAAAAAGRvdWJAkJoAAAAAAGRvdWJAb3AAAAAAAGRvdWJAkJoAAAAAAAAAAAR3YXJwT2JqYwAAAAEAAAAAAAR3YXJwAAAACAAAAAl3YXJwU3R5bGVlbnVtAAAACXdhcnBTdHlsZQAAAAh3YXJwTm9uZQAAAAl3YXJwVmFsdWVkb3ViAAAAAAAAAAAAAAAPd2FycFBlcnNwZWN0aXZlZG91YgAAAAAAAAAAAAAAFHdhcnBQZXJzcGVjdGl2ZU90aGVyZG91YgAAAAAAAAAAAAAACndhcnBSb3RhdGVlbnVtAAAAAE9ybnQAAAAASHJ6bgAAAAZib3VuZHNPYmpjAAAAAQAAAAAADmNsYXNzRmxvYXRSZWN0AAAABAAAAABUb3AgZG91YgAAAAAAAAAAAAAAAExlZnRkb3ViAAAAAAAAAAAAAAAAQnRvbWRvdWJAjmgAAAAAAAAAAABSZ2h0ZG91YkB2sAAAAAAAAAAABnVPcmRlcmxvbmcAAAAEAAAABnZPcmRlcmxvbmcAAAAEAAAAAFN6ICBPYmpjAAAAAQAAAAAAAFBudCAAAAACAAAAAFdkdGhkb3ViQHawAAAAAAAAAAAASGdodGRvdWJAjmgAAAAAAAAAAABSc2x0VW50RiNSc2xAUgAAAAAAAAAAAABjb21wbG9uZ/////8AAAAIY29tcEluZm9PYmpjAAAAAQAAAAAAAG51bGwAAAACAAAABmNvbXBJRGxvbmf/////AAAADm9yaWdpbmFsQ29tcElEbG9uZ/////8AAAAAQ2xNZ09iamMAAAABAAAAAAAAQ2xNZwAAAAEAAAAZcGxhY2VkTGF5ZXJPQ0lPQ29udmVyc2lvbmVudW0AAAAZcGxhY2VkTGF5ZXJPQ0lPQ29udmVyc2lvbgAAAB5wbGFjZWRMYXllck9DSU9Db252ZXJ0RW1iZWRkZWQAAAA='
)
PLLD_TEMPLATE = base64.b64decode(
    'cGxjTAAAAAMkNzUzMmQ5N2MtMWZiOC1jZjQzLWE0ZjgtNThiMmUyMjk2ZDRlAAAAAQAAAAEAAAAQAAAAAkBvcAAAAAAAQFZgAAAAAABAgzQAAAAAAEBWYAAAAAAAQIM0AAAAAABAkJoAAAAAAEBvcAAAAAAAQJCaAAAAAAAAAAAAAAAAEAAAAAEAAAAAAAR3YXJwAAAACAAAAAl3YXJwU3R5bGVlbnVtAAAACXdhcnBTdHlsZQAAAAh3YXJwTm9uZQAAAAl3YXJwVmFsdWVkb3ViAAAAAAAAAAAAAAAPd2FycFBlcnNwZWN0aXZlZG91YgAAAAAAAAAAAAAAFHdhcnBQZXJzcGVjdGl2ZU90aGVyZG91YgAAAAAAAAAAAAAACndhcnBSb3RhdGVlbnVtAAAAAE9ybnQAAAAASHJ6bgAAAAZib3VuZHNPYmpjAAAAAQAAAAAADmNsYXNzRmxvYXRSZWN0AAAABAAAAABUb3AgZG91YgAAAAAAAAAAAAAAAExlZnRkb3ViAAAAAAAAAAAAAAAAQnRvbWRvdWJAjmgAAAAAAAAAAABSZ2h0ZG91YkB2sAAAAAAAAAAABnVPcmRlcmxvbmcAAAAEAAAABnZPcmRlcmxvbmcAAAAEAAAA'
)
OPEN_FILE_TEMPLATE = base64.b64decode(
    'AAAAEAAAAAEAAAAAAABudWxsAAAAAQAAAAhjb21wSW5mb09iamMAAAABAAAAAAAAbnVsbAAAAAIAAAAGY29tcElEbG9uZ/////8AAAAOb3JpZ2luYWxDb21wSURsb25n/////w=='
)
# LinkedLayer v8 꼬리의 {contentID: uuid} 디스크립터.
# psd-tools는 이 필드를 파싱/기록하지 않지만 없으면 Photoshop이 파일을 거부한다.
CONTENT_ID_TEMPLATE = base64.b64decode(
    'AAAAEAAAAAEAAAAAAABudWxsAAAAAQAAAAljb250ZW50SURURVhUAAAAJQAwADgAMgBkAGUAZQBjAGUALQBmAGEAZQAwAC0AMwA3ADQAYgAtADgANwBiAGQALQAzAGEAMgBjAGIAMwAxAGMAOQA5AGMAMQAA'
)


class LinkedLayerV8(LinkedLayer):
    """psd-tools가 누락하는 v8 꼬리 필드(contentID)까지 기록하는 LinkedLayer."""

    def write(self, fp, padding: int = 1, **kwargs) -> int:
        written = super().write(fp, padding=1, **kwargs)
        blk = DescriptorBlock.read(io.BytesIO(CONTENT_ID_TEMPLATE))
        blk[b'contentID'] = String(value=str(uuidlib.uuid4()) + '\x00')
        written += blk.write(fp, padding=1)
        return written


def build_so_blocks(
    uid: str, size: tuple[int, int], box: tuple[int, int, int, int]
) -> tuple[SmartObjectLayerData, PlacedLayerData]:
    """템플릿에 uid/원본 크기/배치 박스를 패치해 (SoLd, PlLd) 블록을 만든다."""
    w, h = float(size[0]), float(size[1])
    x1, y1, x2, y2 = (float(v) for v in box)
    corners = [x1, y1, x2, y1, x2, y2, x1, y2]  # TL TR BR BL

    sold = SmartObjectLayerData.read(io.BytesIO(SOLD_TEMPLATE))
    d = sold.data
    d[b'Idnt'] = String(value=uid + '\x00')
    d[b'placed'] = String(value=str(uuidlib.uuid4()) + '\x00')
    d[b'Trnf'] = List([Double(v) for v in corners])
    d[b'nonAffineTransform'] = List([Double(v) for v in corners])
    d[b'warp'][b'bounds'][b'Btom'] = Double(h)
    d[b'warp'][b'bounds'][b'Rght'] = Double(w)
    d[b'Sz  '][b'Wdth'] = Double(w)
    d[b'Sz  '][b'Hght'] = Double(h)

    plld = PlacedLayerData.read(io.BytesIO(PLLD_TEMPLATE))
    plld.uuid = uid.encode('ascii')
    plld.transform = tuple(corners)
    plld.warp[b'bounds'][b'Btom'] = Double(h)
    plld.warp[b'bounds'][b'Rght'] = Double(w)
    return sold, plld


WINDOWS_RESERVED = {'con', 'prn', 'aux', 'nul'} | {
    f'{p}{i}' for p in ('com', 'lpt') for i in range(1, 10)
}


def safe_filename(name: str, used: set[str]) -> str:
    """파일명 금지 문자를 _로 바꾸고, 대소문자 무시 중복 시 _2, _3... 접미사를 붙인다."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip().rstrip('. ')
    if not s:
        s = 'layer'
    elif s.lower() in WINDOWS_RESERVED:
        s = '_' + s
    base, n = s, 2
    while s.lower() in used:
        s = f'{base}_{n}'
        n += 1
    used.add(s.lower())
    return s


def build_psd(entries: list[dict], canvas: tuple[int, int], out_path: str) -> None:
    """레이어들을 스마트 오브젝트로 쌓은 PSD를 저장한다.

    entries: 아래→위 순서. 각 항목은
      {"name": str, "image": PIL RGBA (rect 크기와 동일), "rect": (x, y, w, h), "visible": bool}
    """
    psd = PSDImage.new('RGB', canvas)
    linked = LinkedLayers()
    used_names: set[str] = set()

    for i, e in enumerate(entries):
        im = e['image']
        x, y, w, h = e['rect']
        name = e['name']
        fname = safe_filename(name, used_names)
        buf = io.BytesIO()
        im.save(buf, 'PNG')
        png = buf.getvalue()

        # RGBA를 직접 넘기면 알파가 TRANSPARENCY_MASK(-1) 채널로 들어간다
        record, channels = PixelLayer._build_layer_record_and_channels(
            im, name, x, y, Compression.RLE
        )
        psd.append(PixelLayer(psd, record, channels))
        record.flags.visible = bool(e.get('visible', True))

        uid = str(uuidlib.uuid4())
        record.tagged_blocks.set_data(Tag.UNICODE_LAYER_NAME, name)
        record.tagged_blocks.set_data(Tag.LAYER_ID, i + 1)
        sold, plld = build_so_blocks(uid, im.size, (x, y, x + w, y + h))
        record.tagged_blocks[Tag.PLACED_LAYER2] = TaggedBlock(
            key=Tag.PLACED_LAYER2, data=plld
        )
        record.tagged_blocks[Tag.SMART_OBJECT_LAYER_DATA1] = TaggedBlock(
            key=Tag.SMART_OBJECT_LAYER_DATA1, data=sold
        )

        linked.append(
            LinkedLayerV8(
                kind=LinkedLayerType.DATA,
                version=8,
                uuid=uid,
                filename=fname + '.png\x00',
                filetype=b'png ',
                creator=b'\x00\x00\x00\x00',
                open_file=DescriptorBlock.read(io.BytesIO(OPEN_FILE_TEMPLATE)),
                data=png,
                child_id='\x00',
                mod_time=0.0,
                lock_state=0,
            )
        )

    li = psd._record.layer_and_mask_information
    if li.tagged_blocks is None:
        li.tagged_blocks = TaggedBlocks()
    li.tagged_blocks[Tag.LINKED_LAYER2] = TaggedBlock(
        key=Tag.LINKED_LAYER2, data=linked
    )
    psd.save(out_path)
