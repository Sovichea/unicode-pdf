#!/usr/bin/env node

// Extract text through PDF.js getTextContent(). This intentionally exercises
// stock PDF.js behavior and does not apply the project's diagnostic patch.

import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [pdfjsPath, pdfPath] = process.argv.slice(2);
if (!pdfjsPath || !pdfPath) {
  console.error("usage: node pdfjs.mjs <pdfjs/build/pdf.mjs> <file.pdf>");
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

const pages = [];
for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
  const page = await document.getPage(pageNumber);
  const content = await page.getTextContent({ disableNormalization: true });
  pages.push(
    content.items
      .filter((item) => "str" in item)
      .map((item) => item.str)
      .join("")
  );
}

process.stdout.write(JSON.stringify({
  version: pdfjs.version ?? "unknown",
  text: pages.join("\n"),
}));
