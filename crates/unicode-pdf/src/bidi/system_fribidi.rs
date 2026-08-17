//! Optional runtime-loaded system `FriBidi` backend.
use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::fmt;
use std::ptr;
use std::sync::Arc;

use crate::bidi::{BidiError, BidiParagraph, BidiResolver, BidiRun};
use crate::core::{SourceRange, TextDirection};

const RTLD_NOW: c_int = 2;
const RTLD_LOCAL: c_int = 0;
const FRIBIDI_PAR_ON: u32 = 0x0000_0040;
const FRIBIDI_MASK_RTL: u32 = 0x0000_0001;

unsafe extern "C" {
    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
    fn dlerror() -> *const c_char;
}

type FriBidiLog2Vis = unsafe extern "C" fn(
    *const u32,
    c_int,
    *mut u32,
    *mut u32,
    *mut c_int,
    *mut c_int,
    *mut i8,
) -> c_int;

struct FriBidiApi {
    handle: *mut c_void,
    log2vis: FriBidiLog2Vis,
}

// FriBidiApi is immutable after loading. The dynamic-library handle remains
// valid until Drop, and the function pointer refers to process-global code.
unsafe impl Send for FriBidiApi {}
unsafe impl Sync for FriBidiApi {}

impl Drop for FriBidiApi {
    fn drop(&mut self) {
        // SAFETY: handle came from a successful dlopen and is closed once.
        unsafe {
            let _ = dlclose(self.handle);
        }
    }
}

impl fmt::Debug for FriBidiApi {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("FriBidiApi").finish_non_exhaustive()
    }
}

impl FriBidiApi {
    fn load() -> Result<Self, BidiError> {
        let candidates = ["libfribidi.so.0", "libfribidi.so", "libfribidi.dylib"];
        let mut last_error = String::new();
        for candidate in candidates {
            let name = CString::new(candidate).expect("static library name has no NUL");
            // SAFETY: name is NUL terminated and flags are valid.
            let handle = unsafe { dlopen(name.as_ptr(), RTLD_NOW | RTLD_LOCAL) };
            if handle.is_null() {
                last_error = dynamic_error();
                continue;
            }
            let symbol = CString::new("fribidi_log2vis").expect("static symbol has no NUL");
            // SAFETY: handle is live and symbol is NUL terminated.
            let pointer = unsafe { dlsym(handle, symbol.as_ptr()) };
            if pointer.is_null() {
                last_error = dynamic_error();
                // SAFETY: handle was opened above and no API object owns it yet.
                unsafe {
                    let _ = dlclose(handle);
                }
                continue;
            }
            // SAFETY: FriBidi exports fribidi_log2vis with this C ABI.
            let log2vis = unsafe { std::mem::transmute::<*mut c_void, FriBidiLog2Vis>(pointer) };
            return Ok(Self { handle, log2vis });
        }
        Err(BidiError::BackendUnavailable(if last_error.is_empty() {
            "no FriBidi shared library found".to_owned()
        } else {
            last_error
        }))
    }
}

/// Runtime-loaded GNU `FriBidi` resolver.
#[derive(Clone, Debug)]
pub struct FriBidiResolver {
    api: Arc<FriBidiApi>,
}

impl FriBidiResolver {
    /// Loads the system `FriBidi` shared library.
    ///
    /// # Errors
    ///
    /// Returns [`BidiError::BackendUnavailable`] if `FriBidi` cannot be loaded.
    pub fn new() -> Result<Self, BidiError> {
        Ok(Self {
            api: Arc::new(FriBidiApi::load()?),
        })
    }
}

impl BidiResolver for FriBidiResolver {
    fn resolve(&self, text: &str) -> Result<BidiParagraph, BidiError> {
        if text.is_empty() {
            return Ok(BidiParagraph {
                base_direction: TextDirection::LeftToRight,
                runs: Vec::new(),
            });
        }

        let mut utf32 = Vec::with_capacity(text.chars().count());
        let mut byte_offsets = Vec::with_capacity(text.chars().count() + 1);
        for (byte_offset, ch) in text.char_indices() {
            byte_offsets.push(byte_offset);
            utf32.push(u32::from(ch));
        }
        byte_offsets.push(text.len());

        let len = c_int::try_from(utf32.len()).map_err(|_| BidiError::InputTooLarge)?;
        let mut base_dir = FRIBIDI_PAR_ON;
        let mut logical_to_visual = vec![0_i32; utf32.len()];
        let mut levels = vec![0_i8; utf32.len()];

        // SAFETY: all arrays contain len elements, pointers remain valid for the
        // call, and null output pointers are explicitly allowed by FriBidi.
        let ok = unsafe {
            (self.api.log2vis)(
                utf32.as_ptr(),
                len,
                &raw mut base_dir,
                ptr::null_mut(),
                logical_to_visual.as_mut_ptr(),
                ptr::null_mut(),
                levels.as_mut_ptr(),
            )
        };
        if ok == 0 {
            return Err(BidiError::BackendFailure);
        }

        let mut ranges = Vec::new();
        let mut start = 0_usize;
        while start < levels.len() {
            let level = levels[start];
            let mut end = start + 1;
            while end < levels.len() && levels[end] == level {
                end += 1;
            }
            let visual_start = logical_to_visual[start..end]
                .iter()
                .copied()
                .min()
                .ok_or(BidiError::BackendFailure)?;
            if visual_start < 0 {
                return Err(BidiError::BackendFailure);
            }
            ranges.push((
                start,
                end,
                level,
                usize::try_from(visual_start).map_err(|_| BidiError::BackendFailure)?,
            ));
            start = end;
        }

        let mut visual_keys: Vec<(usize, usize)> = ranges
            .iter()
            .enumerate()
            .map(|(index, (_, _, _, visual_start))| (index, *visual_start))
            .collect();
        visual_keys.sort_by_key(|(_, visual_start)| *visual_start);
        let mut visual_rank = vec![0_usize; ranges.len()];
        for (rank, (logical_index, _)) in visual_keys.into_iter().enumerate() {
            visual_rank[logical_index] = rank;
        }

        let mut runs = Vec::with_capacity(ranges.len());
        for (index, (char_start, char_end, level, _)) in ranges.into_iter().enumerate() {
            let start_byte = byte_offsets[char_start];
            let end_byte = byte_offsets[char_end];
            let direction = if level & 1 == 0 {
                TextDirection::LeftToRight
            } else {
                TextDirection::RightToLeft
            };
            runs.push(BidiRun {
                source_range: SourceRange::new(start_byte, end_byte)
                    .map_err(|_| BidiError::InvalidRunPartition)?,
                level: u8::try_from(level).map_err(|_| BidiError::BackendFailure)?,
                direction,
                visual_order: visual_rank[index],
            });
        }

        let paragraph = BidiParagraph {
            base_direction: if base_dir & FRIBIDI_MASK_RTL == 0 {
                TextDirection::LeftToRight
            } else {
                TextDirection::RightToLeft
            },
            runs,
        };
        paragraph.validate(text)?;
        Ok(paragraph)
    }
}

fn dynamic_error() -> String {
    // SAFETY: dlerror returns either null or a process-owned NUL-terminated string.
    let pointer = unsafe { dlerror() };
    if pointer.is_null() {
        "dynamic loader error".to_owned()
    } else {
        // SAFETY: non-null dlerror result is a NUL-terminated string.
        unsafe { CStr::from_ptr(pointer) }
            .to_string_lossy()
            .into_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_system_fribidi_when_required() {
        if std::env::var_os("UNICODE_PDF_REQUIRE_SYSTEM_FRIBIDI").is_some() {
            FriBidiResolver::new().expect("system FriBidi must be loadable");
        }
    }

    #[test]
    fn resolves_mixed_direction_runs_when_available() {
        let Ok(resolver) = FriBidiResolver::new() else {
            return;
        };
        let text = "Version 2: العربية 2026 | path";
        let paragraph = resolver.resolve(text).expect("FriBidi resolution");
        paragraph.validate(text).expect("valid partition");
        assert!(paragraph
            .runs
            .iter()
            .any(|run| run.direction == TextDirection::LeftToRight));
        assert!(paragraph
            .runs
            .iter()
            .any(|run| run.direction == TextDirection::RightToLeft));
        let mut visual_orders: Vec<usize> =
            paragraph.runs.iter().map(|run| run.visual_order).collect();
        visual_orders.sort_unstable();
        assert_eq!(visual_orders, (0..paragraph.runs.len()).collect::<Vec<_>>());
    }
}
