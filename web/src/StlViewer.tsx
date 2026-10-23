import { KeyboardEvent, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { Theme } from "./theme";

type ViewMode = "base" | "lid" | "assembled";

type Props = {
  baseUrl: string;
  lidUrl: string;
  assembledUrl?: string | null;
  theme: Theme;
};

type SceneState = {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  controls: OrbitControls;
  group: THREE.Group;
  base: THREE.Mesh;
  lid: THREE.Mesh;
  baseMaterial: THREE.MeshStandardMaterial;
  lidMaterial: THREE.MeshStandardMaterial;
  lidOffset: number;
  maxDimension: number;
};

const VIEW_LABELS: { value: ViewMode; label: string }[] = [
  { value: "base", label: "Base" },
  { value: "lid", label: "Lid" },
  { value: "assembled", label: "Assembled" },
];

function boundsOf(mesh: THREE.Mesh): THREE.Box3 {
  return new THREE.Box3().setFromObject(mesh);
}

function fitCamera(state: SceneState, target: THREE.Box3): void {
  const center = target.getCenter(new THREE.Vector3());
  const size = target.getSize(new THREE.Vector3());
  const maxDimension = Math.max(size.x, size.y, size.z, 1);
  state.maxDimension = maxDimension;
  const distance = maxDimension * 1.9;
  const direction = new THREE.Vector3(1, -1.25, 0.85).normalize();
  state.camera.position.copy(center).addScaledVector(direction, distance);
  state.camera.near = Math.max(distance / 100, 0.1);
  state.camera.far = distance * 20;
  state.camera.updateProjectionMatrix();
  state.controls.target.copy(center);
  state.controls.minDistance = maxDimension * 0.5;
  state.controls.maxDistance = maxDimension * 8;
  state.controls.update();
}

function applyView(
  state: SceneState,
  mode: ViewMode,
  exploded: number,
  theme: Theme,
  fit: boolean,
): void {
  state.base.visible = mode !== "lid";
  state.lid.visible = mode !== "base";

  if (mode === "assembled") {
    state.lid.position.z = state.lidOffset + exploded;
  } else {
    state.lid.position.z = 0;
  }

  if (theme === "dark") {
    state.scene.background = new THREE.Color(0x0b0d11);
    state.baseMaterial.color.set(0x8fa7ff);
    state.lidMaterial.color.set(0x64d9a4);
  } else {
    state.scene.background = new THREE.Color(0xf4f6fb);
    state.baseMaterial.color.set(0x3557c8);
    state.lidMaterial.color.set(0x0f9f6e);
  }
  state.baseMaterial.needsUpdate = true;
  state.lidMaterial.needsUpdate = true;

  if (!fit) return;
  if (mode === "base") fitCamera(state, boundsOf(state.base));
  else if (mode === "lid") fitCamera(state, boundsOf(state.lid));
  else {
    const combined = boundsOf(state.base).union(boundsOf(state.lid));
    fitCamera(state, combined);
  }
}

export default function StlViewer({ baseUrl, lidUrl, assembledUrl, theme }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<SceneState | null>(null);
  const modeRef = useRef<ViewMode>("assembled");
  const themeRef = useRef(theme);
  themeRef.current = theme;
  const [mode, setMode] = useState<ViewMode>("assembled");
  const [exploded, setExploded] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    setLoaded(false);
    setError("");

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(
      42,
      Math.max(mount.clientWidth / Math.max(mount.clientHeight, 1), 0.1),
      0.1,
      5000,
    );
    camera.up.set(0, 1, 0);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      setError("WebGL is not available in this browser.");
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(Math.max(mount.clientWidth, 1), Math.max(mount.clientHeight, 1));
    mount.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0xffffff, 1.55));
    const key = new THREE.DirectionalLight(0xffffff, 2.4);
    key.position.set(70, -50, 120);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x88aaff, 0.9);
    fill.position.set(-80, 40, -40);
    scene.add(fill);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enablePan = true;
    controls.screenSpacePanning = true;

    const group = new THREE.Group();
    group.rotation.x = -Math.PI / 2;
    scene.add(group);

    const baseMaterial = new THREE.MeshStandardMaterial({
      color: 0x8fa7ff,
      metalness: 0.08,
      roughness: 0.58,
    });
    const lidMaterial = new THREE.MeshStandardMaterial({
      color: 0x64d9a4,
      metalness: 0.08,
      roughness: 0.52,
    });

    let cancelled = false;
    let frame = 0;
    let resizeObserver: ResizeObserver | null = null;

    const loader = new STLLoader();
    const assembledPromise = assembledUrl
      ? loader.loadAsync(assembledUrl).catch(() => null)
      : Promise.resolve(null);

    Promise.all([loader.loadAsync(baseUrl), loader.loadAsync(lidUrl), assembledPromise])
      .then(([baseGeometry, lidGeometry, assembledGeometry]) => {
        if (cancelled) {
          baseGeometry.dispose();
          lidGeometry.dispose();
          assembledGeometry?.dispose();
          return;
        }

        const base = new THREE.Mesh(baseGeometry, baseMaterial);
        const lid = new THREE.Mesh(lidGeometry, lidMaterial);
        const baseBounds = new THREE.Box3().setFromBufferAttribute(
          baseGeometry.getAttribute("position") as THREE.BufferAttribute,
        );
        const lidBounds = new THREE.Box3().setFromBufferAttribute(
          lidGeometry.getAttribute("position") as THREE.BufferAttribute,
        );
        const assembledMaxZ = assembledGeometry
          ? new THREE.Box3().setFromBufferAttribute(
              assembledGeometry.getAttribute("position") as THREE.BufferAttribute,
            ).max.z
          : null;
        const lidOffset =
          assembledMaxZ !== null ? assembledMaxZ - lidBounds.max.z : baseBounds.max.z;
        assembledGeometry?.dispose();

        group.add(base, lid);
        const handles: SceneState = {
          scene,
          camera,
          renderer,
          controls,
          group,
          base,
          lid,
          baseMaterial,
          lidMaterial,
          lidOffset,
          maxDimension: 1,
        };
        sceneRef.current = handles;
        applyView(handles, modeRef.current, 0, themeRef.current, true);
        setLoaded(true);

        const animate = () => {
          frame = window.requestAnimationFrame(animate);
          controls.update();
          renderer.render(scene, camera);
        };
        animate();

        const resize = () => {
          const width = mount.clientWidth;
          const height = mount.clientHeight;
          if (width <= 0 || height <= 0) return;
          camera.aspect = width / height;
          camera.updateProjectionMatrix();
          renderer.setSize(width, height);
        };
        resizeObserver = new ResizeObserver(resize);
        resizeObserver.observe(mount);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load the STL enclosure files.");
      });

    return () => {
      cancelled = true;
      sceneRef.current = null;
      window.cancelAnimationFrame(frame);
      resizeObserver?.disconnect();
      controls.dispose();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose();
        }
      });
      baseMaterial.dispose();
      lidMaterial.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [baseUrl, lidUrl, assembledUrl]);

  useEffect(() => {
    const handles = sceneRef.current;
    if (!handles) return;
    const fit = modeRef.current !== mode;
    modeRef.current = mode;
    applyView(handles, mode, exploded, theme, fit);
  }, [mode, exploded, theme, loaded]);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const state = sceneRef.current;
    if (!state) return;
    const offset = state.camera.position.clone().sub(state.controls.target);
    const spherical = new THREE.Spherical().setFromVector3(offset);
    const angleStep = 0.12;
    let handled = true;
    if (event.key === "ArrowLeft") spherical.theta -= angleStep;
    else if (event.key === "ArrowRight") spherical.theta += angleStep;
    else if (event.key === "ArrowUp") spherical.phi -= angleStep;
    else if (event.key === "ArrowDown") spherical.phi += angleStep;
    else if (event.key === "+" || event.key === "=") spherical.radius *= 0.9;
    else if (event.key === "-" || event.key === "_") spherical.radius *= 1.1;
    else handled = false;
    if (!handled) return;
    event.preventDefault();
    spherical.phi = THREE.MathUtils.clamp(spherical.phi, 0.08, Math.PI - 0.08);
    spherical.radius = THREE.MathUtils.clamp(
      spherical.radius,
      state.controls.minDistance,
      state.controls.maxDistance,
    );
    state.camera.position
      .copy(state.controls.target)
      .add(new THREE.Vector3().setFromSpherical(spherical));
    state.controls.update();
  }

  return (
    <div className="viewer-shell">
      <div className="viewer-toolbar">
        <div className="segmented" role="group" aria-label="Enclosure view">
          {VIEW_LABELS.map((view) => (
            <button
              key={view.value}
              type="button"
              className={mode === view.value ? "segment active" : "segment"}
              aria-pressed={mode === view.value}
              onClick={() => setMode(view.value)}
            >
              {view.label}
            </button>
          ))}
        </div>
        <label className="explode-control">
          <span>Exploded</span>
          <input
            type="range"
            min={0}
            max={40}
            step={1}
            value={exploded}
            disabled={mode !== "assembled"}
            onChange={(event) => setExploded(Number(event.target.value))}
            aria-label="Exploded view distance in millimetres"
          />
          <span className="range-value">{exploded} mm</span>
        </label>
      </div>

      <div
        className="viewer-canvas"
        ref={mountRef}
        role="img"
        tabIndex={0}
        onKeyDown={handleKeyDown}
        aria-label="Interactive 3D view of the enclosure. Drag or use arrow keys to orbit and scroll or use plus and minus to zoom."
      >
        {!loaded && !error && <div className="viewer-overlay">Loading enclosure...</div>}
        {error && (
          <div className="viewer-overlay viewer-error" role="alert">
            {error}
          </div>
        )}
      </div>

      <p className="hint">
        Drag or use arrow keys to orbit; scroll or use plus and minus to zoom. The exploded view is
        illustrative and is not a fit check.
      </p>
    </div>
  );
}
