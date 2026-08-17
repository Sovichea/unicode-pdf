#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FAMILIES=["Noto Sans","Noto Sans Khmer","Noto Sans Arabic","Noto Sans Devanagari"]

def run(cmd): return subprocess.run(cmd,cwd=ROOT,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
def font(fam):
 p=run(["fc-match","-f","%{file}\\n",fam]).stdout.strip().splitlines()[0]
 if not Path(p).is_file(): raise RuntimeError(f"missing font {fam}")
 return p

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--pdfjs-dist',type=Path,required=True); ap.add_argument('--out',type=Path,default=ROOT/'target/geometry-conformance'); ap.add_argument('--check-baseline',action='store_true'); a=ap.parse_args()
 cases=json.loads((ROOT/'conformance/geometry_cases.json').read_text()); baseline=json.loads((ROOT/'conformance/geometry_baseline.json').read_text()); a.out.mkdir(parents=True,exist_ok=True)
 fonts=[font(x) for x in FAMILIES]; allres={}; failed=[]
 pdfjs=a.pdfjs_dist; pdfjs=pdfjs/'build/pdf.mjs' if pdfjs.is_dir() else pdfjs
 for c in cases:
  name=c['name']; fixture=ROOT/c['fixture']; pdf=a.out/f'{name}.pdf'; expected=a.out/f'{name}.geometry.json'; pj=a.out/f'{name}.pdfjs.json'; result=a.out/f'{name}.results.json'
  run(['cargo','run','-q','-p','unicode-pdf-cli','--','emit-layout-pdf',str(fixture),str(pdf),*fonts])
  run(['cargo','run','-q','-p','unicode-pdf-cli','--','dump-layout-geometry',str(fixture),str(expected),*fonts])
  run(['node',str(ROOT/'conformance/geometry_pdfjs.mjs'),str(pdfjs),str(pdf),str(pj)])
  run(['python3',str(ROOT/'conformance/geometry_probe.py'),str(expected),str(pdf),'--pdfjs-json',str(pj),'--out',str(result)])
  res=json.loads(result.read_text()); allres[name]=res
  if a.check_baseline:
   for reader,minimum in baseline[name].items():
    cov=res.get(reader,{}).get('coverage',0)
    if cov < minimum: failed.append(f'{name}/{reader}: {cov:.4f} < {minimum:.4f}')
 report=['# Selection Geometry Conformance','', '| Fixture | Poppler | MuPDF | PDFium | PDF.js |','|---|---:|---:|---:|---:|']
 for c in cases:
  r=allres[c['name']]; report.append('| '+c['name']+' | '+' | '.join(f"{100*r[x]['coverage']:.2f}%" for x in ['poppler','mupdf','pdfium','pdfjs'])+' |')
 (a.out/'RESULTS.md').write_text('\n'.join(report)+'\n'); (a.out/'results.json').write_text(json.dumps(allres,ensure_ascii=False,indent=2)+'\n')
 print('\n'.join(report))
 if failed:
  print('\nBaseline regressions:',file=sys.stderr); print('\n'.join(failed),file=sys.stderr); return 1
 return 0
if __name__=='__main__': raise SystemExit(main())
