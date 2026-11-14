# Interactive display renderer

The Workbench uses a local Three.js bundle for supported prepared v3 scenes.
The legacy renderer remains available for older scenes, unsupported structures,
missing assets, unavailable WebGL2 and runtime failures.

## Build

Use Node.js 20 or later for development only:

```sh
cd renderer-src
npm ci
npm run build
```

The resulting `src/pacxio/studio/static/viewer3d-display.js` is bundled with the
Python package. Users need neither Node.js nor a network connection to load it.
Three.js is MIT licensed; the license is included next to the shipped renderer.
The studio environment texture is generated from Three.js RoomEnvironment.

## Asset preparation

`pacxio.mockup.display` builds geometry, ambient occlusion and lossless display
maps with the Python dependencies already used by pacxio. The current supported
styles are RTE, STE and FEFCO 0427, with a single v3 part. Other jobs keep the
legacy view. A completed mockup triggers preparation; downloaded cloud previews
are prepared locally from the returned preview maps. Preparation never upgrades
the resolution or removes a watermark from those maps.

Geometry and AO are cached separately from artwork. Every generation has an
input identity and immutable assets; activation replaces the scene descriptor
atomically. A failed, unsupported or timed-out build leaves the legacy scene
available. Preparation is limited to 45 seconds and 120,000 vertices.

Substrate, artwork and finish maps share one material state between the closed
view and live folding. Relief is normal-map shading, not displaced geometry.
Display-only crease joins do not modify the dieline or manufacturing dimensions.
