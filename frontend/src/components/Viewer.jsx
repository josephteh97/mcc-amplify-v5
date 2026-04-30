import React, { Suspense, useEffect, useState } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, useGLTF } from '@react-three/drei';
import * as THREE from 'three';

import { fetchStoreys, gltfUrl } from '../api.js';

// Mirrors v4/Viewer.jsx: load a single GLTF, frame the camera so the
// whole storey fits in view. v5 GLTFs carry columns (boxes/cylinders)
// and a synthesised plan-extent slab; clickable per-element selection
// is deferred until Stage 5B annotates each mesh node with its
// canonical_idx + audit trail.

class ViewerErrorBoundary extends React.Component {
  state = { hasError: false, error: null };
  static getDerivedStateFromError(error) { return { hasError: true, error }; }
  retry = () => this.setState({ hasError: false, error: null });
  render() {
    if (this.state.hasError) {
      return (
        <div className="viewer-error">
          <p>Model failed to load.</p>
          <p className="muted">{this.state.error?.message || 'Unknown error.'}</p>
          <button className="secondary" onClick={this.retry}>Retry</button>
        </div>
      );
    }
    return this.props.children;
  }
}

function FitToView({ url }) {
  const { scene } = useGLTF(url);
  const { camera, controls } = useThree();
  useEffect(() => {
    if (!scene) return;
    const box = new THREE.Box3().setFromObject(scene);
    if (box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const size   = box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z);
    if (maxDim <= 0) return;
    const fov     = camera.fov * (Math.PI / 180);
    const dist    = (maxDim / (2 * Math.tan(fov / 2))) * 1.6;
    camera.position.set(center.x + dist * 0.5, center.y + dist * 0.7, center.z + dist * 0.6);
    camera.near = Math.max(0.1, dist / 1000);
    camera.far  = dist * 20;
    camera.lookAt(center);
    camera.updateProjectionMatrix();
    if (controls) {
      controls.target.copy(center);
      controls.update();
    }
  }, [scene, camera, controls]);
  return <primitive object={scene} />;
}

function StoreyDropdown({ storeys, value, onChange, disabled }) {
  return (
    <select
      className="viewer-storey-select"
      value={value || ''}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      {storeys.map((s) => (
        <option
          key={s.storey_id}
          value={s.storey_id}
          disabled={!s.has_gltf}
        >
          {s.storey_id}
          {s.column_count != null ? ` · ${s.column_count} cols` : ''}
          {!s.has_gltf ? ' — no GLTF' : ''}
        </option>
      ))}
    </select>
  );
}

export function Viewer({ jobId }) {
  // storeys === null means "still loading"; [] means "loaded, none available".
  const [storeys, setStoreys] = useState(null);
  const [active,  setActive]  = useState(null);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    setStoreys(null);
    setError(null);
    fetchStoreys(jobId)
      .then((data) => {
        if (cancelled) return;
        const list = data.storeys || [];
        setStoreys(list);
        const first = list.find((s) => s.has_gltf);
        if (first) setActive(first.storey_id);
      })
      .catch((e) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [jobId]);

  if (!jobId) return null;
  if (error)         return <div className="viewer-empty error">{error}</div>;
  if (storeys === null) return <div className="viewer-empty muted">Loading storeys…</div>;
  if (!storeys.some((s) => s.has_gltf)) {
    return <div className="viewer-empty muted">No storey passed Stage 5B — see review queue.</div>;
  }

  const url = active ? gltfUrl(jobId, active) : null;

  return (
    <section className="viewer-panel">
      <h2>
        3D preview
        <StoreyDropdown
          storeys={storeys}
          value={active}
          onChange={setActive}
        />
      </h2>
      <div className="viewer-canvas">
        <ViewerErrorBoundary key={url}>
          <Canvas
            shadows
            dpr={[1, 2]}
            camera={{ fov: 50, near: 1, far: 5000, position: [80, 80, 80] }}
          >
            <ambientLight intensity={0.6} />
            <directionalLight
              position={[40, 80, 60]}
              intensity={0.9}
              castShadow
            />
            <Suspense fallback={null}>
              {url && <FitToView url={url} />}
            </Suspense>
            <OrbitControls makeDefault enableDamping />
            <gridHelper args={[200, 40, '#444', '#2a2a2a']} />
          </Canvas>
        </ViewerErrorBoundary>
      </div>
    </section>
  );
}
