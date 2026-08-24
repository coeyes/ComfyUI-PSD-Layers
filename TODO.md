# TODO / Roadmap

## 트랜스폼 편집 (기존 노드 확장 — 별도 SaveEditLayersPSD 노드는 만들지 않는다)

패널이 Save/Load 공용이므로 패널에 편집 능력을 올려 양쪽이 동시에 얻는 방향.
`layer_state`를 `{order, hidden}` → `{order, hidden, transforms: {idx: {dx, dy, sx, sy, rot}}}`로
확장하면 캐시 무효화·직렬화·Re-save 경로를 전부 재사용할 수 있다.

우선순위 순:

1. **이동(translate)** — 미리보기에서 선택 오브젝트 드래그.
   스마트 오브젝트 배치가 `rect → Trnf 코너` 계산이라 rect의 x,y만 바꾸면
   PSD 저장·합성 모두 그대로 따라온다. 사실상 공짜.
2. **스케일** — 바운딩박스 핸들 8개. 임베디드 원본은 그대로 두고 코너만 변경.
   Seedream은 네이티브가 bbox보다 고해상도라 확대해도 포토샵에서 화질 유지.
   미리보기/flatten 프리뷰 래스터만 리사이즈.
3. **회전** — PSD 포맷은 지원(`Trnf` 코너 8개에 회전 좌표, HANDOFF 참조).
   합성 미리보기·flatten 프리뷰의 회전 리샘플링이 필요해 손이 가장 많이 간다.

## 범용 Save Layers PSD

- Seedream 이름/bboxes 의존 제거. v3 `Autogrow` 입력으로 image/mask 쌍을
  꽂는 만큼 받는 구조 (입력 autogrow는 코어 지원 확인됨; 출력 autogrow는 미지원).
- 배치 위치: 옵션 bboxes 또는 알파 바운딩 자동 계산.
- `psd_builder.py`는 입력 소스와 무관하게 분리돼 있어 그대로 재사용.
- 참고: BOUNDING_BOX 타입 자체는 코어 범용이지만 metadata 키
  (`name`/`z_index`/`content_rect`/`flags`)는 Seedream 관례. 현재 Save 노드도
  metadata 없으면 폴백(풀캔버스 임베드)으로 이미 동작한다.

## 소소한 것들

- [x] 레지스트리 ID/이름 재검토 → `comfyui-psd-layers` / "PSD Layers" 로 리브랜딩 완료.
      (구 ID `comfyui-saveseedreamlayerspsd`는 레지스트리 웹 UI에서 삭제 예정)
- [x] README에 XISER Canvas와의 차별점 문단 추가 완료.
- [ ] 레이어가 아주 많을 때(예: 32쌍 = 67슬롯) 노드가 길어지는 문제 — 개별 출력 쌍을
      접고 batch 출력만 노출하는 "슬롯 접기" 옵션 검토.
- [ ] 추가 로케일 (zh-TW 등).
- [ ] (장기) 조정 레이어 일부(커브/레벨 등) 재합성 지원 검토 — psd-tools 미지원이라
      자체 구현 필요, 비용 대비 효과 따져볼 것.
