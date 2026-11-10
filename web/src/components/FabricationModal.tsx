import React from "react";
import { SupportedSpec, Catalog, bundleUrl, artifactUrl } from "../api";

export interface FabricationModalProps {
  runId: string;
  spec: SupportedSpec | null;
  catalog: Catalog | null;
  isOpen: boolean;
  onClose: () => void;
}

export function FabricationModal({
  runId,
  spec,
  catalog,
  isOpen,
  onClose,
}: FabricationModalProps) {
  if (!isOpen) return null;

  const names = new Map(catalog?.parts.map((p) => [p.id, p]) ?? []);
  const partCount = spec?.parts.length ?? 0;

  // Compute total BOM estimate
  const PART_PRICES: Record<string, number> = {
    esp32_devkit_30pin: 4.5,
    dht22: 3.5,
    capacitive_soil_moisture: 1.8,
    ssd1306_i2c: 3.0,
    bme280: 4.2,
    bh1750: 1.5,
    hc_sr04: 1.5,
    sg90_servo: 2.2,
    piezo_buzzer: 0.6,
    led_5mm: 0.1,
    resistor_220r: 0.05,
    button_tactile: 0.2,
    pir_motion: 2.0,
    ws2812b_8led: 2.5,
    voltage_divider: 0.5,
    potentiometer: 1.2,
    mpu6050: 3.5,
    relay_module: 1.8,
    lcd1602_i2c: 3.8,
    ds18b20: 2.2,
  };

  const bomTotal =
    spec?.parts.reduce((sum, p) => {
      const price = PART_PRICES[p.part_id] ?? 2.0;
      return sum + (p.quantity || 1) * price;
    }, 0) ?? 0;

  const estimatedPcbCost = 2.0; // Standard 5 pcs 100x100mm JLCPCB promo tier
  const estimatedPrintCost = 4.5; // Average PLA print service cost
  const totalTurnkeyCost = bomTotal + estimatedPcbCost + estimatedPrintCost;

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-content fab-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <p className="eyebrow">Manufacturing & Turnkey Sourcing</p>
            <h2>🏭 Order Fabrication & Parts</h2>
          </div>
          <button className="ghost-button close-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="fab-body">
          <div className="fab-quote-banner">
            <div className="fab-quote-metric">
              <span className="fab-quote-label">Estimated PCB (5 pcs)</span>
              <span className="fab-quote-val">${estimatedPcbCost.toFixed(2)}</span>
            </div>
            <div className="fab-quote-metric">
              <span className="fab-quote-label">Estimated 3D Enclosure</span>
              <span className="fab-quote-val">${estimatedPrintCost.toFixed(2)}</span>
            </div>
            <div className="fab-quote-metric">
              <span className="fab-quote-label">Component BOM ({partCount} items)</span>
              <span className="fab-quote-val">${bomTotal.toFixed(2)}</span>
            </div>
            <div className="fab-quote-metric fab-quote-total">
              <span className="fab-quote-label">Total Turnkey Estimate</span>
              <span className="fab-quote-val highlight">${totalTurnkeyCost.toFixed(2)}</span>
            </div>
          </div>

          <div className="fab-grid">
            {/* PCB Fabrication Card */}
            <div className="fab-card">
              <div className="fab-card-header">
                <span className="fab-icon">🟢</span>
                <div>
                  <h3>Custom PCB Manufacturing</h3>
                  <p className="muted">2-Layer FR-4, 1.6mm thickness, HASL finish</p>
                </div>
              </div>
              <ul className="fab-card-specs">
                <li>Board Size: ~70 x 50 mm (compact custom carrier)</li>
                <li>Netlist: KiCad S-Expression netlist included</li>
                <li>Turnaround: 24–48 hours standard</li>
              </ul>
              <div className="fab-card-actions">
                <a
                  href="https://jlcpcb.com/quote"
                  target="_blank"
                  rel="noreferrer noopener"
                  className="primary-button fab-action-btn"
                >
                  Order on JLCPCB ↗
                </a>
                <a
                  href="https://www.pcbway.com/orderonline.aspx"
                  target="_blank"
                  rel="noreferrer noopener"
                  className="ghost-button fab-action-btn"
                >
                  PCBWay ↗
                </a>
              </div>
            </div>

            {/* 3D Print Enclosure Card */}
            <div className="fab-card">
              <div className="fab-card-header">
                <span className="fab-icon">🧊</span>
                <div>
                  <h3>3D Printed Enclosure</h3>
                  <p className="muted">Parametric Base & Lid with screw boss standoffs</p>
                </div>
              </div>
              <ul className="fab-card-specs">
                <li>Material: PLA or PETG (0.2 mm layer height)</li>
                <li>Watertightness: Verified by CadQuery kernel</li>
                <li>Files: <code>base.stl</code> and <code>lid.stl</code></li>
              </ul>
              <div className="fab-card-actions">
                <a
                  href="https://craftcloud3d.com"
                  target="_blank"
                  rel="noreferrer noopener"
                  className="primary-button fab-action-btn"
                >
                  Quote on Craftcloud ↗
                </a>
                <a
                  href={artifactUrl(runId, "assembled.stl")}
                  download="assembled.stl"
                  className="ghost-button fab-action-btn"
                >
                  Download STL
                </a>
              </div>
            </div>

            {/* Component Procurement Card */}
            <div className="fab-card">
              <div className="fab-card-header">
                <span className="fab-icon">📦</span>
                <div>
                  <h3>Component Sourcing</h3>
                  <p className="muted">Direct distributor cart integrations</p>
                </div>
              </div>
              <ul className="fab-card-specs">
                <li>Parts: {partCount} components selected from catalog</li>
                <li>Suppliers: Pre-mapped to DigiKey, Mouser & Adafruit</li>
                <li>Format: DigiKey/Mouser bulk upload CSV</li>
              </ul>
              <div className="fab-card-actions">
                <a
                  href="https://www.digikey.com/en/resources/bom-manager"
                  target="_blank"
                  rel="noreferrer noopener"
                  className="primary-button fab-action-btn"
                >
                  DigiKey BOM Manager ↗
                </a>
                <a
                  href={bundleUrl(runId)}
                  download="sparkplan-bundle.zip"
                  className="ghost-button fab-action-btn"
                >
                  Download Bundle
                </a>
              </div>
            </div>
          </div>
        </div>

        <div className="modal-footer">
          <p className="hint">
            Estimates are approximate market prices from online fabrication services. SparkPlan
            does not sell physical parts directly.
          </p>
          <button type="button" className="primary-button" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

export default FabricationModal;
