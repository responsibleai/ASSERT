"use client";

import { motion } from "framer-motion";
import { useEffect, useId, useState, type RefObject } from "react";

type AnimatedBeamProps = {
  containerRef: RefObject<HTMLElement>;
  fromRef: RefObject<HTMLElement>;
  toRef: RefObject<HTMLElement>;
  curvature?: number;
  reverse?: boolean;
  pathColor?: string;
  pathWidth?: number;
  pathOpacity?: number;
  gradientStartColor?: string;
  gradientStopColor?: string;
  delay?: number;
  duration?: number;
  startXOffset?: number;
  startYOffset?: number;
  endXOffset?: number;
  endYOffset?: number;
  /** Show an arrowhead at the end of the path. */
  arrow?: boolean;
  /** When set, overrides path with a custom shape relative to from/to anchors. */
  customPath?: (
    sx: number,
    sy: number,
    ex: number,
    ey: number,
    cx: number,
    cy: number,
  ) => string;
};

export function AnimatedBeam({
  containerRef,
  fromRef,
  toRef,
  curvature = 0,
  reverse = false,
  pathColor = "#c9d1d9",
  pathWidth = 2,
  pathOpacity = 0.85,
  gradientStartColor = "#58a6ff",
  gradientStopColor = "#ffffff",
  delay = 0,
  duration = 7,
  startXOffset = 0,
  startYOffset = 0,
  endXOffset = 0,
  endYOffset = 0,
  arrow = true,
  customPath,
}: AnimatedBeamProps) {
  const id = useId();
  const [pathD, setPathD] = useState("");
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const update = () => {
      const c = containerRef.current;
      const a = fromRef.current;
      const b = toRef.current;
      if (!c || !a || !b) return;
      const cr = c.getBoundingClientRect();
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      setSize({ w: cr.width, h: cr.height });
      const sx = ar.left - cr.left + ar.width / 2 + startXOffset;
      const sy = ar.top - cr.top + ar.height / 2 + startYOffset;
      const ex = br.left - cr.left + br.width / 2 + endXOffset;
      const ey = br.top - cr.top + br.height / 2 + endYOffset;
      const cx = (sx + ex) / 2;
      const cy = (sy + ey) / 2 - curvature;
      if (customPath) {
        setPathD(customPath(sx, sy, ex, ey, cx, cy));
      } else {
        setPathD(`M ${sx},${sy} Q ${cx},${cy} ${ex},${ey}`);
      }
    };
    update();
    const ro = new ResizeObserver(update);
    if (containerRef.current) ro.observe(containerRef.current);
    window.addEventListener("resize", update);
    const t = window.setTimeout(update, 50);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", update);
      window.clearTimeout(t);
    };
  }, [
    containerRef,
    fromRef,
    toRef,
    curvature,
    startXOffset,
    startYOffset,
    endXOffset,
    endYOffset,
    customPath,
  ]);

  const gradientCoords = reverse
    ? { x1: ["100%", "-25%"], x2: ["125%", "0%"] }
    : { x1: ["-25%", "100%"], x2: ["0%", "125%"] };

  const markerId = `${id}-arrow`;

  return (
    <svg
      fill="none"
      width={size.w}
      height={size.h}
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${size.w} ${size.h}`}
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        zIndex: 1,
      }}
    >
      <defs>
        <marker
          id={markerId}
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
          markerUnits="strokeWidth"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill={pathColor} fillOpacity={pathOpacity} />
        </marker>
      </defs>
      <path
        d={pathD}
        stroke={pathColor}
        strokeWidth={pathWidth}
        strokeOpacity={pathOpacity}
        strokeLinecap="round"
        fill="none"
        markerEnd={arrow ? `url(#${markerId})` : undefined}
      />
      <path
        d={pathD}
        stroke={`url(#${id})`}
        strokeWidth={pathWidth + 1}
        strokeLinecap="round"
        fill="none"
      />
      <defs>
        <motion.linearGradient
          id={id}
          gradientUnits="userSpaceOnUse"
          initial={{ x1: "0%", x2: "0%", y1: "0%", y2: "0%" }}
          animate={{
            x1: gradientCoords.x1,
            x2: gradientCoords.x2,
            y1: ["0%", "0%"],
            y2: ["0%", "0%"],
          }}
          transition={{
            delay,
            duration,
            ease: [0.16, 1, 0.3, 1],
            repeat: Infinity,
            repeatDelay: 0,
          }}
        >
          <stop stopColor={gradientStartColor} stopOpacity="0" />
          <stop offset="15%" stopColor={gradientStartColor} />
          <stop offset="50%" stopColor={gradientStopColor} />
          <stop offset="85%" stopColor={gradientStartColor} />
          <stop offset="100%" stopColor={gradientStartColor} stopOpacity="0" />
        </motion.linearGradient>
      </defs>
    </svg>
  );
}
