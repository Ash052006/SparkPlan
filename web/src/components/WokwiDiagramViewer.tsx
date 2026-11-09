/**
 * WokwiDiagramViewer — renders a visual schematic from a Wokwi diagram.json.
 *
 * Uses SVG for the circuit so it scales cleanly at any size. Components are
 * laid out using the top/left coordinates stored in the diagram file. Wires
 * are drawn as direct lines colored by role (the diagram stores the color
 * string directly). No external library is required.
 */
import { useEffect, useRef, useState } from "react";

export type WokwiPart = {
  type: string;
  id: string;
  top: number;
  left: number;
  attrs?: Record<string, string | number>;
};

// A connection is [from_pin_ref, to_pin_ref, color, segments_array]
export type WokwiConnection = [string, string, string, unknown[]];

export type WokwiDiagram = {
  version: number;
  parts: WokwiPart[];
  connections: WokwiConnection[];
};

// ---- Part appearance -------------------------------------------------------

type PartMeta = {
  label: string;
  abbr: string;
  color: string;
  width: number;
  height: number;
  icon: string;
};

const PART_META: Record<string, PartMeta> = {
  "wokwi-esp32-devkit-v1": {
    label: "ESP32 DevKit",
    abbr: "ESP32",
    color: "#1a5c2a",
    width: 110,
    height: 70,
    icon: "🔧",
  },
  "board-ssd1306": {
    label: "SSD1306 OLED",
    abbr: "OLED",
    color: "#1a2e5c",
    width: 70,
    height: 50,
    icon: "🖥",
  },
  "wokwi-dht22": {
    label: "DHT22 Sensor",
    abbr: "DHT22",
    color: "#4a2060",
    width: 60,
    height: 48,
    icon: "🌡",
  },
  "wokwi-led": {
    label: "LED",
    abbr: "LED",
    color: "#7a1a1a",
    width: 42,
    height: 42,
    icon: "💡",
  },
  "wokwi-resistor": {
    label: "Resistor",
    abbr: "RES",
    color: "#5a4010",
    width: 48,
    height: 36,
    icon: "⚡",
  },
  "wokwi-pir-motion-sensor": {
    label: "PIR Sensor",
    abbr: "PIR",
    color: "#1e4a40",
    width: 56,
    height: 48,
    icon: "👁",
  },
  "wokwi-buzzer": {
    label: "Buzzer",
    abbr: "BZR",
    color: "#3a1a50",
    width: 48,
    height: 48,
    icon: "🔔",
  },
  "wokwi-servo": {
    label: "Servo",
    abbr: "SRV",
    color: "#0a3a5c",
    width: 60,
    height: 48,
    icon: "⚙",
  },
  "wokwi-neopixel": {
    label: "NeoPixel",
    abbr: "NEO",
    color: "#3a0a5c",
    width: 48,
    height: 48,
    icon: "🌈",
  },
};

function getPartMeta(type: string): PartMeta {
  return (
    PART_META[type] ?? {
      label: type.replace(/^wokwi-|^board-/, ""),
      abbr: type.slice(0, 3).toUpperCase(),
      color: "#2a3550",
      width: 60,
      height: 48,
      icon: "📦",
    }
  );
}

// ---- Pin resolution --------------------------------------------------------

/**
 * Parse a Wokwi pin reference like "board:D13" or "oled:GND".
 * Returns { instanceId, pin }.
 */
function parsePinRef(ref: string): { instanceId: string; pin: string } {
  const colonIdx = ref.indexOf(":");
  if (colonIdx === -1) return { instanceId: ref, pin: "" };
  return {
    instanceId: ref.slice(0, colonIdx),
    pin: ref.slice(colonIdx + 1),
  };
}

/**
 * Map normalized diagram coordinates to SVG viewport coordinates.
 * The diagram uses top/left in a Wokwi-internal grid where (0,0) is the
 * board centre. We shift and scale to fit the available canvas.
 */
function computeLayout(
  parts: WokwiPart[],
  svgWidth: number,
  svgHeight: number,
): {
  scale: number;
  offsetX: number;
  offsetY: number;
  partBoxes: Map<string, { x: number; y: number; w: number; h: number; meta: PartMeta }>;
} {
  if (parts.length === 0) {
    return { scale: 1, offsetX: 0, offsetY: 0, partBoxes: new Map() };
  }

  const pad = 60;

  // Compute bounding box in diagram space
  let minX = Infinity,
    minY = Infinity,
    maxX = -Infinity,
    maxY = -Infinity;
  for (const p of parts) {
    const meta = getPartMeta(p.type);
    minX = Math.min(minX, p.left);
    minY = Math.min(minY, p.top);
    maxX = Math.max(maxX, p.left + meta.width);
    maxY = Math.max(maxY, p.top + meta.height);
  }

  const diagramW = maxX - minX || 1;
  const diagramH = maxY - minY || 1;
  const scale = Math.min((svgWidth - 2 * pad) / diagramW, (svgHeight - 2 * pad) / diagramH, 1.6);
  const offsetX = pad + ((svgWidth - 2 * pad) - diagramW * scale) / 2 - minX * scale;
  const offsetY = pad + ((svgHeight - 2 * pad) - diagramH * scale) / 2 - minY * scale;

  const partBoxes = new Map<string, { x: number; y: number; w: number; h: number; meta: PartMeta }>();
  for (const p of parts) {
    const meta = getPartMeta(p.type);
    partBoxes.set(p.id, {
      x: p.left * scale + offsetX,
      y: p.top * scale + offsetY,
      w: meta.width * scale,
      h: meta.height * scale,
      meta,
    });
  }

  return { scale, offsetX, offsetY, partBoxes };
}

// ---- Main component --------------------------------------------------------

type Props = {
  diagram: WokwiDiagram;
};

export default function WokwiDiagramViewer({ diagram }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 760, height: 440 });
  const [hovered, setHovered] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        setDims({ width: entry.contentRect.width, height: entry.contentRect.height });
      }
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  const { partBoxes } = computeLayout(diagram.parts, dims.width, dims.height);

  // Build a map of instance centre points for wire routing
  const centres = new Map<string, { cx: number; cy: number }>();
  for (const [id, box] of partBoxes) {
    centres.set(id, { cx: box.x + box.w / 2, cy: box.y + box.h / 2 });
  }

  return (
    <div ref={containerRef} className="wokwi-diagram-container" aria-label="Wokwi circuit diagram">
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${dims.width} ${dims.height}`}
        className="wokwi-diagram-svg"
        role="img"
        aria-label="Generated virtual circuit schematic"
      >
        {/* Background grid */}
        <defs>
          <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
            <path d="M 20 0 L 0 0 0 20" fill="none" stroke="currentColor" strokeWidth="0.3" opacity="0.15" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />

        {/* Wires */}
        {diagram.connections.map((conn, i) => {
          const from = parsePinRef(conn[0]);
          const to = parsePinRef(conn[1]);
          const color = conn[2] || "#aaa";
          const fromCentre = centres.get(from.instanceId);
          const toCentre = centres.get(to.instanceId);
          if (!fromCentre || !toCentre) return null;

          const wireKey = `wire-${i}`;
          const isHovered =
            hovered === from.instanceId || hovered === to.instanceId;

          // Mid-point elbow routing
          const mx = (fromCentre.cx + toCentre.cx) / 2;

          return (
            <g key={wireKey}>
              <polyline
                points={`${fromCentre.cx},${fromCentre.cy} ${mx},${fromCentre.cy} ${mx},${toCentre.cy} ${toCentre.cx},${toCentre.cy}`}
                fill="none"
                stroke={color}
                strokeWidth={isHovered ? 2.5 : 1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
                opacity={isHovered ? 1 : 0.75}
                className="wokwi-wire"
              />
              {/* Pin dots */}
              <circle cx={fromCentre.cx} cy={fromCentre.cy} r={3} fill={color} opacity={0.8} />
              <circle cx={toCentre.cx} cy={toCentre.cy} r={3} fill={color} opacity={0.8} />
              {/* Pin labels (shown on hover) */}
              {isHovered && (
                <>
                  <text
                    x={fromCentre.cx}
                    y={fromCentre.cy - 7}
                    textAnchor="middle"
                    fontSize="8"
                    fill={color}
                    opacity={0.95}
                    fontFamily="monospace"
                  >
                    {from.pin}
                  </text>
                  <text
                    x={toCentre.cx}
                    y={toCentre.cy - 7}
                    textAnchor="middle"
                    fontSize="8"
                    fill={color}
                    opacity={0.95}
                    fontFamily="monospace"
                  >
                    {to.pin}
                  </text>
                </>
              )}
            </g>
          );
        })}

        {/* Parts */}
        {diagram.parts.map((part) => {
          const box = partBoxes.get(part.id);
          if (!box) return null;
          const isHov = hovered === part.id;
          const r = 8;

          return (
            <g
              key={part.id}
              className="wokwi-part"
              onMouseEnter={() => setHovered(part.id)}
              onMouseLeave={() => setHovered(null)}
              style={{ cursor: "default" }}
            >
              {/* Shadow */}
              <rect
                x={box.x + 2}
                y={box.y + 3}
                width={box.w}
                height={box.h}
                rx={r}
                fill="rgba(0,0,0,0.25)"
              />
              {/* Body */}
              <rect
                x={box.x}
                y={box.y}
                width={box.w}
                height={box.h}
                rx={r}
                fill={box.meta.color}
                stroke={isHov ? "#fff" : "rgba(255,255,255,0.2)"}
                strokeWidth={isHov ? 1.5 : 1}
              />
              {/* Icon */}
              <text
                x={box.x + box.w / 2}
                y={box.y + box.h / 2 - 8}
                textAnchor="middle"
                fontSize={Math.min(box.h * 0.3, 20)}
                dominantBaseline="middle"
              >
                {box.meta.icon}
              </text>
              {/* Abbreviation */}
              <text
                x={box.x + box.w / 2}
                y={box.y + box.h / 2 + 8}
                textAnchor="middle"
                fontSize={Math.min(box.w * 0.14, 11)}
                fontWeight="700"
                fill="#fff"
                fontFamily="monospace"
                dominantBaseline="middle"
              >
                {box.meta.abbr}
              </text>
              {/* Instance ID */}
              <text
                x={box.x + box.w / 2}
                y={box.y + box.h + 12}
                textAnchor="middle"
                fontSize="9"
                fill="var(--text-muted)"
                fontFamily="monospace"
              >
                {part.id}
              </text>
              {/* Tooltip on hover */}
              {isHov && (
                <text
                  x={box.x + box.w / 2}
                  y={box.y - 7}
                  textAnchor="middle"
                  fontSize="9"
                  fill="var(--text)"
                  fontWeight="600"
                >
                  {box.meta.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
