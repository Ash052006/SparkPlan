import React, { useState, useRef, useEffect } from "react";
import { ESPLoader, Transport } from "esptool-js";
import { fetchArtifactBytes } from "../api";

export interface HardwareFlasherProps {
  runId: string;
  isOpen: boolean;
  onClose: () => void;
}

type FlashingState = "idle" | "connecting" | "connected" | "flashing" | "done" | "error";

export function HardwareFlasher({ runId, isOpen, onClose }: HardwareFlasherProps) {
  const [state, setState] = useState<FlashingState>("idle");
  const [progress, setProgress] = useState(0);
  const [logs, setLogs] = useState<string[]>([]);
  const [chipInfo, setChipInfo] = useState<{ chip: string; mac: string } | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const portRef = useRef<SerialPort | null>(null);
  const loaderRef = useRef<ESPLoader | null>(null);
  const logContainerRef = useRef<HTMLPreElement | null>(null);

  const isSupported = typeof navigator !== "undefined" && "serial" in navigator;

  const appendLog = (line: string) => {
    setLogs((prev) => [...prev, line]);
  };

  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs]);

  // Clean up serial port if modal is closed mid-operation
  useEffect(() => {
    return () => {
      if (portRef.current && state !== "flashing") {
        portRef.current.close().catch(() => {});
      }
    };
  }, [state]);

  if (!isOpen) return null;

  const handleConnect = async () => {
    if (!isSupported) return;
    setErrorMsg(null);
    setState("connecting");
    appendLog("Requesting serial port...");

    try {
      // Common USB-to-UART bridge vendor IDs (Silicon Labs CP210x, WCH CH340, Espressif)
      const filters = [
        { usbVendorId: 0x10c4 }, // Silicon Labs
        { usbVendorId: 0x1a86 }, // WCH CH340
        { usbVendorId: 0x303a }, // Espressif
        { usbVendorId: 0x0403 }, // FTDI
      ];

      let port: SerialPort;
      try {
        port = await navigator.serial.requestPort({ filters });
      } catch {
        // Fallback without filters if user device has custom VID/PID
        port = await navigator.serial.requestPort();
      }

      portRef.current = port;
      appendLog("Port selected. Connecting to ESP32 bootloader at 115200 baud...");

      const transport = new Transport(port);
      const customTerminal = {
        clean: () => {},
        writeLine: (data: string) => appendLog(data),
        write: (data: string) => appendLog(data),
      };

      const esploader = new ESPLoader({
        transport,
        baudrate: 115200,
        terminal: customTerminal,
      });

      loaderRef.current = esploader;
      const chip = await esploader.main();
      const mac = await esploader.chip.readMac(esploader);

      appendLog(`✅ Connected to ${chip} (MAC: ${mac})`);
      setChipInfo({ chip, mac });
      setState("connected");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      appendLog(`❌ Connection failed: ${msg}`);
      setErrorMsg(msg);
      setState("error");
      if (portRef.current) {
        portRef.current.close().catch(() => {});
        portRef.current = null;
      }
    }
  };

  const handleFlash = async () => {
    if (!loaderRef.current || !portRef.current) return;
    setErrorMsg(null);
    setState("flashing");
    setProgress(0);
    appendLog("Fetching compiled firmware binary (firmware.bin)...");

    try {
      const blob = await fetchArtifactBytes(runId, "firmware.bin");
      const buffer = await blob.arrayBuffer();
      const binData = new Uint8Array(buffer);
      appendLog(`Fetched ${binData.byteLength} bytes. Preparing flash image...`);

      const fileArray = [
        {
          data: binData,
          address: 0x10000, // Standard application partition offset for ESP32
        },
      ];

      appendLog("Erasing and flashing application at 0x10000...");

      await loaderRef.current.writeFlash({
        fileArray,
        flashMode: "keep",
        flashFreq: "keep",
        flashSize: "keep",
        eraseAll: false,
        compress: true,
        reportProgress: (_fileIndex: number, written: number, total: number) => {
          const pct = Math.round((written / total) * 100);
          setProgress(pct);
        },
      });

      appendLog("✅ Flashing complete! Resetting ESP32 to execute firmware...");
      await loaderRef.current.after("hard_reset");
      appendLog("🚀 Device rebooted. Your physical ESP32 is now running SparkPlan firmware!");
      setState("done");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      appendLog(`❌ Flash failed: ${msg}`);
      setErrorMsg(msg);
      setState("error");
    } finally {
      if (portRef.current) {
        portRef.current.close().catch(() => {});
        portRef.current = null;
      }
      loaderRef.current = null;
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-content flasher-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <p className="eyebrow">Hardware Deployment</p>
            <h2>⚡ Flash to Physical ESP32</h2>
          </div>
          <button className="ghost-button close-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        {!isSupported ? (
          <div className="flasher-unsupported">
            <p>
              <strong>Web Serial is not supported in this browser.</strong>
            </p>
            <p className="muted">
              Web Serial is natively supported in <strong>Google Chrome</strong>,{" "}
              <strong>Microsoft Edge</strong>, and <strong>Opera</strong> on desktop (Mac, Windows,
              Linux, Chromebook).
            </p>
            <p className="hint">
              You can also download <code>firmware.bin</code> from the Downloads list and flash it
              manually with <code>esptool.py write_flash 0x10000 firmware.bin</code>.
            </p>
          </div>
        ) : (
          <div className="flasher-body">
            <div className="flasher-instructions">
              <ol>
                <li>Connect your ESP32 board to your computer via USB.</li>
                <li>Click <strong>Connect ESP32</strong> and choose your board's serial port.</li>
                <li>Click <strong>Flash Firmware</strong> to program the device over USB.</li>
              </ol>
            </div>

            {chipInfo && (
              <div className="flasher-device-badge">
                <span className="badge-chip">Target: {chipInfo.chip}</span>
                <span className="badge-mac">MAC: {chipInfo.mac}</span>
              </div>
            )}

            {state === "flashing" && (
              <div className="flasher-progress-wrap">
                <div className="flasher-progress-bar">
                  <div
                    className="flasher-progress-fill"
                    style={{ width: `${progress}%` }}
                    role="progressbar"
                    aria-valuenow={progress}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  />
                </div>
                <span className="flasher-progress-text">{progress}% Flashed</span>
              </div>
            )}

            <div className="flasher-log-wrap">
              <div className="flasher-log-header">
                <span>Serial Console</span>
                {state === "done" && <span className="flasher-success-pill">Success</span>}
              </div>
              <pre ref={logContainerRef} className="flasher-log">
                {logs.length === 0 ? "Ready to connect..." : logs.join("\n")}
              </pre>
            </div>

            {errorMsg && <div className="flasher-error">Error: {errorMsg}</div>}

            <div className="flasher-actions">
              {state === "idle" || state === "error" || state === "connecting" ? (
                <button
                  className="primary-button flasher-primary-btn"
                  onClick={handleConnect}
                  disabled={state === "connecting"}
                >
                  {state === "connecting" ? "Connecting..." : "🔌 Connect ESP32"}
                </button>
              ) : state === "connected" ? (
                <button className="primary-button flasher-primary-btn" onClick={handleFlash}>
                  ⚡ Flash Firmware (0x10000)
                </button>
              ) : state === "flashing" ? (
                <button className="primary-button flasher-primary-btn" disabled>
                  Flashing {progress}%...
                </button>
              ) : (
                <button className="primary-button flasher-primary-btn" onClick={onClose}>
                  Done
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
export default HardwareFlasher;
