#!/usr/bin/env python3
"""Exercise PDF.js TextLayer through a real browser Range/Selection implementation."""

from __future__ import annotations

import argparse
import base64
import json
import shutil
from pathlib import Path

TEXT_LAYER_CSS = r"""
html, body { margin: 0; padding: 0; background: #ddd; }
#page { position: relative; background: white; overflow: hidden; }
#pageImage { position: absolute; inset: 0; width: 100%; height: 100%; }
.textLayer {
  position: absolute;
  inset: 0;
  overflow: clip;
  line-height: 1;
  letter-spacing: normal;
  word-spacing: normal;
  transform-origin: 0 0;
  --min-font-size: 1;
  --total-scale-factor: 1;
  --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
  --min-font-size-inv: calc(1 / var(--min-font-size));
}
.textLayer span, .textLayer br {
  color: transparent;
  position: absolute;
  white-space: pre;
  cursor: text;
  transform-origin: 0 0;
  user-select: text;
  -webkit-user-select: text;
  -moz-user-select: text;
}
.textLayer > :not(.markedContent),
.textLayer .markedContent span:not(.markedContent) {
  --font-height: 0;
  font-size: calc(var(--text-scale-factor) * var(--font-height));
  --scale-x: 1;
  --rotate: 0deg;
  transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
}
.textLayer .markedContent { display: contents; }
::selection { background: rgb(116 151 215 / 0.72); }
"""


def discover_executable(browser: str) -> str | None:
    names = (
        ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]
        if browser == "chromium"
        else ["firefox", "firefox-esr"]
    )
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def image_data_uri(path: Path | None) -> str | None:
    if path is None:
        return None
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def run_probe(
    *,
    pdfjs_main: Path,
    text_content: dict,
    cases: list[dict],
    page_width: float,
    page_height: float,
    browser_name: str,
    executable: str | None,
    screenshot_dir: Path | None = None,
    page_image: Path | None = None,
) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:  # pragma: no cover - depends on developer environment
        raise RuntimeError("Python package 'playwright' is required") from error

    pdfjs_source = pdfjs_main.read_text(encoding="utf-8")
    pdfjs_source += "\nglobalThis.__UNICODE_PDF_PDFJS = { TextLayer, version };\n"
    backdrop = image_data_uri(page_image)
    screenshot_dir and screenshot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser_type = playwright.chromium if browser_name == "chromium" else playwright.firefox
        launch_args = ["--no-sandbox"] if browser_name == "chromium" else []
        launch_options: dict = {"headless": True, "args": launch_args}
        if executable:
            launch_options["executable_path"] = executable
        browser = browser_type.launch(**launch_options)
        page = browser.new_page(
            viewport={
                "width": max(800, int(page_width) + 80),
                "height": max(900, int(page_height) + 80),
            },
            device_scale_factor=1,
        )
        image_html = f'<img id="pageImage" src="{backdrop}">' if backdrop else ""
        page.set_content(
            f"<style>{TEXT_LAYER_CSS}</style>"
            f'<div id="page" style="width:{page_width}px;height:{page_height}px">'
            f'{image_html}<div id="text" class="textLayer"></div></div>'
        )
        page.add_script_tag(content=pdfjs_source, type="module")
        page.wait_for_function("globalThis.__UNICODE_PDF_PDFJS !== undefined")
        page.evaluate(
            """async ({ content, pageWidth, pageHeight }) => {
              const { TextLayer } = globalThis.__UNICODE_PDF_PDFJS;
              const container = document.querySelector("#text");
              const viewport = {
                scale: 1,
                rotation: 0,
                rawDims: { pageWidth, pageHeight, pageX: 0, pageY: 0 },
              };
              const layer = new TextLayer({ textContentSource: content, container, viewport });
              await layer.render();
              globalThis.__UNICODE_PDF_TEXT_LAYER = layer;
            }""",
            {"content": text_content, "pageWidth": page_width, "pageHeight": page_height},
        )

        results = page.evaluate(
            """cases => {
              const root = document.querySelector("#text");
              const base = root.getBoundingClientRect();
              const leaves = [...root.querySelectorAll('span[role="presentation"]')];
              const nodes = [];
              let fullText = "";
              for (const span of leaves) {
                const node = span.firstChild;
                if (!node) continue;
                nodes.push({ node, start: fullText.length, end: fullText.length + node.data.length });
                fullText += node.data;
              }
              function pointAt(offset, isEnd) {
                for (const entry of nodes) {
                  if (offset < entry.end || (isEnd && offset === entry.end)) {
                    return [entry.node, Math.max(0, Math.min(entry.node.length, offset - entry.start))];
                  }
                }
                const last = nodes.at(-1);
                return [last.node, last.node.length];
              }
              return cases.map(testCase => {
                const start = fullText.indexOf(testCase.needle);
                if (start < 0) {
                  return { name: testCase.name, needle: testCase.needle, fullText, error: "needle not found" };
                }
                const end = start + testCase.needle.length;
                const [startNode, startOffset] = pointAt(start, false);
                const [endNode, endOffset] = pointAt(end, true);
                const range = document.createRange();
                range.setStart(startNode, startOffset);
                range.setEnd(endNode, endOffset);
                const selection = getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
                const rects = [...range.getClientRects()].map(rect => [
                  rect.left - base.left,
                  rect.top - base.top,
                  rect.right - base.left,
                  rect.bottom - base.top,
                ]);
                let union = null;
                for (const rect of rects) {
                  union = union === null ? [...rect] : [
                    Math.min(union[0], rect[0]),
                    Math.min(union[1], rect[1]),
                    Math.max(union[2], rect[2]),
                    Math.max(union[3], rect[3]),
                  ];
                }
                const spans = leaves.map(span => {
                  const rect = span.getBoundingClientRect();
                  return {
                    text: span.textContent,
                    rect: [rect.left - base.left, rect.top - base.top, rect.right - base.left, rect.bottom - base.top],
                    scaleX: getComputedStyle(span).getPropertyValue("--scale-x").trim(),
                  };
                });
                return {
                  name: testCase.name,
                  needle: testCase.needle,
                  fullText,
                  copied: selection.toString(),
                  rects,
                  union,
                  spans,
                };
              });
            }""",
            cases,
        )

        if screenshot_dir:
            for case in cases:
                page.evaluate(
                    """needle => {
                      const leaves = [...document.querySelectorAll('#text span[role="presentation"]')];
                      const nodes = [];
                      let full = "";
                      for (const span of leaves) {
                        const node = span.firstChild;
                        if (!node) continue;
                        nodes.push({ node, start: full.length, end: full.length + node.data.length });
                        full += node.data;
                      }
                      const start = full.indexOf(needle);
                      if (start < 0) return;
                      const end = start + needle.length;
                      const locate = (offset, isEnd) => {
                        for (const entry of nodes) {
                          if (offset < entry.end || (isEnd && offset === entry.end)) {
                            return [entry.node, Math.max(0, Math.min(entry.node.length, offset - entry.start))];
                          }
                        }
                        const last = nodes.at(-1);
                        return [last.node, last.node.length];
                      };
                      const [sn, so] = locate(start, false), [en, eo] = locate(end, true);
                      const range = document.createRange();
                      range.setStart(sn, so); range.setEnd(en, eo);
                      const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
                    }""",
                    case["needle"],
                )
                page.locator("#page").screenshot(path=str(screenshot_dir / f"{case['name']}.png"))

        version = page.evaluate("globalThis.__UNICODE_PDF_PDFJS.version")
        browser_version = browser.version
        browser.close()
    return {
        "pdfjs_version": version,
        "browser": browser_name,
        "browser_version": browser_version,
        "full_text": "" if not results else results[0].get("fullText", ""),
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdfjs-main", type=Path, required=True)
    parser.add_argument("--content", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--page-width", type=float, required=True)
    parser.add_argument("--page-height", type=float, required=True)
    parser.add_argument("--browser", choices=["chromium", "firefox"], default="chromium")
    parser.add_argument("--executable")
    parser.add_argument("--screenshot-dir", type=Path)
    parser.add_argument("--page-image", type=Path)
    args = parser.parse_args()

    executable = args.executable or discover_executable(args.browser)
    result = run_probe(
        pdfjs_main=args.pdfjs_main,
        text_content=json.loads(args.content.read_text(encoding="utf-8")),
        cases=json.loads(args.cases.read_text(encoding="utf-8"))["cases"],
        page_width=args.page_width,
        page_height=args.page_height,
        browser_name=args.browser,
        executable=executable,
        screenshot_dir=args.screenshot_dir,
        page_image=args.page_image,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
