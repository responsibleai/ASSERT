"use client";

import Image from "next/image";
import { useRef } from "react";
import { AnimatedBeam } from "./AnimatedBeam";

const BASE_PATH = "/ASSERT";

const NODE_RADIUS = 28;
const CORNER_R = 12;
const ARROW_GAP = 12;

function NodeCircle({
  innerRef,
  icon,
  label,
  tooltip,
  highlight = false,
}: {
  innerRef: React.RefObject<HTMLDivElement>;
  icon: string;
  label: string;
  tooltip: string;
  highlight?: boolean;
}) {
  return (
    <div className="systematizer-node">
      <div
        ref={innerRef}
        className={`systematizer-circle${highlight ? " is-highlight" : ""}`}
        tabIndex={0}
      >
        <Image src={icon} alt="" width={28} height={28} />
        <span className="systematizer-tooltip" role="tooltip">
          {tooltip}
        </span>
      </div>
      <div className={`systematizer-label${highlight ? " is-highlight" : ""}`}>
        {label.split("\n").map((line, i) => (
          <span key={i}>{line}</span>
        ))}
      </div>
    </div>
  );
}

function rectRoute(sx: number, sy: number, ex: number, ey: number, rise: number) {
  const r = CORNER_R;
  const goUp = rise > 0;
  const goRight = ex >= sx;
  const v1 = goUp ? -Math.abs(rise) : Math.abs(rise);
  const v2 = goUp ? Math.abs(rise) : -Math.abs(rise);
  const corner1Sweep = goUp ? (goRight ? 1 : 0) : goRight ? 0 : 1;
  const corner2Sweep = corner1Sweep;
  const yTop = sy + v1;
  const horizDir = goRight ? 1 : -1;
  const vSign = Math.sign(v1) || -1;
  const c1x = sx + r * horizDir;
  const c1y = yTop;
  const c2x = ex - r * horizDir;
  const c2y = yTop;
  return [
    `M ${sx},${sy}`,
    `L ${sx},${yTop - r * vSign}`,
    `A ${r} ${r} 0 0 ${corner1Sweep} ${c1x},${c1y}`,
    `L ${c2x},${c2y}`,
    `A ${r} ${r} 0 0 ${corner2Sweep} ${ex},${yTop + r * Math.sign(v2)}`,
    `L ${ex},${ey}`,
  ].join(" ");
}

export default function SystematizerDiagram() {
  const containerRef = useRef<HTMLDivElement>(null!);
  const riskRef = useRef<HTMLDivElement>(null!);
  const deepRef = useRef<HTMLDivElement>(null!);
  const expertsRef = useRef<HTMLDivElement>(null!);
  const synthRef = useRef<HTMLDivElement>(null!);
  const validatorRef = useRef<HTMLDivElement>(null!);
  const specRef = useRef<HTMLDivElement>(null!);

  const HX_OUT = NODE_RADIUS + ARROW_GAP;

  return (
    <div className="systematizer-diagram" ref={containerRef}>
      <div className="systematizer-process-title" aria-hidden="true">
        Systemization Process
      </div>

      <div className="systematizer-row">
        <NodeCircle
          innerRef={riskRef}
          icon={`${BASE_PATH}/icons/doc.svg`}
          label={"Risk Name &\nDefinition"}
          tooltip="Defines the risk and its meaning as the starting point."
        />
        <NodeCircle
          innerRef={deepRef}
          icon={`${BASE_PATH}/icons/deep_research.svg`}
          label={"Deep Research\nAgent"}
          tooltip="Gathers relevant knowledge and examples to build understanding."
        />
        <NodeCircle
          innerRef={expertsRef}
          icon={`${BASE_PATH}/icons/experts.svg`}
          label={"Expert Discussion\nSimulator"}
          tooltip="Surfaces expert perspectives, nuance, and edge cases."
        />
        <NodeCircle
          innerRef={synthRef}
          icon={`${BASE_PATH}/icons/synthesizer.svg`}
          label={"Synthesizer"}
          tooltip="Synthesizes inputs into a structured, operational concept."
        />
        <NodeCircle
          innerRef={validatorRef}
          icon={`${BASE_PATH}/icons/validator.svg`}
          label={"Validator"}
          tooltip="Evaluates clarity, completeness, and readiness for use."
        />
        <NodeCircle
          innerRef={specRef}
          icon={`${BASE_PATH}/icons/doc.svg`}
          label={"Content Spec"}
          tooltip="A structured, validated representation of the concept for evaluation or policy."
        />
      </div>

      <div className="systematizer-feedback-label" aria-hidden="true">
        Feedback
      </div>

      <AnimatedBeam
        containerRef={containerRef}
        fromRef={riskRef}
        toRef={deepRef}
        duration={9}
        startXOffset={HX_OUT}
        endXOffset={-HX_OUT}
      />
      <AnimatedBeam
        containerRef={containerRef}
        fromRef={deepRef}
        toRef={expertsRef}
        duration={9}
        delay={1.2}
        startXOffset={HX_OUT}
        endXOffset={-HX_OUT}
      />
      <AnimatedBeam
        containerRef={containerRef}
        fromRef={expertsRef}
        toRef={synthRef}
        duration={9}
        delay={2.4}
        startXOffset={HX_OUT}
        endXOffset={-HX_OUT}
      />

      <AnimatedBeam
        containerRef={containerRef}
        fromRef={synthRef}
        toRef={validatorRef}
        duration={9}
        delay={3.6}
        startXOffset={HX_OUT}
        endXOffset={-HX_OUT}
        startYOffset={-8}
        endYOffset={-8}
      />
      <AnimatedBeam
        containerRef={containerRef}
        fromRef={validatorRef}
        toRef={synthRef}
        duration={9}
        delay={4.8}
        reverse
        startXOffset={-HX_OUT}
        endXOffset={HX_OUT}
        startYOffset={8}
        endYOffset={8}
      />

      <AnimatedBeam
        containerRef={containerRef}
        fromRef={validatorRef}
        toRef={specRef}
        duration={9}
        delay={6}
        startXOffset={HX_OUT}
        endXOffset={-HX_OUT}
      />

      <AnimatedBeam
        containerRef={containerRef}
        fromRef={deepRef}
        toRef={synthRef}
        duration={12}
        delay={0.4}
        startYOffset={-(NODE_RADIUS)}
        endYOffset={-(NODE_RADIUS + ARROW_GAP)}
        pathOpacity={0.7}
        customPath={(sx, sy, ex, ey) => rectRoute(sx, sy, ex, ey, 95)}
      />
      <AnimatedBeam
        containerRef={containerRef}
        fromRef={synthRef}
        toRef={deepRef}
        duration={12}
        delay={6}
        reverse
        startYOffset={NODE_RADIUS}
        endYOffset={NODE_RADIUS + ARROW_GAP}
        pathOpacity={0.7}
        customPath={(sx, sy, ex, ey) => rectRoute(sx, sy, ex, ey, -120)}
      />
    </div>
  );
}
