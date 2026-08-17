#!/usr/bin/env node

// Dump one page of PDF.js TextContent for stock or Typsastra logical-text mode.
// This uses the real worker extraction path even though it runs inside Node.

import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [pdfjsPath, pdfPath, outPath, mode = "stock"] = process.argv.slice(2);
if (!pdfjsPath || !pdfPath || !outPath || !["stock", "logical"].includes(mode)) {
  console.error(
    "usage: node pdfjs_text_content.mjs <pdfjs/build/pdf.mjs> <file.pdf> <out.json> [stock|logical]"
  );
  process.exit(2);
}

class DOMMatrix {
  constructor(init) {
    this.a = 1;
    this.b = 0;
    this.c = 0;
    this.d = 1;
    this.e = 0;
    this.f = 0;
    if (Array.isArray(init) && init.length >= 6) {
      [this.a, this.b, this.c, this.d, this.e, this.f] = init;
    }
  }
}

globalThis.DOMMatrix ??= DOMMatrix;
globalThis.ImageData ??= class ImageData {};
globalThis.Path2D ??= class Path2D {};
Promise.try ??= (fn, ...args) => Promise.resolve().then(() => fn(...args));
Math.sumPrecise ??= (values) => Array.from(values).reduce((sum, value) => sum + value, 0);
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
const page = await document.getPage(1);
const params = {
  includeMarkedContent: true,
  disableNormalization: true,
};
if (mode === "logical") {
  params.preserveLogicalText = true;
}
const content = await page.getTextContent(params);
fs.writeFileSync(outPath, `${JSON.stringify(content, null, 2)}\n`);
