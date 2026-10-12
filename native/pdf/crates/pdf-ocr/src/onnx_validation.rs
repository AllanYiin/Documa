use super::{OcrError, OcrErrorCode};
use ort::{session::Session, value::TensorElementType};

pub(super) fn validate(det: &[u8], rec: &[u8], dictionary: &[u8]) -> Result<(), OcrError> {
    let keys =
        std::str::from_utf8(dictionary).map_err(|_| model("OCR dictionary must be UTF-8"))?;
    let count = keys.lines().count();
    if count == 0 || count > 65_536 || keys.lines().any(str::is_empty) {
        return Err(model(
            "OCR dictionary must contain 1..=65536 nonempty lines (a space token is allowed)",
        ));
    }
    let default_library = if cfg!(target_os = "windows") {
        "onnxruntime.dll"
    } else if cfg!(target_os = "macos") {
        "libonnxruntime.dylib"
    } else {
        "libonnxruntime.so"
    };
    let path = std::env::var_os("ORT_DYLIB_PATH")
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| default_library.into());
    // init_from is fallible; calling Session::builder first would panic when the
    // shared library is absent or too old for ort's selected C API.
    ort::init_from(path)
        .map_err(|error| model(&error.to_string()))?
        .commit();
    validate_session(det, false, count)?;
    validate_session(rec, true, count)?;
    Ok(())
}

fn validate_session(bytes: &[u8], recognition: bool, keys: usize) -> Result<(), OcrError> {
    let session = Session::builder()
        .map_err(|error| model(&error.to_string()))?
        .with_intra_threads(1)
        .map_err(|error| model(&error.to_string()))?
        .with_inter_threads(1)
        .map_err(|error| model(&error.to_string()))?
        .commit_from_memory(bytes)
        .map_err(|error| model(&error.to_string()))?;
    let ([input], [output]) = (session.inputs(), session.outputs()) else {
        return Err(model(
            "OCR models must have exactly one input and one output",
        ));
    };
    if input.dtype().tensor_type() != Some(TensorElementType::Float32)
        || output.dtype().tensor_type() != Some(TensorElementType::Float32)
    {
        return Err(model("OCR model input and output tensors must be float32"));
    }
    let shape = input
        .dtype()
        .tensor_shape()
        .ok_or_else(|| model("OCR model input must be a tensor"))?;
    if shape.len() != 4
        || !matches!(shape[0], -1 | 1)
        || shape[1] != 3
        || shape[2] == 0
        || shape[3] == 0
    {
        return Err(model(
            "OCR model input must be NCHW with batch 1/dynamic and three channels",
        ));
    }
    let output_shape = output
        .dtype()
        .tensor_shape()
        .ok_or_else(|| model("OCR model output must be a tensor"))?;
    if recognition {
        if output_shape.len() != 3
            || output_shape[2] != i64::try_from(keys + 1).expect("dictionary length bounded")
        {
            return Err(model(
                "recognition output classes must exactly match dictionary lines plus the CTC blank",
            ));
        }
        if !(1..=512).contains(&shape[2]) || shape[3] > 32_768 || shape[3] < -1 {
            return Err(model("recognition input geometry exceeds supported bounds"));
        }
    } else if output_shape.len() != 4 || output_shape[1] != 1 {
        return Err(model(
            "detection model must return an NCHW single-channel probability map",
        ));
    }
    Ok(())
}

fn model(message: &str) -> OcrError {
    OcrError::new(OcrErrorCode::Model, message)
}
