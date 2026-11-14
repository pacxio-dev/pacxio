// Reversible packing for local display assets. No quantization or GPU format changes.
// Header and reconstructed payload use little-endian byte order on every host.
const HEADER_BYTES=16,MAX_BYTES=128*1024*1024;
const CODECS={raw:0,'rgba16-rdelta-shuffle8':1};
const MAGIC=[80,88,65,49]; // PXA1
function bytes(input){
 if(input instanceof ArrayBuffer)return new Uint8Array(input);
 if(ArrayBuffer.isView(input))return new Uint8Array(input.buffer,input.byteOffset,input.byteLength);
 throw new TypeError('Expected binary asset bytes');
}
function codecId(name){if(!Object.hasOwn(CODECS,name))throw new Error('Unsupported packed asset codec');return CODECS[name];}
function checkedLength(value){if(!Number.isInteger(value)||value<=0||value>MAX_BYTES)throw new Error('Invalid packed asset size');return value;}
function transformRGBA16(source){
 if(source.length%8)throw new Error('RGBA16 data must contain whole pixels');
 const count=source.length/8,out=new Uint8Array(source.length);
 for(let i=0;i<count;i++){
  const j=i*8,r=source[j]|source[j+1]<<8,g=((source[j+2]|source[j+3]<<8)-r)&65535,b=((source[j+4]|source[j+5]<<8)-r)&65535;
  out[i]=r&255;out[count+i]=r>>>8;
  out[2*count+i]=g&255;out[3*count+i]=g>>>8;
  out[4*count+i]=b&255;out[5*count+i]=b>>>8;
  out[6*count+i]=source[j+6];out[7*count+i]=source[j+7];
 }
 return out;
}
function restoreRGBA16(source){
 if(source.length%8)throw new Error('RGBA16 data must contain whole pixels');
 const count=source.length/8,out=new Uint8Array(source.length);
 for(let i=0;i<count;i++){
  const j=i*8,r=source[i]|source[count+i]<<8,g=((source[2*count+i]|source[3*count+i]<<8)+r)&65535,b=((source[4*count+i]|source[5*count+i]<<8)+r)&65535;
  out[j]=r&255;out[j+1]=r>>>8;out[j+2]=g&255;out[j+3]=g>>>8;out[j+4]=b&255;out[j+5]=b>>>8;
  out[j+6]=source[6*count+i];out[j+7]=source[7*count+i];
 }
 return out;
}
export function encodeAssetPayload(input,codec='raw'){
 const raw=bytes(input),length=checkedLength(raw.length),id=codecId(codec);
 const data=id===0?raw:transformRGBA16(raw),out=new Uint8Array(HEADER_BYTES+data.length),h=new DataView(out.buffer);
 out.set(MAGIC);h.setUint8(4,1);h.setUint8(5,id);h.setUint16(6,0,true);
 h.setUint32(8,length,true);h.setUint32(12,data.length,true);out.set(data,HEADER_BYTES);return out;
}
export function decodeAssetPayload(input,descriptor){
 const data=bytes(input),length=checkedLength(descriptor.decodedByteLength),id=codecId(descriptor.codec);
 if(descriptor.format!=='pacxio-packed-gzip'||descriptor.version!==1)throw new Error('Unsupported packed asset format');
 if(data.length!==HEADER_BYTES+length)throw new Error('Packed asset length mismatch');
 const h=new DataView(data.buffer,data.byteOffset,data.byteLength);
 if(MAGIC.some((v,i)=>data[i]!==v)||h.getUint8(4)!==1||h.getUint8(5)!==id||h.getUint16(6,true)!==0)throw new Error('Invalid packed asset header');
 if(h.getUint32(8,true)!==length||h.getUint32(12,true)!==length)throw new Error('Packed asset dimensions mismatch');
 const payload=data.subarray(HEADER_BYTES);return id===0?payload.slice().buffer:restoreRGBA16(payload).buffer;
}
export async function decodePackedAsset(input,descriptor){
 const length=checkedLength(descriptor.decodedByteLength);
 // Bound decompression by the expected size, before allocating arbitrary output.
 const source=input instanceof ReadableStream?input:new Blob([bytes(input)]).stream();
 const reader=source.pipeThrough(new DecompressionStream('gzip')).getReader(),inflated=new Uint8Array(HEADER_BYTES+length);
 let offset=0;
 try{
  while(true){const {value,done}=await reader.read();if(done)break;if(offset+value.length>inflated.length)throw new Error('Packed asset exceeds expected size');inflated.set(value,offset);offset+=value.length;}
 }catch(error){await reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}
 if(offset!==inflated.length)throw new Error('Truncated packed asset');
 const decoded=decodeAssetPayload(inflated,descriptor);
 if(!/^[a-f0-9]{64}$/.test(descriptor.sha256||''))throw new Error('Missing packed asset checksum');
 const digest=await crypto.subtle.digest('SHA-256',decoded),hex=Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,'0')).join('');
 if(hex!==descriptor.sha256)throw new Error('Packed asset checksum mismatch');
 return decoded;
}
