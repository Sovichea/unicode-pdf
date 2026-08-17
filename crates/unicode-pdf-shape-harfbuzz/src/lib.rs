//! System `HarfBuzz` adapter for `unicode-pdf`.
//!
//! The crate loads `HarfBuzz` dynamically at runtime so the core project does not
//! depend on a particular Rust binding. All FFI is isolated here; callers use
//! the safe [`unicode_pdf_shape::TextShaper`] interface.

#![allow(unsafe_code)]
#![deny(unsafe_op_in_unsafe_fn)]

#[cfg(unix)]
mod unix;

#[cfg(unix)]
pub use unix::HarfBuzzShaper;

#[cfg(not(unix))]
compile_error!("unicode-pdf-shape-harfbuzz currently supports Unix-like systems only");
