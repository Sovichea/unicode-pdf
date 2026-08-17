#!/usr/bin/env python3
"""Compare compiler expected selection geometry with reader text boxes."""
from __future__ import annotations
import argparse, json, math, subprocess, xml.etree.ElementTree as ET
from pathlib import Path


def rect_overlap(a, b):
    x0=max(a[0],b[0]); y0=max(a[1],b[1]); x1=min(a[2],b[2]); y1=min(a[3],b[3])
    if x1<=x0 or y1<=y0: return 0.0
    inter=(x1-x0)*(y1-y0)
    area=max((a[2]-a[0])*(a[3]-a[1]), 1e-9)
    return inter/area


def poppler_boxes(pdf_path):
    proc=subprocess.run(["pdftotext","-bbox",str(pdf_path),"-"],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    root=ET.fromstring(proc.stdout.decode("utf-8","replace"))
    out=[]
    pages=root.findall(".//{*}page")
    for pi,page in enumerate(pages):
        h=float(page.attrib["height"])
        for word in page.findall(".//{*}word"):
            x0=float(word.attrib["xMin"]); yt=float(word.attrib["yMin"]); x1=float(word.attrib["xMax"]); yb=float(word.attrib["yMax"])
            out.append({"page":pi,"text":"".join(word.itertext()),"rect":[x0,h-yb,x1,h-yt]})
    return out

def mupdf_boxes(pdf_path):
    import fitz
    doc=fitz.open(pdf_path); out=[]
    for pi,page in enumerate(doc):
        h=page.rect.height
        raw=page.get_text("rawdict")
        for block in raw.get("blocks",[]):
            for line in block.get("lines",[]):
                for span in line.get("spans",[]):
                    for ch in span.get("chars",[]):
                        x0,y0,x1,y1=ch["bbox"]
                        out.append({"page":pi,"text":ch.get("c",""),"rect":[x0,h-y1,x1,h-y0]})
    return out


def pdfium_boxes(pdf_path):
    import pypdfium2 as pdfium
    doc=pdfium.PdfDocument(pdf_path); out=[]
    for pi,page in enumerate(doc):
        tp=page.get_textpage(); n=tp.count_chars()
        for i in range(n):
            try:
                box=tp.get_charbox(i)
                text=tp.get_text_range(i,1)
            except Exception:
                continue
            out.append({"page":pi,"text":text,"rect":[float(box[0]),float(box[1]),float(box[2]),float(box[3])]})
    return out


def load_pdfjs_boxes(path):
    data=json.loads(Path(path).read_text())
    return data["boxes"] if isinstance(data,dict) else data


def evaluate(expected, boxes):
    units=[u for u in expected["units"] if not u["unicode"].isspace()]
    scores=[]; misses=[]
    for u in units:
        er=[u["x0"],u["y0"],u["x1"],u["y1"]]
        candidates=[b for b in boxes if b["page"]==u["page"]]
        best=max((rect_overlap(er,b["rect"]) for b in candidates), default=0.0)
        scores.append(best)
        if best < 0.10:
            misses.append({"source_start":u["source_start"],"source_end":u["source_end"],"unicode":u["unicode"],"page":u["page"],"best_overlap":best})
    covered=sum(s>=0.10 for s in scores)
    return {
        "units":len(units),
        "covered":covered,
        "coverage": covered/len(units) if units else 1.0,
        "mean_best_overlap": sum(scores)/len(scores) if scores else 1.0,
        "misses":misses[:20],
        "reader_box_count":len(boxes),
    }


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("expected"); ap.add_argument("pdf")
    ap.add_argument("--pdfjs-json"); ap.add_argument("--out", required=True)
    args=ap.parse_args()
    expected=json.loads(Path(args.expected).read_text())
    results={}
    for name,fn in [("poppler",poppler_boxes),("mupdf",mupdf_boxes),("pdfium",pdfium_boxes)]:
        try: results[name]=evaluate(expected,fn(args.pdf))
        except Exception as e: results[name]={"error":repr(e)}
    if args.pdfjs_json:
        results["pdfjs"]=evaluate(expected,load_pdfjs_boxes(args.pdfjs_json))
    Path(args.out).write_text(json.dumps(results,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
