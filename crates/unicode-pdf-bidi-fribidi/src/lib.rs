//! Runtime-loaded GNU `FriBidi` adapter for `unicode-pdf`.
//!
//! FFI is isolated in this crate. The rest of the project only depends on the
//! safe [`unicode_pdf_bidi::BidiResolver`] interface.

#![allow(unsafe_code)]
#![deny(unsafe_op_in_unsafe_fn)]

#[cfg(unix)]
mod unix;

#[cfg(unix)]
pub use unix::FriBidiResolver;

#[cfg(not(unix))]
compile_error!("unicode-pdf-bidi-fribidi currently supports Unix-like systems only");
