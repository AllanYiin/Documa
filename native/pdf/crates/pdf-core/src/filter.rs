use std::io::Read;

use flate2::read::ZlibDecoder;

use crate::{
    ErrorCode, ParseLimits, PdfDictionary, PdfError, PdfName, PdfObject, PdfResult, PdfStream,
    decode_budget::DecodeBudget,
};

#[derive(Debug, Clone, Copy)]
struct PredictorParameters {
    predictor: usize,
    colors: usize,
    bits_per_component: usize,
    columns: usize,
}

impl Default for PredictorParameters {
    fn default() -> Self {
        Self {
            predictor: 1,
            colors: 1,
            bits_per_component: 8,
            columns: 1,
        }
    }
}

/// Decode a PDF stream filter chain with default resource limits.
///
/// # Errors
///
/// Returns a structured error for unsupported filters, malformed data, or exceeded limits.
pub fn decode_stream(stream: &PdfStream) -> PdfResult<Vec<u8>> {
    decode_stream_with_limits(stream, &ParseLimits::default())
}

/// Decode a PDF stream filter chain with explicit resource limits.
///
/// # Errors
///
/// Returns a structured error for unsupported filters, malformed data, or exceeded limits.
pub fn decode_stream_with_limits(stream: &PdfStream, limits: &ParseLimits) -> PdfResult<Vec<u8>> {
    let mut budget = DecodeBudget::new(limits.max_total_decoded_bytes);
    decode_stream_impl(stream, limits, None, &mut budget)
}

pub(crate) fn decode_stream_with_budget(
    stream: &PdfStream,
    limits: &ParseLimits,
    budget: &mut DecodeBudget,
) -> PdfResult<Vec<u8>> {
    decode_stream_impl(stream, limits, None, budget)
}

/// Decode a structural stream using a validated format-derived output budget.
///
/// The structural budget may relax the expansion-ratio check, but never the configured absolute
/// or document-lifetime decoded-byte limits.
pub(crate) fn decode_stream_with_structural_budget(
    stream: &PdfStream,
    limits: &ParseLimits,
    structural_limit: usize,
    budget: &mut DecodeBudget,
) -> PdfResult<Vec<u8>> {
    decode_stream_impl(stream, limits, Some(structural_limit), budget)
}

fn decode_stream_impl(
    stream: &PdfStream,
    limits: &ParseLimits,
    structural_limit: Option<usize>,
    budget: &mut DecodeBudget,
) -> PdfResult<Vec<u8>> {
    if stream.data.len() > limits.max_stream_bytes {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "raw stream byte limit exceeded",
        ));
    }
    let filters = filter_names(&stream.dictionary)?;
    if filters.len() > limits.max_filter_chain_depth {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "filter chain depth limit exceeded",
        ));
    }
    let parameters = decode_parameters(&stream.dictionary, filters.len())?;
    let mut current = stream.data.clone();

    for (index, filter) in filters.iter().enumerate() {
        let ratio_limit = stage_output_limit(current.len(), limits);
        let stage_limit = structural_limit
            .map_or(ratio_limit, |structural| {
                ratio_limit.max(structural.min(limits.max_decoded_stream_bytes))
            })
            .min(budget.remaining());
        let decoded = match filter.as_bytes() {
            b"FlateDecode" | b"Fl" => {
                budget.begin_decode()?;
                decode_flate(&current, stage_limit, budget)?
            }
            _ => {
                return Err(PdfError::new(
                    ErrorCode::UnsupportedFeature,
                    None,
                    format!(
                        "unsupported stream filter /{}",
                        String::from_utf8_lossy(filter.as_bytes())
                    ),
                ));
            }
        };
        current = apply_predictor(decoded, parameters[index], limits, budget)?;
    }
    if current.len() > limits.max_decoded_stream_bytes
        || structural_limit.is_some_and(|maximum| current.len() > maximum)
    {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "decoded stream byte limit exceeded",
        ));
    }
    Ok(current)
}

fn filter_names(dictionary: &PdfDictionary) -> PdfResult<Vec<PdfName>> {
    let Some(filter) = dictionary
        .get(&PdfName(b"Filter".to_vec()))
        .or_else(|| dictionary.get(&PdfName(b"F".to_vec())))
    else {
        return Ok(Vec::new());
    };
    match filter {
        PdfObject::Name(name) => Ok(vec![name.clone()]),
        PdfObject::Array(values) => values
            .iter()
            .map(|value| {
                if let PdfObject::Name(name) = value {
                    Ok(name.clone())
                } else {
                    Err(PdfError::new(
                        ErrorCode::InvalidStream,
                        None,
                        "Filter array entries must be names",
                    ))
                }
            })
            .collect(),
        _ => Err(PdfError::new(
            ErrorCode::InvalidStream,
            None,
            "Filter must be a name or array",
        )),
    }
}

fn decode_parameters(
    dictionary: &PdfDictionary,
    filter_count: usize,
) -> PdfResult<Vec<PredictorParameters>> {
    if filter_count == 0 {
        return Ok(Vec::new());
    }
    let value = dictionary
        .get(&PdfName(b"DecodeParms".to_vec()))
        .or_else(|| dictionary.get(&PdfName(b"DP".to_vec())));
    match value {
        None | Some(PdfObject::Null) => Ok(vec![PredictorParameters::default(); filter_count]),
        Some(PdfObject::Dictionary(parameters)) if filter_count == 1 => {
            Ok(vec![parse_predictor_parameters(parameters)?])
        }
        Some(PdfObject::Array(values)) if values.len() == filter_count => values
            .iter()
            .map(|value| match value {
                PdfObject::Null => Ok(PredictorParameters::default()),
                PdfObject::Dictionary(parameters) => parse_predictor_parameters(parameters),
                _ => Err(PdfError::new(
                    ErrorCode::InvalidStream,
                    None,
                    "DecodeParms array entries must be dictionaries or null",
                )),
            })
            .collect(),
        Some(PdfObject::Array(_)) => Err(PdfError::new(
            ErrorCode::InvalidStream,
            None,
            "DecodeParms array length must match Filter array length",
        )),
        Some(_) => Err(PdfError::new(
            ErrorCode::InvalidStream,
            None,
            "DecodeParms must be a dictionary, array, or null",
        )),
    }
}

fn parse_predictor_parameters(dictionary: &PdfDictionary) -> PdfResult<PredictorParameters> {
    let parameters = PredictorParameters {
        predictor: parameter(dictionary, b"Predictor", 1)?,
        colors: parameter(dictionary, b"Colors", 1)?,
        bits_per_component: parameter(dictionary, b"BitsPerComponent", 8)?,
        columns: parameter(dictionary, b"Columns", 1)?,
    };
    if parameters.colors == 0 || parameters.columns == 0 {
        return Err(PdfError::new(
            ErrorCode::InvalidStream,
            None,
            "predictor Colors and Columns must be positive",
        ));
    }
    if !matches!(parameters.bits_per_component, 1 | 2 | 4 | 8 | 16) {
        return Err(PdfError::new(
            ErrorCode::UnsupportedFeature,
            None,
            "predictor BitsPerComponent must be 1, 2, 4, 8, or 16",
        ));
    }
    Ok(parameters)
}

fn parameter(dictionary: &PdfDictionary, name: &[u8], default: usize) -> PdfResult<usize> {
    let Some(value) = dictionary.get(&PdfName(name.to_vec())) else {
        return Ok(default);
    };
    let PdfObject::Integer(value) = value else {
        return Err(PdfError::new(
            ErrorCode::InvalidStream,
            None,
            format!(
                "predictor parameter {} must be an integer",
                String::from_utf8_lossy(name)
            ),
        ));
    };
    usize::try_from(*value).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidStream,
            None,
            format!(
                "predictor parameter {} is negative or out of range",
                String::from_utf8_lossy(name)
            ),
        )
    })
}

fn stage_output_limit(input_bytes: usize, limits: &ParseLimits) -> usize {
    let ratio_limit = input_bytes
        .max(1)
        .saturating_mul(limits.max_stream_expansion_ratio);
    limits.max_decoded_stream_bytes.min(ratio_limit)
}

fn decode_flate(input: &[u8], maximum: usize, budget: &mut DecodeBudget) -> PdfResult<Vec<u8>> {
    let mut decoder = ZlibDecoder::new(input);
    let mut output = Vec::new();
    let mut buffer = [0_u8; 8192];
    loop {
        let count = decoder.read(&mut buffer).map_err(|error| {
            PdfError::new(
                ErrorCode::InvalidStream,
                None,
                format!("FlateDecode failed: {error}"),
            )
        })?;
        if count == 0 {
            return Ok(output);
        }
        if output.len().saturating_add(count) > maximum {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "FlateDecode output limit or expansion ratio exceeded",
            ));
        }
        budget.charge(count)?;
        output.extend_from_slice(&buffer[..count]);
    }
}

fn apply_predictor(
    data: Vec<u8>,
    parameters: PredictorParameters,
    limits: &ParseLimits,
    budget: &mut DecodeBudget,
) -> PdfResult<Vec<u8>> {
    let output = match parameters.predictor {
        1 => data,
        2 => decode_tiff_predictor(&data, parameters, budget)?,
        10..=15 => decode_png_predictor(&data, parameters, budget)?,
        _ => {
            return Err(PdfError::new(
                ErrorCode::UnsupportedFeature,
                None,
                format!("unsupported predictor {}", parameters.predictor),
            ));
        }
    };
    if output.len() > limits.max_decoded_stream_bytes {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "predictor output byte limit exceeded",
        ));
    }
    Ok(output)
}

fn row_geometry(parameters: PredictorParameters) -> PdfResult<(usize, usize)> {
    let samples = parameters
        .colors
        .checked_mul(parameters.columns)
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "predictor sample count overflow",
            )
        })?;
    let bits = samples
        .checked_mul(parameters.bits_per_component)
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "predictor row bit count overflow",
            )
        })?;
    let bytes = bits.checked_add(7).ok_or_else(|| {
        PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "predictor row byte count overflow",
        )
    })? / 8;
    Ok((samples, bytes))
}

fn decode_tiff_predictor(
    data: &[u8],
    parameters: PredictorParameters,
    budget: &mut DecodeBudget,
) -> PdfResult<Vec<u8>> {
    let (samples_per_row, row_bytes) = row_geometry(parameters)?;
    if row_bytes == 0 || !data.len().is_multiple_of(row_bytes) {
        return Err(PdfError::new(
            ErrorCode::InvalidStream,
            None,
            "TIFF predictor data is not an integral number of rows",
        ));
    }
    budget.charge(data.len())?;
    let mut output = vec![0_u8; data.len()];
    let modulus = 1_u32 << parameters.bits_per_component;
    for (source_row, output_row) in data
        .chunks_exact(row_bytes)
        .zip(output.chunks_exact_mut(row_bytes))
    {
        let mut samples = Vec::with_capacity(samples_per_row);
        for index in 0..samples_per_row {
            let encoded = read_bits(
                source_row,
                index * parameters.bits_per_component,
                parameters.bits_per_component,
            );
            let decoded = if index >= parameters.colors {
                (encoded + samples[index - parameters.colors]) % modulus
            } else {
                encoded
            };
            samples.push(decoded);
            write_bits(
                output_row,
                index * parameters.bits_per_component,
                parameters.bits_per_component,
                decoded,
            );
        }
    }
    Ok(output)
}

fn decode_png_predictor(
    data: &[u8],
    parameters: PredictorParameters,
    budget: &mut DecodeBudget,
) -> PdfResult<Vec<u8>> {
    let (_, row_bytes) = row_geometry(parameters)?;
    let encoded_row_bytes = row_bytes.checked_add(1).ok_or_else(|| {
        PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "PNG predictor row byte count overflow",
        )
    })?;
    if row_bytes == 0 || !data.len().is_multiple_of(encoded_row_bytes) {
        return Err(PdfError::new(
            ErrorCode::InvalidStream,
            None,
            "PNG predictor data is not an integral number of tagged rows",
        ));
    }
    let bytes_per_pixel = parameters
        .colors
        .checked_mul(parameters.bits_per_component)
        .and_then(|bits| bits.checked_add(7))
        .map(|bits| (bits / 8).max(1))
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "PNG predictor pixel byte count overflow",
            )
        })?;
    let row_count = data.len() / encoded_row_bytes;
    let output_len = row_count.checked_mul(row_bytes).ok_or_else(|| {
        PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "PNG predictor output size overflow",
        )
    })?;
    budget.charge(output_len)?;
    let mut output = vec![0_u8; output_len];
    let mut previous = vec![0_u8; row_bytes];

    for (row_index, encoded) in data.chunks_exact(encoded_row_bytes).enumerate() {
        let filter = encoded[0];
        if filter > 4 {
            return Err(PdfError::new(
                ErrorCode::InvalidStream,
                None,
                "PNG predictor row has an invalid filter byte",
            ));
        }
        if parameters.predictor != 15 && filter as usize != parameters.predictor - 10 {
            return Err(PdfError::new(
                ErrorCode::InvalidStream,
                None,
                "PNG predictor row filter disagrees with DecodeParms",
            ));
        }
        let row_start = row_index * row_bytes;
        let row = &mut output[row_start..row_start + row_bytes];
        for index in 0..row_bytes {
            let left = if index >= bytes_per_pixel {
                row[index - bytes_per_pixel]
            } else {
                0
            };
            let up = previous[index];
            let upper_left = if index >= bytes_per_pixel {
                previous[index - bytes_per_pixel]
            } else {
                0
            };
            let prediction = match filter {
                0 => 0,
                1 => left,
                2 => up,
                3 => (left & up) + ((left ^ up) >> 1),
                4 => paeth(left, up, upper_left),
                _ => unreachable!("filter range checked above"),
            };
            row[index] = encoded[index + 1].wrapping_add(prediction);
        }
        previous.copy_from_slice(row);
    }
    Ok(output)
}

fn read_bits(bytes: &[u8], bit_offset: usize, width: usize) -> u32 {
    let mut value = 0_u32;
    for index in 0..width {
        let absolute = bit_offset + index;
        let bit = (bytes[absolute / 8] >> (7 - absolute % 8)) & 1;
        value = (value << 1) | u32::from(bit);
    }
    value
}

fn write_bits(bytes: &mut [u8], bit_offset: usize, width: usize, value: u32) {
    for index in 0..width {
        let absolute = bit_offset + index;
        let shift = width - index - 1;
        let bit = ((value >> shift) & 1) as u8;
        let mask = 1_u8 << (7 - absolute % 8);
        if bit == 1 {
            bytes[absolute / 8] |= mask;
        } else {
            bytes[absolute / 8] &= !mask;
        }
    }
}

fn paeth(left: u8, up: u8, upper_left: u8) -> u8 {
    let left_value = i32::from(left);
    let up_value = i32::from(up);
    let upper_left_value = i32::from(upper_left);
    let estimate = left_value + up_value - upper_left_value;
    let left_distance = (estimate - left_value).abs();
    let up_distance = (estimate - up_value).abs();
    let upper_left_distance = (estimate - upper_left_value).abs();
    if left_distance <= up_distance && left_distance <= upper_left_distance {
        left
    } else if up_distance <= upper_left_distance {
        up
    } else {
        upper_left
    }
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    use flate2::{Compression, write::ZlibEncoder};

    use super::*;

    fn compressed_stream(data: &[u8], parameters: Option<PdfDictionary>) -> PdfStream {
        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(data).expect("compress fixture");
        let compressed = encoder.finish().expect("finish fixture");
        let mut dictionary = PdfDictionary::new();
        dictionary.insert(
            PdfName(b"Filter".to_vec()),
            PdfObject::Name(PdfName(b"FlateDecode".to_vec())),
        );
        if let Some(parameters) = parameters {
            dictionary.insert(
                PdfName(b"DecodeParms".to_vec()),
                PdfObject::Dictionary(parameters),
            );
        }
        PdfStream {
            dictionary,
            data: compressed,
        }
    }

    fn predictor_parameters(predictor: i64, columns: i64) -> PdfDictionary {
        let mut parameters = PdfDictionary::new();
        parameters.insert(
            PdfName(b"Predictor".to_vec()),
            PdfObject::Integer(predictor),
        );
        parameters.insert(PdfName(b"Columns".to_vec()), PdfObject::Integer(columns));
        parameters
    }

    #[test]
    fn decodes_flate_stream() {
        let stream = compressed_stream(b"BT /F1 12 Tf (hello) Tj ET", None);
        assert_eq!(
            decode_stream(&stream).expect("valid Flate stream"),
            b"BT /F1 12 Tf (hello) Tj ET"
        );
    }

    #[test]
    fn applies_png_sub_predictor() {
        let stream = compressed_stream(&[1, 10, 10, 10, 10], Some(predictor_parameters(11, 4)));
        assert_eq!(
            decode_stream(&stream).expect("valid PNG predictor"),
            [10, 20, 30, 40]
        );
    }

    #[test]
    fn applies_tiff_predictor_to_four_bit_samples() {
        let mut parameters = predictor_parameters(2, 4);
        parameters.insert(PdfName(b"BitsPerComponent".to_vec()), PdfObject::Integer(4));
        let stream = compressed_stream(&[0x12, 0x11], Some(parameters));
        assert_eq!(
            decode_stream(&stream).expect("valid TIFF predictor"),
            [0x13, 0x45]
        );
    }

    #[test]
    fn rejects_excessive_expansion() {
        let stream = compressed_stream(&vec![b'A'; 4096], None);
        let limits = ParseLimits {
            max_stream_expansion_ratio: 2,
            ..ParseLimits::default()
        };
        let error = decode_stream_with_limits(&stream, &limits)
            .expect_err("expansion ratio must be enforced");
        assert_eq!(error.code, ErrorCode::LimitExceeded);
    }
}
