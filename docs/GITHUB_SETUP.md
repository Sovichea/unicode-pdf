# GitHub repository setup

Suggested repository name:

```text
unicode-pdf
```

Suggested GitHub description:

```text
Unicode-correct PDF generation for Rust with complex-script shaping, logical text preservation, tagged PDFs, and cross-reader conformance.
```

Suggested topics:

```text
rust
pdf
unicode
typography
text-shaping
harfrust
harfbuzz
bidi
khmer
complex-scripts
pdf-generation
accessibility
```

Recommended initial settings:

- Public repository.
- Do not ask GitHub to create a README, `.gitignore`, or license when importing this source tree because they are already included.
- Enable Issues and Discussions if reader interoperability reports will be collected publicly.
- Enable branch protection after the first CI run succeeds.
- Require the pure-Rust and native-reference Rust CI jobs before merging shaping/PDF changes.
