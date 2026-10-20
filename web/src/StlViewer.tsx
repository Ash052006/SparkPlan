import { useEffect, useRef } from "react";
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

type Props = {
  baseUrl: string;
  lidUrl: string;
};

export default function StlViewer({ baseUrl, lidUrl }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b0d11);

    const camera = new THREE.PerspectiveCamera(45, mount.clientWidth / mount.clientHeight, 0.1, 5000);
    camera.position.set(90, -90, 80);
    camera.up.set(0, 0, 1);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    mount.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0xffffff, 0.75));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(60, -50, 120);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x88aaff, 0.5);
    fill.position.set(-80, 40, -30);
    scene.add(fill);

    const loader = new STLLoader();
    const group = new THREE.Group();
    scene.add(group);

    const material = new THREE.MeshStandardMaterial({ color: 0x9fb4ff, metalness: 0.1, roughness: 0.65 });

    Promise.all([loader.loadAsync(baseUrl), loader.loadAsync(lidUrl)])
      .then(([baseGeom, lidGeom]) => {
        const base = new THREE.Mesh(baseGeom, material);
        const lid = new THREE.Mesh(lidGeom, material.clone());
        lid.material.color.set(0x6ee7a0);
        const box = new THREE.Box3().setFromObject(lid);
        lid.position.z = box.max.z - box.min.z + 5; // lift lid for inspection
        group.add(base, lid);
        group.rotation.x = -Math.PI / 2;
        const bounds = new THREE.Box3().setFromObject(group);
        const center = bounds.getCenter(new THREE.Vector3());
        group.position.sub(center);
        const size = bounds.getSize(new THREE.Vector3()).length();
        camera.position.set(size * 0.7, -size * 0.7, size * 0.6);
        camera.lookAt(0, 0, 0);
      })
      .catch(() => {
        /* ignore: viewer is best-effort */
      });

    let frame = 0;
    const animate = () => {
      frame = requestAnimationFrame(animate);
      group.rotation.z += 0.004;
      renderer.render(scene, camera);
    };
    animate();

    const onResize = () => {
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
      renderer.dispose();
      mount.removeChild(renderer.domElement);
    };
  }, [baseUrl, lidUrl]);

  return <div className="viewer" ref={mountRef} />;
}
