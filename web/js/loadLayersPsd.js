// LoadLayersPSD 노드의 인터랙티브 패널.
// - 파일 선택/드래그 업로드 → 서버 inspect → 레이어 리스트 + 합성 미리보기
// - 미리보기는 항상 서버 렌더(편집 전: 포토샵 임베디드 렌더, 편집 후: psd-tools 블렌드 엔진)
// - 순서 드래그 / 눈 토글 → layer_state 위젯 갱신(캐시 무효화) + 서버 재합성 요청
// - 상태바: 편집 여부 + 조정 레이어/레이어 스타일 등 미반영 경고 상시 표기
// - 출력 슬롯: composite/batch 3개 고정 + 레이어 수만큼 image/mask 쌍을 동적으로 트리밍
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { t, getLocale, serverError, formatWarnings } from "./psdI18n.js";

const NODE_NAME = "LoadLayersPSD";
const BASE_OUTPUTS = 3; // composite_image, layers_batch, masks_batch

const STYLE = `
.llp-root { display:flex; flex-direction:column; gap:6px; width:100%; height:100%;
  min-height:360px; font-family:sans-serif; font-size:12px; color:#ddd;
  background:#1b1b1b; border-radius:6px; padding:6px; box-sizing:border-box; }
.llp-main { display:flex; gap:8px; flex:1; min-height:0; }
.llp-preview { position:relative; flex:1; min-width:0; background:#111;
  border-radius:4px; overflow:hidden; }
.llp-preview canvas { position:absolute; inset:0; width:100%; height:100%; cursor:crosshair; }
.llp-empty { position:absolute; inset:0; display:flex; align-items:center; text-align:center;
  justify-content:center; color:#666; pointer-events:none; padding:12px; white-space:pre-line; }
.llp-loaded .llp-empty { display:none; }
.llp-side { width:215px; flex:none; display:flex; flex-direction:column; gap:6px; min-height:0; }
.llp-head { display:flex; justify-content:space-between; align-items:center; gap:6px;
  padding:2px 4px; color:#aaa; }
.llp-head-label { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.llp-head-btns { flex:none; display:flex; gap:4px; white-space:nowrap; }
.llp-reload, .llp-auto { cursor:pointer; background:#2c2c2c; border:1px solid #444; color:#ccc;
  border-radius:4px; font-size:11px; padding:2px 6px; white-space:nowrap; }
.llp-reload:hover, .llp-auto:hover { background:#3a3a3a; }
.llp-reload:disabled { opacity:.35; cursor:default; }
.llp-reload:disabled:hover { background:#2c2c2c; }
.llp-auto.llp-on { background:#3a5ccc; border-color:#3a5ccc; color:#fff; }
.llp-auto.llp-on:hover { background:#4a6cdc; }
.llp-list { flex:1; overflow-y:auto; overflow-x:hidden; display:flex;
  flex-direction:column; gap:2px; min-height:0; }
.llp-row { display:flex; align-items:center; gap:6px; padding:3px 4px; border-radius:4px;
  cursor:grab; border:1px solid transparent; user-select:none; touch-action:none; }
.llp-row:hover { background:#2a2a2a; }
.llp-row.llp-selected { background:#2d3d55; border-color:#4a9eff; }
.llp-row.llp-dragging { opacity:.4; }
.llp-row.llp-drop { box-shadow:0 -2px 0 #4a9eff; }
.llp-row.llp-drop-below { box-shadow:0 2px 0 #4a9eff; }
.llp-thumb { width:34px; height:34px; flex:none; border-radius:3px; overflow:hidden;
  background:repeating-conic-gradient(#333 0% 25%, #222 0% 50%) 0 0 / 12px 12px; }
.llp-thumb img { width:100%; height:100%; object-fit:contain; display:block; }
.llp-nm { flex:1; min-width:0; display:flex; flex-direction:column; }
.llp-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.llp-blend { font-size:10px; color:#c9a34e; }
.llp-eye { flex:none; cursor:pointer; background:none; border:none; color:#ccc;
  font-size:13px; padding:2px 4px; line-height:1; }
.llp-eye.llp-off { opacity:.25; }
.llp-status { flex:none; display:flex; flex-direction:column; gap:2px; padding:4px 6px;
  background:#141414; border-radius:4px; min-height:16px; }
.llp-status-main { color:#9a9; }
.llp-status-main.llp-edited { color:#e0b566; }
.llp-status-warn { color:#777; word-break:break-all; max-height:42px; overflow-y:auto; }
.llp-status-warn.llp-hot { color:#e08080; }
.llp-status-err { color:#e08080; }
.llp-root.llp-dropping { outline:2px dashed #4a9eff; outline-offset:-2px; }
`;

function viewURL(f) {
  return api.apiURL(
    `/view?filename=${encodeURIComponent(f.filename)}` +
    `&subfolder=${encodeURIComponent(f.subfolder || "")}` +
    `&type=${f.type}&rand=${Math.random()}`
  );
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = url;
  });
}

class LoadPsdPanel {
  constructor(node) {
    this.node = node;
    this.state = null; // { canvas, layers: Map, order, hidden:Set, selected, compImg, warnings, edited, maxLayers }
    this.view = null;
    this.recompTimer = null;
    this.buildDOM();
  }

  // ---- 위젯 접근 ----
  psdWidget() { return this.node.widgets?.find((w) => w.name === "psd"); }
  pathWidget() { return this.node.widgets?.find((w) => w.name === "psd_path"); }
  stateWidget() { return this.node.widgets?.find((w) => w.name === "layer_state"); }

  buildDOM() {
    const root = document.createElement("div");
    root.className = "llp-root";
    root.innerHTML = `
      <div class="llp-main">
        <div class="llp-preview"><canvas></canvas>
          <div class="llp-empty"></div></div>
        <div class="llp-side">
          <div class="llp-head"><span class="llp-head-label"><span class="llp-word"></span> <span class="llp-count"></span></span>
            <span class="llp-head-btns"><button class="llp-auto"></button><button class="llp-reload"></button></span></div>
          <div class="llp-list"></div>
        </div>
      </div>
      <div class="llp-status">
        <div class="llp-status-main"></div>
        <div class="llp-status-warn" style="display:none"></div>
      </div>`;
    this.root = root;
    this.previewEl = root.querySelector(".llp-preview");
    this.cv = root.querySelector("canvas");
    this.listEl = root.querySelector(".llp-list");
    this.emptyEl = root.querySelector(".llp-empty");
    this.wordEl = root.querySelector(".llp-word");
    this.autoBtn = root.querySelector(".llp-auto");
    this.reloadBtn = root.querySelector(".llp-reload");
    this.autoBtn.addEventListener("click", () => this.setAutoReload(!this.autoReload));
    this.countEl = root.querySelector(".llp-count");
    this.statusMain = root.querySelector(".llp-status-main");
    this.statusWarn = root.querySelector(".llp-status-warn");
    this.refreshTexts();

    root.querySelector(".llp-reload").addEventListener("click", () => this.inspect(true));
    // 휠 이벤트를 그래프 캔버스로 전달해 Comfy 줌이 동작하게 한다 (레이어 리스트 위에서는 스크롤 유지)
    root.addEventListener("wheel", (e) => {
      if (e.target.closest?.(".llp-list")) return;
      e.preventDefault();
      e.stopPropagation();
      app.canvas?.canvas?.dispatchEvent(new WheelEvent("wheel", e));
    }, { passive: false });
    // 가운데 버튼 드래그 = Comfy 캔버스 팬: down + 이후 move/up 을 그래프 캔버스로 전달
    root.addEventListener("pointerdown", (e) => {
      if (e.button !== 1) return;
      e.preventDefault();
      e.stopPropagation();
      const cv = app.canvas?.canvas;
      if (!cv) return;
      cv.dispatchEvent(new PointerEvent("pointerdown", e));
      const fw = (ev) => { if (ev.isTrusted) cv.dispatchEvent(new PointerEvent(ev.type, ev)); };
      const done = (ev) => {
        fw(ev);
        window.removeEventListener("pointermove", fw, true);
        window.removeEventListener("pointerup", done, true);
      };
      window.addEventListener("pointermove", fw, true);
      window.addEventListener("pointerup", done, true);
    });
    // 패널 영역 드래그 앤 드롭 업로드 (DOM 위젯이 노드를 덮어 LiteGraph 드롭이 안 오므로 직접 처리)
    root.addEventListener("dragover", (e) => {
      if (![...(e.dataTransfer?.items || [])].some((it) => it.kind === "file")) return;
      e.preventDefault();
      e.stopPropagation();
      root.classList.add("llp-dropping");
    });
    root.addEventListener("dragleave", (e) => {
      if (!root.contains(e.relatedTarget)) root.classList.remove("llp-dropping");
    });
    root.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      root.classList.remove("llp-dropping");
      this.handleDrop(e);
    });
    this.cv.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 || !this.state || !this.view) return;
      const r = this.cv.getBoundingClientRect();
      const x = (ev.clientX - r.left - this.view.ox) / this.view.scale;
      const y = (ev.clientY - r.top - this.view.oy) / this.view.scale;
      this.select(this.hitTest(x, y));
    });
    new ResizeObserver(() => this.draw()).observe(this.previewEl);
    this.startZoomWatch();
  }

  // 그래프 줌은 CSS transform 이라 ResizeObserver 가 안 잡힌다.
  // 스케일을 rAF로 감시하다가 줌이 멈추면(180ms) 한 번만 고해상도 재렌더.
  // 같은 루프에서 Comfy.Locale 변경도 감시해 패널 텍스트를 즉시 갱신한다.
  startZoomWatch() {
    let lastScale = null;
    let settleTimer = null;
    let started = false;
    let lastLocale = getLocale();
    const tick = () => {
      if (started && !this.root.isConnected) return; // 노드 삭제 → 감시 종료
      if (this.root.isConnected) started = true;
      const s = app.canvas?.ds?.scale;
      if (s !== undefined && s !== lastScale) {
        if (lastScale !== null) {
          clearTimeout(settleTimer);
          settleTimer = setTimeout(() => this.draw(), 180);
        }
        lastScale = s;
      }
      const loc = getLocale();
      if (loc !== lastLocale) {
        lastLocale = loc;
        this.refreshTexts();
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  // 로케일 반영이 필요한 정적 텍스트들 갱신
  refreshTexts() {
    this.emptyEl.textContent = t("loadEmpty");
    this.wordEl.textContent = t("layers");
    this.reloadBtn.textContent = "⟳ " + t("reload");
    this.autoBtn.textContent = "⟲ " + t("autoReload");
    this.autoBtn.title = t("autoReloadTitle");
    if (this.state) {
      this.updateStatus();
      this.renderList();
    }
  }

  // ---- auto reload (원본 PSD 파일 워칭 — 서버 stat 폴링) ----

  setAutoReload(on) {
    this.autoReload = !!on;
    this.node.properties = this.node.properties || {};
    this.node.properties.llpAutoReload = this.autoReload;
    this.autoBtn.classList.toggle("llp-on", this.autoReload);
    this.reloadBtn.disabled = this.autoReload;
    clearInterval(this.watchTimer);
    this.watchTimer = null;
    if (this.autoReload) {
      this.watchLast = null;    // 마지막으로 로드된 시점의 stat
      this.watchPending = null; // 변경 감지 후 안정화 대기 중인 stat
      this.watchTimer = setInterval(() => this.pollStat(), 1500);
    }
  }

  async pollStat() {
    if (!this.root.isConnected) { // 노드 삭제 → 워처 종료
      clearInterval(this.watchTimer);
      this.watchTimer = null;
      return;
    }
    const psd = this.psdWidget()?.value;
    const psdPath = this.pathWidget()?.value?.trim() || "";
    if ((!psd && !psdPath) || this.watchBusy) return;
    try {
      const res = await api.fetchApi("/load_layers_psd/stat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ psd, psd_path: psdPath }),
      });
      if (!res.ok) return; // 파일 없음/서버 오류 → 다음 폴에서 재시도
      const { stat } = await res.json();
      if (this.watchLast === null) { // 첫 관측은 기준값으로만
        this.watchLast = stat;
        return;
      }
      if (stat === this.watchLast) {
        this.watchPending = null;
        return;
      }
      // 변경 감지 → 쓰기 완료(한 주기 동안 stat 동일)까지 기다렸다가 리로드
      if (stat !== this.watchPending) {
        this.watchPending = stat;
        return;
      }
      this.watchBusy = true;
      try {
        if (await this.inspect(true)) this.watchLast = stat;
        this.watchPending = null;
      } finally {
        this.watchBusy = false;
      }
    } catch {
      /* 폴링 오류는 무시 */
    }
  }

  setError(text) {
    this.statusMain.textContent = text;
    this.statusMain.className = "llp-status-main llp-status-err";
  }

  updateStatus() {
    if (!this.state) return;
    const s = this.state;
    const [W, H] = s.canvas;
    const visible = s.order.filter((i) => !s.hidden.has(i)).length;
    this.countEl.textContent = `${visible}/${s.order.length}`;
    this.statusMain.textContent =
      t("layersCount", { n: s.order.length }) + ` · ${W}×${H}` + t(s.edited ? "edited" : "orig");
    this.statusMain.className = "llp-status-main" + (s.edited ? " llp-edited" : "");
    if (s.warnings.length) {
      this.statusWarn.style.display = "";
      const head = t(s.edited ? "warnHeadEdited" : "warnHeadInfo");
      this.statusWarn.textContent = head + formatWarnings(s.warnings).join(" · ");
      this.statusWarn.className = "llp-status-warn" + (s.edited ? " llp-hot" : "");
    } else {
      this.statusWarn.style.display = "none";
    }
  }

  // ---- inspect / 로드 ----

  autoInspect() {
    if (this.psdWidget()?.value || this.pathWidget()?.value?.trim()) this.inspect(true);
  }

  async inspect(keepState) {
    const psd = this.psdWidget()?.value;
    const psdPath = this.pathWidget()?.value?.trim() || "";
    if (!psd && !psdPath) return false;
    if (!keepState) {
      const sw = this.stateWidget();
      if (sw) sw.value = "";
    }
    try {
      const res = await api.fetchApi("/load_layers_psd/inspect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          node_id: String(this.node.id),
          psd,
          psd_path: psdPath,
          layer_state: this.stateWidget()?.value || "",
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        this.setError(t("readFail", { msg: serverError(data, res.status) ?? data.error }));
        return false;
      }
      await this.load(data);
      return true;
    } catch (e) {
      this.setError(t("readFail", { msg: e }));
      return false;
    }
  }

  async load(payload) {
    const compImg = await loadImage(viewURL(payload.composite));
    const layers = new Map();
    await Promise.all(payload.layers.map(async (l) => {
      const img = await loadImage(viewURL(l.image));
      layers.set(l.index, { ...l, img, actx: null });
    }));
    this.state = {
      canvas: payload.canvas,
      layers,
      order: payload.order.slice(),
      hidden: new Set(payload.hidden),
      selected: null,
      compImg,
      warnings: payload.warnings || [],
      edited: !!payload.edited,
      maxLayers: payload.max_layers || 32,
    };
    this.root.classList.add("llp-loaded");
    this.compBmp = null;
    this.watchLast = null; // 소스가 바뀌었을 수 있으니 워처 기준값 재설정
    this.ensureOutputs(); // 내부에서 fixOverflow 호출
    this.updateStatus();
    this.renderList();
    this.draw();
  }

  // ---- 출력 슬롯 트리밍 ----

  ensureOutputs() {
    const node = this.node;
    if (!node.outputs) return;
    const s = this.state;
    const n = Math.min(s.layers.size, s.maxLayers);
    const want = BASE_OUTPUTS + 2 * n;
    while (node.outputs.length > want) node.removeOutput(node.outputs.length - 1);
    while (node.outputs.length < want) {
      const k = node.outputs.length - BASE_OUTPUTS;
      const li = Math.floor(k / 2) + 1;
      if (k % 2 === 0) node.addOutput(`layer_${li}_image`, "IMAGE");
      else node.addOutput(`layer_${li}_mask`, "MASK");
    }
    // 레이어 이름을 라벨로 (PSD 아래→위 원본 순서 = 슬롯 순서, 편집과 무관하게 고정)
    for (let i = 0; i < n; i++) {
      const name = s.layers.get(i)?.name ?? `layer ${i + 1}`;
      node.outputs[BASE_OUTPUTS + 2 * i].label = `${i + 1}·${name}`;
      node.outputs[BASE_OUTPUTS + 2 * i + 1].label = `${i + 1}·mask`;
    }
    const sz = node.computeSize();
    node.setSize([Math.max(node.size[0], sz[0]), Math.max(node.size[1], sz[1])]);
    node.graph?.setDirtyCanvas(true, true);
    this.fixOverflow();
  }

  // 슬롯 증감/콘텐츠 증가로 패널이 노드 밖으로 넘치면 그만큼 노드를 키운다.
  // scrollHeight-clientHeight 는 CSS px 기준 내부 오버플로량이라 줌 스케일과 무관하다.
  fixOverflow() {
    requestAnimationFrame(() => {
      const node = this.node;
      if (!this.root.isConnected) return;
      const overflow = this.root.scrollHeight - this.root.clientHeight;
      if (overflow > 4) {
        node.setSize([node.size[0], node.size[1] + overflow + 10]);
        node.graph?.setDirtyCanvas(true, true);
      }
    });
  }

  // ---- 편집 → 상태 위젯 + 서버 재합성 ----

  onEdited() {
    const s = this.state;
    const sw = this.stateWidget();
    if (sw) sw.value = JSON.stringify({ order: s.order, hidden: [...s.hidden] });
    s.edited = true;
    this.updateStatus();
    clearTimeout(this.recompTimer);
    this.recompTimer = setTimeout(() => this.recomposite(), 250);
  }

  async recomposite() {
    const s = this.state;
    if (!s) return;
    try {
      const res = await api.fetchApi("/load_layers_psd/recomposite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          node_id: String(this.node.id),
          order: s.order,
          hidden: [...s.hidden],
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        if (res.status === 404) await this.inspect(true); // 세션 유실 → 다시 파싱
        else this.setError(t("recompFail", { msg: serverError(data, res.status) ?? data.error }));
        return;
      }
      s.compImg = await loadImage(viewURL(data.composite));
      s.edited = !!data.edited;
      this.updateStatus();
      this.draw();
    } catch (e) {
      this.setError(t("recompFail", { msg: e }));
    }
  }

  // ---- 미리보기 ----

  draw() {
    const rect = this.previewEl.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return;
    // 줌 확대까지 반영된 화면 픽셀 크기로 백킹 확보 (과도한 확대 대비 4096px 캡)
    const dpr = Math.min(window.devicePixelRatio || 1,
      4096 / Math.max(rect.width, rect.height));
    this.cv.width = Math.round(rect.width * dpr);
    this.cv.height = Math.round(rect.height * dpr);
    const ctx = this.cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    if (!this.state) return;

    const [W, H] = this.state.canvas;
    const scale = Math.min(rect.width / W, rect.height / H);
    const ox = (rect.width - W * scale) / 2;
    const oy = (rect.height - H * scale) / 2;
    this.view = { scale, ox, oy };

    // 투명 배경 체커보드
    ctx.save();
    ctx.beginPath();
    ctx.rect(ox, oy, W * scale, H * scale);
    ctx.clip();
    const cs = 12;
    ctx.fillStyle = "#2a2a2a";
    ctx.fillRect(ox, oy, W * scale, H * scale);
    ctx.fillStyle = "#333";
    for (let yy = 0; yy < H * scale; yy += cs)
      for (let xx = ((yy / cs) % 2) * cs; xx < W * scale; xx += cs * 2)
        ctx.fillRect(ox + xx, oy + yy, cs, cs);
    ctx.imageSmoothingQuality = "high";
    // 큰 축소 비율에서는 고품질 리사이즈 비트맵 캐시 사용 (브라우저 1단계 축소 앨리어싱 방지)
    const needW = Math.max(1, Math.round(W * scale * dpr));
    const needH = Math.max(1, Math.round(H * scale * dpr));
    const bmp = typeof createImageBitmap === "function" &&
      this.state.compImg.naturalWidth > needW * 1.3
        ? this.compBitmapFor(needW, needH) : null;
    ctx.drawImage(bmp || this.state.compImg, ox, oy, W * scale, H * scale);
    ctx.restore();

    if (this.state.selected != null) {
      const L = this.state.layers.get(this.state.selected);
      if (L) {
        const [x, y, w, h] = L.rect;
        const rx = ox + x * scale, ry = oy + y * scale, rw = w * scale, rh = h * scale;
        ctx.strokeStyle = "#4a9eff";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([5, 3]);
        ctx.strokeRect(rx, ry, rw, rh);
        ctx.setLineDash([]);
        ctx.font = "11px sans-serif";
        const label = L.name;
        const tw = ctx.measureText(label).width;
        const ly = ry > 16 ? ry - 15 : ry + 2;
        ctx.fillStyle = "rgba(74,158,255,.85)";
        ctx.fillRect(rx, ly, tw + 8, 14);
        ctx.fillStyle = "#fff";
        ctx.fillText(label, rx + 4, ly + 11);
      }
    }
  }

  compBitmapFor(needW, needH) {
    const img = this.state.compImg;
    const cur = this.compBmp;
    if (cur && cur.src === img && Math.abs(cur.w - needW) <= 2) return cur.bmp;
    const key = `${img.src}:${needW}`;
    if (this.bmpPending !== key) {
      this.bmpPending = key;
      createImageBitmap(img, { resizeWidth: needW, resizeHeight: needH, resizeQuality: "high" })
        .then((bmp) => {
          this.bmpPending = null;
          if (this.state?.compImg !== img) return;
          this.compBmp = { src: img, w: needW, bmp };
          this.draw();
        })
        .catch(() => { this.bmpPending = null; });
    }
    return cur?.bmp ?? null; // 준비 전에는 원본으로 그린다
  }

  alphaAt(L, lx, ly) {
    if (!L.actx) {
      const c = document.createElement("canvas");
      c.width = L.img.naturalWidth;
      c.height = L.img.naturalHeight;
      const g = c.getContext("2d", { willReadFrequently: true });
      g.drawImage(L.img, 0, 0);
      L.actx = g;
    }
    const sx = L.img.naturalWidth / L.rect[2];
    const sy = L.img.naturalHeight / L.rect[3];
    const px = Math.min(Math.max(Math.floor(lx * sx), 0), L.img.naturalWidth - 1);
    const py = Math.min(Math.max(Math.floor(ly * sy), 0), L.img.naturalHeight - 1);
    try {
      return L.actx.getImageData(px, py, 1, 1).data[3];
    } catch {
      return 255;
    }
  }

  hitTest(x, y) {
    const s = this.state;
    if (!s) return null;
    for (let k = s.order.length - 1; k >= 0; k--) {
      const idx = s.order[k];
      if (s.hidden.has(idx)) continue;
      const L = s.layers.get(idx);
      const [lx, ly, lw, lh] = L.rect;
      if (x < lx || y < ly || x >= lx + lw || y >= ly + lh) continue;
      if (this.alphaAt(L, x - lx, y - ly) > 12) return idx;
    }
    return null;
  }

  select(idx) {
    if (!this.state) return;
    this.state.selected = idx;
    for (const row of this.listEl.querySelectorAll(".llp-row")) {
      const on = Number(row.dataset.idx) === idx;
      row.classList.toggle("llp-selected", on);
      if (on) row.scrollIntoView({ block: "nearest" });
    }
    this.draw();
  }

  // ---- 레이어 리스트 ----

  renderList() {
    this.listEl.textContent = "";
    const s = this.state;
    if (!s) return;
    for (const idx of [...s.order].reverse()) { // 위→아래 표시
      const L = s.layers.get(idx);
      const row = document.createElement("div");
      row.className = "llp-row";
      row.dataset.idx = String(idx);
      if (idx === s.selected) row.classList.add("llp-selected");

      const thumb = document.createElement("div");
      thumb.className = "llp-thumb";
      const timg = new Image();
      timg.src = L.img.src;
      thumb.appendChild(timg);

      const nm = document.createElement("div");
      nm.className = "llp-nm";
      const name = document.createElement("span");
      name.className = "llp-name";
      name.textContent = L.name;
      name.title = `${L.name} (${L.kind})`;
      nm.appendChild(name);
      if (L.blend && L.blend !== "NORMAL" && L.blend !== "PASS_THROUGH") {
        const b = document.createElement("span");
        b.className = "llp-blend";
        b.textContent = L.blend.toLowerCase().replace(/_/g, " ");
        nm.appendChild(b);
      }

      const eye = document.createElement("button");
      eye.className = "llp-eye" + (s.hidden.has(idx) ? " llp-off" : "");
      eye.textContent = "\u{1F441}";
      eye.title = t("eyeTitle");
      eye.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      eye.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (s.hidden.has(idx)) s.hidden.delete(idx);
        else s.hidden.add(idx);
        eye.classList.toggle("llp-off", s.hidden.has(idx));
        this.onEdited();
      });

      row.append(thumb, nm, eye);
      row.addEventListener("pointerdown", (ev) => this.onRowPointerDown(ev, row, idx));
      this.listEl.appendChild(row);
    }
  }

  onRowPointerDown(ev, row, idx) {
    if (ev.button !== 0) return;
    ev.preventDefault();
    ev.stopPropagation();
    const startY = ev.clientY;
    let dragging = false;
    let dropPos = -1;
    const rows = [...this.listEl.querySelectorAll(".llp-row")];
    const clearMarks = () => {
      for (const r of rows) r.classList.remove("llp-drop", "llp-drop-below");
    };
    const move = (e) => {
      if (!dragging && Math.abs(e.clientY - startY) > 4) {
        dragging = true;
        row.classList.add("llp-dragging");
      }
      if (!dragging) return;
      dropPos = rows.length;
      for (let i = 0; i < rows.length; i++) {
        const rc = rows[i].getBoundingClientRect();
        if (e.clientY < rc.top + rc.height / 2) { dropPos = i; break; }
      }
      clearMarks();
      if (dropPos < rows.length) rows[dropPos].classList.add("llp-drop");
      else rows[rows.length - 1].classList.add("llp-drop-below");
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      clearMarks();
      row.classList.remove("llp-dragging");
      if (dragging && dropPos >= 0) {
        const disp = [...this.state.order].reverse();
        const from = disp.indexOf(idx);
        let to = dropPos;
        disp.splice(from, 1);
        if (to > from) to--;
        disp.splice(to, 0, idx);
        this.state.order = disp.reverse();
        this.renderList();
        this.onEdited();
      } else if (!dragging) {
        this.select(idx);
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // ---- 드롭 / 업로드 ----

  // 드롭된 파일의 로컬 경로 추출 시도. 일반 브라우저는 보안상 경로를 숨기므로
  // Electron(file.path)이나 file:// URI를 주는 환경에서만 성공한다.
  extractDropPath(e, file) {
    if (file.path && /\.(psd|psb)$/i.test(file.path)) return file.path;
    const uris = (e.dataTransfer?.getData("text/uri-list") ||
                  e.dataTransfer?.getData("text/plain") || "");
    const cand = uris.split(/\r?\n/).map((s) => s.trim())
      .find((s) => /\.(psd|psb)$/i.test(s));
    if (!cand) return "";
    if (cand.startsWith("file://")) {
      try {
        // file:///D:/a/b.psd → D:/a/b.psd (윈도 드라이브 문자 앞 슬래시 제거)
        return decodeURIComponent(new URL(cand).pathname).replace(/^\/([A-Za-z]:)/, "$1");
      } catch { return ""; }
    }
    return /^([A-Za-z]:[\\/]|\\\\)/.test(cand) ? cand : "";
  }

  async handleDrop(e) {
    const file = [...(e.dataTransfer?.files || [])].find((f) =>
      /\.(psd|psb)$/i.test(f.name));
    if (!file) {
      this.setError(t("dropOnlyPsd"));
      return;
    }
    const p = this.extractDropPath(e, file);
    if (p) {
      // 경로를 얻었으면 psd_path 로 직접 로드, 서버에 파일이 없으면(리모트 등) 업로드 폴백
      const pw = this.pathWidget();
      if (pw) pw.value = p;
      const sw = this.stateWidget();
      if (sw) sw.value = "";
      if (await this.inspect(true)) return;
      if (pw) pw.value = "";
    }
    await this.uploadFile(file); // 브라우저가 경로를 숨김 → input 업로드
  }

  async uploadFile(file) {
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await api.fetchApi("/load_layers_psd/upload", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) {
      this.setError(t("uploadFail", { msg: serverError(data, res.status) ?? data.error }));
      return;
    }
    const w = this.psdWidget();
    if (w) {
      if (w.options?.values && !w.options.values.includes(data.name)) w.options.values.push(data.name);
      w.value = data.name;
    }
    // 업로드(input) 방식으로 넘어가면 psd_path 잔존값이 우선권을 갖지 않도록 지운다
    const pw = this.pathWidget();
    if (pw) pw.value = "";
    const sw = this.stateWidget();
    if (sw) sw.value = "";
    await this.inspect(false);
  }
}

app.registerExtension({
  name: "seedream.loadLayersPsd",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      if (!document.getElementById("llp-style")) {
        const s = document.createElement("style");
        s.id = "llp-style";
        s.textContent = STYLE;
        document.head.appendChild(s);
      }
      const panel = new LoadPsdPanel(this);
      this.llpPanel = panel;
      // layer_state 위젯 숨김 (직렬화는 유지)
      const sw = panel.stateWidget();
      if (sw) {
        sw.computeSize = () => [0, -4];
        sw.hidden = true;
      }
      // psd 콤보 변경 → psd_path 해제(콤보 선택이 이기도록) + 상태 리셋 + inspect
      const psdW = panel.psdWidget();
      if (psdW) {
        const orig = psdW.callback;
        psdW.callback = function () {
          const rr = orig?.apply(this, arguments);
          const pathW = panel.pathWidget();
          if (pathW) pathW.value = "";
          panel.inspect(false);
          return rr;
        };
      }
      // psd_path 변경 → 상태 리셋 + inspect (값이 있으면 콤보보다 우선)
      const pathW = panel.pathWidget();
      if (pathW) {
        const orig = pathW.callback;
        pathW.callback = function () {
          const rr = orig?.apply(this, arguments);
          panel.inspect(false);
          return rr;
        };
      }
      const dw = this.addDOMWidget("psd_panel", "div", panel.root, { serialize: false });
      // 프론트엔드 직렬화 스킵 조건은 widget.serialize === false (options 가 아니라 위젯 객체).
      // 이게 없으면 widgets_values 에 ""가 끼어들어 복원 시 psd/layer_state 값이 유실된다.
      dw.serialize = false;
      // 새 노드: 레이어 쌍 슬롯은 inspect 전까지 접어둔다
      if (this.outputs) {
        while (this.outputs.length > BASE_OUTPUTS) this.removeOutput(this.outputs.length - 1);
      }
      const sz = this.computeSize();
      this.setSize([Math.max(sz[0], 640), Math.max(sz[1], 560)]);
      setTimeout(() => panel.autoInspect(), 0);
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const r = onConfigure?.apply(this, arguments);
      // 구버전 저장본(DOM 위젯 값 포함) 구제: named 맵에서 실제 위젯 값 재적용
      const named = info?.widgets_values_named;
      if (named) {
        for (const name of ["psd", "layer_state", "psd_path"]) {
          if (typeof named[name] !== "string") continue;
          const w = this.widgets?.find((x) => x.name === name);
          if (w && w.value !== named[name]) w.value = named[name];
        }
      }
      if (this.properties?.llpAutoReload) this.llpPanel?.setAutoReload(true);
      setTimeout(() => this.llpPanel?.autoInspect(), 0);
      return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      onExecuted?.apply(this, arguments);
      const payload = message?.load_layers_psd?.[0];
      if (payload) this.llpPanel?.load(payload);
    };

    nodeType.prototype.onDragOver = function (e) {
      return !![...(e.dataTransfer?.items || [])].length;
    };
    nodeType.prototype.onDragDrop = function (e) {
      const file = [...(e.dataTransfer?.files || [])].find((f) =>
        /\.(psd|psb)$/i.test(f.name));
      if (!file) return false;
      this.llpPanel?.handleDrop(e);
      return true;
    };
  },
});
