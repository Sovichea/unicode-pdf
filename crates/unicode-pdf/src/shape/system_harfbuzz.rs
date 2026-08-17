//! Optional runtime-loaded system `HarfBuzz` shaping backend.
use std::ffi::{c_char, c_int, c_uint, c_void, CStr, CString};
use std::fmt;
use std::ptr;
use std::slice;
use std::sync::Arc;

use crate::core::{logical_units_from_shaped_glyphs, LogicalTextRun, ShapedGlyph, TextDirection};
use crate::shape::{ShapeError, ShapeOptions, ShapeOutput, TextShaper};

const RTLD_NOW: c_int = 2;
const RTLD_LOCAL: c_int = 0;
const HB_MEMORY_MODE_DUPLICATE: c_uint = 0;
const HB_DIRECTION_LTR: c_uint = 4;
const HB_DIRECTION_RTL: c_uint = 5;

unsafe extern "C" {
    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
    fn dlerror() -> *const c_char;
}

type HbDestroyFunc = Option<unsafe extern "C" fn(*mut c_void)>;
type HbBlobCreate =
    unsafe extern "C" fn(*const c_char, c_uint, c_uint, *mut c_void, HbDestroyFunc) -> *mut c_void;
type HbBlobDestroy = unsafe extern "C" fn(*mut c_void);
type HbFaceCreate = unsafe extern "C" fn(*mut c_void, c_uint) -> *mut c_void;
type HbFaceDestroy = unsafe extern "C" fn(*mut c_void);
type HbFaceGetUpem = unsafe extern "C" fn(*mut c_void) -> c_uint;
type HbFontCreate = unsafe extern "C" fn(*mut c_void) -> *mut c_void;
type HbFontDestroy = unsafe extern "C" fn(*mut c_void);
type HbOtFontSetFuncs = unsafe extern "C" fn(*mut c_void);
type HbFontSetScale = unsafe extern "C" fn(*mut c_void, c_int, c_int);
type HbBufferCreate = unsafe extern "C" fn() -> *mut c_void;
type HbBufferDestroy = unsafe extern "C" fn(*mut c_void);
type HbBufferAddUtf8 = unsafe extern "C" fn(*mut c_void, *const c_char, c_int, c_uint, c_int);
type HbBufferGuessSegmentProperties = unsafe extern "C" fn(*mut c_void);
type HbBufferSetDirection = unsafe extern "C" fn(*mut c_void, c_uint);
type HbBufferGetDirection = unsafe extern "C" fn(*mut c_void) -> c_uint;
type HbShape = unsafe extern "C" fn(*mut c_void, *mut c_void, *const c_void, c_uint);
type HbBufferGetGlyphInfos = unsafe extern "C" fn(*mut c_void, *mut c_uint) -> *const HbGlyphInfo;
type HbBufferGetGlyphPositions =
    unsafe extern "C" fn(*mut c_void, *mut c_uint) -> *const HbGlyphPosition;

#[repr(C)]
#[derive(Clone, Copy)]
struct HbGlyphInfo {
    codepoint: u32,
    mask: u32,
    cluster: u32,
    var1: u32,
    var2: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct HbGlyphPosition {
    x_advance: i32,
    y_advance: i32,
    x_offset: i32,
    y_offset: i32,
    var: u32,
}

struct HarfBuzzApi {
    handle: *mut c_void,
    blob_create: HbBlobCreate,
    blob_destroy: HbBlobDestroy,
    face_create: HbFaceCreate,
    face_destroy: HbFaceDestroy,
    face_get_upem: HbFaceGetUpem,
    font_create: HbFontCreate,
    font_destroy: HbFontDestroy,
    ot_font_set_funcs: HbOtFontSetFuncs,
    font_set_scale: HbFontSetScale,
    buffer_create: HbBufferCreate,
    buffer_destroy: HbBufferDestroy,
    buffer_add_utf8: HbBufferAddUtf8,
    buffer_guess_segment_properties: HbBufferGuessSegmentProperties,
    buffer_set_direction: HbBufferSetDirection,
    buffer_get_direction: HbBufferGetDirection,
    shape: HbShape,
    buffer_get_glyph_infos: HbBufferGetGlyphInfos,
    buffer_get_glyph_positions: HbBufferGetGlyphPositions,
}

// HarfBuzzApi is immutable after loading and HarfBuzz function pointers are
// process-global entry points. The raw handle remains valid until Drop.
unsafe impl Send for HarfBuzzApi {}
unsafe impl Sync for HarfBuzzApi {}

impl Drop for HarfBuzzApi {
    fn drop(&mut self) {
        // SAFETY: `handle` came from a successful `dlopen` and is closed once.
        unsafe {
            let _ = dlclose(self.handle);
        }
    }
}

impl fmt::Debug for HarfBuzzApi {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("HarfBuzzApi").finish_non_exhaustive()
    }
}

impl HarfBuzzApi {
    fn load() -> Result<Self, ShapeError> {
        let candidates = ["libharfbuzz.so.0", "libharfbuzz.so", "libharfbuzz.dylib"];
        let mut last_error = String::new();

        for candidate in candidates {
            let name = CString::new(candidate).expect("static library name has no NUL");
            // SAFETY: `name` is NUL terminated and flags are valid for `dlopen`.
            let handle = unsafe { dlopen(name.as_ptr(), RTLD_NOW | RTLD_LOCAL) };
            if handle.is_null() {
                last_error = dynamic_error();
                continue;
            }

            // SAFETY: every symbol is checked for null and transmuted to the
            // exact function signature declared by the HarfBuzz C API.
            let loaded = unsafe { Self::from_handle(handle) };
            match loaded {
                Ok(api) => return Ok(api),
                Err(error) => {
                    // SAFETY: the partially loaded handle is still owned here.
                    unsafe {
                        let _ = dlclose(handle);
                    }
                    return Err(error);
                }
            }
        }

        Err(ShapeError::BackendUnavailable(if last_error.is_empty() {
            "HarfBuzz shared library was not found".to_owned()
        } else {
            last_error
        }))
    }

    unsafe fn from_handle(handle: *mut c_void) -> Result<Self, ShapeError> {
        macro_rules! load {
            ($symbol:literal, $ty:ty) => {{
                let name = CStr::from_bytes_with_nul(concat!($symbol, "\0").as_bytes())
                    .expect("static symbol name is NUL terminated");
                // SAFETY: caller owns a valid dynamic-library handle.
                let pointer = unsafe { dlsym(handle, name.as_ptr()) };
                if pointer.is_null() {
                    return Err(ShapeError::BackendUnavailable(format!(
                        "missing HarfBuzz symbol {}: {}",
                        $symbol,
                        dynamic_error()
                    )));
                }
                // SAFETY: HarfBuzz exports this symbol with the declared C ABI.
                unsafe { std::mem::transmute::<*mut c_void, $ty>(pointer) }
            }};
        }

        Ok(Self {
            handle,
            blob_create: load!("hb_blob_create", HbBlobCreate),
            blob_destroy: load!("hb_blob_destroy", HbBlobDestroy),
            face_create: load!("hb_face_create", HbFaceCreate),
            face_destroy: load!("hb_face_destroy", HbFaceDestroy),
            face_get_upem: load!("hb_face_get_upem", HbFaceGetUpem),
            font_create: load!("hb_font_create", HbFontCreate),
            font_destroy: load!("hb_font_destroy", HbFontDestroy),
            ot_font_set_funcs: load!("hb_ot_font_set_funcs", HbOtFontSetFuncs),
            font_set_scale: load!("hb_font_set_scale", HbFontSetScale),
            buffer_create: load!("hb_buffer_create", HbBufferCreate),
            buffer_destroy: load!("hb_buffer_destroy", HbBufferDestroy),
            buffer_add_utf8: load!("hb_buffer_add_utf8", HbBufferAddUtf8),
            buffer_guess_segment_properties: load!(
                "hb_buffer_guess_segment_properties",
                HbBufferGuessSegmentProperties
            ),
            buffer_set_direction: load!("hb_buffer_set_direction", HbBufferSetDirection),
            buffer_get_direction: load!("hb_buffer_get_direction", HbBufferGetDirection),
            shape: load!("hb_shape", HbShape),
            buffer_get_glyph_infos: load!("hb_buffer_get_glyph_infos", HbBufferGetGlyphInfos),
            buffer_get_glyph_positions: load!(
                "hb_buffer_get_glyph_positions",
                HbBufferGetGlyphPositions
            ),
        })
    }
}

fn dynamic_error() -> String {
    // SAFETY: `dlerror` returns either null or a NUL-terminated process-owned
    // string valid until the next dynamic-loader operation on this thread.
    unsafe {
        let pointer = dlerror();
        if pointer.is_null() {
            "dynamic loader error".to_owned()
        } else {
            CStr::from_ptr(pointer).to_string_lossy().into_owned()
        }
    }
}

/// Safe runtime-loaded adapter for the system `HarfBuzz` library.
#[derive(Clone, Debug)]
pub struct HarfBuzzShaper {
    api: Arc<HarfBuzzApi>,
}

impl HarfBuzzShaper {
    /// Loads the system `HarfBuzz` shared library.
    ///
    /// # Errors
    ///
    /// Returns [`ShapeError::BackendUnavailable`] if `HarfBuzz` or a required
    /// symbol cannot be loaded.
    pub fn new() -> Result<Self, ShapeError> {
        Ok(Self {
            api: Arc::new(HarfBuzzApi::load()?),
        })
    }
}

impl TextShaper for HarfBuzzShaper {
    #[allow(clippy::too_many_lines)]
    fn shape(
        &self,
        font_data: &[u8],
        text: &str,
        options: ShapeOptions,
    ) -> Result<ShapeOutput, ShapeError> {
        let font_len = c_uint::try_from(font_data.len()).map_err(|_| ShapeError::InputTooLarge)?;
        let text_len = c_int::try_from(text.len()).map_err(|_| ShapeError::InputTooLarge)?;

        // HB_MEMORY_MODE_DUPLICATE makes the blob own a private copy, so the
        // Rust slice does not need to outlive this function.
        // SAFETY: font_data is readable for font_len bytes; HarfBuzz duplicates it.
        let blob = unsafe {
            (self.api.blob_create)(
                font_data.as_ptr().cast::<c_char>(),
                font_len,
                HB_MEMORY_MODE_DUPLICATE,
                ptr::null_mut(),
                None,
            )
        };
        if blob.is_null() {
            return Err(ShapeError::InvalidFont);
        }
        let _blob = BlobGuard {
            pointer: blob,
            destroy: self.api.blob_destroy,
        };

        // SAFETY: blob is a valid HarfBuzz blob and the face index is caller supplied.
        let face = unsafe { (self.api.face_create)(blob, options.face_index) };
        if face.is_null() {
            return Err(ShapeError::InvalidFont);
        }
        let _face = FaceGuard {
            pointer: face,
            destroy: self.api.face_destroy,
        };

        // SAFETY: face is valid for the duration of this call.
        let units_per_em = unsafe { (self.api.face_get_upem)(face) };
        if units_per_em == 0 || units_per_em > i32::MAX as u32 {
            return Err(ShapeError::InvalidFont);
        }

        // SAFETY: face is valid and owned until after font destruction.
        let font = unsafe { (self.api.font_create)(face) };
        if font.is_null() {
            return Err(ShapeError::InvalidFont);
        }
        let _font = FontGuard {
            pointer: font,
            destroy: self.api.font_destroy,
        };

        // SAFETY: font is a valid HarfBuzz font.
        unsafe {
            (self.api.ot_font_set_funcs)(font);
            let scale = i32::try_from(units_per_em).map_err(|_| ShapeError::InvalidFont)?;
            (self.api.font_set_scale)(font, scale, scale);
        }

        // SAFETY: HarfBuzz owns the returned buffer until buffer_destroy.
        let buffer = unsafe { (self.api.buffer_create)() };
        if buffer.is_null() {
            return Err(ShapeError::InvalidBackendOutput);
        }
        let _buffer = BufferGuard {
            pointer: buffer,
            destroy: self.api.buffer_destroy,
        };

        // SAFETY: text points to text_len UTF-8 bytes for the duration of shape.
        unsafe {
            (self.api.buffer_add_utf8)(
                buffer,
                text.as_ptr().cast::<c_char>(),
                text_len,
                0,
                text_len,
            );
            match options.direction {
                TextDirection::LeftToRight => {
                    (self.api.buffer_set_direction)(buffer, HB_DIRECTION_LTR);
                }
                TextDirection::RightToLeft => {
                    (self.api.buffer_set_direction)(buffer, HB_DIRECTION_RTL);
                }
                TextDirection::Auto => {}
            }
            (self.api.buffer_guess_segment_properties)(buffer);
        }

        // SAFETY: buffer has valid segment properties after guessing.
        let direction_value = unsafe { (self.api.buffer_get_direction)(buffer) };
        let direction = match direction_value {
            HB_DIRECTION_LTR => TextDirection::LeftToRight,
            HB_DIRECTION_RTL => TextDirection::RightToLeft,
            other => return Err(ShapeError::UnsupportedDirection(other)),
        };

        // SAFETY: font and buffer are valid; no feature array is supplied.
        unsafe {
            (self.api.shape)(font, buffer, ptr::null(), 0);
        }

        let mut info_count = 0_u32;
        let mut position_count = 0_u32;
        // SAFETY: buffer remains alive and returned arrays are owned by it.
        let infos = unsafe { (self.api.buffer_get_glyph_infos)(buffer, &raw mut info_count) };
        // SAFETY: same as above.
        let positions =
            unsafe { (self.api.buffer_get_glyph_positions)(buffer, &raw mut position_count) };

        if info_count != position_count {
            return Err(ShapeError::InvalidBackendOutput);
        }
        let count = usize::try_from(info_count).map_err(|_| ShapeError::InputTooLarge)?;
        if count > 0 && (infos.is_null() || positions.is_null()) {
            return Err(ShapeError::InvalidBackendOutput);
        }

        let infos: &[HbGlyphInfo] = if count == 0 {
            &[]
        } else {
            // SAFETY: count is nonzero and HarfBuzz returned a non-null array
            // containing exactly `count` items.
            unsafe { slice::from_raw_parts(infos, count) }
        };
        let positions: &[HbGlyphPosition] = if count == 0 {
            &[]
        } else {
            // SAFETY: count is nonzero and HarfBuzz returned a non-null array
            // containing exactly `count` items.
            unsafe { slice::from_raw_parts(positions, count) }
        };

        let mut glyphs = Vec::with_capacity(count);
        let mut pen_x = 0_i64;
        let mut pen_y = 0_i64;

        for (info, position) in infos.iter().zip(positions) {
            let run_x = i32::try_from(pen_x).map_err(|_| ShapeError::CoordinateOverflow)?;
            let run_y = i32::try_from(pen_y).map_err(|_| ShapeError::CoordinateOverflow)?;
            let cluster_start =
                usize::try_from(info.cluster).map_err(|_| ShapeError::InputTooLarge)?;

            glyphs.push(ShapedGlyph {
                glyph_id: info.codepoint,
                cluster_start,
                run_x,
                run_y,
                x_offset: position.x_offset,
                y_offset: position.y_offset,
                x_advance: position.x_advance,
                y_advance: position.y_advance,
            });

            pen_x += i64::from(position.x_advance);
            pen_y += i64::from(position.y_advance);
        }

        let units = logical_units_from_shaped_glyphs(text, options.font_id, &glyphs)
            .map_err(|error| ShapeError::LogicalModel(error.to_string()))?;
        let run = LogicalTextRun {
            original_text: text.to_owned(),
            direction,
            units,
        };
        run.validate_round_trip()
            .map_err(|error| ShapeError::LogicalModel(error.to_string()))?;

        Ok(ShapeOutput { units_per_em, run })
    }
}

macro_rules! guard {
    ($name:ident, $destroy:ty) => {
        struct $name {
            pointer: *mut c_void,
            destroy: $destroy,
        }

        impl Drop for $name {
            fn drop(&mut self) {
                // SAFETY: each guard owns one valid HarfBuzz object and calls
                // the corresponding destroy function exactly once.
                unsafe {
                    (self.destroy)(self.pointer);
                }
            }
        }
    };
}

guard!(BlobGuard, HbBlobDestroy);
guard!(FaceGuard, HbFaceDestroy);
guard!(FontGuard, HbFontDestroy);
guard!(BufferGuard, HbBufferDestroy);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_system_harfbuzz_when_available() {
        if std::env::var_os("UNICODE_PDF_REQUIRE_SYSTEM_HARFBUZZ").is_some() {
            HarfBuzzShaper::new().expect("system HarfBuzz must be loadable");
        }
    }
}
