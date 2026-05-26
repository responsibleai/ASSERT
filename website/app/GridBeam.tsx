"use client";

import React from "react";
import { motion } from "framer-motion";

/**
 * GridBeam — background grid with an animated gradient beam tracing the lines.
 * Adapted from https://ui.lukacho.com/components/background-grid-beam
 */
export default function GridBeam({
  className = "",
  gridColor = "rgba(255,255,255,0.08)",
  style,
}: {
  className?: string;
  gridColor?: string;
  style?: React.CSSProperties;
}) {
  const gridSvg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' width='32' height='32' fill='none' stroke='${gridColor}'><path d='M0 .5H31.5V32'/></svg>`;
  const dataUri = `url("data:image/svg+xml;utf8,${encodeURIComponent(gridSvg)}")`;

  return (
    <div
      aria-hidden="true"
      className={className}
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        overflow: "hidden",
        backgroundImage: dataUri,
        backgroundSize: "24px 24px",
        ...style,
      }}
    >
      <svg
        width="117"
        height="47"
        viewBox="0 0 156 63"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ position: "absolute", top: 0, left: 0, marginLeft: 96, marginTop: 32 }}
      >
        <path
          d="M31 .5h32M0 .5h32m30 31h32m-1 0h32m-1 31h32M62.5 32V0m62 63V31"
          stroke="url(#gridbeam-grad1)"
          strokeWidth={1.5}
        />
        <defs>
          <motion.linearGradient
            variants={{
              initial: { x1: "40%", x2: "50%", y1: "160%", y2: "180%" },
              animate: { x1: "0%", x2: "10%", y1: "-40%", y2: "-20%" },
            }}
            animate="animate"
            initial="initial"
            transition={{
              duration: 1.8,
              repeat: Infinity,
              repeatType: "loop",
              ease: "linear",
              repeatDelay: 2,
            }}
            id="gridbeam-grad1"
          >
            <stop stopColor="#AF86F5" stopOpacity="0" />
            <stop stopColor="#AF86F5" />
            <stop offset="0.325" stopColor="#7A4FE0" />
            <stop offset="1" stopColor="#643FB2" stopOpacity="0" />
          </motion.linearGradient>
        </defs>
      </svg>
    </div>
  );
}
