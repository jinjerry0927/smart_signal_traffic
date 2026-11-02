/* Shared SVG traffic view. All geometry is in metres; no prediction or control. */
(function (root) {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const distance = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
  const angleDelta = (a, b) => ((b - a + 540) % 360) - 180;
  const node = (tag, attrs, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  };
  const path = pts => pts.map((p, i) => `${i ? 'L' : 'M'}${p[0]},${p[1]}`).join(' ');
  function bodyCenter(v) {
    const r = v.angle * Math.PI / 180;
    return [v.x - Math.sin(r) * v.length / 2, v.y - Math.cos(r) * v.length / 2];
  }
  function vehicleDisplayMode(v, pixelsPerMetre) {
    return pixelsPerMetre < 1.3 && v.length * pixelsPerMetre < 7 ? 'minimum-marker' : 'physical-dimensions';
  }
  function offsetPolyline(points, offset) {
    if (points.length < 2) return points.map(p => [...p]);
    return points.map((p, i) => {
      const normal = (a, b) => {
        const len = distance(a, b) || 1;
        return [-(b[1] - a[1]) / len, (b[0] - a[0]) / len];
      };
      const a = normal(points[Math.max(0, i - 1)], points[i === 0 ? 1 : i]);
      const b = normal(points[i === points.length - 1 ? i - 1 : i], points[Math.min(i + 1, points.length - 1)]);
      const sum = [a[0] + b[0], a[1] + b[1]], len = Math.hypot(...sum) || 1;
      const m = [sum[0] / len, sum[1] / len];
      const scale = Math.min(Math.abs(offset) * 2, Math.abs(offset) / Math.max(.5, m[0] * b[0] + m[1] * b[1]));
      return [p[0] + m[0] * scale * Math.sign(offset), p[1] + m[1] * scale * Math.sign(offset)];
    });
  }
  function projectToLane(p, points) {
    let best = null, traversed = 0;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i], len = distance(a, b);
      if (!len) continue;
      const t = clamp(((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / len ** 2, 0, 1);
      const q = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
      const error = distance(p, q);
      if (!best || error < best.error) best = {s: traversed + t * len, error,
        lateral: ((p[0] - q[0]) * -(b[1] - a[1]) + (p[1] - q[1]) * (b[0] - a[0])) / len};
      traversed += len;
    }
    return best;
  }
  function atDistance(points, s) {
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i], len = distance(a, b);
      if (s <= len || i === points.length - 1) {
        const t = clamp(s / (len || 1), 0, 1);
        return {x: a[0] + t * (b[0] - a[0]), y: a[1] + t * (b[1] - a[1]),
          nx: -(b[1] - a[1]) / (len || 1), ny: (b[0] - a[0]) / (len || 1)};
      }
      s -= len;
    }
    return {x: points[0][0], y: points[0][1], nx: 0, ny: 0};
  }
  function motionPlan(previous, next, context, lane) {
    const dt = context.time - context.previousTime;
    const no = reason => ({animate: false, reason, to: next});
    if (!previous || !context.sameRun || context.status !== 'running' || context.previousStatus !== 'running') return no('state');
    if (dt <= 0 || dt > 2 || context.speed > 10 || context.wallGap > 1200) return no('time-or-speed');
    if (!lane || previous.lane_id !== next.lane_id || !lane.shape || lane.shape.length < 2) return no('lane-change');
    const move = distance([previous.x, previous.y], [next.x, next.y]);
    if (move > Math.min(35, Math.max(6, Math.max(previous.speed, next.speed) * dt * 1.4 + 2))) return no('large-jump');
    const from = projectToLane([previous.x, previous.y], lane.shape), to = projectToLane([next.x, next.y], lane.shape);
    if (!from || !to || from.error > lane.width * .35 || to.error > lane.width * .35 || to.s < from.s - .1) return no('off-lane');
    return {animate: move > .01 || Math.abs(angleDelta(previous.angle, next.angle)) > .1,
      from: previous, to: next, start: from, end: to, lane,
      duration: clamp(context.wallGap, 80, 1000), reason: 'observed-same-lane'};
  }
  function sampleMotion(plan, t) {
    if (!plan.animate || t >= 1) return plan.to;
    if (t <= 0) return plan.from;
    const p = atDistance(plan.lane.shape, plan.start.s + (plan.end.s - plan.start.s) * t);
    const lateral = plan.start.lateral + (plan.end.lateral - plan.start.lateral) * t;
    return {...plan.to, x: p.x + p.nx * lateral, y: p.y + p.ny * lateral,
      angle: plan.from.angle + angleDelta(plan.from.angle, plan.to.angle) * t};
  }
  function movementSignals(data, junctions) {
    const live = new Map(junctions.map(j => [j.id, j]));
    const result = [];
    for (const [jid, signal] of Object.entries(data.signals)) {
      const current = live.get(jid);
      for (const movement of signal.movements) result.push({...movement, junction: jid,
        color: current ? current.state[movement.index] || '?' : '?'});
    }
    return result;
  }

  class TrafficMap {
    constructor(svg, options = {}) {
      this.svg = svg; this.options = options; this.cars = new Map(); this.frame = null;
      this.lastReceived = 0; this.raf = 0; this.zoom = 1; this.destroyed = false;
      this.bindNavigation();
      this.resizeObserver = new ResizeObserver(() => this.refreshLOD());
      this.resizeObserver.observe(svg);
      this.media = matchMedia('(prefers-reduced-motion: reduce)');
    }
    bindNavigation() {
      this.svg.style.touchAction = 'none'; this.svg.style.cursor = 'grab';
      this.svg.setAttribute('tabindex', '0');
      this.svg.addEventListener('pointerdown', e => {
        if (e.button !== 0 || !this.box || e.target.closest?.('.tm-label')) return;
        this.drag = {x: e.clientX, y: e.clientY, center: [...this.center]};
        this.svg.setPointerCapture(e.pointerId); this.svg.style.cursor = 'grabbing';
      });
      this.svg.addEventListener('pointermove', e => {
        if (!this.drag) return;
        const scale = this.pixelsPerMetre();
        this.center = [this.drag.center[0] - (e.clientX - this.drag.x) / scale,
          this.drag.center[1] - (e.clientY - this.drag.y) / scale];
        this.view();
      });
      const up = () => {this.drag = null; this.svg.style.cursor = 'grab';};
      this.svg.addEventListener('pointerup', up); this.svg.addEventListener('pointercancel', up);
      this.svg.addEventListener('wheel', e => {
        if (!this.box) return;
        e.preventDefault(); this.zoomBy(e.deltaY < 0 ? 1.25 : .8);
      }, {passive: false});
      this.svg.addEventListener('keydown', e => {
        if (!this.box) return;
        if (e.key === '+' || e.key === '=') this.zoomBy(1.4);
        else if (e.key === '-') this.zoomBy(1 / 1.4);
        else if (e.key === 'Home') this.reset();
        else if (e.key.startsWith('Arrow')) {
          const step = 35 / this.pixelsPerMetre();
          this.center[0] += e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0;
          this.center[1] += e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0;
          this.view();
        } else return;
        e.preventDefault();
      });
    }
    load(data) {
      this.freeze(); this.frame = null; this.cars.clear(); this.data = data;
      this.svg.replaceChildren(); this.svg.classList.add('tm-map');
      const [x0, y0, x1, y1] = data.bounds;
      this.project = p => [p[0] - x0 + 35, y1 - p[1] + 35];
      this.box = [0, 0, x1 - x0 + 70, y1 - y0 + 70];
      const style = node('style', {}, this.svg);
      style.textContent = '.tm-map{background:#edf3f3}.tm-map .tm-road{fill:none;stroke:#344b58;stroke-linecap:butt;stroke-linejoin:round}.tm-map .tm-boundary{fill:none;stroke:#e5eef0;stroke-linejoin:round;stroke-width:.14;pointer-events:none}.tm-map .tm-lane-line{stroke-dasharray:3 6;stroke-width:.12;opacity:.75}.tm-map .tm-car{color:#24b7bc}.tm-map .tm-car.tm-stopped{color:#ef7783}.tm-map .tm-body{fill:currentColor;stroke:#102d39;stroke-width:.12}.tm-map .tm-glass{fill:#c8e6ed;stroke:none}.tm-map .tm-roof{fill:none;stroke:#1f5967;stroke-width:.1}.tm-map .tm-proxy{fill:currentColor;stroke:#163b48;stroke-width:.5}.tm-map .tm-signal-label{fill:#f7fcfd;font:600 6px sans-serif;text-anchor:middle}.tm-map .tm-label{font:600 12px sans-serif;fill:#183e4b;paint-order:stroke;stroke:#f7fbfc;stroke-width:4;stroke-linejoin:round;cursor:pointer}.tm-map .tm-arrow{fill:none;stroke:#dce9ec;stroke-width:.2;opacity:.75}.tm-map .tm-stop{stroke:#f1f6f7;stroke-width:.5}.tm-map .tm-highlight{fill:none;stroke:#f6b740;stroke-opacity:.55;stroke-linecap:butt;pointer-events:none}';
      this.roadLayer = node('g', {'data-layer': 'roads'}, this.svg);
      this.markLayer = node('g', {'data-layer': 'markings'}, this.svg);
      this.highlightLayer = node('g', {id: 'decision-lanes'}, this.svg);
      this.carLayer = node('g', {id: 'cars', 'data-layer': 'vehicles'}, this.svg);
      this.signalLayer = node('g', {'data-layer': 'signals'}, this.svg);
      this.labelLayer = node('g', {'data-layer': 'labels'}, this.svg);
      this.drawRoads(); this.drawSignals(); this.drawLabels(); this.reset();
      const description = node('desc', {}, this.svg);
      description.textContent = 'SUMO 차선 중심선·폭과 교차로 경계. 차량 위치는 앞범퍼, 확대 차량은 실제 치수입니다. 전체 보기의 작은 차량 표식은 가독성 보정입니다. 저배속은 수신한 두 관측 사이를 최대 1초 지연으로 보간하며 고배속은 최신 관측을 표시합니다. 신호 표시는 연결 이동별이며 일부 녹색이 차선 전체 허용을 뜻하지 않습니다. 드래그, 휠, 방향키, +/−, Home으로 지도를 조작합니다.';
    }
    drawRoads() {
      const data = this.data;
      for (const edge of data.edges) for (const lid of edge.lanes) {
        const lane = data.lanes[lid];
        node('path', {d: path(lane.shape.map(this.project)), class: 'tm-road', 'stroke-width': lane.width,
          'data-lane': lid, 'data-physical-width': lane.width}, this.roadLayer);
      }
      for (const j of data.road_junctions || data.junctions) node('path', {
        d: path(j.shape.map(this.project)) + 'Z', fill: '#344b58', 'data-junction-surface': j.id
      }, this.roadLayer);
      for (const edge of data.edges) {
        const ids = edge.lanes;
        for (let i = 0; i < ids.length; i++) {
          const lane = data.lanes[ids[i]], points = lane.shape.map(this.project);
          node('path', {d: path(offsetPolyline(points, lane.width / 2)),
            class: 'tm-boundary' + (i ? ' tm-lane-line' : ''), 'data-boundary-of': ids[i]}, this.markLayer);
          if (i === ids.length - 1) node('path', {d: path(offsetPolyline(points, -lane.width / 2)), class: 'tm-boundary'}, this.markLayer);
          const total = points.slice(1).reduce((sum, p, n) => sum + distance(points[n], p), 0);
          if (total > 35) {
            const end = atDistance(points, Math.max(15, total - 25));
            const angle = Math.atan2(end.ny, end.nx) * 180 / Math.PI;
            node('path', {d: 'M-0.65,1.2 L0,0 L0.65,1.2 M0,0 L0,3', class: 'tm-arrow',
              transform: `translate(${end.x} ${end.y}) rotate(${angle})`}, this.markLayer);
          }
        }
      }
      const incoming = new Set(Object.values(data.signals).flatMap(s => s.movements.map(m => m.incoming)));
      for (const lid of incoming) {
        const lane = data.lanes[lid], points = lane.shape.map(this.project), a = points.at(-2), b = points.at(-1);
        const len = distance(a, b) || 1, nx = -(b[1] - a[1]) / len, ny = (b[0] - a[0]) / len;
        node('line', {x1: b[0] - nx * lane.width * .45, y1: b[1] - ny * lane.width * .45,
          x2: b[0] + nx * lane.width * .45, y2: b[1] + ny * lane.width * .45, class: 'tm-stop'}, this.markLayer);
      }
    }
    drawSignals() {
      this.signalNodes = []; this.badges = [];
      for (const [jid, signal] of Object.entries(this.data.signals)) {
        const slots = new Map();
        for (const m of signal.movements) {
          const lane = this.data.lanes[m.incoming], p = lane.shape.map(this.project), a = p.at(-2), b = p.at(-1);
          const len = distance(a, b) || 1, ux = (b[0] - a[0]) / len, uy = (b[1] - a[1]) / len;
          const slot = slots.get(m.incoming) || 0; slots.set(m.incoming, slot + 1);
          const group = node('g', {'data-signal-junction': jid, 'data-signal-index': m.index,
            'data-incoming': m.incoming, 'data-outgoing': m.outgoing, 'data-signal-state': '?'}, this.signalLayer);
          const title = node('title', {}, group); title.textContent = `${jid}: ${m.incoming} → ${m.outgoing}`;
          const head = node('g', {}, group);
          node('rect', {x: -3, y: -13, width: 6, height: 17, rx: 2, fill: '#152c36', stroke: '#d6e3e7', 'stroke-width': .6}, head);
          const bulbs = ['r', 'y', 'G'].map((s, i) => node('circle', {cx: 0, cy: -10 + 4 * i, r: 1.6, fill: '#425760', 'data-bulb': s}, head));
          const arrow = node('path', {d: ({l: 'M1,1 L-1,1 L-1,-1 M-1,1 L-1,3', r: 'M-1,1 L1,1 L1,-1 M1,1 L1,3',
            s: 'M0,3 L0,-1 M-1,0 L0,-1 L1,0', t: 'M1,3 L1,0 Q1,-2 -1,-2 L-1,1 M-2,0 L-1,1 L0,0'})[m.turn] || 'M0,3 L0,-1',
            fill: 'none', stroke: '#e8f5f6', 'stroke-width': .75}, head);
          this.signalNodes.push({jid, index: m.index, group, head, bulbs, arrow, title, movement: m, anchor: b, ux, uy, slot, width: lane.width});
        }
        const j = this.data.junctions.find(j => j.id === jid), group = node('g', {}, this.labelLayer);
        node('rect', {x: 13, y: 5, width: 91, height: 18, rx: 5, fill: '#f8fcfd', stroke: '#b9cdd1', 'stroke-width': .7}, group);
        const bulbs = ['r', 'y', 'G'].map((s, i) => node('circle', {cx: 21 + i * 8, cy: 14, r: 2.7, fill: '#b6c5ca'}, group));
        const text = node('text', {x: 46, y: 17, fill: '#274b59', 'font-size': 9}, group);
        this.badges.push({jid, anchor: this.project([j.x, j.y]), group, bulbs, text});
      }
    }
    drawLabels() {
      this.labels = this.data.junctions.map(j => {
        const group = node('g', {}, this.labelLayer), text = node('text', {x: 13, y: -5, class: 'tm-label'}, group);
        text.textContent = j.name; text.addEventListener('click', () => {this.focus(j.id); this.options.onJunction?.(j.id);});
        return {group, anchor: this.project([j.x, j.y])};
      });
    }
    pixelsPerMetre() {
      if (!this.box) return 1;
      const rect = this.svg.getBoundingClientRect();
      return Math.max(.001, Math.min(rect.width / (this.box[2] / this.zoom), rect.height / (this.box[3] / this.zoom)));
    }
    refreshLOD() {
      if (!this.box) return;
      const ppm = this.pixelsPerMetre(), detailed = ppm >= 1.3;
      this.svg.dataset.lod = detailed ? 'detail' : 'overview'; this.svg.dataset.pixelsPerMetre = ppm.toFixed(4);
      this.markLayer.style.opacity = ppm < .3 ? '.55' : '1';
      for (const label of [...this.labels, ...this.badges]) label.group.setAttribute('transform', `translate(${label.anchor}) scale(${1 / ppm})`);
      const narrow = this.svg.getBoundingClientRect().width < 600;
      for (const label of this.labels) {
        const text = label.group.querySelector('text');
        text.setAttribute('x', narrow ? -13 : 13); text.setAttribute('text-anchor', narrow ? 'end' : 'start');
      }
      for (const badge of this.badges) {
        badge.group.style.display = detailed ? 'none' : '';
        if (narrow) badge.group.setAttribute('transform', `translate(${badge.anchor}) scale(${1 / ppm}) translate(-117 0)`);
      }
      for (const signal of this.signalNodes) {
        signal.group.style.display = detailed ? '' : 'none';
        const {anchor, ux, uy, slot, width} = signal;
        const x = anchor[0] - ux * (2 + slot * 19 / ppm) - uy * (width / 2 + 1),
          y = anchor[1] - uy * (2 + slot * 19 / ppm) + ux * (width / 2 + 1);
        signal.group.setAttribute('transform', `translate(${x} ${y}) scale(${1 / ppm})`);
      }
      for (const entry of this.cars.values()) this.vehicleLOD(entry, ppm);
      this.options.onLOD?.({detailed, pixelsPerMetre: ppm});
    }
    vehicleLOD(entry, ppm) {
      const v = entry.latest, mode = vehicleDisplayMode(v, ppm), proxy = mode === 'minimum-marker';
      entry.detail.style.display = proxy ? 'none' : '';
      entry.proxy.style.display = proxy ? '' : 'none';
      entry.proxy.setAttribute('transform', `translate(0 ${v.length / 2}) scale(${1 / ppm})`);
      entry.group.dataset.displayMode = mode;
    }
    view() {
      const w = this.box[2] / this.zoom, h = this.box[3] / this.zoom;
      this.svg.setAttribute('viewBox', `${this.center[0] - w / 2} ${this.center[1] - h / 2} ${w} ${h}`);
      this.refreshLOD();
    }
    reset() {if (!this.box) return; this.zoom = 1; this.center = [this.box[2] / 2, this.box[3] / 2]; this.view();}
    zoomBy(factor) {if (!this.box) return; this.zoom = clamp(this.zoom * factor, 1, 40); this.view();}
    focus(jid) {
      const j = this.data.junctions.find(j => j.id === jid); if (!j) return;
      this.zoom = clamp(this.box[3] / 125, 1, 40); this.center = this.project([j.x, j.y]); this.view();
    }
    highlight(phase) {
      this.highlightLayer.replaceChildren(); if (!phase) return;
      for (const lid of phase.incoming) {
        const lane = this.data.lanes[lid]; if (!lane) continue;
        node('path', {d: path(lane.shape.map(this.project)), class: 'tm-highlight', 'stroke-width': lane.width * .9}, this.highlightLayer);
      }
    }
    createCar(v) {
      const group = node('g', {class: 'tm-car', 'data-vehicle-id': v.id}, this.carLayer);
      const title = node('title', {}, group), detail = node('g', {}, group);
      const L = v.length, W = v.width, cls = v.vehicle_class;
      node('rect', {x: -W / 2, y: 0, width: W, height: L, rx: Math.min(W * .22, .45), class: 'tm-body'}, detail);
      const windshieldY = cls === 'truck' ? .3 : L * .16;
      node('rect', {x: -W * .36, y: windshieldY, width: W * .72, height: Math.min(.7, L * .16), rx: .15, class: 'tm-glass'}, detail);
      if (cls === 'bus' || cls === 'coach') {
        for (let y = L * .32; y < L * .85; y += 1.1) node('path', {d: `M${-W * .35},${y} h${W * .7}`, class: 'tm-roof'}, detail);
      } else if (cls === 'truck') {
        node('rect', {x: -W * .4, y: Math.min(2.3, L * .3), width: W * .8, height: L * .6, rx: .1, class: 'tm-roof'}, detail);
      } else {
        node('rect', {x: -W * .32, y: L * .64, width: W * .64, height: L * .12, rx: .1, class: 'tm-glass'}, detail);
      }
      const proxy = node('g', {}, group);
      node('path', {d: 'M0,-2 L1,-.8 L1,2 L-1,2 L-1,-.8 Z', class: 'tm-proxy'}, proxy);
      group.dataset.physicalLength = L; group.dataset.physicalWidth = W;
      group.dataset.vehicleClass = cls || 'unknown'; group.dataset.positionReference = 'front-bumper';
      title.textContent = `${v.id} · ${cls || '차종 미제공'} · ${L} × ${W} m`;
      return {group, detail, proxy, latest: v, plan: null};
    }
    place(entry, v) {
      const p = this.project([v.x, v.y]);
      entry.group.setAttribute('transform', `translate(${p}) rotate(${v.angle})`);
      entry.group.dataset.frontX = v.x; entry.group.dataset.frontY = v.y; entry.group.dataset.angle = v.angle;
    }
    update(state) {
      if (!this.data) return;
      const now = performance.now(), sameRun = this.frame && this.frame.run_id === state.run_id && this.frame.session_id === state.session_id;
      if (!sameRun) {this.freeze(); for (const e of this.cars.values()) e.group.remove(); this.cars.clear();}
      const changed = !this.frame || state.time_s !== this.frame.time_s || !sameRun;
      if (changed || state.status !== 'running') {
        const live = new Set(state.vehicles.map(v => v.id));
        for (const [id, entry] of this.cars) if (!live.has(id)) {entry.group.remove(); this.cars.delete(id);}
        for (const raw of state.vehicles) {
          // Legacy payloads remain supported, with dimensions explicitly marked as unavailable.
          const provided = raw.length > 0 && raw.width > 0;
          const v = {...raw, length: provided ? raw.length : 4.5, width: provided ? raw.width : 1.8};
          let entry = this.cars.get(v.id);
          if (entry && (entry.latest.length !== v.length || entry.latest.width !== v.width || entry.latest.vehicle_class !== v.vehicle_class)) {
            entry.group.remove(); this.cars.delete(v.id); entry = null;
          }
          if (!entry) {entry = this.createCar(v); this.cars.set(v.id, entry);}
          const plan = motionPlan(entry.latest, v, {sameRun, time: state.time_s, previousTime: this.frame?.time_s,
            status: state.status, previousStatus: this.frame?.status, speed: state.speed,
            wallGap: now - this.lastReceived}, this.data.lanes[v.lane_id]);
          if (this.media.matches || !provided) plan.animate = false;
          entry.group.dataset.sizeSource = provided ? 'sumo' : 'legacy-generic';
          entry.group.dataset.motion = plan.animate ? 'observed-interpolation' : 'latest-sample';
          entry.group.classList.toggle('tm-stopped', v.speed < .1);
          entry.latest = v; entry.plan = {...plan, started: now};
          this.place(entry, plan.animate ? plan.from : v);
        }
        this.refreshLOD();
        if (changed) this.lastReceived = now;
      }
      const junctions = new Map(state.junctions.map(j => [j.id, j]));
      const colors = {r: '#ef5965', y: '#ffd24f', G: '#26df9f', g: '#26df9f'};
      const observedSignals = movementSignals(this.data, state.junctions);
      for (const [i, signal] of this.signalNodes.entries()) {
        const color = observedSignals[i].color;
        signal.group.dataset.signalState = color;
        signal.title.textContent = `${signal.movement.incoming} → ${signal.movement.outgoing} · 연결 ${signal.index} · ${({r:'정지',y:'황색',G:'통과',g:'양보 후 통과'})[color] || '미수신'} · ${state.time_s}초`;
        signal.bulbs.forEach((bulb, i) => bulb.setAttribute('fill', (i === 0 && color === 'r') || (i === 1 && color === 'y') || (i === 2 && /[Gg]/.test(color)) ? colors[color] : '#425760'));
      }
      for (const badge of this.badges) {
        const j = junctions.get(badge.jid), value = j?.state || '';
        badge.bulbs.forEach((bulb, i) => bulb.setAttribute('fill', [value.includes('r'), value.includes('y'), /[Gg]/.test(value)][i] ? [colors.r, colors.y, colors.G][i] : '#b6c5ca'));
        badge.text.textContent = !j ? '실행 전' : j.stage === 'all_red' ? '전체 적색' : j.stage === 'yellow' ? '황색 전환' : value.includes('r') && /[Gg]/.test(value) ? '일부 통과' : '신호 확인';
        badge.group.dataset.signalStage = j?.stage || 'ready';
      }
      this.frame = state;
      if (!this.raf && [...this.cars.values()].some(e => e.plan?.animate)) this.raf = requestAnimationFrame(t => this.tick(t));
    }
    tick(now) {
      this.raf = 0; if (this.destroyed) return;
      let active = false;
      for (const entry of this.cars.values()) {
        if (!entry.plan?.animate) continue;
        const t = clamp((now - entry.plan.started) / entry.plan.duration, 0, 1);
        this.place(entry, sampleMotion(entry.plan, t));
        if (t < 1) active = true; else entry.plan.animate = false;
      }
      if (active) this.raf = requestAnimationFrame(t => this.tick(t));
    }
    freeze() {
      if (this.raf) cancelAnimationFrame(this.raf); this.raf = 0;
      for (const entry of this.cars.values()) {entry.plan = null; this.place(entry, entry.latest);}
    }
  }
  const api = {TrafficMap, bodyCenter, vehicleDisplayMode, angleDelta, offsetPolyline, projectToLane, atDistance, motionPlan, sampleMotion, movementSignals};
  root.SignalTrafficMap = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
