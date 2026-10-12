use super::{
    ImageOcrEngine, OcrDocumentResult, OcrError, OcrErrorCode, OcrOptions, OcrPageResult,
    decode_candidate_image_with_limit, validate_options,
};
use pdf_core::{DirectOcrPage, DirectOcrPageProducer, OcrLayoutBlock, PdfDocument};
use serde::{Deserialize, Serialize};

/// Document-wide logical output and per-image decode bounds, including streaming calls.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct OcrDocumentLimits {
    /// Sum of page text plus separators in the collected document text.
    pub max_total_text_bytes: usize,
    pub max_total_blocks: usize,
    /// Logical retained payload: three text copies plus page/block struct sizes.
    /// This is not a process RSS measurement or an ONNX internal allocation limit.
    pub max_total_result_bytes: usize,
    pub max_decoded_image_bytes: usize,
}

impl Default for OcrDocumentLimits {
    fn default() -> Self {
        Self {
            max_total_text_bytes: 64 * 1024 * 1024,
            max_total_blocks: 65_536,
            max_total_result_bytes: 256 * 1024 * 1024,
            max_decoded_image_bytes: 128 * 1024 * 1024,
        }
    }
}

/// OCR one page at a time. The engine may be borrowed or owned; parser state is owned.
/// Dropping the iterator releases its parser state and cancels future inference.
pub struct OcrPageProducer<'a, E: ImageOcrEngine> {
    candidates: DirectOcrPageProducer,
    engine: EngineHandle<'a, E>,
    options: OcrOptions,
    limits: OcrDocumentLimits,
    text_bytes: usize,
    blocks: usize,
    result_bytes: usize,
    has_text: bool,
    done: bool,
}

enum EngineHandle<'a, E> {
    Borrowed(&'a E),
    Owned(E),
}
impl<E> EngineHandle<'_, E> {
    fn get(&self) -> &E {
        match self {
            Self::Borrowed(engine) => engine,
            Self::Owned(engine) => engine,
        }
    }
}

/// Create a lazy page OCR iterator with explicit document-level bounds.
///
/// # Errors
///
/// Returns invalid option/page-tree errors immediately; later errors are yielded once.
pub fn ocr_pages<'a, E: ImageOcrEngine>(
    document: &PdfDocument,
    engine: &'a E,
    options: OcrOptions,
    limits: OcrDocumentLimits,
) -> Result<OcrPageProducer<'a, E>, OcrError> {
    ocr_pages_selected(
        document,
        engine,
        options,
        limits,
        &pdf_core::PageSelection::all(),
    )
}

/// Lazily OCR selected source pages with document-level budgets.
///
/// # Errors
/// Returns selection, option or page-tree errors; later failures are yielded.
pub fn ocr_pages_selected<'a, E: ImageOcrEngine>(
    document: &PdfDocument,
    engine: &'a E,
    options: OcrOptions,
    limits: OcrDocumentLimits,
    selection: &pdf_core::PageSelection,
) -> Result<OcrPageProducer<'a, E>, OcrError> {
    make_producer(
        document,
        EngineHandle::Borrowed(engine),
        options,
        limits,
        selection,
    )
}

/// Own the engine and parser state for a stream that can outlive its creator.
///
/// # Errors
/// Returns selection, option or page-tree errors; later failures are yielded.
pub fn ocr_pages_owned<E: ImageOcrEngine + 'static>(
    document: &PdfDocument,
    engine: E,
    options: OcrOptions,
    limits: OcrDocumentLimits,
    selection: &pdf_core::PageSelection,
) -> Result<OcrPageProducer<'static, E>, OcrError> {
    make_producer(
        document,
        EngineHandle::Owned(engine),
        options,
        limits,
        selection,
    )
}

fn make_producer<'a, E: ImageOcrEngine>(
    document: &PdfDocument,
    engine: EngineHandle<'a, E>,
    options: OcrOptions,
    limits: OcrDocumentLimits,
    selection: &pdf_core::PageSelection,
) -> Result<OcrPageProducer<'a, E>, OcrError> {
    validate_options(options)?;
    if limits.max_decoded_image_bytes == 0 {
        return Err(limit("OCR decoded image budget must be positive"));
    }
    Ok(OcrPageProducer {
        candidates: document.direct_ocr_pages_selected(options.direct_image, selection)?,
        engine,
        options,
        limits,
        text_bytes: 0,
        blocks: 0,
        result_bytes: 0,
        has_text: false,
        done: false,
    })
}

impl<E: ImageOcrEngine> Iterator for OcrPageProducer<'_, E> {
    type Item = Result<OcrPageResult, OcrError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        let Some(candidate) = self.candidates.next() else {
            self.done = true;
            return None;
        };
        let result = candidate.map_err(OcrError::from).and_then(|candidate| {
            let page = recognize_page(
                candidate,
                self.engine.get(),
                self.options,
                self.limits.max_decoded_image_bytes,
            )?;
            let separator = usize::from(self.has_text && !page.text.is_empty());
            let new_text = page
                .text
                .len()
                .checked_add(separator)
                .ok_or_else(|| limit("OCR text byte count overflow"))?;
            let payload = new_text
                .checked_mul(3)
                .and_then(|bytes| {
                    page.blocks
                        .len()
                        .checked_mul(std::mem::size_of::<OcrLayoutBlock>())
                        .and_then(|blocks| bytes.checked_add(blocks))
                })
                .and_then(|bytes| bytes.checked_add(std::mem::size_of::<OcrPageResult>()))
                .ok_or_else(|| limit("OCR result byte count overflow"))?;
            charge(
                &mut self.text_bytes,
                new_text,
                self.limits.max_total_text_bytes,
                "OCR document text byte limit exceeded",
            )?;
            charge(
                &mut self.blocks,
                page.blocks.len(),
                self.limits.max_total_blocks,
                "OCR document block count limit exceeded",
            )?;
            charge(
                &mut self.result_bytes,
                payload,
                self.limits.max_total_result_bytes,
                "OCR document result byte limit exceeded",
            )?;
            self.has_text |= !page.text.is_empty();
            Ok(page)
        });
        if result.is_err() {
            self.done = true;
        }
        Some(result)
    }
}

impl<E: ImageOcrEngine> std::iter::FusedIterator for OcrPageProducer<'_, E> {}

/// Collect lazy OCR results while enforcing document-level budgets.
///
/// # Errors
///
/// Returns a stable parser, image, inference or limit error.
pub fn ocr_document_with_limits(
    document: &PdfDocument,
    engine: &impl ImageOcrEngine,
    options: OcrOptions,
    limits: OcrDocumentLimits,
) -> Result<OcrDocumentResult, OcrError> {
    ocr_document_selected(
        document,
        engine,
        options,
        limits,
        &pdf_core::PageSelection::all(),
    )
}

/// Collect OCR for selected pages with explicit limits.
///
/// # Errors
/// Returns a stable selection, parser, inference or limit error.
pub fn ocr_document_selected(
    document: &PdfDocument,
    engine: &impl ImageOcrEngine,
    options: OcrOptions,
    limits: OcrDocumentLimits,
    selection: &pdf_core::PageSelection,
) -> Result<OcrDocumentResult, OcrError> {
    let mut pages = Vec::new();
    let mut text = String::new();
    for page in ocr_pages_selected(document, engine, options, limits, selection)? {
        let page = page?;
        if !page.text.is_empty() {
            if !text.is_empty() {
                text.push('\n');
            }
            text.push_str(&page.text);
        }
        pages.push(page);
    }
    Ok(OcrDocumentResult { pages, text })
}

fn recognize_page(
    page: DirectOcrPage,
    engine: &impl ImageOcrEngine,
    options: OcrOptions,
    image_limit: usize,
) -> Result<OcrPageResult, OcrError> {
    let Some(candidate) = page.candidate else {
        return Ok(OcrPageResult {
            page_index: page.page_index,
            reason: page.reason,
            blocks: Vec::new(),
            text: String::new(),
        });
    };
    let image = decode_candidate_image_with_limit(&candidate.image, image_limit)?;
    let pixel_blocks = engine.recognize(&image, options.max_blocks_per_page)?;
    if pixel_blocks.len() > options.max_blocks_per_page {
        return Err(limit("OCR backend returned too many blocks"));
    }
    let mut text_bytes = 0;
    let mut blocks = Vec::with_capacity(pixel_blocks.len());
    for block in pixel_blocks {
        if !block.confidence.is_finite() || !(0.0..=1.0).contains(&block.confidence) {
            return Err(OcrError::new(
                OcrErrorCode::Inference,
                "OCR backend confidence must be finite and in [0, 1]",
            ));
        }
        if block.confidence < options.min_confidence || block.text.is_empty() {
            continue;
        }
        let bytes = block
            .text
            .len()
            .checked_add(usize::from(!blocks.is_empty()))
            .ok_or_else(|| limit("OCR page text byte count overflow"))?;
        charge(
            &mut text_bytes,
            bytes,
            options.max_total_text_bytes_per_page,
            "OCR page text length limit exceeded",
        )?;
        blocks.push(candidate.map_block(&block)?);
    }
    let text = blocks
        .iter()
        .map(|block| block.text.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    Ok(OcrPageResult {
        page_index: page.page_index,
        reason: page.reason,
        blocks,
        text,
    })
}

fn charge(total: &mut usize, amount: usize, maximum: usize, message: &str) -> Result<(), OcrError> {
    if amount > maximum.saturating_sub(*total) {
        return Err(limit(message));
    }
    *total += amount;
    Ok(())
}

fn limit(message: &str) -> OcrError {
    OcrError::new(OcrErrorCode::LimitExceeded, message)
}
