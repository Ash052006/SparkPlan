import { Catalog, Issue, RunResult, SpecPart, SupportedSpec, Trace } from "../src/api";

const warning: Issue = {
  code: "MOUNT_CLEARANCE",
  severity: "warning",
  message: "The display is close to the enclosure wall.",
  part_ids: ["ssd1306_oled_096"],
  fix_hint: "Check the lid clearance before printing.",
};

const warningRunIssue: Issue = {
  ...warning,
};

const slicerIssue: Issue = {
  code: "SLICER_UNAVAILABLE",
  severity: "error",
  message: "PrusaSlicer is not installed or is not executable.",
  part_ids: [],
  fix_hint: "Install prusa-slicer or set SPARKPLAN_SLICER to its executable.",
};

const arduinoIssue: Issue = {
  code: "ARDUINO_CLI_MISSING",
  severity: "error",
  message: "arduino-cli not found; install it or set SPARKPLAN_ARDUINO_CLI.",
  part_ids: [],
  fix_hint: "Install arduino-cli and add it to PATH.",
};

function stage(name: string, status: "passed" | "failed" | "skipped" | "unverified") {
  return {
    name,
    status,
    started_at: "2026-10-22T12:00:00Z",
    finished_at: "2026-10-22T12:00:01Z",
    duration_ms: 1000,
    issues: [] as Issue[],
    reason: status === "skipped" ? "Not attempted in this test." : null,
    evidence: {},
    artifacts: {},
  };
}

export const RUN: RunResult = {
  schema_version: "1.0",
  run_id: "run_demo",
  status: "ok",
  stages: [
    stage("plan", "passed"),
    {
      ...stage("validation", "passed"),
      issues: [warning],
    },
    stage("geometry", "passed"),
    {
      ...stage("slicing", "unverified"),
      issues: [slicerIssue],
      reason: "PrusaSlicer is not installed or is not executable.",
    },
    stage("firmware_generation", "passed"),
    {
      ...stage("firmware_compile", "unverified"),
      issues: [arduinoIssue],
      reason: "arduino-cli not found; install it or set SPARKPLAN_ARDUINO_CLI.",
    },
    stage("guide", "passed"),
  ],
  issues: [warningRunIssue],
  artifacts: {
    spec: "spec.json",
    report: "report.json",
    trace: "trace.json",
    base_stl: "base.stl",
    lid_stl: "lid.stl",
    assembled_stl: "assembled.stl",
    sketch: "sketch/sketch.ino",
    guide: "GUIDE.md",
  },
  refusal: null,
  created_at: "2026-10-22T12:00:00Z",
  updated_at: "2026-10-22T12:00:01Z",
  finished_at: "2026-10-22T12:00:01Z",
};

const unsupportedIssue: Issue = {
  code: "UNSUPPORTED_BUILD",
  severity: "error",
  message: "SparkPlan has no flight-control hardware.",
  part_ids: [],
  fix_hint: "Choose a build that uses the catalog.",
};

export const REFUSAL_RUN: RunResult = {
  schema_version: "1.0",
  run_id: "run_refused",
  status: "unsupported",
  stages: [
    {
      ...stage("plan", "failed"),
      issues: [unsupportedIssue],
      reason: "SparkPlan has no flight-control hardware.",
    },
    ...["validation", "geometry", "slicing", "firmware_generation", "firmware_compile", "guide"].map(
      (name) => ({
        ...stage(name, "skipped"),
        reason: "Not reached because the planner refused the idea.",
      }),
    ),
  ],
  issues: [unsupportedIssue],
  artifacts: {},
  refusal: {
    reasons: [
      "A drone needs a flight controller, brushless motors and an IMU for stabilization.",
      "SparkPlan has no flight-control hardware.",
    ],
    missing_capabilities: ["flight_control", "brushless_motor_drive", "imu_stabilization"],
    closest_supported_alternative: "A ground rover using DC motors and a distance sensor.",
  },
  created_at: "2026-10-22T12:00:00Z",
  updated_at: "2026-10-22T12:00:01Z",
  finished_at: "2026-10-22T12:00:01Z",
};

const parts: SpecPart[] = [
  { instance_id: "board", part_id: "esp32_devkit_30pin", quantity: 1, label: "ESP32 DevKit" },
  { instance_id: "env", part_id: "dht22", quantity: 1, label: "DHT22 sensor" },
  { instance_id: "screen", part_id: "ssd1306_oled_096", quantity: 1, label: "OLED display" },
];

export const SPEC: SupportedSpec = {
  status: "ok",
  title: "Desk weather station",
  summary: "A DHT22 weather sensor with an OLED display.",
  parts,
  connections: [
    {
      from_instance: "env",
      from_pin: "VCC",
      to_instance: "board",
      to_pin: "3V3",
      signal_role: "rail",
      rail_name: "3V3",
      note: "",
    },
    {
      from_instance: "env",
      from_pin: "DATA",
      to_instance: "board",
      to_pin: "D4",
      signal_role: "signal",
      rail_name: null,
      note: "",
    },
    {
      from_instance: "screen",
      from_pin: "SDA",
      to_instance: "board",
      to_pin: "D21",
      signal_role: "sda",
      rail_name: null,
      note: "",
    },
  ],
  layout: [
    { part_id: "dht22", mount: "wall", note: "" },
    { part_id: "ssd1306_oled_096", mount: "lid", note: "" },
  ],
  behaviors: [{ name: "show_weather", description: "Show temperature and humidity." }],
};

export const CATALOG: Catalog = {
  board: {
    id: "esp32_devkit_30pin",
    name: "ESP32 DevKit 30-pin",
    rails: [
      {
        name: "3V3",
        voltage_v: 3.3,
        max_current_ma: 500,
        source_note: "Approximate catalog budget.",
        approximate: true,
        verify_against_datasheet: true,
      },
    ],
  },
  parts: [
    {
      id: "esp32_devkit_30pin",
      name: "ESP32 DevKit 30-pin",
      category: "board",
      interface: "none",
      mount: "base",
      supply_voltage_v: { min: 4.5, max: 5.5 },
      typical_current_ma: 80,
      peak_current_ma: 240,
      pins: ["3V3", "GND", "D4", "D21", "D22"],
    },
    {
      id: "dht22",
      name: "DHT22 temperature and humidity sensor",
      category: "sensor",
      interface: "digital",
      mount: "wall",
      supply_voltage_v: { min: 3.3, max: 6 },
      typical_current_ma: 1.5,
      peak_current_ma: 2.5,
      pins: ["VCC", "DATA", "GND"],
    },
    {
      id: "ssd1306_oled_096",
      name: "SSD1306 0.96 inch OLED",
      category: "display",
      interface: "i2c",
      mount: "lid",
      supply_voltage_v: { min: 3.3, max: 5 },
      typical_current_ma: 20,
      peak_current_ma: 30,
      pins: ["VCC", "GND", "SDA", "SCL"],
    },
  ],
  capabilities: ["temperature", "humidity", "display"],
};

export const TRACE: Trace = {
  schema_version: "1.0",
  run_id: "run_demo",
  status: "ok",
  idea: "A desk weather station",
  verification: {
    validated: true,
    compiled: "skipped",
    sliced: "skipped",
    simulated: "skipped",
  },
  validation_report: {
    passed: true,
    issues: [warning],
    timings_ms: { V1_membership: 1.2, V2_pins: 0.8 },
  },
  issues: [warning],
  stages: RUN.stages,
};

export const GUIDE = `# Desk weather station

## 1. Print the enclosure

Print the base and lid in PETG.

| Part | Quantity |
| --- | --- |
| Base | 1 |
| Lid | 1 |

## 2. Wire the DHT22

Connect DATA to D4 and power to 3V3.

Not physically built.
`;

export const SKETCH = `#include <DHT.h>

void setup() {
  Serial.begin(115200);
}

void loop() {
  delay(2000);
}
`;

function facet(a: number[], b: number[], c: number[]): string {
  return `facet normal 0 0 1
  outer loop
    vertex ${a.join(" ")}
    vertex ${b.join(" ")}
    vertex ${c.join(" ")}
  endloop
endfacet
`;
}

function boxStl(width: number, depth: number, height: number): string {
  const x0 = 0;
  const x1 = width;
  const y0 = 0;
  const y1 = depth;
  const z0 = 0;
  const z1 = height;
  const points = [
    [x0, y0, z0],
    [x1, y0, z0],
    [x1, y1, z0],
    [x0, y1, z0],
    [x0, y0, z1],
    [x1, y0, z1],
    [x1, y1, z1],
    [x0, y1, z1],
  ];
  const faces = [
    [0, 2, 1],
    [0, 3, 2],
    [4, 5, 6],
    [4, 6, 7],
    [0, 1, 5],
    [0, 5, 4],
    [1, 2, 6],
    [1, 6, 5],
    [2, 3, 7],
    [2, 7, 6],
    [3, 0, 4],
    [3, 4, 7],
  ];
  return `solid sparkplan\n${faces.map(([a, b, c]) => facet(points[a], points[b], points[c])).join("")}endsolid sparkplan\n`;
}

export const ARTIFACTS: Record<string, { body: string; contentType: string }> = {
  "spec.json": { body: JSON.stringify(SPEC), contentType: "application/json" },
  "report.json": {
    body: JSON.stringify({ run_id: "run_demo", status: "ok" }),
    contentType: "application/json",
  },
  "trace.json": { body: JSON.stringify(TRACE), contentType: "application/json" },
  "GUIDE.md": { body: GUIDE, contentType: "text/markdown; charset=utf-8" },
  "sketch.ino": { body: SKETCH, contentType: "text/plain; charset=utf-8" },
  "base.stl": { body: boxStl(60, 40, 10), contentType: "model/stl" },
  "lid.stl": { body: boxStl(60, 40, 5), contentType: "model/stl" },
  "assembled.stl": { body: boxStl(60, 40, 15), contentType: "model/stl" },
};
