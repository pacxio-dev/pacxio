import * as THREE from 'three';
export function surfaceDetail(material,kind,layers){
 material.customProgramCacheKey=()=>`paper-${kind}-layers-v2-${!!layers?.hasReliefMap}`;
 material.onBeforeCompile=shader=>{
  shader.vertexShader='attribute vec3 paperCoord; attribute float bakedAO; varying vec3 vPaper; varying float vAO;\n'+shader.vertexShader;
  shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\nvPaper=paperCoord; vAO=bakedAO;');
  shader.fragmentShader=`varying vec3 vPaper; varying float vAO;
  float paperHash(vec3 p){p=fract(p*.3183099+vec3(.1,.3,.7));p*=17.;return fract(p.x*p.y*p.z*(p.x+p.y+p.z));}
  float paperNoise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(mix(paperHash(i),paperHash(i+vec3(1,0,0)),f.x),mix(paperHash(i+vec3(0,1,0)),paperHash(i+vec3(1,1,0)),f.x),f.y),mix(mix(paperHash(i+vec3(0,0,1)),paperHash(i+vec3(1,0,1)),f.x),mix(paperHash(i+vec3(0,1,1)),paperHash(i+vec3(1,1,1)),f.x),f.y),f.z);}
  `+shader.fragmentShader;
  if(kind==='print'){
   Object.assign(shader.uniforms,layers.uniforms);
   shader.fragmentShader='uniform sampler2D finishRelief, finishRegion, finishHolo; uniform float finishSelected, finishStrength, finishHeight, finishCavity, finishGradientRange, finishHasHolo; uniform vec2 finishTexel; uniform vec3 finishLight;\n'+shader.fragmentShader;
   if(layers.hasReliefMap){
    shader.fragmentShader=shader.fragmentShader.replace('#include <normal_fragment_maps>',`#include <normal_fragment_maps>
     vec2 uvFinish=vMapUv;
     vec4 finishData=texture2D(finishRelief,uvFinish);
     // Image rows run opposite to UV v, matching TextureLoader.flipY.
     vec2 differences=(finishData.rg*255.-128.)*(finishGradientRange/127.);
     vec2 heightGradient=vec2(differences.x,-differences.y)/(510.*finishTexel)*finishHeight*finishStrength;
     vec3 sx=dFdx(-vViewPosition),sy=dFdy(-vViewPosition),rx=cross(sy,normal),ry=cross(normal,sx);
     float determinant=dot(sx,rx);
     normal=normalize(abs(determinant)*normal-sign(determinant)*(dot(heightGradient,dFdx(uvFinish))*rx+dot(heightGradient,dFdy(uvFinish))*ry));
    `);
    shader.fragmentShader=shader.fragmentShader.replace('#include <opaque_fragment>',`
     outgoingLight*=1.-finishCavity*finishData.b*finishStrength;
     if(finishHasHolo>.5 && texture2D(finishHolo,vMapUv).r>.5){vec3 refl=reflect(-geometryViewDir,normal);outgoingLight*=.72+.35*cos(vec3(0.,2.1,4.2)+dot(refl,normalize((viewMatrix*vec4(finishLight,0.)).xyz))*13.);}
     #include <opaque_fragment>
    `);
   }
   shader.fragmentShader=shader.fragmentShader.replace('#include <colorspace_fragment>',`#include <colorspace_fragment>
    float selectedArea=0.; if(finishSelected>.5)selectedArea=texture2D(finishRegion,vMapUv).r;
    gl_FragColor.rgb=mix(gl_FragColor.rgb,vec3(.05,.75,.95),selectedArea*.55);
   `);

   shader.fragmentShader=shader.fragmentShader.replace('#include <roughnessmap_fragment>',THREE.ShaderChunk.roughnessmap_fragment.replace('texelRoughness.g','texelRoughness.r * (1.0-0.60*texelRoughness.g)'));
   shader.fragmentShader=shader.fragmentShader.replace('#include <metalnessmap_fragment>',THREE.ShaderChunk.metalnessmap_fragment.replace('texelMetalness.b','texelMetalness.g'));
   shader.fragmentShader=shader.fragmentShader.replace('#include <lights_physical_fragment>',THREE.ShaderChunk.lights_physical_fragment.replace('texture2D( clearcoatMap, vClearcoatMapUv ).x','texture2D( clearcoatMap, vClearcoatMapUv ).b'));
  }
  if(kind!=='cut'){
   shader.fragmentShader=shader.fragmentShader.replace('#include <normal_fragment_maps>',`#include <normal_fragment_maps>
    float footprint=max(length(dFdx(vPaper)),length(dFdy(vPaper)));
    float detailFade=1.-smoothstep(.25,.75,footprint*6200.);
    float fibre=0.; if(detailFade>.001)fibre=(paperNoise(vPaper*6200.)-.5)*.00035*${kind==='print'?'.23*(1.-.96*metalnessFactor)':'0.6'}*detailFade;
    vec3 dp1=dFdx(-vViewPosition),dp2=dFdy(-vViewPosition);
    vec3 r1=cross(dp2,normal),r2=cross(normal,dp1);float det=dot(dp1,r1);
    normal=normalize(abs(det)*normal-sign(det)*(dFdx(fibre)*r1+dFdy(fibre)*r2));
   `);
  }
  shader.fragmentShader=shader.fragmentShader.replace('#include <aomap_fragment>','#include <aomap_fragment>\nreflectedLight.indirectDiffuse*=mix(1.,clamp(vAO,0.,1.),.30);reflectedLight.indirectSpecular*=mix(1.,clamp(vAO,0.,1.),.12);');
 };
}

// A shared finish state drives both static and folding materials. The source
// artwork is explicitly marked composite: legacy foil/white colours are retained.
export function createMaterialLayers(manifest,{paper,print,texture,assetURL,wake,renderer}){
 const config=manifest.materialLayers??{substrate:{color:manifest.parts[0].paper},artwork:{encoding:'legacy-composite'},finishes:{regions:[]}};
 paper.roughness=config.substrate.roughness??.82;
 const finishes=config.finishes,neutral=new THREE.DataTexture(new Uint8Array([0,0,0,255]),1,1);neutral.needsUpdate=true;
 const uniforms={finishLight:{value:new THREE.Vector3(-3.6,-3.8,6.5).normalize()},finishHasHolo:{value:finishes.hasHolographic?1:0},finishHolo:{value:neutral},finishRelief:{value:neutral},finishRegion:{value:neutral},finishSelected:{value:0},finishStrength:{value:1},finishHeight:{value:2*(finishes.reliefHeightMM??.08)*.01},finishTexel:{value:new THREE.Vector2(1,1)},finishCavity:{value:finishes.cavityStrength??0},finishGradientRange:{value:finishes.gradientRange??1}};
 let selected='',token=0,regionTexture=null,disposed=false;
 async function selectRegion(id){
  if(disposed)return;
  const entry=(finishes.regions??[]).find(r=>r.id===id);
  if(id&&!entry)throw Error('Unknown finish region: '+id);
  const request=++token;selected=id;uniforms.finishSelected.value=0;wake();
  if(!entry){regionTexture?.dispose();regionTexture=null;uniforms.finishRegion.value=neutral;return;}
  try{
   const loaded=await texture(assetURL(entry.mask));
   if(disposed||request!==token){loaded.dispose();return;}
   renderer.initTexture(loaded);const old=regionTexture;regionTexture=loaded;
   uniforms.finishRegion.value=loaded;uniforms.finishSelected.value=1;old?.dispose();wake();
  }catch(error){if(request===token)selected='';throw error;}
 }
 return {config,uniforms,hasReliefMap:!!finishes.relief,
  async load(){if(finishes.relief){const t=await texture(assetURL(finishes.relief));uniforms.finishRelief.value=t;uniforms.finishTexel.value.set(1/t.image.width,1/t.image.height);t.generateMipmaps=false;t.minFilter=THREE.LinearFilter;renderer.initTexture(t);}if(finishes.holographicMask){const t=await texture(assetURL(finishes.holographicMask));uniforms.finishHolo.value=t;renderer.initTexture(t);}},
  setLight:direction=>uniforms.finishLight.value.copy(direction).normalize(),selectRegion,state:()=>({layers:config,selected,reliefStrength:uniforms.finishStrength.value,regionReady:uniforms.finishSelected.value===1}),
  dispose(){disposed=true;++token;selected='';regionTexture?.dispose();neutral.dispose();}
 };
}
