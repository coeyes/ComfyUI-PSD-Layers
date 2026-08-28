# ComfyUI-PSD-Layers

ComfyUI용 PSD 레이어 워크플로우 노드. Photoshop 설치 불필요.

[English documentation](README.md)

- **Save Seedream Layers PSD** — ByteDance Seedream 레이어 분리 결과를 **모든 레이어가
  임베디드 스마트 오브젝트로 유지되는** PSD로 저장: 어떤 것도 래스터로 병합되지 않아,
  포토샵에서 각 요소를 개별적으로 이동·변형·재편집할 수 있다
- **Load Layers PSD** — 임의의 PSD를 레이어별로 읽어 composite + 레이어별 이미지/마스크 출력

두 노드 모두 인터랙티브 패널을 제공한다: 합성 미리보기, 알파 정밀 오브젝트 선택(바운딩박스
표시), 드래그 z 순서 변경, 레이어별 표시/숨김 토글, 상태바.

<p align="center">
<img src="https://raw.githubusercontent.com/coeyes/ComfyUI-PSD-Layers/main/docs/screenshot_save.png" alt="Save Seedream Layers PSD" height="560">
<img src="https://raw.githubusercontent.com/coeyes/ComfyUI-PSD-Layers/main/docs/screenshot_load.png" alt="Load Layers PSD" height="560">
</p>

이미지들을 자체 상태 포맷으로 패킹하는 캔버스 편집기류 노드(XISER Canvas 등)와 달리,
이 팩은 **PSD 네이티브 왕복**이다: 진짜 PSD를 읽고(마스크·클리핑·블렌드 모드), 포토샵이
무손실로 여는 진짜 스마트 오브젝트 PSD를 쓰며, 그 사이를 레이어 단위 그래프 출력으로
배선한다.

## 노드: Save Seedream Layers PSD

`ByteDanceSeedreamLayerSeparationNode`의 출력(`layer_stack` 제외)을 위에서 아래로 그대로
연결한다.

### 입력

| 입력 | 타입 | 연결 대상 |
|---|---|---|
| `base_image` | IMAGE | base_image (배경 플레이트) |
| `base_mask` | MASK | base_mask (1 = 투명) |
| `layers` | IMAGE | layers 배치 (full canvas / minimal size 둘 다 지원) |
| `masks` | MASK | masks (1 = 투명) |
| `bboxes` | BOUNDING_BOX | bboxes (metadata의 name/z_index/content_rect 사용) |
| `filename_prefix` | STRING | SaveImage와 동일한 프리픽스 템플릿 (`서브폴더/이름`, `%date:...%` 등) |

### 출력

| 출력 | 타입 | 설명 |
|---|---|---|
| `composite_image` | IMAGE | 전 레이어 합성 (저장되는 PNG와 동일) |

### 동작

- 실행 시 `output` 폴더에 `<prefix>_00001_.psd` + `<prefix>_00001_.png`(합성 미리보기) 저장.
- PSD는 배경 + 각 레이어를 **임베디드 스마트 오브젝트**로 z_index 순서로 쌓는다 —
  AI가 분리한 요소들이 납작한 래스터로 합쳐지지 않고 각자의 픽셀을 유지하므로,
  포토샵에서 화질 손실 없이 개별 이동·스케일·교체가 가능하다.
- 패널: 미리보기에서 오브젝트 클릭(알파 히트테스트) 또는 리스트에서 선택,
  **드래그로 z 순서 변경**, 눈 아이콘으로 표시/숨김 토글.
- **Re-save PSD 버튼**: 그래프 재실행 없이 조정한 순서/가시성으로 재저장(카운터 증가).
  숨긴 레이어는 PSD에 `visible=false`로 포함되고 미리보기 PNG에서는 제외된다.

참고: Save 버튼 세션은 서버 메모리에 있다 — 서버 재시작 후에는 노드를 한 번 실행해야 한다.

## 노드: Load Layers PSD

`input` 폴더의 PSD를 선택하거나 노드에 파일을 드래그해서 업로드한다 (범용 — Seedream 전용
아님). PSB도 지원한다.

`psd_path`에 **서버 로컬 절대 경로**를 넣으면(따옴표 포함 붙여넣기 허용) input 콤보 대신 그
파일을 직접 읽는다 — 포토샵에서 수정 후 Reload 하는 루프에 적합하다. 리모트 접속 시에는
서버가 읽을 수 있는 경로여야 하며, 아니면 드래그 업로드를 쓴다.

### 출력

| 출력 | 타입 | 설명 |
|---|---|---|
| `composite_image` | IMAGE | 전체 합성. 편집 전: PSD 임베디드 포토샵 렌더(픽셀 퍼펙트), 편집 후: psd-tools 블렌드 엔진 재합성 |
| `layers_batch` / `masks_batch` | IMAGE / MASK | 전 레이어 풀캔버스 배치 (레이어 수 제한 없음) |
| `layer_N_image` / `layer_N_mask` | IMAGE / MASK | 레이어별 타이트 크롭 쌍 (최대 32쌍, PSD 아래→위 순서 고정). 미사용 슬롯은 UI에서 자동으로 접힌다 |

### 동작

- **그룹 평탄화**: 리프 레이어만 추출. 레이어 마스크·그룹 래스터 마스크·클리핑 마스크는
  알파에 사전 적용.
- **텍스트 레이어**는 임베디드 래스터 사용(폰트 불필요), **셰이프 레이어**는 psd-tools 벡터
  래스터라이즈.
- **포토샵 블렌드 모드 전 세트 지원** (psd-tools `BLEND_FUNC` 재사용, 포토샵 렌더 대비 평균
  오차 ≈ 0.03/255 실측).
- **조정 레이어 / 레이어 스타일(이펙트)**: 재합성 시 미반영 — 상태바에 경고가 상시 표기된다.
  편집 전 composite(임베디드 렌더)에는 반영돼 있다.
- 패널의 순서/숨김 편집은 `layer_state` 위젯에 직렬화 → 캐시 무효화 + composite 출력에 반영.
  레이어별 출력 슬롯 순서는 편집과 무관하게 고정.
- **Reload 버튼** + 원본 파일 변경 시 다음 실행에서 자동 재로드(mtime 기반 `IS_CHANGED`).
- **Auto reload 토글** (Reload 왼쪽, 기본 꺼짐, 노드별로 상태 유지): 원본 파일 —
  `psd_path` 또는 input 폴더의 PSD — 을 감시해 변경되면 패널을 자동으로 다시 읽는다.
  저장이 진행 중인 파일은 안정될 때까지 기다렸다가 읽고, 켜져 있는 동안 수동 Reload
  버튼은 비활성화된다. 포토샵에서 수정하며 작업하는 루프에 적합하다.

## 설치

이 폴더를 `ComfyUI/custom_nodes/`에 복사한 뒤:

```
pip install -r requirements.txt   # psd-tools[composite]>=1.18.0, pillow, numpy
```

## 예제 워크플로우

`example_workflows/`에 바로 쓸 수 있는 템플릿이 들어 있다 (ComfyUI 워크플로우 템플릿
브라우저에도 노출됨).

**`seedream_separation_to_psd`** — 이미지 → Seedream 레이어 분리 → 스마트 오브젝트 PSD:

<img src="https://raw.githubusercontent.com/coeyes/ComfyUI-PSD-Layers/main/docs/workflow_save.png" alt="Seedream 분리 → PSD 워크플로우">

**`load_psd_layers`** — PSD → composite + 개별 레이어(`layer_9_image`)를 따로 뽑아 미리보기:

<img src="https://raw.githubusercontent.com/coeyes/ComfyUI-PSD-Layers/main/docs/workflow_load.png" alt="PSD 레이어 로드 워크플로우">

## 로컬라이징

노드 툴팁/설명과 패널 UI는 ComfyUI 언어 설정(`Settings → Locale`)을 따른다.
포함 언어: English, 한국어, 日本語, 中文, Español, Français, Русский.
패널 텍스트는 즉시 전환되며, 번역은 `locales/`와 `web/js/psdI18n.js`에 있다.

## 구현 노트

psd-tools는 스마트 오브젝트 *쓰기*를 공식 지원하지 않는다. Save 노드는 실제 포토샵 파일에서
추출한 base64 템플릿으로 `SoLd`/`PlLd`/`lnk2` 바이너리 블록을 조립하고 uuid·트랜스폼·크기만
런타임에 패치하며, psd-tools의 LinkedLayer v8 `contentID` 누락 버그(포토샵이 파일을 거부하게
됨)를 `LinkedLayerV8` 서브클래스로 우회한다. 역공학 전 과정은 동반 프로젝트
[Fal.ai-Seedream5-Layers-To-Save-PSD](https://github.com/coeyes/Fal.ai-Seedream5-Layers-To-Save-PSD)
(CLI+GUI)의 [TECH.ko.md](https://github.com/coeyes/Fal.ai-Seedream5-Layers-To-Save-PSD/blob/master/TECH.ko.md)에 정리돼 있다.

Load 노드는 래스터 레이어를 직독(`layer.numpy()`, 합성 엔진 대비 약 10배)하고 벡터 요소가
있을 때만 엔진을 쓴다. UI 미리보기 에셋은 프리멀티플라이드 알파 LANCZOS로 2048px 캡
다운스케일하며, 그래프 쪽 렌더링은 고품질 `createImageBitmap` 캐시 + 줌 안정화 재렌더를
사용한다.

`_smoke_test*.py`로 오프라인 검증이 가능하다 (빌더 / Save 노드 / Load 노드).

## 라이선스 / 제작자

MIT License — [LICENSE](LICENSE) 참조.

제작자: **Hyeongjik Song** <coeyes@gmail.com>
