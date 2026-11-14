import { mountViewer as mountLegacy } from './viewer3d-legacy.js';
import { canonical } from './display-identity.js';
import { tr } from './i18n.js';

// Preserve the public workbench interface. Enhanced assets are opt-in per scene;
// old jobs, inline previews and unsupported structures always keep the old path.
export function mountViewer(host, options={}) {
 const {sceneUrl,scene:givenScene,fileUrl=u=>u,onError,preferenceKey='default',initialFold=1,initialView=null}=options;
 const controller=new AbortController(), preference=`pacxio:lighting:v2:${preferenceKey}`;
 const defaults={direction:-30,brightness:1};let light={...defaults};
 try{const stored=JSON.parse(localStorage.getItem(preference)||localStorage.getItem(`packengine:lighting:v2:${preferenceKey}`)||'null');if(stored)light={...light,...stored};}catch{}
 let backend=null,scene=null,dead=false,attached=false,epoch=0,kind='loading',fold=initialFold,view=initialView,selected='',background=0,listener=null,playDone=null,pendingState=null;
 let readyResolve,timer,poll;const ready=new Promise(resolve=>readyResolve=resolve);
 const mount=document.createElement('div');mount.style.cssText='position:absolute;inset:0';host.prepend(mount);
 const status=document.createElement('div');status.className='viewer-status';status.setAttribute('role','status');status.textContent=tr('Loading 3D preview…');host.append(status);
 function settle(){clearTimeout(timer);readyResolve(kind);}
 function report(error){onError?.(error);}
 function cancelPlay(){const done=playDone;playDone=null;backend?.stopFold();done?.();}
 function notify(t){if(!attached)return;fold=t;listener?.(t);}
 function apply(){
  attached=true;backend.onFold(notify);backend.setLighting(light);
  if(backend.foldProgress()!=null&&Math.abs(backend.foldProgress()-fold)>1e-7)backend.setFold(fold);
  for(let i=0;i<background;i++)backend.nextBackground();
  if(selected)Promise.resolve(backend.selectRegion(selected)).catch(report);
  if(pendingState)backend.setState(pendingState);
  if(playDone){const done=playDone;backend.playFold(()=>{if(playDone===done){playDone=null;done();}});}
 }
 function fallback(error){
  if(dead||kind==='legacy')return;
  const reason=error?.message||String(error||'');
  fold=backend?.foldProgress()??fold;view=backend?.view()??view;
  ++epoch;attached=false;controller.abort();clearTimeout(timer);backend?.destroy();backend=null;mount.replaceChildren();
  kind='legacy';host.dataset.renderer=kind;
  if(error){host.dataset.rendererFallback=reason;status.textContent=tr('Using standard 3D preview');}else status.remove();
  try{
   backend=mountLegacy(mount,{...options,scene,initialFold:fold,initialView:view,onError:e=>{if(dead)return;status.textContent=tr('3D preview could not be loaded.');host.append(status);report(e);settle();}});
   if(!backend){status.textContent=tr('3D preview could not be loaded.');host.append(status);settle();return;}
   backend.onFold(notify);backend.setLighting(light);
   // The legacy API is synchronous but textures load asynchronously.
   const started=performance.now();
   const wait=()=>{if(dead)return;if(!mount.querySelector('canvas.loading')){apply();settle();return;}if(performance.now()-started>15000){settle();return;}poll=setTimeout(wait,25);};wait();
  }catch(e){status.textContent=tr('3D preview could not be loaded.');host.append(status);report(e);settle();}
 }
 function localURL(path,base){const url=new URL(path,base||location.href);if(url.origin!==location.origin||!/^https?:$/.test(url.protocol))throw Error('Display assets must be local');return url.href;}
 async function loadJSON(url){const r=await fetch(url,{signal:controller.signal,cache:'no-cache'});if(!r.ok)throw Error('3D scene unavailable');return r.json();}
 async function start(){
  try{
   scene=givenScene||await loadJSON(sceneUrl);if(dead)return;
   if(givenScene||scene.version!==3||scene.parts?.length!==1||!scene.display){fallback();return;}
   if(scene.display.version!==1||!globalThis.DecompressionStream||!globalThis.createImageBitmap||!globalThis.crypto?.subtle){fallback();return;}
   const probe=document.createElement('canvas'),gl=probe.getContext('webgl2');if(!gl){fallback();return;}gl.getExtension('WEBGL_lose_context')?.loseContext();
   timer=setTimeout(()=>fallback(Error('Display initialization timed out')),15000);
   const raw={...scene};delete raw.display;
   const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(scene.display.hashFormat === "typed-ieee754-v1" ? canonical(raw) : JSON.stringify(raw))))].map(x=>x.toString(16).padStart(2,'0')).join('');
   if(hash!==scene.display.sourceSHA256){fallback(Error('Prepared display assets are out of date'));return;}
   const manifestURL=localURL(fileUrl(scene.display.manifest)),manifest=await loadJSON(manifestURL);
   if(manifest.adapterVersion!==1||manifest.sourceSHA256!==hash||manifest.parts?.length!==1)throw Error('Incompatible display manifest');
   const ownEpoch=epoch;
   const {createDisplayViewer}=await import('./viewer3d-display.js');if(dead||ownEpoch!==epoch)return;
   const candidate=await createDisplayViewer(mount,{manifest,assetURL:path=>localURL(path,manifestURL),signal:controller.signal,initialFold:fold,initialView:view,lighting:light,onFatal:error=>queueMicrotask(()=>fallback(error))});
   if(dead||ownEpoch!==epoch){candidate.destroy();return;}
   backend=candidate;kind='enhanced';host.dataset.renderer=kind;status.remove();if(Math.abs(backend.foldProgress()-fold)>1e-7)backend.setFold(fold);apply();settle();
  }catch(error){if(!dead&&kind!=='legacy'){
    if(scene)fallback(error);else{kind='error';host.dataset.renderer=kind;status.textContent=tr('3D preview could not be loaded.');report(error);settle();}
   }}
 }
 start();
 return {ready,rendererKind:()=>kind,resize:()=>backend?.resize(),lighting:()=>({...light}),
  setLighting(change){for(const [key,min,max] of [['direction',-180,180],['brightness',.5,1.5]])if(Number.isFinite(change[key]))light[key]=Math.max(min,Math.min(max,change[key]));try{localStorage.setItem(preference,JSON.stringify(light));}catch{}backend?.setLighting(light);},
  resetLighting(){this.setLighting(defaults);},
  async selectRegion(id){selected=id;try{await backend?.selectRegion(id);}catch(error){if(!dead)report(error);}},
  states:()=>backend?.states()||Object.keys(scene?.states||{}),view:()=>backend?.view()||view||{az:35,el:24,zoom:1},state:()=>backend?.state()||pendingState||'closed',
  setState(name){cancelPlay();pendingState=name;if(scene?.states?.[name]?.t!=null)fold=scene.states[name].t;if(attached)backend?.setState(name);},
  foldProgress:()=>backend?.foldProgress()??fold,setFold(t){if(!Number.isFinite(t))return;cancelPlay();pendingState=null;fold=Math.max(0,Math.min(1,t));if(attached)backend?.setFold(fold);},
  playFold(done=()=>{}){cancelPlay();pendingState=null;playDone=done;if(backend)backend.playFold(()=>{if(playDone===done){playDone=null;done();}});},stopFold:cancelPlay,
  onFold(fn){listener=fn;},nextBackground(){background=(background+1)%3;backend?.nextBackground();},reset(){view=null;backend?.reset();},
  useLegacy(){fallback();},
  destroy(){if(dead)return;dead=true;++epoch;cancelPlay();controller.abort();clearTimeout(timer);clearTimeout(poll);backend?.destroy();backend=null;mount.remove();status.remove();kind='destroyed';host.dataset.renderer=kind;settle();}
 };
}
