# Crate distribution

## Public packages

The repository intentionally keeps a small crates.io surface:

- `unicode-pdf`: published library and stable façade.
- `unicode-pdf-cli`: development CLI with `publish = false`.

The former research workspace crates have been folded into modules of `unicode-pdf`. This lets internal architecture evolve during the alpha series without forcing every internal package to become a separately versioned public dependency.

## Default dependencies

Normal consumers use the pure-Rust feature set:

```toml
[dependencies]
unicode-pdf = "0.1.0-alpha.3"
```

which enables:

```text
harfrust 0.13
unicode-bidi 0.3
```

Cargo resolves their transitive dependencies from crates.io on the consuming developer's machine.

## Reference native backends

For conformance work on Unix, the library retains optional system backends:

```toml
unicode-pdf = {
  version = "0.1.0-alpha.3",
  default-features = false,
  features = ["system-harfbuzz", "system-fribidi"],
}
```

This path is useful in the network-restricted development sandbox and for comparing pure-Rust shaping against HarfBuzz/FriBidi.

## Cargo.lock policy

The source bundle intentionally does not ship a hand-generated `Cargo.lock`. Cargo will resolve and create a normal lockfile on the first build of the repository. Library consumers never depend on this repository lockfile when using `unicode-pdf` from crates.io.

Before a tagged release, maintainers should generate and commit a real lockfile from an internet-connected machine and run both backend configurations in CI.
