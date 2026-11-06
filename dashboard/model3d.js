// Draws the wireframe cylinder and sensors on a 2D canvas with a small
// hand-written 3D projection, so the page has no libraries to download and
// works offline. The frame is see-through, so both sides' sensors show at once.
//
// World axes: x right, y up, z along the cylinder.

const RING_SEGMENTS = 96;
const LENGTHWISE_LINES = 24;
const RING_SECTIONS = 8;
const CAMERA_DISTANCE = 16;
const DRAG_THRESHOLD_PX = 4;

export const VIEW_PRESETS = {
  angled: { yaw: -1.05, pitch: 0.12 },
  sideA: { yaw: Math.PI / 2, pitch: 0.12 },
  sideB: { yaw: -Math.PI / 2, pitch: 0.12 },
};

function normalize([x, y, z]) {
  const length = Math.hypot(x, y, z);
  return [x / length, y / length, z / length];
}

export class CylinderView {
  constructor(canvas, { sensors, sides, cylinder, headDiameterIn, onSelect, onHover }) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.sensors = sensors;
    this.sides = sides;
    this.radius = cylinder.diameterIn / 2;
    this.length = cylinder.lengthIn;
    this.headRadius = headDiameterIn / 2;
    this.onSelect = onSelect;
    this.onHover = onHover;
    this.onChange = () => {};
    this.yaw = VIEW_PRESETS.angled.yaw;
    this.pitch = VIEW_PRESETS.angled.pitch;
    this.width = 0;
    this.height = 0;
    this.visible = [];
    this.bindPointer();
    this.bindKeys();
  }

  setView({ yaw, pitch }) {
    this.yaw = yaw;
    this.pitch = pitch;
    this.onChange();
  }

  resize(width, height) {
    const dpr = window.devicePixelRatio || 1;
    this.width = width;
    this.height = height;
    this.canvas.width = Math.round(width * dpr);
    this.canvas.height = Math.round(height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.scale = (Math.min(width, height) * 0.46) / Math.hypot(this.radius, this.length / 2);
  }

  // world direction -> view coordinates (camera looks down -z from +z)
  rotate([x, y, z]) {
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const x1 = x * cy + z * sy;
    const z1 = -x * sy + z * cy;
    return [x1, y * cp - z1 * sp, y * sp + z1 * cp];
  }

  project(viewPoint) {
    const [x, y, z] = viewPoint;
    const s = (this.scale * CAMERA_DISTANCE) / (CAMERA_DISTANCE - z);
    return [this.width / 2 + x * s, this.height / 2 - y * s];
  }

  facing(viewPoint, viewNormal) {
    const toCamera = normalize([-viewPoint[0], -viewPoint[1], CAMERA_DISTANCE - viewPoint[2]]);
    return toCamera[0] * viewNormal[0] + toCamera[1] * viewNormal[1] + toCamera[2] * viewNormal[2];
  }

  surfacePoint(angle, z) {
    return [this.radius * Math.cos(angle), this.radius * Math.sin(angle), z];
  }

  screenPoint(angle, z) {
    return this.project(this.rotate(this.surfacePoint(angle, z)));
  }

  isNearSide(angle) {
    const point = this.rotate(this.surfacePoint(angle, 0));
    return this.facing(point, this.rotate([Math.cos(angle), Math.sin(angle), 0])) > 0;
  }

  // Far-side lines are fainter, which is what makes a wireframe read as 3D.
  addRing(z, nearPath, farPath) {
    const step = (2 * Math.PI) / RING_SEGMENTS;
    for (let i = 0; i < RING_SEGMENTS; i++) {
      const a0 = i * step, a1 = a0 + step;
      const path = this.isNearSide(a0 + step / 2) ? nearPath : farPath;
      path.moveTo(...this.screenPoint(a0, z));
      path.lineTo(...this.screenPoint(a1, z));
    }
  }

  drawWireframe(tokens) {
    const { ctx } = this;
    const half = this.length / 2;
    const near = new Path2D();
    const far = new Path2D();

    for (let i = 0; i < LENGTHWISE_LINES; i++) {
      const angle = (i / LENGTHWISE_LINES) * 2 * Math.PI;
      const path = this.isNearSide(angle) ? near : far;
      path.moveTo(...this.screenPoint(angle, -half));
      path.lineTo(...this.screenPoint(angle, half));
    }
    for (let i = 0; i <= RING_SECTIONS; i++) {
      if (i !== RING_SECTIONS / 2) this.addRing(-half + (i / RING_SECTIONS) * this.length, near, far);
    }

    ctx.lineWidth = 1;
    ctx.strokeStyle = tokens.wireFar;
    ctx.stroke(far);
    ctx.strokeStyle = tokens.wire;
    ctx.stroke(near);

    // emphasized: the ring the sensors sit on, and the horizontal central axis
    const guideNear = new Path2D();
    const guideFar = new Path2D();
    this.addRing(0, guideNear, guideFar);
    for (const z of [half, -half]) {
      guideNear.moveTo(...this.project(this.rotate([-this.radius, 0, z])));
      guideNear.lineTo(...this.project(this.rotate([this.radius, 0, z])));
    }
    ctx.strokeStyle = tokens.wire;
    ctx.stroke(guideFar);
    ctx.strokeStyle = tokens.guide;
    ctx.lineWidth = 1.5;
    ctx.stroke(guideNear);
  }

  drawSensor(sensor, style, tokens) {
    const { ctx } = this;
    const center = this.rotate(this.surfacePoint(sensor.angle, 0));
    const near = this.isNearSide(sensor.angle);

    // the head is flat on the surface, so it foreshortens as it turns away
    const outline = [];
    for (let i = 0; i < 28; i++) {
      const phi = (i / 28) * 2 * Math.PI;
      const arc = this.headRadius * Math.cos(phi);
      const z = this.headRadius * Math.sin(phi);
      outline.push(this.screenPoint(sensor.angle + arc / this.radius, z));
    }
    const [cx, cy] = this.project(center);

    ctx.beginPath();
    outline.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.fillStyle = style.fill;
    ctx.fill();
    ctx.strokeStyle = style.selectedColor || (near ? tokens.ring : tokens.wire);
    ctx.lineWidth = style.selectedColor ? 3 : near ? 2 : 1;
    ctx.stroke();

    const width = Math.max(...outline.map(([x]) => x)) - Math.min(...outline.map(([x]) => x));
    const height = Math.max(...outline.map(([, y]) => y)) - Math.min(...outline.map(([, y]) => y));
    if (Math.min(width, height) >= 17) {
      ctx.fillStyle = style.ink;
      ctx.font = "600 10px system-ui, -apple-system, 'Segoe UI', sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(`S${sensor.channel}`, cx, cy + 0.5);
    }

    this.visible.push({ sensor, x: cx, y: cy, depth: center[2], radius: Math.max(14, width / 2) });
  }

  drawSideLabels(tokens) {
    const { ctx } = this;
    ctx.font = "500 13px system-ui, -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    for (const side of this.sides) {
      const angle = (side.angleDeg * Math.PI) / 180;
      const [x, y] = this.screenPoint(angle, -this.length / 2 + 0.5);
      // a halo in the background color keeps the text clear of the wires behind it
      ctx.strokeStyle = tokens.surface;
      ctx.lineWidth = 4;
      ctx.strokeText(side.name, x, y);
      ctx.fillStyle = this.isNearSide(angle) ? tokens.label : tokens.muted;
      ctx.fillText(side.name, x, y);
    }
  }

  // styleFor(sensor) -> { fill, ink, selectedColor }
  render(styleFor, tokens) {
    if (!this.width) return;
    this.ctx.clearRect(0, 0, this.width, this.height);
    this.visible = [];
    this.drawWireframe(tokens);

    // far side first, so the near sensors sit on top where they line up
    const byDepth = [...this.sensors].sort((a, b) => (
      this.rotate(this.surfacePoint(a.angle, 0))[2] - this.rotate(this.surfacePoint(b.angle, 0))[2]
    ));
    for (const sensor of byDepth) this.drawSensor(sensor, styleFor(sensor), tokens);
    this.drawSideLabels(tokens);
  }

  sensorAt(x, y) {
    let best = null;
    for (const item of this.visible) {
      const hit = Math.hypot(item.x - x, item.y - y) <= item.radius;
      if (hit && (!best || item.depth > best.depth)) best = item; // the one drawn on top wins
    }
    return best ? best.sensor : null;
  }

  bindPointer() {
    let drag = null;
    const position = (event) => {
      const rect = this.canvas.getBoundingClientRect();
      return [event.clientX - rect.left, event.clientY - rect.top];
    };

    this.canvas.addEventListener("pointerdown", (event) => {
      drag = { x: event.clientX, y: event.clientY, moved: false };
      this.canvas.setPointerCapture(event.pointerId);
    });

    this.canvas.addEventListener("pointermove", (event) => {
      if (drag) {
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        if (drag.moved || Math.hypot(dx, dy) > DRAG_THRESHOLD_PX) {
          drag.moved = true;
          drag.x = event.clientX;
          drag.y = event.clientY;
          this.yaw += dx * 0.008;
          this.pitch = Math.max(-1.3, Math.min(1.3, this.pitch + dy * 0.008));
          this.onHover(null, event);
          this.onChange();
        }
        return;
      }
      const [x, y] = position(event);
      const sensor = this.sensorAt(x, y);
      this.canvas.style.cursor = sensor ? "pointer" : "grab";
      this.onHover(sensor, event);
    });

    this.canvas.addEventListener("pointerup", (event) => {
      if (drag && !drag.moved) {
        const sensor = this.sensorAt(...position(event));
        if (sensor) this.onSelect(sensor);
      }
      drag = null;
    });

    this.canvas.addEventListener("pointercancel", () => { drag = null; });
    this.canvas.addEventListener("pointerleave", (event) => this.onHover(null, event));
  }

  bindKeys() {
    this.canvas.addEventListener("keydown", (event) => {
      const moves = {
        ArrowLeft: [-0.1, 0], ArrowRight: [0.1, 0], ArrowUp: [0, -0.1], ArrowDown: [0, 0.1],
      };
      const move = moves[event.key];
      if (!move) return;
      event.preventDefault();
      this.yaw += move[0];
      this.pitch = Math.max(-1.3, Math.min(1.3, this.pitch + move[1]));
      this.onChange();
    });
  }
}
