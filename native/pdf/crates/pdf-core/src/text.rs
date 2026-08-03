use std::{cmp::Ordering, collections::BTreeSet};

#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};
use unicode_normalization::UnicodeNormalization;

use crate::graphics::{Matrix, parse_matrix, resolve_dictionary};
use crate::{
    ContentOperation, ErrorCode, PdfDictionary, PdfDocument, PdfError, PdfName, PdfObject, PdfPage,
    PdfResult, PdfString,
    font::FontDecoder,
    font::load_fonts_from_resources,
    font_metrics::FontVerticalMetrics,
    marked_content::{MarkedContentProperties, resolve_marked_content_properties},
    parse_content,
    text_model::{PositionedGlyph, TextOrigin, TextSeparator, WritingMode},
};

/// Controls Unicode normalization and layout reconstruction.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct TextExtractionOptions {
    /// Apply NFC after decoding `ToUnicode` mappings. Raw mapped Unicode is retained when false.
    pub normalize_unicode: bool,
    /// Reorder spans into approximate visual lines instead of preserving content-stream order.
    pub layout: bool,
}

impl Default for TextExtractionOptions {
    fn default() -> Self {
        Self {
            normalize_unicode: false,
            layout: true,
        }
    }
}

/// Selects how decoded text is ordered and separated.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "kebab-case"))]
pub enum ExtractionMode {
    /// Preserve text-showing operation order and explicit Unicode whitespace.
    ContentOrder,
    /// Use the legacy geometry-based layout reconstruction.
    Layout,
    /// Use script-aware geometry, font metrics, rotations, and bounded ambiguity fallback.
    Auto,
}

impl ExtractionMode {
    /// Return the stable mode name used by CLI and language bindings.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ContentOrder => "content-order",
            Self::Layout => "layout",
            Self::Auto => "auto",
        }
    }

    const fn uses_layout(self) -> bool {
        matches!(self, Self::Layout | Self::Auto)
    }
}

/// V2 text extraction options shared by all front ends.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct TextExtractionOptionsV2 {
    /// Apply NFC after font and `CMap` decoding.
    pub normalize_unicode: bool,
    /// Select source order, legacy layout, or automatic reconstruction.
    pub mode: ExtractionMode,
    /// Include aggregate quality counters in the result.
    pub include_quality_metadata: bool,
}

impl Default for TextExtractionOptionsV2 {
    fn default() -> Self {
        Self {
            normalize_unicode: false,
            mode: ExtractionMode::Auto,
            include_quality_metadata: true,
        }
    }
}

/// One positioned text fragment.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct TextSpan {
    pub page_index: usize,
    pub text: String,
    pub font_resource: Option<String>,
    pub font_name: Option<String>,
    pub font_size: f64,
    pub x: f64,
    pub y: f64,
}

/// Text and spans extracted from one page.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct PageText {
    pub page_index: usize,
    pub text: String,
    pub spans: Vec<TextSpan>,
}

/// A non-fatal extraction limitation that may affect fidelity.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct TextWarning {
    pub code: String,
    pub page_index: usize,
    pub font_resource: Option<String>,
    pub message: String,
}

/// Full-document text extraction result.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct ExtractedText {
    pub text: String,
    pub pages: Vec<PageText>,
    pub warnings: Vec<TextWarning>,
}

/// Aggregate fidelity counters for a V2 extraction.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct TextQuality {
    pub inserted_spaces: usize,
    pub inserted_line_breaks: usize,
    pub fallback_glyphs: usize,
    pub replacement_characters: usize,
    pub ambiguous_boundaries: usize,
}

impl TextQuality {
    pub(crate) fn merge(&mut self, other: Self) {
        self.inserted_spaces = self.inserted_spaces.saturating_add(other.inserted_spaces);
        self.inserted_line_breaks = self
            .inserted_line_breaks
            .saturating_add(other.inserted_line_breaks);
        self.fallback_glyphs = self.fallback_glyphs.saturating_add(other.fallback_glyphs);
        self.replacement_characters = self
            .replacement_characters
            .saturating_add(other.replacement_characters);
        self.ambiguous_boundaries = self
            .ambiguous_boundaries
            .saturating_add(other.ambiguous_boundaries);
    }
}

/// V2 full-document text extraction result.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct ExtractedTextV2 {
    pub mode: ExtractionMode,
    pub text: String,
    pub pages: Vec<PageText>,
    pub warnings: Vec<TextWarning>,
    pub glyphs: Vec<PositionedGlyph>,
    pub separators: Vec<TextSeparator>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub quality: Option<TextQuality>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct GlyphMarkedContent {
    pub source_ordinal: u64,
    pub tag: Option<String>,
    pub alt_text: Option<String>,
    pub actual_text: Option<String>,
    pub artifact: bool,
}

impl GlyphMarkedContent {
    fn from_properties(source_ordinal: u64, properties: &MarkedContentProperties) -> Self {
        Self {
            source_ordinal,
            tag: properties.tag.clone(),
            alt_text: properties.alt_text.clone(),
            actual_text: properties.actual_text.clone(),
            artifact: properties.artifact,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct GlyphLayoutGeometry {
    pub(crate) top: [f64; 2],
    pub(crate) bottom: [f64; 2],
}

type InternalTextExtraction = (
    ExtractedText,
    Vec<PositionedGlyph>,
    Vec<GlyphMarkedContent>,
    Vec<TextSeparator>,
    TextQuality,
    Vec<crate::vector_paths::PageVectorPaths>,
    Option<String>,
);

#[derive(Debug, Default)]
struct PageCollector {
    spans: Vec<TextSpan>,
    glyphs: Vec<PositionedGlyph>,
    glyph_marked_content: Vec<GlyphMarkedContent>,
    glyph_layout_geometry: Vec<GlyphLayoutGeometry>,
    next_source_ordinal: u64,
    quality: TextQuality,
}

#[derive(Debug)]
struct MarkedContentFrame {
    properties: MarkedContentProperties,
    glyph_start: usize,
    span_start: usize,
}

#[derive(Debug, Default)]
struct MarkedContentState {
    frames: Vec<MarkedContentFrame>,
}

impl MarkedContentState {
    fn current_mcid(&self) -> Option<i64> {
        self.frames
            .iter()
            .rev()
            .find_map(|frame| frame.properties.mcid)
    }

    fn has_actual_text(&self) -> bool {
        self.frames
            .iter()
            .any(|frame| frame.properties.actual_text.is_some())
    }

    fn is_artifact(&self) -> bool {
        self.frames.iter().any(|frame| frame.properties.artifact)
    }

    fn current_alt_text(&self) -> Option<String> {
        self.frames
            .iter()
            .rev()
            .find_map(|frame| frame.properties.alt_text.clone())
    }

    fn glyph_context(&self, source_ordinal: u64) -> GlyphMarkedContent {
        GlyphMarkedContent {
            source_ordinal,
            tag: self
                .frames
                .iter()
                .rev()
                .find_map(|frame| frame.properties.tag.clone()),
            alt_text: self.current_alt_text(),
            actual_text: self
                .frames
                .iter()
                .rev()
                .find_map(|frame| frame.properties.actual_text.clone()),
            artifact: self.is_artifact(),
        }
    }
}

#[derive(Debug)]
pub(crate) struct ExtractedPage {
    pub(crate) source_page: PdfPage,
    pub(crate) page: PageText,
    pub(crate) glyphs: Vec<PositionedGlyph>,
    pub(crate) glyph_marked_content: Vec<GlyphMarkedContent>,
    pub(crate) glyph_layout_geometry: Vec<GlyphLayoutGeometry>,
    pub(crate) separators: Vec<TextSeparator>,
    pub(crate) quality: TextQuality,
    pub(crate) warnings: Vec<TextWarning>,
    pub(crate) vector_paths: Option<PdfResult<crate::vector_paths::PageVectorPaths>>,
}

pub(crate) struct TextPageProducer {
    document: PdfDocument,
    pages: Vec<PdfPage>,
    options: TextExtractionOptions,
    use_auto_layout: bool,
    collect_vector_paths: bool,
    next_page: usize,
    next_source_ordinal: u64,
    total_glyphs: usize,
    vector_collection: crate::vector_paths::VectorCollectionState,
    vector_path_error: Option<String>,
}

impl TextPageProducer {
    fn new(
        document: &PdfDocument,
        pages: &[PdfPage],
        options: TextExtractionOptions,
        use_auto_layout: bool,
        collect_vector_paths: bool,
    ) -> Self {
        Self {
            document: document.clone(),
            pages: pages.to_vec(),
            options,
            use_auto_layout,
            collect_vector_paths,
            next_page: 0,
            next_source_ordinal: 0,
            total_glyphs: 0,
            vector_collection: crate::vector_paths::VectorCollectionState::default(),
            vector_path_error: None,
        }
    }

    pub(crate) fn vector_path_error(&self) -> Option<&str> {
        self.vector_path_error.as_deref()
    }
}

impl Iterator for TextPageProducer {
    type Item = PdfResult<ExtractedPage>;

    fn next(&mut self) -> Option<Self::Item> {
        let page = self.pages.get(self.next_page)?;
        self.next_page = self.next_page.saturating_add(1);
        let collect_page_vectors = self.collect_vector_paths && self.vector_path_error.is_none();
        let extracted = match extract_page(
            &self.document,
            page,
            self.options,
            &mut self.next_source_ordinal,
            self.use_auto_layout,
            collect_page_vectors,
            &mut self.vector_collection,
        ) {
            Ok(extracted) => extracted,
            Err(error) => {
                self.next_page = self.pages.len();
                return Some(Err(error));
            }
        };
        if extracted.glyphs.len()
            > self
                .document
                .limits
                .max_text_spans
                .saturating_sub(self.total_glyphs)
        {
            self.next_page = self.pages.len();
            return Some(Err(PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "document positioned glyph limit exceeded",
            )));
        }
        if extracted.glyphs.len() != extracted.glyph_marked_content.len()
            || extracted.glyphs.len() != extracted.glyph_layout_geometry.len()
        {
            self.next_page = self.pages.len();
            return Some(Err(PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "glyph and internal geometry metadata counts differ",
            )));
        }
        if let Some(Err(error)) = &extracted.vector_paths {
            if error.code == ErrorCode::LimitExceeded {
                self.next_page = self.pages.len();
                return Some(Err(error.clone()));
            }
            self.vector_path_error = Some(error.message.clone());
        }
        self.total_glyphs = self.total_glyphs.saturating_add(extracted.glyphs.len());
        Some(Ok(extracted))
    }
}

#[derive(Debug, Clone)]
struct TextState {
    ctm: Matrix,
    text_matrix: Matrix,
    line_matrix: Matrix,
    legacy_text_matrix: Matrix,
    legacy_line_matrix: Matrix,
    font: Option<PdfName>,
    font_size: f64,
    char_spacing: f64,
    word_spacing: f64,
    horizontal_scaling: f64,
    leading: f64,
    rise: f64,
}

impl Default for TextState {
    fn default() -> Self {
        Self {
            ctm: Matrix::IDENTITY,
            text_matrix: Matrix::IDENTITY,
            line_matrix: Matrix::IDENTITY,
            legacy_text_matrix: Matrix::IDENTITY,
            legacy_line_matrix: Matrix::IDENTITY,
            font: None,
            font_size: 0.0,
            char_spacing: 0.0,
            word_spacing: 0.0,
            horizontal_scaling: 100.0,
            leading: 0.0,
            rise: 0.0,
        }
    }
}

impl PdfDocument {
    /// Extract Unicode text from every page without rendering.
    ///
    /// `ToUnicode` `CMaps` are preferred; fallback decoding is reported through `warnings`.
    ///
    /// # Errors
    ///
    /// Returns a structured error for malformed page trees, content streams, fonts, or limits.
    pub fn extract_text(&self, options: TextExtractionOptions) -> PdfResult<ExtractedText> {
        self.extract_text_internal(options, false, false)
            .map(|result| result.0)
    }

    /// Extract Unicode text through the V2 mode contract.
    ///
    /// # Examples
    ///
    /// ```no_run
    /// use pdf_core::{ExtractionMode, PdfDocument, TextExtractionOptionsV2};
    ///
    /// # fn main() -> Result<(), Box<dyn std::error::Error>> {
    /// let bytes = std::fs::read("input.pdf")?;
    /// let document = PdfDocument::parse(&bytes)?;
    /// let result = document.extract_text_v2(TextExtractionOptionsV2 {
    ///     normalize_unicode: false,
    ///     mode: ExtractionMode::Auto,
    ///     include_quality_metadata: true,
    /// })?;
    /// assert_eq!(result.mode, ExtractionMode::Auto);
    /// # Ok(())
    /// # }
    /// ```
    ///
    /// # Errors
    ///
    /// Returns a structured error for malformed page trees, content streams, fonts, or limits.
    pub fn extract_text_v2(&self, options: TextExtractionOptionsV2) -> PdfResult<ExtractedTextV2> {
        let use_auto_layout = options.mode == ExtractionMode::Auto;
        let (extracted, glyphs, _, separators, quality, _, _) = self.extract_text_internal(
            TextExtractionOptions {
                normalize_unicode: options.normalize_unicode,
                layout: options.mode.uses_layout(),
            },
            use_auto_layout,
            false,
        )?;
        Ok(ExtractedTextV2 {
            mode: options.mode,
            text: extracted.text,
            pages: extracted.pages,
            warnings: extracted.warnings,
            glyphs,
            separators,
            quality: options.include_quality_metadata.then_some(quality),
        })
    }

    pub(crate) fn extract_text_page_producer(
        &self,
        pages: &[PdfPage],
        options: TextExtractionOptions,
        use_auto_layout: bool,
        collect_vector_paths: bool,
    ) -> TextPageProducer {
        TextPageProducer::new(self, pages, options, use_auto_layout, collect_vector_paths)
    }

    fn extract_text_internal(
        &self,
        options: TextExtractionOptions,
        use_auto_layout: bool,
        collect_vector_paths: bool,
    ) -> PdfResult<InternalTextExtraction> {
        let pages = self.pages()?;
        let mut page_results = Vec::with_capacity(pages.len());
        let mut glyphs = Vec::new();
        let mut glyph_marked_content = Vec::new();
        let mut separators = Vec::new();
        let mut quality = TextQuality::default();
        let mut page_vector_paths = Vec::with_capacity(pages.len());
        let mut warnings = Vec::new();
        let mut producer =
            self.extract_text_page_producer(&pages, options, use_auto_layout, collect_vector_paths);
        for extracted in &mut producer {
            let ExtractedPage {
                source_page: _,
                page,
                glyphs: page_glyphs,
                glyph_marked_content: page_glyph_marked_content,
                glyph_layout_geometry: _,
                separators: page_separators,
                quality: page_quality,
                warnings: page_warnings,
                vector_paths,
            } = extracted?;
            if collect_vector_paths {
                match vector_paths {
                    Some(Ok(paths)) => page_vector_paths.push(paths),
                    Some(Err(_)) => {
                        page_vector_paths.clear();
                        page_vector_paths.resize(
                            page_results.len() + 1,
                            crate::vector_paths::PageVectorPaths::default(),
                        );
                    }
                    None => page_vector_paths.push(crate::vector_paths::PageVectorPaths::default()),
                }
            }
            page_results.push(page);
            glyphs.extend(page_glyphs);
            glyph_marked_content.extend(page_glyph_marked_content);
            separators.extend(page_separators);
            quality.merge(page_quality);
            warnings.extend(page_warnings);
        }
        let vector_path_error = producer.vector_path_error().map(str::to_owned);
        let text = page_results
            .iter()
            .map(|page| page.text.as_str())
            .collect::<Vec<_>>()
            .join("\n\n");
        Ok((
            ExtractedText {
                text,
                pages: page_results,
                warnings,
            },
            glyphs,
            glyph_marked_content,
            separators,
            quality,
            page_vector_paths,
            vector_path_error,
        ))
    }
}

#[allow(clippy::too_many_arguments)]
fn extract_page(
    document: &PdfDocument,
    page: &PdfPage,
    options: TextExtractionOptions,
    next_source_ordinal: &mut u64,
    use_auto_layout: bool,
    collect_vector_paths: bool,
    vector_collection: &mut crate::vector_paths::VectorCollectionState,
) -> PdfResult<ExtractedPage> {
    let content = document.decoded_page_content(page)?;
    let operations = parse_content(&content, &document.limits)?;
    let mut state = TextState::default();
    let mut warnings = Vec::new();
    let mut collector = PageCollector {
        next_source_ordinal: *next_source_ordinal,
        ..PageCollector::default()
    };
    let mut warning_keys = BTreeSet::new();
    let mut form_stack = BTreeSet::new();
    let mut marked_content = MarkedContentState::default();
    process_operation_sequence(
        document,
        page,
        &page.resources,
        &operations,
        &mut state,
        &mut collector,
        &mut warnings,
        &mut warning_keys,
        &mut marked_content,
        &mut form_stack,
        0,
    )?;
    let vector_paths = collect_vector_paths.then(|| {
        crate::vector_paths::collect_vector_paths(document, page, &operations, vector_collection)
    });
    *next_source_ordinal = collector.next_source_ordinal;
    if options.normalize_unicode {
        for span in &mut collector.spans {
            span.text = span.text.nfc().collect();
        }
        for glyph in &mut collector.glyphs {
            glyph.unicode = glyph.unicode.nfc().collect();
        }
    }
    let (text, separators) = if use_auto_layout {
        let auto = crate::layout::auto_layout(page.index, &collector.glyphs);
        collector.quality.inserted_spaces = collector
            .quality
            .inserted_spaces
            .saturating_add(auto.inserted_spaces);
        collector.quality.inserted_line_breaks = collector
            .quality
            .inserted_line_breaks
            .saturating_add(auto.inserted_line_breaks);
        collector.quality.ambiguous_boundaries = collector
            .quality
            .ambiguous_boundaries
            .saturating_add(auto.ambiguous_boundaries);
        if auto.reading_order_ambiguous {
            push_warning_once(
                page.index,
                "reading_order_ambiguous",
                None,
                "Auto layout fell back to source order for ambiguous orientation, overlap, or columns",
                &mut warnings,
                &mut warning_keys,
            );
        }
        (auto.text, auto.separators)
    } else if options.layout {
        (layout_text(&collector.spans), Vec::new())
    } else {
        (
            collector
                .glyphs
                .iter()
                .map(|glyph| glyph.unicode.as_str())
                .collect(),
            Vec::new(),
        )
    };
    Ok(ExtractedPage {
        source_page: page.clone(),
        page: PageText {
            page_index: page.index,
            text,
            spans: collector.spans,
        },
        glyphs: collector.glyphs,
        glyph_marked_content: collector.glyph_marked_content,
        glyph_layout_geometry: collector.glyph_layout_geometry,
        separators,
        quality: collector.quality,
        warnings,
        vector_paths,
    })
}

#[allow(clippy::too_many_arguments)]
fn process_operation_sequence(
    document: &PdfDocument,
    page: &PdfPage,
    resources: &PdfDictionary,
    operations: &[ContentOperation],
    state: &mut TextState,
    collector: &mut PageCollector,
    warnings: &mut Vec<TextWarning>,
    warning_keys: &mut BTreeSet<(String, Option<String>)>,
    marked_content: &mut MarkedContentState,
    form_stack: &mut BTreeSet<crate::ObjectId>,
    depth: usize,
) -> PdfResult<()> {
    if depth > document.limits.max_object_depth {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "Form XObject nesting limit exceeded",
        ));
    }
    let fonts = load_fonts_from_resources(document, resources)?;
    let mut graphics_stack = Vec::new();
    let initial_marked_depth = marked_content.frames.len();
    for operation in operations {
        if operation.operator == b"Do" {
            process_form_xobject(
                document,
                page,
                resources,
                operation,
                state,
                collector,
                warnings,
                warning_keys,
                marked_content,
                form_stack,
                depth,
            )?;
        } else {
            process_operation(
                document,
                resources,
                operation,
                page,
                &fonts,
                state,
                &mut graphics_stack,
                collector,
                warnings,
                warning_keys,
                marked_content,
                document.limits.max_text_spans,
            )?;
        }
    }
    while marked_content.frames.len() > initial_marked_depth {
        push_warning_once(
            page.index,
            "actual_text_invalid",
            None,
            "marked-content sequence ended without EMC; ActualText was closed implicitly",
            warnings,
            warning_keys,
        );
        let frame = marked_content.frames.pop().expect("depth checked");
        close_marked_content_frame(
            page,
            state,
            collector,
            frame,
            document.limits.max_text_spans,
        )?;
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn process_form_xobject(
    document: &PdfDocument,
    page: &PdfPage,
    resources: &PdfDictionary,
    operation: &ContentOperation,
    state: &TextState,
    collector: &mut PageCollector,
    warnings: &mut Vec<TextWarning>,
    warning_keys: &mut BTreeSet<(String, Option<String>)>,
    marked_content: &mut MarkedContentState,
    form_stack: &mut BTreeSet<crate::ObjectId>,
    depth: usize,
) -> PdfResult<()> {
    require_operand_count(operation, 1)?;
    let PdfObject::Name(name) = &operation.operands[0] else {
        return invalid_operation(operation, "Do operand must be a name");
    };
    let Some(xobjects) = resources.get(&PdfName(b"XObject".to_vec())) else {
        return Ok(());
    };
    let xobjects = resolve_dictionary(document, xobjects)?;
    let Some(target) = xobjects.get(name) else {
        return Ok(());
    };
    let target_id = target.as_reference();
    if let Some(id) = target_id
        && !form_stack.insert(id)
    {
        return Err(PdfError::new(
            ErrorCode::InvalidReference,
            Some(operation.offset),
            "cyclic Form XObject reference",
        ));
    }
    let result = (|| {
        let value = if let Some(id) = target_id {
            document.object(id)?.value
        } else {
            target.clone()
        };
        let PdfObject::Stream(stream) = value else {
            return Ok(());
        };
        if !matches!(
            stream.dictionary.get(&PdfName(b"Subtype".to_vec())),
            Some(PdfObject::Name(subtype)) if subtype.is(b"Form")
        ) {
            return Ok(());
        }
        let form_resources = stream
            .dictionary
            .get(&PdfName(b"Resources".to_vec()))
            .map(|object| resolve_dictionary(document, object))
            .transpose()?
            .unwrap_or_else(|| resources.clone());
        let mut form_state = state.clone();
        if let Some(matrix) = stream.dictionary.get(&PdfName(b"Matrix".to_vec())) {
            form_state.ctm = form_state.ctm.multiply(parse_matrix(matrix)?);
            if !form_state.ctm.is_finite() {
                return Err(PdfError::new(
                    ErrorCode::InvalidObject,
                    None,
                    "Form transformation matrix must be finite",
                ));
            }
        }
        let decoded = document.decode_stream(&stream)?;
        let operations = parse_content(&decoded, &document.limits)?;
        process_operation_sequence(
            document,
            page,
            &form_resources,
            &operations,
            &mut form_state,
            collector,
            warnings,
            warning_keys,
            marked_content,
            form_stack,
            depth + 1,
        )
    })();
    if let Some(id) = target_id {
        form_stack.remove(&id);
    }
    result
}

#[allow(clippy::too_many_arguments, clippy::too_many_lines)]
fn process_operation(
    document: &PdfDocument,
    resources: &PdfDictionary,
    operation: &ContentOperation,
    page: &PdfPage,
    fonts: &std::collections::BTreeMap<PdfName, FontDecoder>,
    state: &mut TextState,
    graphics_stack: &mut Vec<TextState>,
    collector: &mut PageCollector,
    warnings: &mut Vec<TextWarning>,
    warning_keys: &mut BTreeSet<(String, Option<String>)>,
    marked_content: &mut MarkedContentState,
    max_spans: usize,
) -> PdfResult<()> {
    match operation.operator.as_slice() {
        b"BMC" => {
            require_operand_count(operation, 1)?;
            let PdfObject::Name(tag) = &operation.operands[0] else {
                return invalid_operation(operation, "BMC tag operand must be a name");
            };
            let properties = MarkedContentProperties::for_tag(tag).inherit_context(
                marked_content.is_artifact(),
                marked_content.current_alt_text(),
            );
            push_marked_content_frame(
                marked_content,
                properties,
                collector,
                document.limits.max_object_depth,
                operation,
            )?;
        }
        b"BDC" => {
            require_operand_count(operation, 2)?;
            let PdfObject::Name(tag) = &operation.operands[0] else {
                return invalid_operation(operation, "BDC tag operand must be a name");
            };
            let properties = match resolve_marked_content_properties(
                document,
                resources,
                tag,
                &operation.operands[1],
            ) {
                Ok(resolution) => {
                    if resolution.invalid_actual_text {
                        push_warning_once(
                            page.index,
                            "actual_text_invalid",
                            None,
                            "marked-content ActualText is invalid; enclosed text was retained",
                            warnings,
                            warning_keys,
                        );
                    }
                    if resolution.invalid_other {
                        push_warning_once(
                            page.index,
                            "marked_content_invalid",
                            None,
                            "marked-content MCID or Alt is invalid; valid properties were retained",
                            warnings,
                            warning_keys,
                        );
                    }
                    resolution.properties
                }
                Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
                Err(error) => {
                    let (code, message) = if error.code == ErrorCode::InvalidReference {
                        (
                            "actual_text_invalid",
                            "cyclic marked-content property was retained for legacy compatibility",
                        )
                    } else {
                        (
                            "marked_content_invalid",
                            "marked-content property list is invalid; tag and enclosed text were retained",
                        )
                    };
                    push_warning_once(page.index, code, None, message, warnings, warning_keys);
                    MarkedContentProperties::for_tag(tag)
                }
            };
            let properties = properties.inherit_context(
                marked_content.is_artifact(),
                marked_content.current_alt_text(),
            );
            push_marked_content_frame(
                marked_content,
                properties,
                collector,
                document.limits.max_object_depth,
                operation,
            )?;
        }
        b"EMC" => {
            require_operand_count(operation, 0)?;
            if let Some(frame) = marked_content.frames.pop() {
                close_marked_content_frame(page, state, collector, frame, max_spans)?;
            } else {
                push_warning_once(
                    page.index,
                    "actual_text_invalid",
                    None,
                    "EMC has no matching marked-content opener",
                    warnings,
                    warning_keys,
                );
            }
        }
        b"q" => graphics_stack.push(state.clone()),
        b"Q" => {
            if let Some(saved) = graphics_stack.pop() {
                *state = saved;
            }
        }
        b"cm" => {
            let values = six_numbers(operation)?;
            state.ctm = state.ctm.multiply(Matrix {
                a: values[0],
                b: values[1],
                c: values[2],
                d: values[3],
                e: values[4],
                f: values[5],
            });
            if !state.ctm.is_finite() {
                return invalid_operation(operation, "graphics matrix must be finite");
            }
        }
        b"BT" => {
            state.text_matrix = Matrix::IDENTITY;
            state.line_matrix = Matrix::IDENTITY;
            state.legacy_text_matrix = Matrix::IDENTITY;
            state.legacy_line_matrix = Matrix::IDENTITY;
        }
        b"Tf" => {
            require_operand_count(operation, 2)?;
            let PdfObject::Name(name) = &operation.operands[0] else {
                return invalid_operation(operation, "Tf font operand must be a name");
            };
            state.font = Some(name.clone());
            state.font_size = number(&operation.operands[1], operation)?;
        }
        b"Tc" => state.char_spacing = one_number(operation)?,
        b"Tw" => state.word_spacing = one_number(operation)?,
        b"Tz" => state.horizontal_scaling = one_number(operation)?,
        b"TL" => state.leading = one_number(operation)?,
        b"Ts" => state.rise = one_number(operation)?,
        b"Tm" => {
            let values = six_numbers(operation)?;
            let matrix = Matrix {
                a: values[0],
                b: values[1],
                c: values[2],
                d: values[3],
                e: values[4],
                f: values[5],
            };
            if !matrix.is_finite() {
                return invalid_operation(operation, "text matrix must be finite");
            }
            state.text_matrix = matrix;
            state.line_matrix = matrix;
            state.legacy_text_matrix = matrix;
            state.legacy_line_matrix = matrix;
        }
        b"Td" => {
            let [x, y] = two_numbers(operation)?;
            move_text_line(state, x, y);
        }
        b"TD" => {
            let [x, y] = two_numbers(operation)?;
            state.leading = -y;
            move_text_line(state, x, y);
        }
        b"T*" => move_text_line(state, 0.0, -state.leading),
        b"Tj" => {
            require_operand_count(operation, 1)?;
            let PdfObject::String(string) = &operation.operands[0] else {
                return invalid_operation(operation, "Tj operand must be a string");
            };
            show_text(
                string,
                page,
                fonts,
                state,
                collector,
                warnings,
                warning_keys,
                marked_content,
                max_spans,
            )?;
        }
        b"TJ" => {
            require_operand_count(operation, 1)?;
            let PdfObject::Array(items) = &operation.operands[0] else {
                return invalid_operation(operation, "TJ operand must be an array");
            };
            for item in items {
                match item {
                    PdfObject::String(string) => show_text(
                        string,
                        page,
                        fonts,
                        state,
                        collector,
                        warnings,
                        warning_keys,
                        marked_content,
                        max_spans,
                    )?,
                    PdfObject::Integer(_) | PdfObject::Real(_) => {
                        let adjustment = number(item, operation)?;
                        let displacement = -adjustment / 1_000.0 * state.font_size;
                        let writing_mode = state
                            .font
                            .as_ref()
                            .and_then(|name| fonts.get(name))
                            .map_or(WritingMode::Horizontal, FontDecoder::writing_mode);
                        match writing_mode {
                            WritingMode::Horizontal => translate_text_matrix(
                                state,
                                displacement * state.horizontal_scaling / 100.0,
                                0.0,
                            ),
                            WritingMode::Vertical => {
                                translate_text_matrix(state, 0.0, -displacement);
                            }
                        }
                        state.legacy_text_matrix.e +=
                            displacement * state.horizontal_scaling / 100.0;
                    }
                    _ => {
                        return invalid_operation(
                            operation,
                            "TJ entries must be strings or numbers",
                        );
                    }
                }
            }
        }
        b"'" => {
            require_operand_count(operation, 1)?;
            move_text_line(state, 0.0, -state.leading);
            let PdfObject::String(string) = &operation.operands[0] else {
                return invalid_operation(operation, "' operand must be a string");
            };
            show_text(
                string,
                page,
                fonts,
                state,
                collector,
                warnings,
                warning_keys,
                marked_content,
                max_spans,
            )?;
        }
        b"\"" => {
            require_operand_count(operation, 3)?;
            state.word_spacing = number(&operation.operands[0], operation)?;
            state.char_spacing = number(&operation.operands[1], operation)?;
            move_text_line(state, 0.0, -state.leading);
            let PdfObject::String(string) = &operation.operands[2] else {
                return invalid_operation(operation, "\" third operand must be a string");
            };
            show_text(
                string,
                page,
                fonts,
                state,
                collector,
                warnings,
                warning_keys,
                marked_content,
                max_spans,
            )?;
        }
        _ => {}
    }
    Ok(())
}

fn push_marked_content_frame(
    marked_content: &mut MarkedContentState,
    properties: MarkedContentProperties,
    collector: &PageCollector,
    max_depth: usize,
    operation: &ContentOperation,
) -> PdfResult<()> {
    if marked_content.frames.len() >= max_depth {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            Some(operation.offset),
            "marked-content nesting limit exceeded",
        ));
    }
    marked_content.frames.push(MarkedContentFrame {
        properties,
        glyph_start: collector.glyphs.len(),
        span_start: collector.spans.len(),
    });
    Ok(())
}

fn close_marked_content_frame(
    page: &PdfPage,
    state: &TextState,
    collector: &mut PageCollector,
    frame: MarkedContentFrame,
    max_spans: usize,
) -> PdfResult<()> {
    let glyph_context =
        GlyphMarkedContent::from_properties(collector.next_source_ordinal, &frame.properties);
    let Some(actual_text) = frame.properties.actual_text else {
        return Ok(());
    };
    let first_glyph = collector.glyphs.get(frame.glyph_start).cloned();
    let first_layout_geometry = collector
        .glyph_layout_geometry
        .get(frame.glyph_start)
        .copied();
    let last_glyph = collector
        .glyphs
        .last()
        .cloned()
        .filter(|_| collector.glyphs.len() > frame.glyph_start);
    let first_span = collector.spans.get(frame.span_start).cloned();
    collector.glyphs.truncate(frame.glyph_start);
    collector.glyph_marked_content.truncate(frame.glyph_start);
    collector.glyph_layout_geometry.truncate(frame.glyph_start);
    collector.spans.truncate(frame.span_start);
    if actual_text.is_empty() {
        return Ok(());
    }
    if collector.glyphs.len() >= max_spans || collector.spans.len() >= max_spans {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "ActualText replacement limit exceeded",
        ));
    }

    let origin = first_glyph.as_ref().map_or_else(
        || page_text_point(state, 0.0, state.rise),
        |glyph| Ok(glyph.origin),
    )?;
    let baseline = first_glyph
        .as_ref()
        .map_or_else(|| page_baseline(state), |glyph| Ok(glyph.baseline))?;
    let advance = last_glyph.as_ref().map_or([0.0, 0.0], |glyph| {
        [
            glyph.origin[0] + glyph.advance[0] - origin[0],
            glyph.origin[1] + glyph.advance[1] - origin[1],
        ]
    });
    let font_resource = first_glyph.as_ref().map_or_else(
        || {
            state
                .font
                .as_ref()
                .map(|name| String::from_utf8_lossy(name.as_bytes()).into_owned())
        },
        |glyph| glyph.font_resource.clone(),
    );
    let font_size = first_glyph
        .as_ref()
        .map_or(state.font_size, |glyph| glyph.font_size);
    let writing_mode = first_glyph
        .as_ref()
        .map_or(WritingMode::Horizontal, |glyph| glyph.writing_mode);
    let layout_geometry = first_layout_geometry.map_or_else(
        || glyph_layout_geometry(state, FontVerticalMetrics::default()),
        Ok,
    )?;
    collector.glyph_marked_content.push(glyph_context);
    collector.glyph_layout_geometry.push(layout_geometry);
    collector.glyphs.push(PositionedGlyph {
        page_index: page.index,
        source_ordinal: collector.next_source_ordinal,
        unicode: actual_text.clone(),
        text_origin: TextOrigin::ActualText,
        mcid: frame.properties.mcid,
        font_resource: font_resource.clone(),
        font_size,
        writing_mode,
        origin,
        advance,
        baseline,
        rotation_bucket: rotation_bucket(baseline),
    });
    collector.next_source_ordinal =
        collector
            .next_source_ordinal
            .checked_add(1)
            .ok_or_else(|| {
                PdfError::new(
                    ErrorCode::LimitExceeded,
                    None,
                    "text source ordinal overflow",
                )
            })?;
    collector.spans.push(TextSpan {
        page_index: page.index,
        text: actual_text,
        font_resource,
        font_name: first_span.and_then(|span| span.font_name),
        font_size,
        x: origin[0],
        y: origin[1],
    });
    Ok(())
}

#[allow(
    clippy::too_many_arguments,
    clippy::cast_precision_loss,
    clippy::too_many_lines
)]
fn show_text(
    string: &PdfString,
    page: &PdfPage,
    fonts: &std::collections::BTreeMap<PdfName, FontDecoder>,
    state: &mut TextState,
    collector: &mut PageCollector,
    warnings: &mut Vec<TextWarning>,
    warning_keys: &mut BTreeSet<(String, Option<String>)>,
    marked_content: &mut MarkedContentState,
    max_spans: usize,
) -> PdfResult<()> {
    if collector.spans.len() >= max_spans {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "text span limit exceeded",
        ));
    }
    let font = state.font.as_ref().and_then(|name| fonts.get(name));
    let suppress_fidelity = marked_content.has_actual_text();
    let decoded = if let Some(font) = font {
        font.decode(&string.0)
    } else {
        let glyphs = string
            .0
            .iter()
            .map(|byte| {
                let unicode = if byte.is_ascii() {
                    char::from(*byte).to_string()
                } else {
                    "\u{fffd}".to_owned()
                };
                crate::font::DecodedGlyph {
                    unicode,
                    code: u32::from(*byte),
                    missing_mapping: !byte.is_ascii(),
                    invalid_mapping: false,
                    used_fallback: true,
                }
            })
            .collect::<Vec<_>>();
        crate::font::DecodedText {
            text: glyphs.iter().map(|glyph| glyph.unicode.as_str()).collect(),
            missing_mappings: glyphs.iter().filter(|glyph| glyph.missing_mapping).count(),
            invalid_mappings: 0,
            used_fallback: true,
            legacy_glyph_count: string.0.len(),
            glyphs,
        }
    };
    let resource = state
        .font
        .as_ref()
        .map(|name| String::from_utf8_lossy(name.as_bytes()).into_owned());
    if !suppress_fidelity {
        if font.is_none() {
            push_warning_once(
                page.index,
                "font_not_found",
                resource.clone(),
                "text-showing operator references an unavailable font",
                warnings,
                warning_keys,
            );
        } else if decoded.used_fallback {
            push_warning_once(
                page.index,
                "font_fallback_encoding",
                resource.clone(),
                "font has no ToUnicode CMap; fallback decoding may be ambiguous",
                warnings,
                warning_keys,
            );
        }
        if decoded.invalid_mappings > 0 {
            push_warning_once(
                page.index,
                "unicode_mapping_invalid",
                resource.clone(),
                "one or more character codes have an invalid ToUnicode destination",
                warnings,
                warning_keys,
            );
        }
        if decoded.missing_mappings > 0 {
            push_warning_once(
                page.index,
                "unicode_mapping_missing",
                resource.clone(),
                "one or more character codes have no Unicode mapping",
                warnings,
                warning_keys,
            );
        }
    }
    let run_origin = legacy_text_point(state)?;
    let font_name = font.and_then(|font| font.base_name.clone());
    for glyph in &decoded.glyphs {
        if collector.glyphs.len() >= max_spans {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "positioned glyph limit exceeded",
            ));
        }
        let origin = page_text_point(state, 0.0, state.rise)?;
        let baseline = page_baseline(state)?;
        let writing_mode = font.map_or(WritingMode::Horizontal, FontDecoder::writing_mode);
        let vertical_metrics =
            font.map_or_else(FontVerticalMetrics::default, FontDecoder::vertical_metrics);
        let layout_geometry = glyph_layout_geometry(state, vertical_metrics)?;
        let width = font.map_or(500.0, |font| font.glyph_width(glyph.code));
        if !width.is_finite() {
            return Err(PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "font width must be finite",
            ));
        }
        let word_spacing = if font.map_or(glyph.code == 32, |font| {
            font.word_spacing_applies(glyph.code)
        }) {
            state.word_spacing
        } else {
            0.0
        };
        let displacement = width / 1_000.0 * state.font_size + state.char_spacing + word_spacing;
        let (text_x, text_y) = match writing_mode {
            WritingMode::Horizontal => (displacement * state.horizontal_scaling / 100.0, 0.0),
            WritingMode::Vertical => (0.0, -displacement),
        };
        if !text_x.is_finite() || !text_y.is_finite() {
            return Err(PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "text advance must be finite",
            ));
        }
        translate_text_matrix(state, text_x, text_y);
        let next_origin = page_text_point(state, 0.0, state.rise)?;
        let text_origin = if glyph.missing_mapping || glyph.unicode.contains('\u{fffd}') {
            TextOrigin::Replacement
        } else if glyph.used_fallback {
            TextOrigin::FontFallback
        } else {
            TextOrigin::ToUnicode
        };
        collector
            .glyph_marked_content
            .push(marked_content.glyph_context(collector.next_source_ordinal));
        collector.glyph_layout_geometry.push(layout_geometry);
        collector.glyphs.push(PositionedGlyph {
            page_index: page.index,
            source_ordinal: collector.next_source_ordinal,
            unicode: glyph.unicode.clone(),
            text_origin,
            mcid: marked_content.current_mcid(),
            font_resource: resource.clone(),
            font_size: state.font_size,
            writing_mode,
            origin,
            advance: [next_origin[0] - origin[0], next_origin[1] - origin[1]],
            baseline,
            rotation_bucket: rotation_bucket(baseline),
        });
        collector.next_source_ordinal =
            collector
                .next_source_ordinal
                .checked_add(1)
                .ok_or_else(|| {
                    PdfError::new(
                        ErrorCode::LimitExceeded,
                        None,
                        "text source ordinal overflow",
                    )
                })?;
        if !suppress_fidelity {
            if glyph.used_fallback {
                collector.quality.fallback_glyphs += 1;
            }
            collector.quality.replacement_characters += glyph
                .unicode
                .chars()
                .filter(|value| *value == '\u{fffd}')
                .count();
        }
    }
    let spaces = decoded
        .text
        .chars()
        .filter(|character| *character == ' ')
        .count();
    let nominal = decoded.legacy_glyph_count as f64 * state.font_size * 0.5;
    let spacing =
        decoded.legacy_glyph_count as f64 * state.char_spacing + spaces as f64 * state.word_spacing;
    state.legacy_text_matrix.e += (nominal + spacing) * state.horizontal_scaling / 100.0;
    collector.spans.push(TextSpan {
        page_index: page.index,
        text: decoded.text,
        font_resource: resource,
        font_name,
        font_size: state.font_size,
        x: run_origin[0],
        y: run_origin[1],
    });
    Ok(())
}

fn legacy_text_point(state: &TextState) -> PdfResult<[f64; 2]> {
    let point = state.ctm.transform(
        state.legacy_text_matrix.e,
        state.legacy_text_matrix.f + state.rise,
    );
    if point.0.is_finite() && point.1.is_finite() {
        Ok([point.0, point.1])
    } else {
        Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "legacy text geometry must be finite",
        ))
    }
}

fn page_text_point(state: &TextState, x: f64, y: f64) -> PdfResult<[f64; 2]> {
    let text_point = state.text_matrix.transform(x, y);
    let page_point = state.ctm.transform(text_point.0, text_point.1);
    if page_point.0.is_finite() && page_point.1.is_finite() {
        Ok([page_point.0, page_point.1])
    } else {
        Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "text geometry must be finite",
        ))
    }
}

fn glyph_layout_geometry(
    state: &TextState,
    metrics: FontVerticalMetrics,
) -> PdfResult<GlyphLayoutGeometry> {
    let top = page_text_point(
        state,
        0.0,
        state.rise + state.font_size * metrics.ascent / 1_000.0,
    )?;
    let bottom = page_text_point(
        state,
        0.0,
        state.rise + state.font_size * metrics.descent / 1_000.0,
    )?;
    Ok(GlyphLayoutGeometry { top, bottom })
}

fn page_baseline(state: &TextState) -> PdfResult<[f64; 2]> {
    let start = page_text_point(state, 0.0, state.rise)?;
    let end = page_text_point(state, 1.0, state.rise)?;
    let x = end[0] - start[0];
    let y = end[1] - start[1];
    let length = x.hypot(y);
    if !length.is_finite() || length <= f64::EPSILON {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "text baseline is degenerate",
        ));
    }
    Ok([x / length, y / length])
}

fn translate_text_matrix(state: &mut TextState, x: f64, y: f64) {
    state.text_matrix.e += x * state.text_matrix.a + y * state.text_matrix.c;
    state.text_matrix.f += x * state.text_matrix.b + y * state.text_matrix.d;
}

#[allow(clippy::cast_possible_truncation)]
fn rotation_bucket(baseline: [f64; 2]) -> i16 {
    let degrees = baseline[1].atan2(baseline[0]).to_degrees();
    let bucket = (degrees / 90.0).round() * 90.0;
    let normalized = if bucket > 180.0 {
        bucket - 360.0
    } else if bucket <= -180.0 {
        bucket + 360.0
    } else {
        bucket
    };
    normalized as i16
}

fn push_warning_once(
    page_index: usize,
    code: &str,
    font_resource: Option<String>,
    message: &str,
    warnings: &mut Vec<TextWarning>,
    keys: &mut BTreeSet<(String, Option<String>)>,
) {
    let key = (code.to_owned(), font_resource.clone());
    if keys.insert(key) {
        warnings.push(TextWarning {
            code: code.to_owned(),
            page_index,
            font_resource,
            message: message.to_owned(),
        });
    }
}

fn move_text_line(state: &mut TextState, x: f64, y: f64) {
    state.line_matrix.e += x;
    state.line_matrix.f += y;
    state.text_matrix = state.line_matrix;
    state.legacy_line_matrix.e += x;
    state.legacy_line_matrix.f += y;
    state.legacy_text_matrix = state.legacy_line_matrix;
}

fn require_operand_count(operation: &ContentOperation, expected: usize) -> PdfResult<()> {
    if operation.operands.len() == expected {
        Ok(())
    } else {
        invalid_operation(
            operation,
            &format!(
                "{} expects {expected} operand(s), got {}",
                String::from_utf8_lossy(&operation.operator),
                operation.operands.len()
            ),
        )
    }
}

fn one_number(operation: &ContentOperation) -> PdfResult<f64> {
    require_operand_count(operation, 1)?;
    number(&operation.operands[0], operation)
}

fn two_numbers(operation: &ContentOperation) -> PdfResult<[f64; 2]> {
    require_operand_count(operation, 2)?;
    Ok([
        number(&operation.operands[0], operation)?,
        number(&operation.operands[1], operation)?,
    ])
}

fn six_numbers(operation: &ContentOperation) -> PdfResult<[f64; 6]> {
    require_operand_count(operation, 6)?;
    Ok([
        number(&operation.operands[0], operation)?,
        number(&operation.operands[1], operation)?,
        number(&operation.operands[2], operation)?,
        number(&operation.operands[3], operation)?,
        number(&operation.operands[4], operation)?,
        number(&operation.operands[5], operation)?,
    ])
}

#[allow(clippy::cast_precision_loss)]
fn number(object: &PdfObject, operation: &ContentOperation) -> PdfResult<f64> {
    let value = match object {
        PdfObject::Integer(value) => *value as f64,
        PdfObject::Real(value) => *value,
        _ => return invalid_operation(operation, "numeric operand required"),
    };
    if value.is_finite() {
        Ok(value)
    } else {
        invalid_operation(operation, "numeric operand must be finite")
    }
}

fn invalid_operation<T>(operation: &ContentOperation, message: &str) -> PdfResult<T> {
    Err(PdfError::new(
        ErrorCode::InvalidObject,
        Some(operation.offset),
        message,
    ))
}

fn layout_text(spans: &[TextSpan]) -> String {
    let mut ordered = spans.to_vec();
    ordered.sort_by(|left, right| {
        right
            .y
            .partial_cmp(&left.y)
            .unwrap_or(Ordering::Equal)
            .then_with(|| left.x.partial_cmp(&right.x).unwrap_or(Ordering::Equal))
    });
    let mut output = String::new();
    let mut previous: Option<&TextSpan> = None;
    for span in &ordered {
        if let Some(last) = previous {
            let tolerance = last.font_size.abs().max(span.font_size.abs()).max(1.0) * 0.5;
            if (span.y - last.y).abs() > tolerance {
                if !output.ends_with('\n') {
                    output.push('\n');
                }
            } else if !output.ends_with(char::is_whitespace)
                && !span.text.starts_with(char::is_whitespace)
                && span.x > last.x + last.font_size.max(1.0) * 0.2
            {
                output.push(' ');
            }
        }
        output.push_str(&span.text);
        previous = Some(span);
    }
    output
}
#[cfg(test)]
mod page_producer_tests {
    use super::*;
    use crate::ParseLimits;

    #[test]
    fn producer_delivers_one_page_at_a_time_with_document_ordinals() {
        let bytes = two_page_text_pdf();
        let document = PdfDocument::parse(&bytes).expect("parse two-page PDF");
        let pages = document.pages().expect("pages");
        let mut producer = document.extract_text_page_producer(
            &pages,
            TextExtractionOptions {
                normalize_unicode: false,
                layout: false,
            },
            false,
            false,
        );

        let first = producer.next().expect("first page").expect("extract page");
        assert_eq!(first.page.page_index, 0);
        assert_eq!(first.page.text, "A");
        assert_eq!(first.glyphs.len(), 1);
        assert_eq!(first.glyphs[0].source_ordinal, 0);
        drop(first);

        let second = producer.next().expect("second page").expect("extract page");
        assert_eq!(second.page.page_index, 1);
        assert_eq!(second.page.text, "B");
        assert_eq!(second.glyphs.len(), 1);
        assert_eq!(second.glyphs[0].source_ordinal, 1);
        drop(second);

        assert!(producer.next().is_none());
        assert!(producer.vector_path_error().is_none());
    }

    #[test]
    fn producer_enforces_the_document_glyph_limit_incrementally() {
        let bytes = two_page_text_pdf();
        let limits = ParseLimits {
            max_text_spans: 1,
            ..ParseLimits::default()
        };
        let document = PdfDocument::parse_with_limits(&bytes, limits).expect("parse PDF");
        let pages = document.pages().expect("pages");
        let mut producer = document.extract_text_page_producer(
            &pages,
            TextExtractionOptions {
                normalize_unicode: false,
                layout: false,
            },
            false,
            false,
        );

        producer.next().expect("first page").expect("exact limit");
        let error = producer
            .next()
            .expect("second page")
            .expect_err("document limit must reject the second glyph");
        assert_eq!(error.code, ErrorCode::LimitExceeded);
        assert_eq!(error.message, "document positioned glyph limit exceeded");
        assert!(producer.next().is_none());
    }

    fn two_page_text_pdf() -> Vec<u8> {
        let first_content = b"BT /F1 12 Tf 10 100 Td (A) Tj ET";
        let second_content = b"BT /F1 12 Tf 10 100 Td (B) Tj ET";
        classic_pdf(&[
            b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
            b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 /MediaBox [0 0 200 200] /Resources << /Font << /F1 7 0 R >> >> >>".to_vec(),
            b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>".to_vec(),
            stream_object(first_content),
            b"<< /Type /Page /Parent 2 0 R /Contents 6 0 R >>".to_vec(),
            stream_object(second_content),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        ])
    }

    fn stream_object(content: &[u8]) -> Vec<u8> {
        let mut object = format!("<< /Length {} >>\nstream\n", content.len()).into_bytes();
        object.extend_from_slice(content);
        object.extend_from_slice(b"\nendstream");
        object
    }

    fn classic_pdf(objects: &[Vec<u8>]) -> Vec<u8> {
        let mut pdf = b"%PDF-1.4\n".to_vec();
        let mut offsets = Vec::with_capacity(objects.len());
        for (index, object) in objects.iter().enumerate() {
            offsets.push(pdf.len());
            pdf.extend_from_slice(format!("{} 0 obj\n", index + 1).as_bytes());
            pdf.extend_from_slice(object);
            pdf.extend_from_slice(b"\nendobj\n");
        }
        let xref_offset = pdf.len();
        pdf.extend_from_slice(format!("xref\n0 {}\n", objects.len() + 1).as_bytes());
        pdf.extend_from_slice(b"0000000000 65535 f \n");
        for offset in offsets {
            pdf.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
        }
        pdf.extend_from_slice(
            format!(
                "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n",
                objects.len() + 1
            )
            .as_bytes(),
        );
        pdf
    }
}
