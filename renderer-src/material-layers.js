import * as THREE from 'three';
export function surfaceDetail(material,kind,layers){
 material.customProgramCacheKey=()=>`paper-${kind}-layers-v5-${!!layers?.hasReliefMap}`;
 material.onBeforeCompile=shader=>{
  shader.vertexShader='attribute vec3 paperCoord; attribute float bakedAO; varying vec3 vPaper; varying vec3 vWorldPos; varying float vAO;\n'+shader.vertexShader;
  shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\nvPaper=paperCoord; vAO=bakedAO;');
  shader.vertexShader=shader.vertexShader.replace('#include <worldpos_vertex>','#include <worldpos_vertex>\nvWorldPos=(modelMatrix*vec4(transformed,1.)).xyz;');
  shader.fragmentShader=`varying vec3 vPaper; varying vec3 vWorldPos; varying float vAO;
  float paperHash(vec3 p){p=fract(p*.3183099+vec3(.1,.3,.7));p*=17.;return fract(p.x*p.y*p.z*(p.x+p.y+p.z));}
  float paperNoise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(mix(paperHash(i),paperHash(i+vec3(1,0,0)),f.x),mix(paperHash(i+vec3(0,1,0)),paperHash(i+vec3(1,1,0)),f.x),f.y),mix(mix(paperHash(i+vec3(0,0,1)),paperHash(i+vec3(1,0,1)),f.x),mix(paperHash(i+vec3(0,1,1)),paperHash(i+vec3(1,1,1)),f.x),f.y),f.z);}
  `+shader.fragmentShader;
  if(kind==='print'){
   Object.assign(shader.uniforms,layers.uniforms);
   shader.fragmentShader='uniform sampler2D finishRelief, finishRegion, finishHolo; uniform float finishSelected, finishStrength, finishHeight, finishCavity, finishGradientRange, finishHasHolo, finishBrightness, finishRadius; uniform vec2 finishTexel; uniform vec3 finishLight, finishCenter;\n'+shader.fragmentShader;
   if(layers.hasReliefMap){
    shader.fragmentShader=shader.fragmentShader.replace('#include <normal_fragment_maps>',`#include <normal_fragment_maps>
     vec2 uvFinish=vMapUv;
     vec4 finishData=texture2D(finishRelief,uvFinish);
     // Image rows run opposite to UV v, matching TextureLoader.flipY.
     vec2 differences=(finishData.rg*255.-128.)*(finishGradientRange/127.);
     vec2 heightGradient=vec2(differences.x,-differences.y)/(510.*finishTexel)*finishHeight*finishStrength;
     vec3 sx=dFdx(-vViewPosition),sy=dFdy(-vViewPosition),rx=cross(sy,normal),ry=cross(normal,sx);
     float determinant=dot(sx,rx);
     if(abs(determinant)>1e-8)normal=normalize(abs(determinant)*normal-sign(determinant)*(dot(heightGradient,dFdx(uvFinish))*rx+dot(heightGradient,dFdy(uvFinish))*ry));
    `);
    shader.fragmentShader=shader.fragmentShader.replace('#include <opaque_fragment>',`
     outgoingLight*=1.-finishCavity*finishData.b*finishStrength;
     if(finishHasHolo>.5 && texture2D(finishHolo,vMapUv).r>.5){vec3 refl=reflect(-geometryViewDir,normal);outgoingLight*=.82+.28*cos(vec3(0.,2.1,4.2)+dot(refl,normalize((viewMatrix*vec4(finishLight,0.)).xyz))*11.);}
     #include <opaque_fragment>
    `);
   }
   shader.fragmentShader=shader.fragmentShader.replace('#include <lights_fragment_end>',`#include <lights_fragment_end>
    if(metalnessFactor>.04){
     vec3 Rv=reflect(-geometryViewDir,normal);
     vec3 Rw=normalize((vec4(Rv,0.)*viewMatrix).xyz);
     vec3 rel=(vWorldPos-finishCenter)/max(finishRadius,.05);
     vec3 hit=normalize(Rw*1.20+rel*1.05);
     vec3 Lw=normalize(finishLight);
     vec3 Hw=normalize(vec3(Lw.x,Lw.y,0.)+vec3(1e-4,0.,0.));
     vec3 Sw=normalize(vec3(-Hw.y,Hw.x,0.));
     float dMain=dot(hit,normalize(Hw*.22+Sw*.78+vec3(0.,0.,.58)));
     float dSide=dot(hit,normalize(-Hw*.75+Sw*.48+vec3(0.,0.,.45)));
     float metalW=smoothstep(.42,.65,metalnessFactor);
     float pearlW=smoothstep(.04,.12,metalnessFactor)*(1.-smoothstep(.30,.46,metalnessFactor));
     if(metalW>.001){
      // High-contrast studio light-tent reflection matching real photographed silver/gold card:
      // cool metallic shadow -> bright diagonal softbox ribbon -> cool metallic falloff + bright bevel glint.
      float spread=mix(.14,.28,clamp(roughnessFactor,.08,.65));
      float softboxLobe=exp(-pow((dMain-.14)/spread,2.));
      float sideLobe=exp(-pow((dSide-.28)/.28,2.));
      float topStrip=exp(-pow((Rw.z-.72)/.30,2.));
      float fresEdge=pow(1.-clamp(dot(normal,geometryViewDir),0.,1.),3.2);
      float studioMetal=(.15+1.45*softboxLobe+.58*sideLobe+.42*topStrip+.55*fresEdge)*finishBrightness;
      vec3 f0=mix(vec3(.04),diffuseColor.rgb,metalnessFactor);
      reflectedLight.indirectSpecular=mix(reflectedLight.indirectSpecular,f0*studioMetal,metalW*.92);
     }
     if(pearlW>.001){
      // Luminous mica pearlescent card: keep bright creamy diffuse (no greying from metalness),
      // add a sweeping silky satin-pearl highlight ribbon, subtle warm/cool mother-of-pearl shift,
      // soft grazing edge luster, and ultra-fine optical mica shimmer (zero surface bumps).
      float diffLift=mix(1.,1./max(1.-metalnessFactor,.5),pearlW);
      reflectedLight.directDiffuse*=diffLift;
      reflectedLight.indirectDiffuse*=diffLift;
      float pMain=exp(-pow((dMain-.12)/.30,2.));
      float pSide=exp(-pow((dSide-.22)/.32,2.));
      float pTop=exp(-pow((Rw.z-.66)/.36,2.));
      float pFres=pow(1.-clamp(dot(normal,geometryViewDir),0.,1.),2.2);
      float pPhase=dMain*2.4+dot(normal,geometryViewDir)*1.1;
      vec3 pTint=mix(vec3(.93,.97,1.07),vec3(1.07,.99,.90),.5+.5*sin(pPhase*2.6));
      float inkLuma=dot(diffuseColor.rgb,vec3(.299,.587,.114));
      float pMask=mix(.20,1.,smoothstep(.05,.50,inkLuma));
      float pFp=max(length(dFdx(vPaper)),length(dFdy(vPaper)));
      float pFade=1.-smoothstep(.15,.60,pFp*16000.);
      float pMica=pFade>.001?pow(paperNoise(vPaper*16000.+Rw*2.8),5.)*.28*pFade:0.;
      float pSheen=(.06+.52*pMain+.30*pSide+.20*pTop+.36*pFres+pMica*(.35+pMain+pSide))*finishBrightness*pMask;
      reflectedLight.indirectDiffuse*=mix(1.,.86+.18*pMain+.12*pSide,pearlW);
      reflectedLight.indirectSpecular=mix(reflectedLight.indirectSpecular,pTint*pSheen,pearlW*.90);
     }
    }
    if(roughnessFactor>.74&&metalnessFactor<.05){
     // Subtle cotton-linen pulp tonal micro-variation on textured art paper (visible gently at normal distance).
     float tW=smoothstep(.74,.80,roughnessFactor)*(1.-smoothstep(.02,.05,metalnessFactor));
     float tFp=max(length(dFdx(vPaper)),length(dFdy(vPaper)));
     float tFade=1.-smoothstep(.35,1.20,tFp*2000.);
     if(tW>.001&&tFade>.001){
      float weave=(paperNoise(vPaper*vec3(1400.,4600.,1400.))+paperNoise(vPaper*vec3(4600.,1400.,4600.))-1.);
      float inkKeep=mix(.35,1.,smoothstep(.05,.45,dot(diffuseColor.rgb,vec3(.299,.587,.114))));
      reflectedLight.indirectDiffuse*=1.+weave*.040*tFade*tW*inkKeep;
     }
    }
   `);
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
    ${kind==='print'?`float texArtW=smoothstep(.74,.80,roughnessFactor)*(1.-smoothstep(.02,.05,metalnessFactor));
    if(texArtW>.001){
     float tFade1=1.-smoothstep(.35,1.15,footprint*2200.);
     float tFade2=1.-smoothstep(.25,.80,footprint*6800.);
     float tInk=mix(.50,1.,smoothstep(.05,.45,dot(diffuseColor.rgb,vec3(.299,.587,.114))));
     if(tFade1>.001)fibre+=(paperNoise(vPaper*vec3(1400.,4600.,1400.))+paperNoise(vPaper*vec3(4600.,1400.,4600.))-1.)*.000050*tFade1*texArtW*tInk;
     if(tFade2>.001)fibre+=(paperNoise(vPaper*6800.)-.5)*.000048*tFade2*texArtW*tInk;
    }`:''}
    vec3 dp1=dFdx(-vViewPosition),dp2=dFdy(-vViewPosition);
    vec3 r1=cross(dp2,normal),r2=cross(normal,dp1);float det=dot(dp1,r1);
    if(abs(det)>1e-8)normal=normalize(abs(det)*normal-sign(det)*(dFdx(fibre)*r1+dFdy(fibre)*r2));
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
 const uniforms={finishLight:{value:new THREE.Vector3(-3.6,-3.8,6.5).normalize()},finishBrightness:{value:1},finishCenter:{value:new THREE.Vector3()},finishRadius:{value:1},finishHasHolo:{value:finishes.hasHolographic?1:0},finishHolo:{value:neutral},finishRelief:{value:neutral},finishRegion:{value:neutral},finishSelected:{value:0},finishStrength:{value:1},finishHeight:{value:2*(finishes.reliefHeightMM??.08)*.01},finishTexel:{value:new THREE.Vector2(1,1)},finishCavity:{value:finishes.cavityStrength??0},finishGradientRange:{value:finishes.gradientRange??1}};
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
  setLight:(direction,brightness=1,center=null,radius=1)=>{uniforms.finishLight.value.copy(direction).normalize();uniforms.finishBrightness.value=brightness;if(center)uniforms.finishCenter.value.copy(center);if(radius>0)uniforms.finishRadius.value=radius;},selectRegion,state:()=>({layers:config,selected,reliefStrength:uniforms.finishStrength.value,regionReady:uniforms.finishSelected.value===1}),
  dispose(){disposed=true;++token;selected='';regionTexture?.dispose();neutral.dispose();}
 };
}
