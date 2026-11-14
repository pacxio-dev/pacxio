// Live fold geometry, using the original workbench hinge and crease algorithms.
// The caller owns materials, camera, clipping, shadows, and the exact closed asset.
import {BufferGeometry,DynamicDrawUsage,Group,InterleavedBuffer,InterleavedBufferAttribute,Mesh} from 'three';
import {allocStrips,panelBuffers,posePart} from './fold-math.js';

const ROLES=['print','paper','cut'],STRIDE=12;
function captureGL(){
 let bound;
 return {ARRAY_BUFFER:0,STATIC_DRAW:1,DYNAMIC_DRAW:2,createBuffer:()=>({}),
  bindBuffer(_,buffer){bound=buffer;},
  bufferData(_,data){bound.data=data;}};
}

export function createLiveFold(source,{materials,scale=.01}={}){
 if(source.version!==3||source.parts?.length!==1)throw Error('Live folding requires one v3 textured part');
 if(!Number.isFinite(scale)||scale<=0||!Number.isInteger(source.steps)||source.steps<1)throw Error('Invalid live-fold scale or steps');
 for(const role of ROLES)if(!materials?.[role])throw Error(`Missing fold material: ${role}`);
 const gl=captureGL(),parts=structuredClone(source.parts);
 for(const part of parts){
  for(const panel of part.panels)Object.assign(panel,panelBuffers(gl,part,panel));
  allocStrips(part);part.outerBuf=gl.createBuffer();part.innerBuf=gl.createBuffer();
 }
 const fold={parts,steps:source.steps,groundStep:source.ground_step,
  poseEase:Math.max(...parts.map(p=>p.poses[1].step)),s:source.steps,gl};
 posePart(fold);
 // Material coordinates remain attached to each original vertex during movement.
 // Closed crease coordinates match the previously exported display mesh.
 for(const part of parts){part.outerPaper=part.outer.slice();part.innerPaper=part.inner.slice();}
 const counts={print:0,paper:0,cut:0};
 for(const part of parts){
  for(const panel of part.panels){
   counts.print+=panel.frontCount;counts.paper+=panel.frontCount;
   counts.cut+=panel.restCount-panel.frontCount;
  }
  counts.print+=part.outerCount;counts.paper+=part.innerCount;
 }
 const group=new Group(),meshes={},buffers={},ibuffers={};group.name='live-fold';
 for(const role of ROLES){
  const arr=new Float32Array(counts[role]*STRIDE),ib=new InterleavedBuffer(arr,STRIDE).setUsage(DynamicDrawUsage),g=new BufferGeometry();
  for(const [name,size,offset] of [['position',3,0],['normal',3,3],['uv',2,6],['paperCoord',3,8],['bakedAO',1,11]])
   g.setAttribute(name,new InterleavedBufferAttribute(ib,size,offset));
  g.setDrawRange(0,counts[role]);
  const mesh=new Mesh(g,materials[role]);mesh.name=`fold-${role}`;mesh.castShadow=mesh.receiveShadow=true;
  // Bounds are recomputed for camera fitting below, not by traversing GPU attributes.
  mesh.frustumCulled=false;group.add(mesh);meshes[role]=mesh;buffers[role]=arr;ibuffers[role]=ib;
 }
 const lo=[0,0,0],hi=[0,0,0],vertices=new Float64Array(3*STRIDE);
 let progress=NaN,disposed=false,updates=0;
 function updateBuffer(role,part,data,start,count,matrix,paperData=data){
  const arr=buffers[role];let dst=writeOffsets[role];
  for(let j=start*8;j<(start+count)*8;j+=24){
   for(let v=0;v<3;v++){
    const a=j+v*8,d=v*STRIDE,x=data[a],y=data[a+1],z=data[a+2],nx=data[a+5],ny=data[a+6],nz=data[a+7];
    if(matrix){
     vertices[d]=(matrix[0]*x+matrix[4]*y+matrix[8]*z+matrix[12])*scale;
     vertices[d+1]=(matrix[1]*x+matrix[5]*y+matrix[9]*z+matrix[13])*scale;
     vertices[d+2]=(matrix[2]*x+matrix[6]*y+matrix[10]*z+matrix[14])*scale;
     vertices[d+3]=matrix[0]*nx+matrix[4]*ny+matrix[8]*nz;
     vertices[d+4]=matrix[1]*nx+matrix[5]*ny+matrix[9]*nz;
     vertices[d+5]=matrix[2]*nx+matrix[6]*ny+matrix[10]*nz;
    }else{
     vertices[d]=x*scale;vertices[d+1]=y*scale;vertices[d+2]=z*scale;
     vertices[d+3]=nx;vertices[d+4]=ny;vertices[d+5]=nz;
    }
    vertices[d+6]=data[a+3];vertices[d+7]=1-data[a+4];
    vertices[d+8]=paperData[a]*.001;vertices[d+9]=paperData[a+1]*.001;vertices[d+10]=paperData[a+2]*.001;
    if(role==='cut'){
     vertices[d+6]=vertices[d+8]/.0031;
     const caliper=part.caliper*.001;vertices[d+7]=(vertices[d+10]+caliper)/caliper;
    }
    vertices[d+11]=1; // Never carry baked closed-box occlusion onto unfolding panels.
   }
   // Match explicit paper normals for lighting and shadow-map face orientation.
   const bx=vertices[12]-vertices[0],by=vertices[13]-vertices[1],bz=vertices[14]-vertices[2];
   const cx=vertices[24]-vertices[0],cy=vertices[25]-vertices[1],cz=vertices[26]-vertices[2];
   const reverse=(by*cz-bz*cy)*vertices[3]+(bz*cx-bx*cz)*vertices[4]+(bx*cy-by*cx)*vertices[5]<0;
   for(let v=0;v<3;v++){
    const d=(reverse&&v>0?3-v:v)*STRIDE;
    for(let k=0;k<STRIDE;k++)arr[dst+k]=vertices[d+k];
    for(let k=0;k<3;k++){lo[k]=Math.min(lo[k],arr[dst+k]);hi[k]=Math.max(hi[k],arr[dst+k]);}
    dst+=STRIDE;
   }
  }
  writeOffsets[role]=dst;
 }
 const writeOffsets={print:0,paper:0,cut:0};
 function state(){return {progress,steps:fold.steps,poseEase:fold.poseEase,ground:fold.ground,
  groundPlaneConstant:.001*scale,size:fold.size.map(v=>v*scale),
  bounds:{min:lo.slice(),max:hi.slice()},center:fold.size.map(v=>v*scale/2),
  vertices:counts.print+counts.paper+counts.cut,triangles:(counts.print+counts.paper+counts.cut)/3,updates};}
 function setProgress(value){
  if(disposed)throw Error('Live fold has been disposed');
  if(!Number.isFinite(value))throw Error('Fold progress must be finite');
  value=Math.max(0,Math.min(1,value));if(value===progress)return state();
  progress=value;fold.s=value*fold.steps;posePart(fold);
  for(let k=0;k<3;k++){lo[k]=Infinity;hi[k]=-Infinity;}
  for(const role of ROLES)writeOffsets[role]=0;
  for(const part of parts){
   for(const panel of part.panels){
    const data=panel.buf.data,n=panel.frontCount;
    updateBuffer('print',part,data,0,n,panel.model);
    updateBuffer('paper',part,data,n,n,panel.model);
    updateBuffer('cut',part,data,2*n,panel.restCount-n,panel.model);
   }
   updateBuffer('print',part,part.outer,0,part.outerCount,null,part.outerPaper);
   updateBuffer('paper',part,part.inner,0,part.innerCount,null,part.innerPaper);
  }
  for(const role of ROLES){
   if(writeOffsets[role]!==buffers[role].length)throw Error('Fold vertex count changed unexpectedly');
   ibuffers[role].needsUpdate=true;
  }
  updates++;return state();
 }
 setProgress(1);
 return {group,meshes,setProgress,state,
  debugBuffers:()=>Object.fromEntries(ROLES.map(role=>[role,{array:buffers[role],stride:STRIDE,count:counts[role]}])),
  dispose(){if(disposed)return;disposed=true;for(const mesh of Object.values(meshes))mesh.geometry.dispose();group.removeFromParent();}};
}
