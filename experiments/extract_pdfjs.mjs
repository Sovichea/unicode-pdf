#!/usr/bin/env node

// Usage:
//   npm install skia-canvas
//   node experiments/extract_pdfjs.mjs /path/to/pdfjs/build/pdf.mjs file.pdf

import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [pdfjsPath, pdfPath] = process.argv.slice(2);
if (!pdfjsPath || !pdfPath) {
  console.error("usage: node extract_pdfjs.mjs <pdfjs/build/pdf.mjs> <file.pdf>");
  process.exit(2);
}

try {
  const { DOMMatrix, ImageData, Path2D } = await import("skia-canvas");
  globalThis.DOMMatrix ??= DOMMatrix;
  globalThis.ImageData ??= ImageData;
  globalThis.Path2D ??= Path2D;
} catch {
  console.error("Node extraction requires the optional package: npm install skia-canvas");
  process.exit(2);
}

Promise.try ??= (fn, ...args) => Promise.resolve().then(() => fn(...args));
Uint8Array.prototype.toHex ??= function toHex() {
  return Buffer.from(this.buffer, this.byteOffset, this.byteLength).toString("hex");
};

const pdfjs = await import(pathToFileURL(pdfjsPath));
const bytes = new Uint8Array(fs.readFileSync(pdfPath));
const document = await pdfjs.getDocument({
  data: bytes,
  disableWorker: true,
  useSystemFonts: true,
}).promise;

for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
  const page = await document.getPage(pageNumber);
  const content = await page.getTextContent({ disableNormalization: true });
  const text = content.items
    .filter((item) => "str" in item)
    .map((item) => item.str)
    .join("");
  process.stdout.write(text);
  if (pageNumber !== document.numPages) process.stdout.write("\n");
}
