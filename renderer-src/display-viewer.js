import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RectAreaLightUniformsLib } from 'three/addons/lights/RectAreaLightUniformsLib.js';
import { surfaceDetail, createMaterialLayers } from './material-layers.js';
import { decodePackedAsset } from './packed-assets.js';
import { createLiveFold } from './live-fold-geometry.js';

// Instance-owned renderer. No page IDs, global controls, network CDN or idle loop.
export async function createDisplayViewer(host, {manifest, assetURL, signal, onFatal,
  initialFold=1, initialView=null, lighting={direction:-30,brightness:1}}) {
 const S=10, textures=new Set(), geometries=new Set(), materials=new Set();
 const local=new AbortController(), abort=()=>local.abort();
 signal.addEventListener('abort',abort,{once:true});
 let dead=false, renderer, scene, camera, controls, observer, layers, live, closed, key, softbox, floor, shadow;
 let raf=0, ready=false, fold=1, animation=null, last=0, motionRadius=0, framed=false, background=0, foldListener=()=>{};
 let viewDistance=1, current='closed', light={...lighting};
 const center=new THREE.Vector3(), clip=new THREE.Plane(new THREE.Vector3(0,0,1),10000);
 const backgrounds=['#f5f5f3','#0b0b0d','#ffffff'];
 const check=()=>{if(dead||signal.aborted||local.signal.aborted)throw new DOMException('Viewer disposed','AbortError');};
 function ownTexture(t){textures.add(t);const dispose=t.dispose.bind(t);let disposed=false;t.dispose=()=>{if(disposed)return;disposed=true;dispose();t.image?.close?.();textures.delete(t);};return t;}
 const material=m=>(materials.add(m),m), geometry=g=>(geometries.add(g),g);
 async function fetchAsset(path){check();const r=await fetch(assetURL(path),{signal:local.signal});if(!r.ok)throw Error('Display asset unavailable');return r;}
 async function texture(path,srgb=false){
  const response=await fetchAsset(path), blob=await response.blob();check();
  const bitmap=await createImageBitmap(blob,{imageOrientation:'flipY',premultiplyAlpha:'none',colorSpaceConversion:'none'});
  if(dead||signal.aborted){bitmap.close();check();}
  const t=ownTexture(new THREE.Texture(bitmap));t.flipY=false;t.colorSpace=srgb?THREE.SRGBColorSpace:THREE.NoColorSpace;
  t.anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());t.needsUpdate=true;return t;
 }
 async function packed(d){if(!d)throw Error('Missing packed display asset');const r=await fetchAsset(d.url);const data=await decodePackedAsset(r.body,d);check();return data;}
 function wake(){if(!dead&&ready&&!raf&&!document.hidden)raf=requestAnimationFrame(draw);}
 function stopFold(){const done=animation?.done;animation=null;done?.();}
 function draw(now){
  raf=0;if(dead||!ready||document.hidden)return;
  try{
   if(animation){const a=animation;a.elapsed+=Math.min(last?(now-last):0,100);const t=Math.min(1,a.elapsed/a.duration);applyFold(a.from+(a.to-a.from)*t);if(t===1)stopFold();}
   last=now;renderer.render(scene,camera);if(animation)wake();
  }catch(error){onFatal(error);}
 }
 function visibility(){last=performance.now();if(!document.hidden)wake();}
 function stopBubble(event){event.stopPropagation();}
 function resetOnDoubleClick(event){event.stopPropagation();stopFold();setView();}
 function contextLost(event){event.preventDefault();if(!dead)onFatal(Error('WebGL context lost'));}
 function poseLight(state){
  const r=THREE.MathUtils.degToRad(light.direction+30);
  scene.environmentRotation.z=r;scene.environmentIntensity=.95*light.brightness;
  key.intensity=.7*light.brightness;softbox.intensity=2.2*light.brightness;
  const direction=new THREE.Vector3(-3.6,-3.8,6.5).applyAxisAngle(new THREE.Vector3(0,0,1),r);
  key.position.copy(center).add(direction);key.target.position.copy(center);layers.setLight(direction);
  softbox.position.copy(center).add(new THREE.Vector3(-3.6,-3.8,5.7));softbox.lookAt(center);
  const span=fold===1?4:Math.max(4,Math.hypot(...state.size)*1.05);
  Object.assign(key.shadow.camera,{left:-span,right:span,top:span,bottom:-span});key.shadow.camera.updateProjectionMatrix();renderer.shadowMap.needsUpdate=true;
 }
 function applyFold(value){
  const t=Math.max(0,Math.min(1,Number(value)));if(!Number.isFinite(t))return;
  const old=center.clone();fold=t;const state=live.setProgress(t), isClosed=t===1;
  closed.visible=isClosed;live.group.visible=!isClosed;
  center.copy(isClosed?new THREE.Vector3(...manifest.size).multiplyScalar(S/2):new THREE.Vector3(...state.center));
  const shift=center.clone().sub(old);camera.position.add(shift);controls.target.add(shift);controls.update();
  const ground=isClosed?1:state.ground;clip.constant=ground>.5?state.groundPlaneConstant:10000;
  floor.visible=shadow.visible=ground>.5;shadow.material.opacity=.22*ground;poseLight(state);
  current=Object.keys(manifest.states||{}).find(k=>Math.abs(manifest.states[k].t-t)<1e-6)||'';
  if(ready){foldListener(t);wake();}
 }
 function frameMotion(){
  if(framed)return;
  const angle=Math.min(Math.PI/12,Math.atan(Math.tan(Math.PI/12)*camera.aspect));
  const offset=camera.position.clone().sub(controls.target),distance=motionRadius/Math.sin(angle)*1.08;
  offset.setLength(Math.max(distance,offset.length()));camera.position.copy(controls.target).add(offset);controls.maxDistance=Math.max(22,distance*3);controls.update();framed=true;
 }
 function setView(view={az:35,el:24,zoom:1}){
  const az=THREE.MathUtils.degToRad(view.az??35),el=THREE.MathUtils.degToRad(view.el??24);
  viewDistance=Math.hypot(...manifest.size)*S/2/Math.sin(Math.PI/12)*1.12*Math.max(1,1.2/camera.aspect);
  const distance=viewDistance/Math.max(.1,view.zoom??1);
  controls.target.copy(center);camera.position.copy(center).add(new THREE.Vector3(Math.sin(az)*Math.cos(el),-Math.cos(az)*Math.cos(el),Math.sin(el)).multiplyScalar(distance));
  camera.lookAt(center);controls.update();framed=false;wake();
 }
 function view(){const d=camera.position.clone().sub(controls.target),n=d.length();return {az:THREE.MathUtils.radToDeg(Math.atan2(d.x,-d.y)),el:THREE.MathUtils.radToDeg(Math.asin(d.z/n)),zoom:viewDistance/n};}
 function resize(){
  if(dead||!renderer)return;const rect=host.getBoundingClientRect(),w=Math.max(1,rect.width),h=Math.max(1,rect.height);
  const scale=Math.min(devicePixelRatio||1,1.5,1800/Math.max(w,h));renderer.setSize(Math.round(w*scale),Math.round(h*scale),false);
  const old=camera.aspect;camera.aspect=w/h;
  if(ready)camera.position.sub(controls.target).multiplyScalar(Math.max(1,1.2/camera.aspect)/Math.max(1,1.2/old)).add(controls.target);
  camera.updateProjectionMatrix();framed=false;wake();
 }
 function destroy(){
  if(dead)return;dead=true;ready=false;local.abort();signal.removeEventListener('abort',abort);cancelAnimationFrame(raf);observer?.disconnect();
  document.removeEventListener('visibilitychange',visibility);renderer?.domElement.removeEventListener('webglcontextlost',contextLost);
  renderer?.domElement.removeEventListener('wheel',stopBubble);renderer?.domElement.removeEventListener('pointerdown',stopBubble);renderer?.domElement.removeEventListener('dblclick',resetOnDoubleClick);
  stopFold();controls?.dispose();layers?.dispose();live?.dispose();
  for(const g of geometries)g.dispose();for(const m of materials)m.dispose();for(const t of [...textures])t.dispose();
  key?.shadow.dispose();renderer?.dispose();renderer?.forceContextLoss();renderer?.domElement.remove();
 }
 try{
  check();renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'high-performance'});
  renderer.domElement.className='viewer-canvas';renderer.domElement.style.cssText='width:100%;height:100%;display:block;touch-action:none';host.prepend(renderer.domElement);
  renderer.domElement.addEventListener('webglcontextlost',contextLost);
  renderer.debug.onShaderError=()=>{throw Error('Display shader compilation failed');};
  renderer.setPixelRatio(1);renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.NeutralToneMapping;renderer.toneMappingExposure=1.05;
  renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.VSMShadowMap;renderer.shadowMap.autoUpdate=false;renderer.localClippingEnabled=true;
  scene=new THREE.Scene();scene.background=new THREE.Color(backgrounds[0]);camera=new THREE.PerspectiveCamera(30,1,.01,300);camera.up.set(0,0,1);
  controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=false;controls.rotateSpeed=.65;controls.zoomSpeed=.7;controls.minDistance=.5;controls.maxDistance=30;controls.maxPolarAngle=Math.PI*.495;
  renderer.domElement.addEventListener('wheel',stopBubble,{passive:false});renderer.domElement.addEventListener('pointerdown',stopBubble);renderer.domElement.addEventListener('dblclick',resetOnDoubleClick);
  controls.addEventListener('change',wake);controls.addEventListener('start',()=>{framed=false;});
  const urls={...manifest.textures,basePreview:manifest.materialLayers.artwork.basePreview,baseFull:manifest.materialLayers.artwork.baseFull,material:manifest.materialLayers.finishes.properties};
  const [bytes,base,finish,cutMask,envBytes,foldBytes]=await Promise.all([packed(manifest.geometryAsset),texture(urls.basePreview,true),texture(urls.material),texture(urls.cutMask),packed(manifest.environmentAsset),packed(manifest.foldAsset)]);
  check();if(bytes.byteLength!==manifest.vertexCount*manifest.stride*4||manifest.stride!==12)throw Error('Invalid display geometry');
  const source=JSON.parse(new TextDecoder().decode(foldBytes));if(source.version!==3||source.parts?.length!==1)throw Error('Unsupported fold source');manifest.states=source.states;
  center.fromArray(manifest.size).multiplyScalar(S/2);
  const print=material(new THREE.MeshPhysicalMaterial({map:base,roughness:1,roughnessMap:finish,metalness:1,metalnessMap:finish,clearcoat:1,clearcoatMap:finish,clearcoatRoughness:.30,ior:1.48,side:THREE.DoubleSide}));
  const paperColor=new THREE.Color(...manifest.materialLayers.substrate.color.map(v=>v/255)).convertSRGBToLinear(),caliper=manifest.materialLayers.substrate.caliperMM/1000;
  const paper=material(new THREE.MeshStandardMaterial({color:paperColor,roughness:.82,side:THREE.DoubleSide}));cutMask.wrapS=THREE.RepeatWrapping;
  const cut=material(new THREE.MeshStandardMaterial({color:paperColor.clone().multiplyScalar(.78),roughness:.86,alphaMap:cutMask,alphaTest:.5,side:THREE.DoubleSide}));
  layers=createMaterialLayers(manifest,{paper,print,texture,assetURL:p=>p,wake,renderer});await layers.load();check();
  const surface={print,paper,cut};for(const [role,m] of Object.entries(surface))surfaceDetail(m,role,layers);
  closed=new THREE.Group();scene.add(closed);const raw=new Float32Array(bytes);
  for(const group of manifest.groups){
   if(!surface[group.role]||!Number.isInteger(group.count)||group.start<0||group.start+group.count>manifest.vertexCount)throw Error('Invalid geometry group');
   const arr=raw.slice(group.start*12,(group.start+group.count)*12);
   for(let i=0;i<group.count;i++){const j=i*12;arr[j]*=S;arr[j+1]*=S;arr[j+2]*=S;if(group.role==='cut'){arr[j+6]=arr[j+8]/.0031;arr[j+7]=(arr[j+10]+caliper)/caliper;}}
   const ib=new THREE.InterleavedBuffer(arr,12),g=geometry(new THREE.BufferGeometry());
   for(const [name,size,offset] of [['position',3,0],['normal',3,3],['uv',2,6],['paperCoord',3,8],['bakedAO',1,11]])g.setAttribute(name,new THREE.InterleavedBufferAttribute(ib,size,offset));
   const mesh=new THREE.Mesh(g,surface[group.role]);mesh.castShadow=mesh.receiveShadow=true;closed.add(mesh);
  }
  const folding={};for(const [role,m] of Object.entries(surface)){folding[role]=material(m.clone());surfaceDetail(folding[role],role,layers);folding[role].clippingPlanes=[clip];folding[role].clipShadows=true;}
  live=createLiveFold(source,{materials:folding,scale:.01});live.group.visible=false;scene.add(live.group);
  if(envBytes.byteLength!==6291456)throw Error('Invalid environment asset');
  const env=ownTexture(new THREE.DataTexture(new Uint16Array(envBytes),768,1024,THREE.RGBAFormat,THREE.HalfFloatType));env.mapping=THREE.CubeUVReflectionMapping;env.minFilter=env.magFilter=THREE.LinearFilter;env.colorSpace=THREE.LinearSRGBColorSpace;env.needsUpdate=true;scene.environment=env;
  RectAreaLightUniformsLib.init();softbox=new THREE.RectAreaLight(0xffffff,2.2,5,4.2);softbox.up.set(0,0,1);scene.add(softbox);
  key=new THREE.DirectionalLight(0xffffff,.7);key.castShadow=true;key.shadow.mapSize.set(2048,2048);Object.assign(key.shadow.camera,{left:-4,right:4,top:4,bottom:-4,near:.1,far:18});key.shadow.radius=22;key.shadow.blurSamples=32;key.shadow.bias=-.00008;key.shadow.normalBias=.0015;scene.add(key,key.target);
  floor=new THREE.Mesh(geometry(new THREE.PlaneGeometry(80,80)),material(new THREE.MeshBasicMaterial({color:backgrounds[0],toneMapped:false})));floor.position.z=-.0003;scene.add(floor);
  shadow=new THREE.Mesh(geometry(new THREE.PlaneGeometry(80,80)),material(new THREE.ShadowMaterial({opacity:.22,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1})));shadow.position.z=-.0002;shadow.receiveShadow=true;scene.add(shadow);
  resize();setView(initialView||undefined);applyFold(initialFold);await renderer.compileAsync(scene,camera);check();renderer.render(scene,camera);
  // Complete shader/texture preparation before exposing interactive fold controls.
  const full=await texture(urls.baseFull,true);check();print.map=full;folding.print.map=full;print.needsUpdate=folding.print.needsUpdate=true;
  await renderer.compileAsync(live.group,camera,scene);check();
  for(let i=0;i<=28;i++)motionRadius=Math.max(motionRadius,Math.hypot(...live.setProgress(i/28).size)/2);
  for(const t of [0,1-1e-6]){applyFold(t);renderer.setScissor(0,0,1,1);renderer.setScissorTest(true);renderer.render(scene,camera);}
  renderer.setScissorTest(false);applyFold(initialFold);renderer.render(scene,camera);base.dispose();
  const gl=renderer.getContext(),fence=gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE,0);gl.flush();
  try{while(gl.clientWaitSync(fence,0,0)===gl.TIMEOUT_EXPIRED){await new Promise(resolve=>setTimeout(resolve,8));check();}}finally{gl.deleteSync(fence);}
  check();ready=true;observer=new ResizeObserver(resize);observer.observe(host);document.addEventListener('visibilitychange',visibility);wake();
  function animate(to,done){stopFold();frameMotion();if(Math.abs(to-fold)<1e-7){done?.();return;}animation={from:fold,to,elapsed:0,duration:Math.max(400,Math.abs(to-fold)*6500),done};last=performance.now();wake();}
  return {resize,view,setView,lighting:()=>({...light}),setLighting(change){light={...light,...change};poseLight(live.state());wake();},
   selectRegion:id=>layers.selectRegion(id),states:()=>Object.keys(source.states),state:()=>current,
   setState(name){if(source.states[name])animate(source.states[name].t);},foldProgress:()=>fold,
   setFold(t){stopFold();frameMotion();applyFold(t);},playFold(done){stopFold();frameMotion();applyFold(0);animate(1,done);},stopFold,onFold(fn){foldListener=fn||(()=>{});},
   nextBackground(){background=(background+1)%backgrounds.length;scene.background.set(backgrounds[background]);floor.material.color.set(backgrounds[background]);wake();},
   reset(){stopFold();setView();},destroy};
 }catch(error){destroy();throw error;}
}
