import { useCallback, useEffect, useRef } from "react";
import type { DagBounds } from "./dagLayout";

export type ViewportTransform = { x: number; y: number; scale: number };

const MIN_SCALE = 0.05;
const MAX_SCALE = 4;
const WHEEL_ZOOM_INTENSITY = 0.0012;

function clampScale(scale: number) {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

export function useDagViewport() {
  const containerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const transformRef = useRef<ViewportTransform>({ x: 32, y: 32, scale: 1 });
  const panningRef = useRef(false);
  const panStartRef = useRef({ pointerX: 0, pointerY: 0, x: 0, y: 0 });
  const animRef = useRef<number | null>(null);

  const applyTransform = useCallback((transform: ViewportTransform) => {
    transformRef.current = transform;
    const el = contentRef.current;
    if (!el) return;
    el.style.transform = `translate3d(${transform.x}px, ${transform.y}px, 0) scale(${transform.scale})`;
  }, []);

  const cancelAnimation = useCallback(() => {
    if (animRef.current !== null) {
      cancelAnimationFrame(animRef.current);
      animRef.current = null;
    }
  }, []);

  const animateTo = useCallback(
    (target: ViewportTransform, durationMs = 280) => {
      cancelAnimation();
      const start = { ...transformRef.current };
      const startTime = performance.now();

      const step = (now: number) => {
        const t = Math.min(1, (now - startTime) / durationMs);
        const eased = 1 - (1 - t) ** 3;
        applyTransform({
          x: lerp(start.x, target.x, eased),
          y: lerp(start.y, target.y, eased),
          scale: lerp(start.scale, target.scale, eased)
        });
        if (t < 1) {
          animRef.current = requestAnimationFrame(step);
        } else {
          animRef.current = null;
        }
      };

      animRef.current = requestAnimationFrame(step);
    },
    [applyTransform, cancelAnimation]
  );

  const zoomToBounds = useCallback(
    (bounds: DagBounds, padding = 48, animate = true) => {
      const container = containerRef.current;
      if (!container) return;

      const cw = container.clientWidth;
      const ch = container.clientHeight;
      const bw = Math.max(bounds.width + NODE_PAD * 2, 120);
      const bh = Math.max(bounds.height + NODE_PAD * 2, 80);
      const scale = clampScale(Math.min(cw / bw, ch / bh) * 0.92);
      const x = (cw - bw * scale) / 2 - bounds.minX * scale;
      const y = (ch - bh * scale) / 2 - bounds.minY * scale;
      const target = { x, y, scale };
      if (animate) animateTo(target);
      else applyTransform(target);
    },
    [animateTo, applyTransform]
  );

  const fitAll = useCallback(
    (bounds: DagBounds) => zoomToBounds(bounds, 48, true),
    [zoomToBounds]
  );

  const resetView = useCallback(() => {
    animateTo({ x: 32, y: 32, scale: 1 });
  }, [animateTo]);

  useEffect(() => {
    applyTransform(transformRef.current);
    return cancelAnimation;
  }, [applyTransform, cancelAnimation]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = container.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      const current = transformRef.current;
      const delta = -event.deltaY * WHEEL_ZOOM_INTENSITY;
      const nextScale = clampScale(current.scale * Math.exp(delta));
      const scaleRatio = nextScale / current.scale;
      const nx = px - (px - current.x) * scaleRatio;
      const ny = py - (py - current.y) * scaleRatio;
      applyTransform({ x: nx, y: ny, scale: nextScale });
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      panningRef.current = true;
      panStartRef.current = {
        pointerX: event.clientX,
        pointerY: event.clientY,
        x: transformRef.current.x,
        y: transformRef.current.y
      };
      container.setPointerCapture(event.pointerId);
      cancelAnimation();
    };

    const onPointerMove = (event: PointerEvent) => {
      if (!panningRef.current) return;
      const start = panStartRef.current;
      const dx = event.clientX - start.pointerX;
      const dy = event.clientY - start.pointerY;
      applyTransform({
        ...transformRef.current,
        x: start.x + dx,
        y: start.y + dy
      });
    };

    const onPointerUp = (event: PointerEvent) => {
      if (!panningRef.current) return;
      panningRef.current = false;
      container.releasePointerCapture(event.pointerId);
    };

    container.addEventListener("wheel", onWheel, { passive: false });
    container.addEventListener("pointerdown", onPointerDown);
    container.addEventListener("pointermove", onPointerMove);
    container.addEventListener("pointerup", onPointerUp);
    container.addEventListener("pointercancel", onPointerUp);

    return () => {
      container.removeEventListener("wheel", onWheel);
      container.removeEventListener("pointerdown", onPointerDown);
      container.removeEventListener("pointermove", onPointerMove);
      container.removeEventListener("pointerup", onPointerUp);
      container.removeEventListener("pointercancel", onPointerUp);
    };
  }, [applyTransform, cancelAnimation]);

  return {
    containerRef,
    contentRef,
    transformRef,
    fitAll,
    resetView,
    zoomToBounds,
    animateTo
  };
}

const NODE_PAD = 40;
