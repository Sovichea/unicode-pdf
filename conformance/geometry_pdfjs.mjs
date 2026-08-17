#!/usr/bin/env node
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const [pdfjsPath,pdfPath,outPath]=process.argv.slice(2);
if(!pdfjsPath||!pdfPath||!outPath){console.error('usage: node geometry_pdfjs.mjs <pdfjs.mjs> <pdf> <out.json>');process.exit(2);}
class DOMMatrix { constructor(init){this.a=1;this.b=0;this.c=0;this.d=1;this.e=0;this.f=0;if(Array.isArray(init)&&init.length>=6)[this.a,this.b,this.c,this.d,this.e,this.f]=init;} }
globalThis.DOMMatrix ??= DOMMatrix;
globalThis.ImageData ??= class ImageData {};
globalThis.Path2D ??= class Path2D {};
Promise.try ??= (fn,...args)=>Promise.resolve().then(()=>fn(...args));
Math.sumPrecise ??= (values)=>Array.from(values).reduce((s,v)=>s+v,0);
Uint8Array.prototype.toHex ??= function toHex(){return Buffer.from(this.buffer,this.byteOffset,this.byteLength).toString('hex');};
const pdfjs=await import(pathToFileURL(pdfjsPath));
const bytes=new Uint8Array(fs.readFileSync(pdfPath));
const document=await pdfjs.getDocument({data:bytes,disableWorker:true,useSystemFonts:true}).promise;
const boxes=[];
for(let pageNumber=1;pageNumber<=document.numPages;pageNumber+=1){
 const page=await document.getPage(pageNumber); const content=await page.getTextContent({disableNormalization:true});
 for(const item of content.items){
  if(!('str' in item)) continue;
  const t=item.transform; const x=t[4], y=t[5]; const h=Math.abs(item.height||t[3]||0); const w=Math.abs(item.width||0);
  boxes.push({page:pageNumber-1,text:item.str,rect:[x,y-h*0.25,x+w,y+h*0.85]});
 }
}
fs.writeFileSync(outPath,JSON.stringify({version:pdfjs.version??'unknown',boxes},null,2));
