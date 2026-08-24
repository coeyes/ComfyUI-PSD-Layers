// SaveSeedreamLayersPSD 노드의 인터랙티브 레이어 패널.
// - 미리보기 캔버스: 배경 + 보이는 레이어를 z 순서로 합성, 선택 레이어 바운딩박스 표시
// - 미리보기 클릭: 알파 히트테스트로 최상단 오브젝트 선택
// - 레이어 리스트: 클릭 선택, 드래그로 순서(z_index) 변경, 눈 아이콘으로 숨김 토글
// - Save PSD 버튼: 현재 순서/가시성으로 서버에 재저장 요청
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { t, getLocale } from "./psdI18n.js";

const NODE_NAME = "SaveSeedreamLayersPSD";

const STYLE = `
.ssl-root { display:flex; gap:8px; width:100%; height:100%; min-height:340px;
  font-family:sans-serif; font-size:12px; color:#ddd; background:#1b1b1b;
  border-radius:6px; padding:6px; box-sizing:border-box; }
.ssl-preview { position:relative; flex:1; min-width:0; background:#111;
  border-radius:4px; overflow:hidden; }
.ssl-preview canvas { position:absolute; inset:0; width:100%; height:100%; cursor:crosshair; }
.ssl-empty { position:absolute; inset:0; display:flex; align-items:center;
  justify-content:center; color:#666; pointer-events:none; }
.ssl-loaded .ssl-empty { display:none; }
.ssl-side { width:210px; flex:none; display:flex; flex-direction:column; gap:6px; min-height:0; }
.ssl-head { display:flex; justify-content:space-between; padding:2px 4px; color:#aaa; }
.ssl-list { flex:1; overflow-y:auto; overflow-x:hidden; display:flex;
  flex-direction:column; gap:2px; min-height:0; }
.ssl-row { display:flex; align-items:center; gap:6px; padding:3px 4px; border-radius:4px;
  cursor:grab; border:1px solid transparent; user-select:none; touch-action:none; }
.ssl-row:hover { background:#2a2a2a; }
.ssl-row.ssl-selected { background:#2d3d55; border-color:#4a9eff; }
.ssl-row.ssl-dragging { opacity:.4; }
.ssl-row.ssl-drop { box-shadow:0 -2px 0 #4a9eff; }
.ssl-row.ssl-drop-below { box-shadow:0 2px 0 #4a9eff; }
.ssl-thumb { width:34px; height:34px; flex:none; border-radius:3px; overflow:hidden;
  background:repeating-conic-gradient(#333 0% 25%, #222 0% 50%) 0 0 / 12px 12px; }
.ssl-thumb img { width:100%; height:100%; object-fit:contain; display:block; }
.ssl-name { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ssl-eye { flex:none; cursor:pointer; background:none; border:none; color:#ccc;
  font-size:13px; padding:2px 4px; line-height:1; }
.ssl-eye.ssl-off { opacity:.25; }
.ssl-save { flex:none; padding:6px; border:none; border-radius:4px; cursor:pointer;
  background:#3a5ccc; color:#fff; font-size:12px; }
.ssl-save:hover { background:#4a6cdc; }
.ssl-save:disabled { background:#333; color:#777; cursor:default; }
.ssl-info { flex:none; color:#9a9; padding:0 2px; }
.ssl-status { flex:none; min-height:15px; max-height:45px; overflow-y:auto;
  color:#8b8; padding:0 2px; word-break:break-all; }
.ssl-status.ssl-err { color:#c77; }
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

class LayerPanel {
  constructor(node) {
    this.node = node;
    this.state = null; // { canvas, baseImg, layers: Map(index -> layer), order, selected, nodeId }
    this.view = null;  // { scale, ox, oy }
    this.buildDOM();
  }

  buildDOM() {
    const root = document.createElement("div");
    root.className = "ssl-root";
    root.innerHTML = `
      <div class="ssl-preview"><canvas></canvas>
        <div class="ssl-empty"></div></div>
      <div class="ssl-side">
        <div class="ssl-head"><span class="ssl-word"></span><span class="ssl-count"></span></div>
        <div class="ssl-list"></div>
        <button class="ssl-save"></button>
        <div class="ssl-info"></div>
        <div class="ssl-status"></div>
      </div>`;
    this.root = root;
    this.previewEl = root.querySelector(".ssl-preview");
    this.cv = root.querySelector("canvas");
    this.listEl = root.querySelector(".ssl-list");
    this.emptyEl = root.querySelector(".ssl-empty");
    this.wordEl = root.querySelector(".ssl-word");
    this.countEl = root.querySelector(".ssl-count");
    this.saveBtn = root.querySelector(".ssl-save");
    this.infoEl = root.querySelector(".ssl-info");
    this.statusEl = root.querySelector(".ssl-status");
    this.refreshTexts();

    this.saveBtn.addEventListener("click", () => this.save());
    // 휠 이벤트를 그래프 캔버스로 전달해 Comfy 줌이 동작하게 한다 (레이어 리스트 위에서는 스크롤 유지)
    root.addEventListener("wheel", (e) => {
      if (e.target.closest?.(".ssl-list")) return;
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

  // key/vars 로 저장해 두면 로케일 변경 시 refreshTexts 가 재번역한다. raw=true 면 원문 그대로.
  setStatus(key, vars = null, isError = false, raw = false) {
    this._status = { key, vars, isError, raw };
    this.renderStatus();
  }

  renderStatus() {
    if (!this._status) return;
    const { key, vars, isError, raw } = this._status;
    this.statusEl.textContent = raw ? key : t(key, vars);
    this.statusEl.classList.toggle("ssl-err", isError);
  }

  // 로케일 반영이 필요한 정적 텍스트들 갱신
  refreshTexts() {
    this.emptyEl.textContent = t("saveEmpty");
    this.wordEl.textContent = t("layers");
    this.saveBtn.textContent = t("savePsdBtn");
    this.renderStatus();
    if (this.state) {
      this.updateCount();
      this.renderList();
    }
  }

  async load(payload) {
    try {
      const baseImg = await loadImage(viewURL(payload.base));
      const layers = new Map();
      await Promise.all(payload.layers.map(async (l) => {
        const img = await loadImage(viewURL(l.image));
        layers.set(l.index, { ...l, img, visible: true, actx: null });
      }));
      this.state = {
        canvas: payload.canvas,
        baseImg,
        layers,
        order: payload.layers.map((l) => l.index), // 아래→위
        selected: null,
        nodeId: payload.node_id,
      };
      this.root.classList.add("ssl-loaded");
      this.bmpCache = new Map();
      this.updateCount();
      this.setStatus("saved", { files: payload.saved?.psd?.filename ?? "" });
      this.renderList();
      this.draw();
    } catch (e) {
      this.setStatus("previewFail", { msg: e }, true);
    }
  }

  updateCount() {
    if (!this.state) return;
    const total = this.state.order.length;
    const visible = this.state.order.filter((i) => this.state.layers.get(i).visible).length;
    this.countEl.textContent = `${visible}/${total}`;
    const [W, H] = this.state.canvas;
    this.infoEl.textContent = t("layersCount", { n: total }) + ` · ${W}×${H}`;
  }

  // ---- 미리보기 캔버스 ----

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

    ctx.imageSmoothingQuality = "high";
    const px = scale * dpr;
    ctx.drawImage(this.bitmapFor(this.state.baseImg, Math.round(W * px), Math.round(H * px)),
      ox, oy, W * scale, H * scale);
    for (const idx of this.state.order) {
      const L = this.state.layers.get(idx);
      if (!L.visible) continue;
      const [x, y, w, h] = L.rect;
      ctx.drawImage(this.bitmapFor(L.img, Math.round(w * px), Math.round(h * px)),
        ox + x * scale, oy + y * scale, w * scale, h * scale);
    }

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
        // 이름 라벨
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

  // 큰 축소 비율에서 고품질 리사이즈 비트맵 캐시 (준비 전에는 원본 반환)
  bitmapFor(img, needW, needH) {
    if (typeof createImageBitmap !== "function" || needW < 1 || needH < 1 ||
        img.naturalWidth <= needW * 1.3) return img;
    if (!this.bmpCache) this.bmpCache = new Map();
    const rec = this.bmpCache.get(img);
    if (rec && Math.abs(rec.w - needW) <= 2) return rec.bmp || img;
    if (rec?.pending === needW) return rec.bmp || img;
    this.bmpCache.set(img, { ...(rec || {}), pending: needW });
    createImageBitmap(img, { resizeWidth: needW, resizeHeight: needH, resizeQuality: "high" })
      .then((bmp) => { this.bmpCache.set(img, { w: needW, bmp }); this.draw(); })
      .catch(() => { this.bmpCache.delete(img); });
    return rec?.bmp || img;
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
      return 255; // CORS 등으로 픽셀을 못 읽으면 박스 히트만으로 선택
    }
  }

  hitTest(x, y) {
    if (!this.state) return null;
    for (let k = this.state.order.length - 1; k >= 0; k--) {
      const idx = this.state.order[k];
      const L = this.state.layers.get(idx);
      if (!L.visible) continue;
      const [lx, ly, lw, lh] = L.rect;
      if (x < lx || y < ly || x >= lx + lw || y >= ly + lh) continue;
      if (this.alphaAt(L, x - lx, y - ly) > 12) return idx;
    }
    return null;
  }

  select(idx) {
    if (!this.state) return;
    this.state.selected = idx;
    for (const row of this.listEl.querySelectorAll(".ssl-row")) {
      const on = Number(row.dataset.idx) === idx;
      row.classList.toggle("ssl-selected", on);
      if (on) row.scrollIntoView({ block: "nearest" });
    }
    this.draw();
  }

  // ---- 레이어 리스트 ----

  renderList() {
    this.listEl.textContent = "";
    if (!this.state) return;
    const display = [...this.state.order].reverse(); // 위→아래 표시
    for (const idx of display) {
      const L = this.state.layers.get(idx);
      const row = document.createElement("div");
      row.className = "ssl-row";
      row.dataset.idx = String(idx);
      if (idx === this.state.selected) row.classList.add("ssl-selected");

      const thumb = document.createElement("div");
      thumb.className = "ssl-thumb";
      const timg = new Image();
      timg.src = L.img.src;
      thumb.appendChild(timg);

      const name = document.createElement("span");
      name.className = "ssl-name";
      name.textContent = L.name;
      name.title = L.name;

      const eye = document.createElement("button");
      eye.className = "ssl-eye" + (L.visible ? "" : " ssl-off");
      eye.textContent = "\u{1F441}";
      eye.title = t("eyeTitle");
      eye.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      eye.addEventListener("click", (ev) => {
        ev.stopPropagation();
        L.visible = !L.visible;
        eye.classList.toggle("ssl-off", !L.visible);
        this.updateCount();
        this.draw();
      });

      row.append(thumb, name, eye);
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
    const rows = [...this.listEl.querySelectorAll(".ssl-row")];

    const clearMarks = () => {
      for (const r of rows) r.classList.remove("ssl-drop", "ssl-drop-below");
    };
    const move = (e) => {
      if (!dragging && Math.abs(e.clientY - startY) > 4) {
        dragging = true;
        row.classList.add("ssl-dragging");
      }
      if (!dragging) return;
      dropPos = rows.length;
      for (let i = 0; i < rows.length; i++) {
        const rc = rows[i].getBoundingClientRect();
        if (e.clientY < rc.top + rc.height / 2) { dropPos = i; break; }
      }
      clearMarks();
      if (dropPos < rows.length) rows[dropPos].classList.add("ssl-drop");
      else rows[rows.length - 1].classList.add("ssl-drop-below");
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      clearMarks();
      row.classList.remove("ssl-dragging");
      if (dragging && dropPos >= 0) {
        // 표시 리스트(위→아래)에서 재배치 후 order(아래→위)로 환원
        const disp = [...this.state.order].reverse();
        const from = disp.indexOf(idx);
        let to = dropPos;
        disp.splice(from, 1);
        if (to > from) to--;
        disp.splice(to, 0, idx);
        this.state.order = disp.reverse();
        this.renderList();
        this.draw();
      } else if (!dragging) {
        this.select(idx);
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // ---- 저장 ----

  async save() {
    if (!this.state) {
      this.setStatus("runFirst", null, true);
      return;
    }
    const prefixW = this.node.widgets?.find((w) => w.name === "filename_prefix");
    this.saveBtn.disabled = true;
    this.setStatus("saving");
    try {
      const res = await api.fetchApi("/seedream_psd/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          node_id: this.state.nodeId,
          order: this.state.order,
          hidden: this.state.order.filter((i) => !this.state.layers.get(i).visible),
          filename_prefix: prefixW?.value,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        this.setStatus("saved", { files: `${data.psd.filename} / ${data.png.filename}` });
      } else {
        const msg = data.code === "session_missing"
          ? t("err.session_missing_run") : (data.error || res.status);
        this.setStatus("saveFail", { msg }, true);
      }
    } catch (e) {
      this.setStatus("saveFail", { msg: e }, true);
    } finally {
      this.saveBtn.disabled = false;
    }
  }
}

app.registerExtension({
  name: "seedream.saveLayersPsd",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      if (!document.getElementById("ssl-style")) {
        const s = document.createElement("style");
        s.id = "ssl-style";
        s.textContent = STYLE;
        document.head.appendChild(s);
      }
      const panel = new LayerPanel(this);
      this.sslPanel = panel;
      const dw = this.addDOMWidget("psd_layers", "div", panel.root, { serialize: false });
      // 프론트엔드 직렬화 스킵 조건은 widget.serialize === false (options 가 아니라 위젯 객체).
      // 이게 없으면 widgets_values 에 ""가 끼어들어 복원 시 filename_prefix 값이 유실된다.
      dw.serialize = false;
      const sz = this.computeSize();
      this.setSize([Math.max(sz[0], 640), Math.max(sz[1], 540)]);
      return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      onExecuted?.apply(this, arguments);
      const payload = message?.seedream_psd?.[0];
      if (payload) this.sslPanel?.load(payload);
    };

    // 구버전 저장본(DOM 위젯 값이 widgets_values 에 포함된 워크플로우) 구제:
    // 배열 정렬이 어긋나 값이 유실돼도 named 맵에는 원본 값이 남아 있다.
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const r = onConfigure?.apply(this, arguments);
      const named = info?.widgets_values_named;
      if (named && typeof named.filename_prefix === "string") {
        const w = this.widgets?.find((x) => x.name === "filename_prefix");
        if (w && w.value !== named.filename_prefix) w.value = named.filename_prefix;
      }
      return r;
    };
  },
});
