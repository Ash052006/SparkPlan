import { SupportedSpec } from "../api";

export interface SuggestionsProps {
  spec: SupportedSpec | null;
  onApplySuggestion: (suggestion: string) => void;
}

export default function OptimizationSuggestions({
  spec,
  onApplySuggestion,
}: SuggestionsProps) {
  const parts = spec?.parts ?? [];
  const hasDht = parts.some((p: { part_id: string }) => p.part_id.includes("dht"));
  const hasOled = parts.some((p: { part_id: string }) => p.part_id.includes("oled") || p.part_id.includes("display"));
  const hasBuzzer = parts.some((p: { part_id: string }) => p.part_id.includes("buzzer"));

  const suggestions: { title: string; prompt: string; tag: "cost" | "power" | "feature" }[] = [];

  if (hasDht) {
    suggestions.push({
      title: "Cost cutting: Switch to DHT11 sensor",
      prompt: "Change DHT22 to DHT11 to cut bill-of-materials cost",
      tag: "cost",
    });
  }

  if (!hasBuzzer) {
    suggestions.push({
      title: "Add a passive buzzer that beeps when temp > 30°C",
      prompt: "Add a passive buzzer that beeps when temp > 30°C",
      tag: "feature",
    });
  }

  suggestions.push({
    title: "Optimize power: Enable 60s deep sleep duty cycle",
    prompt: "Optimize firmware with 60-second deep sleep to extend battery life",
    tag: "power",
  });

  if (!hasOled) {
    suggestions.push({
      title: "Add SSD1306 0.96\" I2C OLED display for readings",
      prompt: "Add SSD1306 I2C OLED display to show live sensor metrics",
      tag: "feature",
    });
  }

  return (
    <section className="panel suggestions-panel" aria-labelledby="iterative-opt-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Hardware engineering advice</p>
          <h2 id="iterative-opt-heading">Iterative Hardware Modification</h2>
        </div>
        <span className="stage-tag ai-tag">AI Optimizer & Cost Cutting</span>
      </div>

      <div className="suggestions-grid">
        <div className="suggestion-card cost-card">
          <div className="suggestion-badge cost-badge">💰 Cost Reduction Tip</div>
          <p className="suggestion-text">
            {hasDht
              ? "For consumer deployments where ±2°C precision suffices, switching DHT22 ($4.50) to DHT11 ($1.20) saves ~70% on sensor BOM. Standardize SMD 0805 passives for JLCPCB basic part discounts."
              : "Consolidate sensors onto the shared I2C bus (SDA: GPIO 21, SCL: GPIO 22) to minimize required pin headers and keep PCB layer routing to a standard 2-layer low-cost stackup."}
          </p>
        </div>

        <div className="suggestion-card reliability-card">
          <div className="suggestion-badge reliability-badge">⚡ Reliability & Power Tip</div>
          <p className="suggestion-text">
            Add a 100nF ceramic decoupling capacitor across 3.3V and GND close to sensor power pins.
            Duty-cycling sensor polling with ESP32 deep sleep reduces idle draw to 15µA, extending 1200mAh LiPo runtimes from hours to months.
          </p>
        </div>
      </div>

      <div className="suggestion-pills-wrap">
        <span className="suggestion-pills-label">💡 1-Click Modifications:</span>
        <div className="suggestion-pills">
          {suggestions.map((s, idx) => (
            <button
              key={idx}
              type="button"
              className={`suggestion-pill pill-${s.tag}`}
              onClick={() => onApplySuggestion(s.prompt)}
              title={`Click to apply: ${s.prompt}`}
            >
              <span className="pill-icon">
                {s.tag === "cost" ? "🏷️" : s.tag === "power" ? "🔋" : "⚡"}
              </span>
              <span>{s.title}</span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
