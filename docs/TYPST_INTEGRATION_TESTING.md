# Testing the Typst integration

The integration is built in the `typst-unicode-pdf` checkout and uses the
local Krilla and `unicode-pdf` worktrees through Cargo path dependencies.

## Build the patched Typst CLI

From the Typst checkout:

```powershell
cd C:\Users\Sovichea\Documents\git\typst-unicode-pdf
cargo build -p typst-cli
.\target\debug\typst.exe --version
```

The expected version for this integration baseline is Typst 0.15.1.

## Compile your document

System fonts are discovered automatically. Add `--font-path` when the document
uses fonts stored elsewhere:

```powershell
.\target\debug\typst.exe compile `
  C:\path\to\document.typ `
  C:\path\to\document.pdf `
  --font-path C:\path\to\fonts
```

On Windows, multiple font directories can be supplied in one argument,
separated by semicolons:

```powershell
--font-path "C:\fonts\project;C:\fonts\shared"
```

For a document that only uses installed system fonts, omit `--font-path`:

```powershell
.\target\debug\typst.exe compile C:\path\to\document.typ C:\path\to\document.pdf
```

## Run the repository fixture

```powershell
.\target\debug\typst.exe compile `
  unicode-pdf-fixture.typ `
  target\typst-unicode-pdf.pdf `
  --font-path C:\Users\Sovichea\Documents\git\krilla\assets\fonts
```

The fixture covers Khmer, Devanagari, Arabic/RTL, Thai, CJK fallback, Latin
ligatures, spaces, punctuation, and styled text.

## Verify logical extraction with the CI PDF.js patch

From the `unicode-pdf` checkout, patch the pinned PDF.js distribution once:

```powershell
cd C:\Users\Sovichea\Documents\git\unicode-pdf
npm install --prefix target\conformance-node --no-save pdfjs-dist@6.2.108
python integrations\pdfjs\apply_typsastra_patch.py `
  --input target\conformance-node\node_modules\pdfjs-dist `
  --output target\typst-pdfjs-patched
```

Then extract one page in logical-text mode:

```powershell
node conformance\pdfjs_text_content.mjs `
  target\typst-pdfjs-patched\build\pdf.mjs `
  C:\path\to\document.pdf `
  target\document.logical.json `
  logical
```

Inspect every `str` field in `target\document.logical.json`. Stock PDF.js mode
is not an acceptance oracle for complex-script logical order; use the patched
`logical` mode used by CI.

## Rebuild after integration changes

```powershell
cd C:\Users\Sovichea\Documents\git\typst-unicode-pdf
cargo build -p typst-cli
```

Cargo detects changes in the path-patched Krilla and `unicode-pdf` crates and
rebuilds the affected exporter automatically.
