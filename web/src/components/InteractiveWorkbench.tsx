import React, { useEffect, useState } from "react";
import "@wokwi/elements";
import { WokwiDiagram } from "./WokwiDiagramViewer";

export interface InteractiveWorkbenchProps {
  diagram: WokwiDiagram | null;
}

export function InteractiveWorkbench({ diagram }: InteractiveWorkbenchProps) {
  const [ledStates, setLedStates] = useState<Record<string, boolean>>({});
  const [potValues, setPotValues] = useState<Record<string, number>>({});
  const [buttonPressed, setButtonPressed] = useState<Record<string, boolean>>({});
  const [buzzerActive, setBuzzerActive] = useState<Record<string, boolean>>({});
  const [servoAngles, setServoAngles] = useState<Record<string, number>>({});
  const [audioCtx, setAudioCtx] = useState<AudioContext | null>(null);

  useEffect(() => {
    // Initialize defaults from diagram
    if (!diagram?.parts) return;
    const initialLeds: Record<string, boolean> = {};
    const initialPots: Record<string, number> = {};
    const initialServos: Record<string, number> = {};
    diagram.parts.forEach((p) => {
      if (p.type.includes("led")) initialLeds[p.id] = true;
      if (p.type.includes("potentiometer")) initialPots[p.id] = 512;
      if (p.type.includes("servo")) initialServos[p.id] = 90;
    });
    setLedStates(initialLeds);
    setPotValues(initialPots);
    setServoAngles(initialServos);
  }, [diagram]);

  const toggleLed = (id: string) => {
    setLedStates((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const handlePotChange = (id: string, val: number) => {
    setPotValues((prev) => ({ ...prev, [id]: val }));
  };

  const handleServoChange = (id: string, val: number) => {
    setServoAngles((prev) => ({ ...prev, [id]: val }));
  };

  const triggerBuzzer = (id: string) => {
    setBuzzerActive((prev) => ({ ...prev, [id]: true }));
    try {
      const ctx = audioCtx || new (window.AudioContext || (window as any).webkitAudioContext)();
      if (!audioCtx) setAudioCtx(ctx);
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.25);
    } catch {
      // AudioContext may be restricted by autoplay policy
    }
    setTimeout(() => {
      setBuzzerActive((prev) => ({ ...prev, [id]: false }));
    }, 250);
  };

  if (!diagram || !diagram.parts || diagram.parts.length === 0) {
    return (
      <div className="sim-empty">
        No components detected in diagram.json to display on workbench.
      </div>
    );
  }

  return (
    <div className="interactive-bench-container">
      <div className="bench-header-card">
        <div className="bench-title-row">
          <span className="bench-badge">Interactive Simulation</span>
          <h4>Live Hardware Workbench</h4>
        </div>
        <p className="bench-desc">
          Interact with authentic Wokwi web components. Click buttons, rotate potentiometers, toggle
          LEDs, and pulse actuators to verify behaviour before physical assembly.
        </p>
      </div>

      <div className="bench-grid">
        {diagram.parts.map((part) => {
          const type = part.type.toLowerCase();
          const id = part.id;
          const colorAttr = (part.attrs?.color as string) || "red";

          if (type.includes("esp32")) {
            return (
              <div key={id} className="bench-component-card esp-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">Microcontroller</span>
                </div>
                <div className="bench-element-wrapper">
                  <wokwi-esp32-devkit-v1 />
                </div>
                <div className="bench-controls">
                  <span className="bench-status-badge">ESP32 DevKit (Active)</span>
                </div>
              </div>
            );
          }

          if (type.includes("led") && !type.includes("bar")) {
            const isLit = Boolean(ledStates[id]);
            return (
              <div key={id} className="bench-component-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">{colorAttr.toUpperCase()} LED</span>
                </div>
                <div className="bench-element-wrapper">
                  <wokwi-led color={colorAttr} value={isLit} />
                </div>
                <div className="bench-controls">
                  <button
                    type="button"
                    className={`bench-btn ${isLit ? "active" : ""}`}
                    onClick={() => toggleLed(id)}
                  >
                    {isLit ? "💡 Turn OFF" : "🔌 Turn ON"}
                  </button>
                </div>
              </div>
            );
          }

          if (type.includes("pushbutton")) {
            const isDown = Boolean(buttonPressed[id]);
            return (
              <div key={id} className="bench-component-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">Tactile Button</span>
                </div>
                <div
                  className="bench-element-wrapper interactive-clickable"
                  onMouseDown={() => setButtonPressed((prev) => ({ ...prev, [id]: true }))}
                  onMouseUp={() => setButtonPressed((prev) => ({ ...prev, [id]: false }))}
                  onTouchStart={() => setButtonPressed((prev) => ({ ...prev, [id]: true }))}
                  onTouchEnd={() => setButtonPressed((prev) => ({ ...prev, [id]: false }))}
                >
                  <wokwi-pushbutton color={colorAttr} pressed={isDown} />
                </div>
                <div className="bench-controls">
                  <span className="bench-press-hint">
                    {isDown ? "⬇ Pressed (HIGH/LOW)" : "👆 Click/hold to press"}
                  </span>
                </div>
              </div>
            );
          }

          if (type.includes("potentiometer")) {
            const potVal = potValues[id] ?? 512;
            return (
              <div key={id} className="bench-component-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">Potentiometer (ADC)</span>
                </div>
                <div className="bench-element-wrapper">
                  <wokwi-potentiometer value={potVal} min={0} max={1023} />
                </div>
                <div className="bench-controls">
                  <div className="bench-slider-row">
                    <input
                      type="range"
                      min="0"
                      max="1023"
                      value={potVal}
                      onChange={(e) => handlePotChange(id, Number(e.target.value))}
                      className="bench-range"
                    />
                    <span className="bench-val-display">{potVal} / 1023</span>
                  </div>
                </div>
              </div>
            );
          }

          if (type.includes("buzzer")) {
            const active = Boolean(buzzerActive[id]);
            return (
              <div key={id} className="bench-component-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">Piezo Buzzer</span>
                </div>
                <div className="bench-element-wrapper">
                  <wokwi-buzzer hasSignal={active} />
                </div>
                <div className="bench-controls">
                  <button
                    type="button"
                    className="bench-btn"
                    onClick={() => triggerBuzzer(id)}
                  >
                    🔊 Beep Tone
                  </button>
                </div>
              </div>
            );
          }

          if (type.includes("lcd1602")) {
            return (
              <div key={id} className="bench-component-card wide-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">LCD 1602 (I2C)</span>
                </div>
                <div className="bench-element-wrapper">
                  <wokwi-lcd1602 pins="i2c" text="SparkPlan Ready\nTemp: 24.5 C" backlight={true} />
                </div>
                <div className="bench-controls">
                  <span className="bench-status-badge">I2C 0x27 Active</span>
                </div>
              </div>
            );
          }

          if (type.includes("dht22")) {
            return (
              <div key={id} className="bench-component-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">DHT22 Temp & Humidity</span>
                </div>
                <div className="bench-element-wrapper">
                  <wokwi-dht22 temperature="24" humidity="50" />
                </div>
                <div className="bench-controls">
                  <span className="bench-status-badge">24°C · 50% RH</span>
                </div>
              </div>
            );
          }

          if (type.includes("hc-sr04") || type.includes("hcsr04")) {
            return (
              <div key={id} className="bench-component-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">Ultrasonic Distance</span>
                </div>
                <div className="bench-element-wrapper">
                  <wokwi-hc-sr04 distance="100" />
                </div>
                <div className="bench-controls">
                  <span className="bench-status-badge">100 mm Distance</span>
                </div>
              </div>
            );
          }

          if (type.includes("mpu6050")) {
            return (
              <div key={id} className="bench-component-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">MPU6050 6-DoF IMU</span>
                </div>
                <div className="bench-element-wrapper">
                  <wokwi-mpu6050 led1={true} />
                </div>
                <div className="bench-controls">
                  <span className="bench-status-badge">I2C 0x68 Active</span>
                </div>
              </div>
            );
          }

          if (type.includes("servo")) {
            const angle = servoAngles[id] ?? 90;
            return (
              <div key={id} className="bench-component-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">Micro Servo</span>
                </div>
                <div className="bench-element-wrapper">
                  <wokwi-servo angle={angle} />
                </div>
                <div className="bench-controls">
                  <div className="bench-slider-row">
                    <input
                      type="range"
                      min="0"
                      max="180"
                      value={angle}
                      onChange={(e) => handleServoChange(id, Number(e.target.value))}
                      className="bench-range"
                    />
                    <span className="bench-val-display">{angle}°</span>
                  </div>
                </div>
              </div>
            );
          }

          if (type.includes("resistor")) {
            return (
              <div key={id} className="bench-component-card">
                <div className="bench-card-meta">
                  <span className="component-id">{id}</span>
                  <span className="component-role">Resistor</span>
                </div>
                <div className="bench-element-wrapper">
                  <wokwi-resistor value="1000" />
                </div>
                <div className="bench-controls">
                  <span className="bench-status-badge">1 kΩ Pull-up / Limiter</span>
                </div>
              </div>
            );
          }

          // Generic fallback card
          return (
            <div key={id} className="bench-component-card">
              <div className="bench-card-meta">
                <span className="component-id">{id}</span>
                <span className="component-role">{type.replace(/^wokwi-|^board-/, "")}</span>
              </div>
              <div className="bench-element-wrapper generic-bench-module">
                <span className="generic-icon">🔌</span>
                <span className="generic-name">{id}</span>
              </div>
              <div className="bench-controls">
                <span className="bench-status-badge">Connected</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default InteractiveWorkbench;
