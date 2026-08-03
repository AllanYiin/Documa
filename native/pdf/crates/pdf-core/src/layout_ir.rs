use std::{
    collections::{BTreeMap, BTreeSet},
    fmt::Write as _,
};

#[cfg(not(target_arch = "wasm32"))]
use std::time::Instant;

#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::text::{ExtractedPage, GlyphLayoutGeometry, GlyphMarkedContent};

use crate::{
    BBox, CoordinateSpace, ErrorCode, LAYOUT_SPACE, ObjectId, PageGeometry, ParseLimits,
    PdfDocument, PdfError, PdfPage, PdfResult, Point, PositionedGlyph, Quad, TextExtractionOptions,
    TextOrigin, TextQuality, TextWarning, WritingMode, version_info,
};

/// Current serialized Layout IR schema version.
pub const LAYOUT_IR_SCHEMA_VERSION: u32 = 1;

const GLYPH_RULE_ID: &str = "stage1b_glyph_projection_v1";
const SPAN_RULE_ID: &str = "stage1b_source_span_v1";
const BLOCK_RULE_ID: &str = "stage1b_page_text_block_v1";
const MARKED_BLOCK_RULE_ID: &str = "stage2_marked_content_block_v1";
const TAGGED_BLOCK_RULE_ID: &str = "stage2_tagged_structure_block_v1";
const TEXT_GEOMETRY_CONFIDENCE: f32 = 0.75;
const BLOCK_CONFIDENCE: f32 = 0.60;
const MIN_TEXT_EXTENT: f64 = 0.01;

#[cfg(feature = "serde")]
#[allow(clippy::trivially_copy_pass_by_ref)]
const fn is_false(value: &bool) -> bool {
    !*value
}

/// Options for building the versioned Layout IR.
#[allow(clippy::struct_excessive_bools)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutExtractionOptions {
    pub normalize_unicode: bool,
    pub include_quality_metadata: bool,
    pub include_debug_glyphs: bool,
    pub include_timings: bool,
}

impl Default for LayoutExtractionOptions {
    fn default() -> Self {
        Self {
            normalize_unicode: false,
            include_quality_metadata: true,
            include_debug_glyphs: false,
            include_timings: false,
        }
    }
}

/// Parser identity embedded in each Layout IR result.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutParserInfo {
    pub name: String,
    pub version: String,
    pub stage: String,
}

/// Explicit feature availability for this schema result.
#[allow(clippy::struct_excessive_bools)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutCapabilities {
    pub source_order: bool,
    pub tagged_order: bool,
    pub inferred_order: bool,
    pub main_flow: bool,
    #[cfg_attr(feature = "serde", serde(default))]
    pub visual_reading: bool,
    pub text_blocks: bool,
    pub semantic_roles: bool,
    pub tables: bool,
    pub image_placements: bool,
    pub navigation: bool,
}

impl LayoutCapabilities {
    const STAGE_1B: Self = Self {
        source_order: true,
        tagged_order: false,
        inferred_order: false,
        main_flow: false,
        visual_reading: false,
        text_blocks: true,
        semantic_roles: false,
        tables: false,
        image_placements: false,
        navigation: false,
    };
}

/// Optional non-deterministic timing data.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutTimings {
    pub layout_ns: u64,
}

/// A finite vector in the declared page coordinate space.
#[derive(Debug, Clone, Copy, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutVector {
    pub x: f64,
    pub y: f64,
}

/// A directed line segment in the declared page coordinate space.
#[derive(Debug, Clone, Copy, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutSegment {
    pub start: Point,
    pub end: Point,
}

/// Source evidence attached to a Layout IR decision.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutProvenance {
    pub page_object: ObjectId,
    pub source_ordinal_start: u64,
    pub source_ordinal_end: u64,
    pub mcids: Vec<i64>,
    pub text_origins: Vec<TextOrigin>,
}

/// A stable non-fatal Layout IR warning.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutWarning {
    pub code: String,
    pub page_index: Option<usize>,
    pub font_resource: Option<String>,
    pub node_id: Option<String>,
    pub message: String,
}

/// One debug-only positioned glyph after projection into `LayoutSpace`.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutGlyph {
    pub source_ordinal: u64,
    pub text: String,
    pub text_origin: TextOrigin,
    pub mcid: Option<i64>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub tag: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub alt_text: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub actual_text: Option<String>,
    #[cfg_attr(feature = "serde", serde(default, skip_serializing_if = "is_false"))]
    pub artifact: bool,
    pub font_resource: Option<String>,
    pub font_size: f64,
    pub writing_mode: WritingMode,
    pub rotation: i16,
    pub origin: Point,
    pub advance: LayoutVector,
    pub baseline: LayoutSegment,
    pub bbox: BBox,
    pub quad: Quad,
    pub confidence: f32,
    pub rule_id: String,
}

/// One bounded source-order text run.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutTextSpan {
    pub id: String,
    pub text: String,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub tag: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub alt_text: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub actual_text: Option<String>,
    #[cfg_attr(feature = "serde", serde(default, skip_serializing_if = "is_false"))]
    pub artifact: bool,
    pub font_resource: Option<String>,
    pub font_size: f64,
    pub writing_mode: WritingMode,
    pub rotation: i16,
    pub origin: Point,
    pub advance: LayoutVector,
    pub baseline: LayoutSegment,
    pub bbox: BBox,
    pub quad: Quad,
    pub confidence: f32,
    pub rule_id: String,
    pub provenance: LayoutProvenance,
}

/// Coarse node type; later stages may add more variants without changing coordinates.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum LayoutNodeKind {
    TextBlock,
}

/// Semantic role availability is explicit instead of inferred by consumers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum LayoutNodeRole {
    Unclassified,
    Document,
    Part,
    Section,
    Heading,
    Paragraph,
    List,
    ListItem,
    Label,
    ListBody,
    Table,
    TableRow,
    TableHeader,
    TableCell,
    Figure,
    Caption,
    Formula,
    Form,
    Header,
    Footer,
    PageNumber,
    Artifact,
}

/// One semantic node and its source-order spans.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutNode {
    pub id: String,
    pub kind: LayoutNodeKind,
    pub role: LayoutNodeRole,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub tag: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub alt_text: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub actual_text: Option<String>,
    #[cfg_attr(feature = "serde", serde(default, skip_serializing_if = "is_false"))]
    pub artifact: bool,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub structure_object: Option<ObjectId>,
    pub text: String,
    pub bbox: BBox,
    pub quad: Option<Quad>,
    pub confidence: f32,
    pub rule_id: String,
    pub provenance: LayoutProvenance,
    pub spans: Vec<LayoutTextSpan>,
}

/// Four explicit and non-aliasing node orders.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutOrders {
    pub source_order: Vec<String>,
    pub tagged_order: Vec<String>,
    pub inferred_order: Vec<String>,
    pub main_flow: Vec<String>,
}

/// How content inside one visual-attention block is perceived.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum LayoutVisualBlockOrder {
    /// The parser does not impose a serial order within the block.
    Simultaneous,
}

/// A stable visual cue used by the deterministic attention heuristic.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum LayoutVisualCue {
    Heading,
    LargeText,
    TopEntry,
    CentralPlacement,
    StructuredContent,
    TableAnchor,
    ImageAnchor,
    PeripheralMargin,
    PageFurniture,
    Artifact,
}

/// One semantic block as an atomic visual-attention unit.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutVisualBlock {
    pub id: String,
    pub node_id: String,
    pub bbox: BBox,
    pub internal_order: LayoutVisualBlockOrder,
    pub salience: f64,
    pub may_be_skipped: bool,
    pub cues: Vec<LayoutVisualCue>,
    pub rule_id: String,
}

/// One possible initial focus. Multiple candidates may coexist.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutVisualFocus {
    pub block_id: String,
    pub salience: f64,
}

/// Cognitive movement represented by one visual-reading graph edge.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum LayoutVisualTransitionKind {
    Continue,
    SkipAhead,
    Regression,
}

/// One weighted possible movement between visual-attention blocks.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutVisualTransition {
    pub from_block_id: String,
    pub to_block_id: String,
    pub kind: LayoutVisualTransitionKind,
    pub weight: f64,
}

/// A non-linear, block-based visual reading model.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutVisualReading {
    pub blocks: Vec<LayoutVisualBlock>,
    pub focus_candidates: Vec<LayoutVisualFocus>,
    pub transitions: Vec<LayoutVisualTransition>,
    pub rule_id: String,
}

/// Evidence source selected for one reconstructed table.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum LayoutTableEvidence {
    Tagged,
    VectorLattice,
    TextAlignment,
    Fused,
}

/// Semantic role of one reconstructed table cell.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum LayoutTableCellRole {
    Data,
    RowHeader,
    ColumnHeader,
    BothHeader,
}

/// One bounded, coordinate-normalized reconstructed table.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutTable {
    pub id: String,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub bbox: Option<BBox>,
    pub rows: usize,
    pub columns: usize,
    pub cells: Vec<LayoutTableCell>,
    pub evidence: LayoutTableEvidence,
    pub source_node_ids: Vec<String>,
    pub confidence: f32,
    pub rule_id: String,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub structure_object: Option<ObjectId>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub provenance: Option<LayoutProvenance>,
}

/// One logical cell in a reconstructed table.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutTableCell {
    pub id: String,
    pub row: usize,
    pub column: usize,
    pub row_span: usize,
    pub column_span: usize,
    pub role: LayoutTableCellRole,
    pub text: String,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub bbox: Option<BBox>,
    pub source_node_ids: Vec<String>,
    pub confidence: f32,
    pub rule_id: String,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub structure_object: Option<ObjectId>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub provenance: Option<LayoutProvenance>,
}
/// Typed image placement populated by Stage 5.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutImagePlacement {
    pub id: String,
    pub paint_ordinal: u64,
    pub resource_name: String,
    pub object: Option<ObjectId>,
    pub bbox: BBox,
    pub quad: Quad,
    pub source_node_ids: Vec<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub tag: Option<String>,
    #[cfg_attr(feature = "serde", serde(default, skip_serializing_if = "is_false"))]
    pub artifact: bool,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub structure_object: Option<ObjectId>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub alt_text: Option<String>,
    pub confidence: f32,
    pub rule_id: String,
    pub provenance: LayoutProvenance,
}

/// Safe navigation target metadata. No action is ever executed by the parser.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum LayoutNavigationTargetKind {
    Uri,
    GoTo,
    Unsupported,
}

/// One normalized local, external, or unsupported navigation target.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutNavigationTarget {
    pub kind: LayoutNavigationTargetKind,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub uri: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub destination_name: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub page_index: Option<usize>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub page_object: Option<ObjectId>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub fit: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub unsupported_action: Option<String>,
}

/// One page Link annotation with `LayoutSpace` geometry.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutLinkAnnotation {
    pub id: String,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub object: Option<ObjectId>,
    pub bbox: BBox,
    pub quads: Vec<Quad>,
    pub target: LayoutNavigationTarget,
    pub confidence: f32,
    pub rule_id: String,
}

/// One catalog named destination resolved without executing actions.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutNamedDestination {
    pub name: String,
    pub target: LayoutNavigationTarget,
    pub rule_id: String,
}

/// One flattened outline item in deterministic preorder.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutOutlineItem {
    pub id: String,
    pub title: String,
    pub depth: usize,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub parent_id: Option<String>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub object: Option<ObjectId>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub target: Option<LayoutNavigationTarget>,
    pub rule_id: String,
}

/// Canonical layout result for one page.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct PageLayout {
    pub page_index: usize,
    pub page_number: usize,
    pub object: ObjectId,
    pub coordinate_space: CoordinateSpace,
    pub geometry: PageGeometry,
    pub text: String,
    pub semantic_nodes: Vec<LayoutNode>,
    pub tables: Vec<LayoutTable>,
    pub image_placements: Vec<LayoutImagePlacement>,
    pub links: Vec<LayoutLinkAnnotation>,
    pub orders: LayoutOrders,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub visual_reading: Option<LayoutVisualReading>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub debug_glyphs: Option<Vec<LayoutGlyph>>,
}

/// Versioned document Layout IR shared by all front ends.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct DocumentLayout {
    pub schema_version: u32,
    pub parser: LayoutParserInfo,
    pub coordinate_space: CoordinateSpace,
    pub options: LayoutExtractionOptions,
    pub options_digest: String,
    pub capabilities: LayoutCapabilities,
    pub text: String,
    pub pages: Vec<PageLayout>,
    pub named_destinations: Vec<LayoutNamedDestination>,
    pub outlines: Vec<LayoutOutlineItem>,
    pub warnings: Vec<LayoutWarning>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub quality: Option<TextQuality>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub timings: Option<LayoutTimings>,
}

/// Metadata emitted before the first page in a native Layout event stream.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutDocumentStart {
    pub schema_version: u32,
    pub parser: LayoutParserInfo,
    pub coordinate_space: CoordinateSpace,
    pub options: LayoutExtractionOptions,
    pub options_digest: String,
    pub capabilities: LayoutCapabilities,
    pub page_count: usize,
}

/// Final values for one node whose document-level classification was delayed.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutNodeFinalization {
    pub node_id: String,
    pub role: LayoutNodeRole,
    pub confidence: f32,
    pub rule_id: String,
}

/// Final document-level updates for one already emitted page.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutPageFinalization {
    pub page_index: usize,
    pub node_updates: Vec<LayoutNodeFinalization>,
    pub main_flow: Vec<String>,
}

/// Metadata emitted after every page in a native Layout event stream.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct LayoutDocumentFinalize {
    pub page_count: usize,
    pub text: String,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Option::is_none")
    )]
    pub capabilities: Option<LayoutCapabilities>,
    pub named_destinations: Vec<LayoutNamedDestination>,
    pub outlines: Vec<LayoutOutlineItem>,
    pub warnings: Vec<LayoutWarning>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub quality: Option<TextQuality>,
    #[cfg_attr(feature = "serde", serde(skip_serializing_if = "Option::is_none"))]
    pub timings: Option<LayoutTimings>,
    #[cfg_attr(
        feature = "serde",
        serde(default, skip_serializing_if = "Vec::is_empty")
    )]
    pub page_finalizations: Vec<LayoutPageFinalization>,
}

/// One item in the bounded native Layout production protocol.
#[allow(clippy::large_enum_variant)] // Boxing PageLayout would add one allocation per page.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(
    feature = "serde",
    serde(tag = "event", content = "payload", rename_all = "snake_case")
)]
pub enum LayoutEvent {
    DocumentStart(LayoutDocumentStart),
    Page(PageLayout),
    DocumentFinalize(LayoutDocumentFinalize),
}

/// Owning, no-clone iterator over a finalized document's compatibility events.
#[derive(Debug)]
pub struct LayoutEventStream {
    start: Option<LayoutDocumentStart>,
    pages: std::vec::IntoIter<PageLayout>,
    finalize: Option<LayoutDocumentFinalize>,
}

impl Iterator for LayoutEventStream {
    type Item = LayoutEvent;

    fn next(&mut self) -> Option<Self::Item> {
        if let Some(start) = self.start.take() {
            return Some(LayoutEvent::DocumentStart(start));
        }
        if let Some(page) = self.pages.next() {
            return Some(LayoutEvent::Page(page));
        }
        self.finalize.take().map(LayoutEvent::DocumentFinalize)
    }

    fn size_hint(&self) -> (usize, Option<usize>) {
        let remaining = self.len();
        (remaining, Some(remaining))
    }
}

impl ExactSizeIterator for LayoutEventStream {
    fn len(&self) -> usize {
        usize::from(self.start.is_some()) + self.pages.len() + usize::from(self.finalize.is_some())
    }
}

impl std::iter::FusedIterator for LayoutEventStream {}

impl DocumentLayout {
    /// Move a finalized document into the compatibility event protocol without cloning pages.
    #[must_use]
    pub fn into_event_stream(self) -> LayoutEventStream {
        self.into_event_stream_with_finalizations(Vec::new())
    }

    fn into_event_stream_with_finalizations(
        self,
        page_finalizations: Vec<LayoutPageFinalization>,
    ) -> LayoutEventStream {
        let Self {
            schema_version,
            parser,
            coordinate_space,
            options,
            options_digest,
            capabilities,
            text,
            pages,
            named_destinations,
            outlines,
            warnings,
            quality,
            timings,
        } = self;
        let page_count = pages.len();
        LayoutEventStream {
            start: Some(LayoutDocumentStart {
                schema_version,
                parser,
                coordinate_space,
                options,
                options_digest,
                capabilities,
                page_count,
            }),
            pages: pages.into_iter(),
            finalize: Some(LayoutDocumentFinalize {
                page_count,
                text,
                capabilities: None,
                named_destinations,
                outlines,
                warnings,
                quality,
                timings,
                page_finalizations,
            }),
        }
    }
}

fn furniture_page_finalizations(
    finalization: &crate::reading_order::FurnitureFinalization,
) -> Vec<LayoutPageFinalization> {
    let mut updates_by_page = BTreeMap::<usize, Vec<LayoutNodeFinalization>>::new();
    for patch in &finalization.node_patches {
        updates_by_page
            .entry(patch.page_index)
            .or_default()
            .push(LayoutNodeFinalization {
                node_id: patch.node_id.clone(),
                role: patch.role,
                confidence: patch.confidence,
                rule_id: patch.rule_id.clone(),
            });
    }
    finalization
        .page_main_flow
        .iter()
        .map(|(page_index, main_flow)| LayoutPageFinalization {
            page_index: *page_index,
            node_updates: updates_by_page.remove(page_index).unwrap_or_default(),
            main_flow: main_flow.clone(),
        })
        .collect()
}

/// Fallible, genuinely incremental producer for native Layout events.
///
/// The producer owns clone-shared parser state and compact document indexes. A page is
/// released as soon as its page-local semantics are complete. Cross-page furniture and
/// content-derived capability flags are delivered by `DocumentFinalize`.
#[allow(clippy::struct_excessive_bools)]
pub struct LayoutEventProducer {
    document: PdfDocument,
    options: LayoutExtractionOptions,
    start: Option<LayoutDocumentStart>,
    text_pages: crate::text::TextPageProducer,
    tagged_pages: Vec<crate::tagged_structure::PageTaggedStructureIndex>,
    associations: Vec<crate::tagged_structure::TaggedAssociation>,
    tables: Vec<crate::tagged_structure::TaggedTable>,
    unindexed_table_indices: Vec<usize>,
    page_links: std::vec::IntoIter<Vec<LayoutLinkAnnotation>>,
    named_destinations: Vec<LayoutNamedDestination>,
    outlines: Vec<LayoutOutlineItem>,
    navigation_available: bool,
    warnings: Vec<LayoutWarning>,
    vector_path_warnings: Vec<LayoutWarning>,
    reading_warnings: Vec<LayoutWarning>,
    association_warnings: Vec<(usize, LayoutWarning)>,
    tagged_warnings: Vec<LayoutWarning>,
    tagged_table_warning_groups: Vec<(usize, Vec<LayoutWarning>)>,
    vector_table_warnings: Vec<LayoutWarning>,
    text_table_warnings: Vec<LayoutWarning>,
    figure_warnings: Vec<LayoutWarning>,
    navigation_prelude_warnings: Vec<LayoutWarning>,
    quality: TextQuality,
    reading_state: crate::reading_order::ReadingOrderState,
    vector_table_state: crate::table_reconstruction::VectorTableState,
    text_table_state: crate::table_reconstruction::TextTableState,
    furniture: Option<crate::reading_order::FurnitureCollector>,
    tagged_order: bool,
    semantic_roles: bool,
    semantic_nodes_present: bool,
    document_text: String,
    page_count: usize,
    emitted_pages: usize,
    timer: Option<LayoutTimer>,
    done: bool,
}

impl LayoutEventProducer {
    /// Number of page events that have not yet been produced.
    #[must_use]
    pub fn remaining_pages(&self) -> usize {
        self.page_count.saturating_sub(self.emitted_pages)
    }

    #[allow(clippy::too_many_lines)]
    fn build_page_event(&mut self, extracted: ExtractedPage) -> PdfResult<LayoutEvent> {
        let ExtractedPage {
            source_page: page,
            page: page_text,
            glyphs: page_glyphs,
            glyph_marked_content: page_glyph_marked_content,
            glyph_layout_geometry: page_glyph_layout_geometry,
            separators: _,
            quality: page_quality,
            warnings: page_warnings,
            vector_paths: page_vector_paths,
        } = extracted;
        if page.index != page_text.page_index || page.index != self.emitted_pages {
            return Err(PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "page and extracted-text indices differ",
            ));
        }
        for (glyph, marked_content) in page_glyphs.iter().zip(&page_glyph_marked_content) {
            if glyph.source_ordinal != marked_content.source_ordinal {
                return Err(PdfError::new(
                    ErrorCode::InvalidObject,
                    None,
                    "glyph and marked-content source ordinals differ",
                ));
            }
            if glyph.page_index != page.index {
                return Err(PdfError::new(
                    ErrorCode::InvalidObject,
                    None,
                    "positioned glyph references an unknown page",
                ));
            }
        }

        let mut page_layout = build_page_layout(
            &page,
            &page_text.text,
            page_glyphs
                .iter()
                .zip(&page_glyph_marked_content)
                .zip(&page_glyph_layout_geometry),
            self.options.include_debug_glyphs,
        )?;
        let page_paths = match page_vector_paths {
            Some(Ok(mut paths)) => {
                if let Some(message) = &paths.image_placement_error {
                    self.vector_path_warnings.push(LayoutWarning {
                        code: "image_placement_invalid".to_owned(),
                        page_index: Some(page.index),
                        font_resource: None,
                        node_id: None,
                        message: message.clone(),
                    });
                }
                page_layout.image_placements = std::mem::take(&mut paths.image_placements);
                paths
            }
            Some(Err(_)) | None => crate::vector_paths::PageVectorPaths::default(),
        };
        crate::reading_order::rebuild_page(
            &mut page_layout,
            &self.document.limits,
            &mut self.reading_warnings,
            &mut self.reading_state,
        )?;
        let tagged_page = self.tagged_pages.get(page.index).ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "tagged page index count mismatch",
            )
        })?;
        let (page_has_tagged_order, page_association_warnings) =
            apply_tagged_page_associations(&mut page_layout, tagged_page, &self.associations);
        self.tagged_order |= page_has_tagged_order;
        self.association_warnings.extend(page_association_warnings);
        for &table_index in &tagged_page.table_indices {
            let mut page_table_warnings = Vec::new();
            crate::table_reconstruction::apply_page_tagged_tables(
                &mut page_layout,
                &self.tables,
                &[table_index],
                &self.document.limits,
                &mut page_table_warnings,
            )?;
            self.vector_table_state
                .observe_warnings(&page_table_warnings);
            self.tagged_table_warning_groups
                .push((table_index, page_table_warnings));
        }
        crate::table_reconstruction::apply_page_vector_tables(
            &mut page_layout,
            &page_paths.segments,
            &self.document.limits,
            &mut self.vector_table_warnings,
            &mut self.vector_table_state,
        )?;
        for prior_warnings in [
            self.vector_path_warnings.as_slice(),
            self.reading_warnings.as_slice(),
            self.vector_table_warnings.as_slice(),
        ] {
            self.text_table_state.observe_warnings(prior_warnings);
        }
        crate::table_reconstruction::apply_page_text_tables(
            &mut page_layout,
            &self.document.limits,
            &mut self.text_table_warnings,
            &mut self.text_table_state,
        )?;
        crate::figure_flow::apply_page_figure_caption_flow(
            &mut page_layout,
            &mut self.figure_warnings,
        );
        page_layout.visual_reading = crate::reading_order::build_visual_reading(&page_layout);
        page_layout.links = self.page_links.next().ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "navigation page index ended before page production",
            )
        })?;

        self.semantic_nodes_present |= !page_layout.semantic_nodes.is_empty();
        self.semantic_roles |= page_layout
            .semantic_nodes
            .iter()
            .any(|node| node.role != LayoutNodeRole::Unclassified);
        self.furniture
            .as_mut()
            .expect("furniture collector exists before finalization")
            .push_page(&page_layout);
        if self.emitted_pages != 0 {
            self.document_text.push_str("\n\n");
        }
        self.document_text.push_str(&page_layout.text);
        self.quality.merge(page_quality);
        self.warnings
            .extend(page_warnings.into_iter().map(LayoutWarning::from));
        self.emitted_pages = self.emitted_pages.saturating_add(1);
        Ok(LayoutEvent::Page(page_layout))
    }

    #[cfg_attr(target_arch = "wasm32", allow(clippy::unit_arg))]
    #[allow(clippy::too_many_lines)]
    fn build_finalize_event(&mut self) -> PdfResult<LayoutEvent> {
        if self.emitted_pages != self.page_count {
            return Err(layout_event_error(
                "native layout producer ended before the declared page count",
            ));
        }
        if self.page_links.next().is_some() {
            return Err(PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "navigation page index contains excess pages",
            ));
        }
        if let Some(message) = self.text_pages.vector_path_error().map(str::to_owned) {
            self.vector_path_warnings.clear();
            self.vector_path_warnings.push(LayoutWarning {
                code: "vector_path_invalid".to_owned(),
                page_index: None,
                font_resource: None,
                node_id: None,
                message,
            });
            self.vector_table_warnings.clear();
        }

        let mut no_pages = Vec::<PageLayout>::new();
        for table_index in std::mem::take(&mut self.unindexed_table_indices) {
            let mut table_warnings = Vec::new();
            crate::table_reconstruction::apply_tagged_table_indices(
                &mut no_pages,
                &self.tables,
                std::iter::once(table_index),
                &self.document.limits,
                &mut table_warnings,
            )?;
            self.tagged_table_warning_groups
                .push((table_index, table_warnings));
        }
        self.association_warnings
            .sort_by_key(|(association_index, _)| *association_index);
        self.tagged_warnings.extend(
            std::mem::take(&mut self.association_warnings)
                .into_iter()
                .map(|(_, warning)| warning),
        );
        self.tagged_table_warning_groups
            .sort_by_key(|(table_index, _)| *table_index);
        self.tagged_warnings.extend(
            std::mem::take(&mut self.tagged_table_warning_groups)
                .into_iter()
                .flat_map(|(_, warnings)| warnings),
        );

        let inferred_order = self.reading_state.available();
        let furniture = self
            .furniture
            .take()
            .expect("furniture collector is finalized exactly once")
            .finish();
        let main_flow = furniture.available;
        self.semantic_roles |= !furniture.node_patches.is_empty();
        let page_finalizations = furniture_page_finalizations(&furniture);

        self.warnings.append(&mut self.vector_path_warnings);
        self.warnings.append(&mut self.reading_warnings);
        self.warnings.append(&mut self.tagged_warnings);
        self.warnings.append(&mut self.vector_table_warnings);
        self.warnings.extend(furniture.warnings);
        self.warnings.append(&mut self.text_table_warnings);
        self.warnings.append(&mut self.figure_warnings);
        self.warnings.append(&mut self.navigation_prelude_warnings);
        if self.semantic_nodes_present {
            self.warnings.push(LayoutWarning {
                code: "layout_text_bbox_estimated".to_owned(),
                page_index: None,
                font_resource: None,
                node_id: None,
                message: "Text bounds use bounded font metrics and effective text transforms; "
                    .to_owned()
                    + "glyph outlines, Type3 FontMatrix, and vertical writing remain estimated",
            });
        }

        let capabilities = LayoutCapabilities {
            tagged_order: self.tagged_order,
            inferred_order,
            main_flow,
            visual_reading: inferred_order,
            semantic_roles: self.semantic_roles,
            tables: true,
            image_placements: true,
            navigation: self.navigation_available,
            ..LayoutCapabilities::STAGE_1B
        };
        let timings = finish_layout_timer(
            self.timer
                .take()
                .expect("layout timer is finalized exactly once"),
        );
        Ok(LayoutEvent::DocumentFinalize(LayoutDocumentFinalize {
            page_count: self.page_count,
            text: std::mem::take(&mut self.document_text),
            capabilities: Some(capabilities),
            named_destinations: std::mem::take(&mut self.named_destinations),
            outlines: std::mem::take(&mut self.outlines),
            warnings: std::mem::take(&mut self.warnings),
            quality: self
                .options
                .include_quality_metadata
                .then_some(std::mem::take(&mut self.quality)),
            timings,
            page_finalizations,
        }))
    }
}

impl Iterator for LayoutEventProducer {
    type Item = PdfResult<LayoutEvent>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        if let Some(start) = self.start.take() {
            return Some(Ok(LayoutEvent::DocumentStart(start)));
        }
        match self.text_pages.next() {
            Some(Ok(page)) => {
                let event = self.build_page_event(page);
                if event.is_err() {
                    self.done = true;
                }
                Some(event)
            }
            Some(Err(error)) => {
                self.done = true;
                Some(Err(error))
            }
            None => {
                self.done = true;
                Some(self.build_finalize_event())
            }
        }
    }

    fn size_hint(&self) -> (usize, Option<usize>) {
        if self.done {
            return (0, Some(0));
        }
        let remaining = usize::from(self.start.is_some())
            .saturating_add(self.remaining_pages())
            .saturating_add(1);
        (remaining, Some(remaining))
    }
}

impl std::iter::FusedIterator for LayoutEventProducer {}

/// Convert compatibility and fallible native producer items into one validated event stream.
pub trait LayoutEventItem {
    /// Return the event or propagate the producer's stable parser error.
    ///
    /// # Errors
    ///
    /// Returns the original producer error for fallible native event items.
    fn into_layout_event(self) -> PdfResult<LayoutEvent>;
}

impl LayoutEventItem for LayoutEvent {
    fn into_layout_event(self) -> PdfResult<LayoutEvent> {
        Ok(self)
    }
}

impl LayoutEventItem for PdfResult<LayoutEvent> {
    fn into_layout_event(self) -> PdfResult<LayoutEvent> {
        self
    }
}

/// Collect and validate a Layout event stream into the stable complete-document DTO.
///
/// # Errors
///
/// Returns a stable limit error when declared pages or finalization updates exceed
/// `limits`, and a stable invalid-object error for malformed event ordering,
/// coordinates, page identities, patches, or completion counts.
pub fn collect_layout_events<I>(events: I, limits: &ParseLimits) -> PdfResult<DocumentLayout>
where
    I: IntoIterator,
    I::Item: LayoutEventItem,
{
    let mut events = events.into_iter();
    let Some(first) = events.next() else {
        return Err(layout_event_error(
            "layout event stream must begin with document_start",
        ));
    };
    let LayoutEvent::DocumentStart(start) = first.into_layout_event()? else {
        return Err(layout_event_error(
            "layout event stream must begin with document_start",
        ));
    };
    if start.page_count > limits.max_pages {
        return Err(layout_event_limit("layout event page count exceeds limit"));
    }

    let mut pages = Vec::with_capacity(start.page_count);
    let mut finalize = None;
    for event in events {
        let event = event.into_layout_event()?;
        if finalize.is_some() {
            return Err(layout_event_error(
                "layout event stream contains data after document_finalize",
            ));
        }
        match event {
            LayoutEvent::DocumentStart(_) => {
                return Err(layout_event_error(
                    "layout event stream contains duplicate document_start",
                ));
            }
            LayoutEvent::Page(page) => {
                if pages.len() >= start.page_count {
                    return Err(layout_event_error(
                        "layout event stream contains more pages than declared",
                    ));
                }
                if page.page_index != pages.len() || page.page_number != pages.len() + 1 {
                    return Err(layout_event_error(
                        "layout event page identity is not contiguous",
                    ));
                }
                if page.coordinate_space != start.coordinate_space {
                    return Err(layout_event_error(
                        "layout event page coordinate space differs from document_start",
                    ));
                }
                pages.push(page);
            }
            LayoutEvent::DocumentFinalize(value) => finalize = Some(value),
        }
    }

    let finalize = finalize
        .ok_or_else(|| layout_event_error("layout event stream is missing document_finalize"))?;
    if pages.len() != start.page_count || finalize.page_count != start.page_count {
        return Err(layout_event_error(
            "layout event page counts do not match document_start",
        ));
    }
    apply_layout_finalizations(
        &mut pages,
        finalize.page_finalizations,
        limits.max_pages,
        limits.max_text_spans,
    )?;

    Ok(DocumentLayout {
        schema_version: start.schema_version,
        parser: start.parser,
        coordinate_space: start.coordinate_space,
        options: start.options,
        options_digest: start.options_digest,
        capabilities: finalize.capabilities.unwrap_or(start.capabilities),
        text: finalize.text,
        pages,
        named_destinations: finalize.named_destinations,
        outlines: finalize.outlines,
        warnings: finalize.warnings,
        quality: finalize.quality,
        timings: finalize.timings,
    })
}

fn apply_layout_finalizations(
    pages: &mut [PageLayout],
    finalizations: Vec<LayoutPageFinalization>,
    max_pages: usize,
    max_node_updates: usize,
) -> PdfResult<()> {
    if finalizations.len() > max_pages {
        return Err(layout_event_limit(
            "layout page finalization count exceeds limit",
        ));
    }
    let mut seen_pages = BTreeSet::new();
    let mut total_updates = 0usize;
    for finalization in finalizations {
        if !seen_pages.insert(finalization.page_index) {
            return Err(layout_event_error(
                "layout event stream contains duplicate page finalization",
            ));
        }
        total_updates = total_updates
            .checked_add(finalization.node_updates.len())
            .ok_or_else(|| layout_event_limit("layout node finalization count overflows"))?;
        if total_updates > max_node_updates {
            return Err(layout_event_limit(
                "layout node finalization count exceeds limit",
            ));
        }
        let page = pages.get_mut(finalization.page_index).ok_or_else(|| {
            layout_event_error("layout page finalization references an unknown page")
        })?;
        let node_indices = page
            .semantic_nodes
            .iter()
            .enumerate()
            .map(|(index, node)| (node.id.clone(), index))
            .collect::<BTreeMap<_, _>>();
        let mut seen_nodes = BTreeSet::new();
        for update in finalization.node_updates {
            if !seen_nodes.insert(update.node_id.clone()) {
                return Err(layout_event_error(
                    "layout page finalization contains a duplicate node update",
                ));
            }
            if update.node_id.is_empty()
                || update.rule_id.is_empty()
                || !update.confidence.is_finite()
                || !(0.0..=1.0).contains(&update.confidence)
            {
                return Err(layout_event_error(
                    "layout node finalization contains invalid values",
                ));
            }
            let index = node_indices.get(&update.node_id).ok_or_else(|| {
                layout_event_error("layout node finalization references an unknown node")
            })?;
            let node = &mut page.semantic_nodes[*index];
            node.role = update.role;
            node.confidence = update.confidence;
            node.rule_id = update.rule_id;
        }

        let known_nodes = page
            .semantic_nodes
            .iter()
            .map(|node| node.id.as_str())
            .collect::<BTreeSet<_>>();
        let mut seen_main_flow = BTreeSet::new();
        if finalization.main_flow.iter().any(|node_id| {
            !known_nodes.contains(node_id.as_str()) || !seen_main_flow.insert(node_id.as_str())
        }) {
            return Err(layout_event_error(
                "layout page finalization contains an invalid main_flow",
            ));
        }
        page.orders.main_flow = finalization.main_flow;
    }
    Ok(())
}

fn layout_event_error(message: &str) -> PdfError {
    PdfError::new(ErrorCode::InvalidObject, None, message)
}

fn layout_event_limit(message: &str) -> PdfError {
    PdfError::new(ErrorCode::LimitExceeded, None, message)
}
impl PdfDocument {
    /// Build the stable complete-document Layout IR by collecting validated events.
    ///
    /// # Errors
    ///
    /// Returns a stable parser error for malformed pages, text, geometry, events, or limits.
    pub fn extract_layout(&self, options: LayoutExtractionOptions) -> PdfResult<DocumentLayout> {
        collect_layout_events(self.extract_layout_events(options)?, &self.limits)
    }

    /// Build a genuinely incremental, fallible native Layout event producer.
    ///
    /// Compact tagged/navigation indexes are prepared up front. Page text, geometry,
    /// tables, figures, and links are then produced one page at a time. Cross-page
    /// furniture and final capability flags are delivered by `DocumentFinalize`.
    ///
    /// # Errors
    ///
    /// Returns stable parser errors for document-level index preparation. Page-local
    /// errors discovered later are yielded by [`LayoutEventProducer`].
    pub fn extract_layout_events(
        &self,
        options: LayoutExtractionOptions,
    ) -> PdfResult<LayoutEventProducer> {
        self.build_layout_event_producer(options)
    }

    fn build_layout_event_producer(
        &self,
        options: LayoutExtractionOptions,
    ) -> PdfResult<LayoutEventProducer> {
        #[cfg_attr(target_arch = "wasm32", allow(clippy::let_unit_value))]
        let timer = start_layout_timer(options.include_timings)?;
        let pages = self.pages()?;
        let page_count = pages.len();
        let (tagged, tagged_warnings) = extract_tagged_structure_index(self, &pages)?;
        validate_tagged_structure_index(&tagged)?;
        let crate::tagged_structure::TaggedStructureIndex {
            result,
            pages: tagged_pages,
            unindexed_association_indices: _,
            unindexed_table_indices,
        } = tagged;
        let crate::tagged_structure::TaggedStructureResult {
            associations,
            tables,
            warnings: _,
        } = result;
        let (navigation, navigation_available, mut navigation_prelude_warnings) =
            extract_navigation_index(self, &pages)?;
        if navigation.page_links.len() != page_count {
            return Err(PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "navigation page index count mismatch",
            ));
        }
        let crate::navigation::NavigationIndex {
            page_links,
            named_destinations,
            outlines,
            warnings: navigation_warnings,
        } = navigation;
        navigation_prelude_warnings.extend(navigation_warnings);
        let initial_capabilities = LayoutCapabilities {
            tables: true,
            image_placements: true,
            navigation: navigation_available,
            ..LayoutCapabilities::STAGE_1B
        };
        let start = LayoutDocumentStart {
            schema_version: LAYOUT_IR_SCHEMA_VERSION,
            parser: parser_info(),
            coordinate_space: CoordinateSpace::LayoutSpace,
            options,
            options_digest: options_digest(options),
            capabilities: initial_capabilities,
            page_count,
        };
        let text_pages = self.extract_text_page_producer(
            &pages,
            TextExtractionOptions {
                normalize_unicode: options.normalize_unicode,
                layout: false,
            },
            false,
            true,
        );
        Ok(LayoutEventProducer {
            document: self.clone(),
            options,
            start: Some(start),
            text_pages,
            tagged_pages,
            associations,
            tables,
            unindexed_table_indices,
            page_links: page_links.into_iter(),
            named_destinations,
            outlines,
            navigation_available,
            warnings: Vec::new(),
            vector_path_warnings: Vec::new(),
            reading_warnings: Vec::new(),
            association_warnings: Vec::new(),
            tagged_warnings,
            tagged_table_warning_groups: Vec::new(),
            vector_table_warnings: Vec::new(),
            text_table_warnings: Vec::new(),
            figure_warnings: Vec::new(),
            navigation_prelude_warnings,
            quality: TextQuality::default(),
            reading_state: crate::reading_order::ReadingOrderState::default(),
            vector_table_state: crate::table_reconstruction::VectorTableState::default(),
            text_table_state: crate::table_reconstruction::TextTableState::default(),
            furniture: Some(crate::reading_order::FurnitureCollector::new(page_count)),
            tagged_order: false,
            semantic_roles: false,
            semantic_nodes_present: false,
            document_text: String::new(),
            page_count,
            emitted_pages: 0,
            timer: Some(timer),
            done: false,
        })
    }
}

fn extract_tagged_structure_index(
    document: &PdfDocument,
    source_pages: &[PdfPage],
) -> PdfResult<(
    crate::tagged_structure::TaggedStructureIndex,
    Vec<LayoutWarning>,
)> {
    match crate::tagged_structure::extract_tagged_structure(document, source_pages) {
        Ok(mut tagged) => {
            let warnings = std::mem::take(&mut tagged.warnings)
                .into_iter()
                .map(|warning| LayoutWarning {
                    code: warning.code,
                    page_index: warning.page_index,
                    font_resource: None,
                    node_id: None,
                    message: warning.message,
                })
                .collect();
            Ok((tagged.into_page_index(source_pages.len()), warnings))
        }
        Err(error) if error.code == ErrorCode::LimitExceeded => Err(error),
        Err(error) => Ok((
            crate::tagged_structure::TaggedStructureResult::default()
                .into_page_index(source_pages.len()),
            vec![LayoutWarning {
                code: "tagged_structure_invalid".to_owned(),
                page_index: None,
                font_resource: None,
                node_id: None,
                message: error.message,
            }],
        )),
    }
}

fn validate_tagged_structure_index(
    tagged: &crate::tagged_structure::TaggedStructureIndex,
) -> PdfResult<()> {
    let mut association_indices = tagged
        .pages
        .iter()
        .flat_map(|page| page.association_indices.iter().copied())
        .chain(tagged.unindexed_association_indices.iter().copied())
        .collect::<Vec<_>>();
    association_indices.sort_unstable();
    if association_indices != (0..tagged.result.associations.len()).collect::<Vec<_>>() {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "tagged association page index count mismatch",
        ));
    }
    let mut table_indices = tagged
        .pages
        .iter()
        .flat_map(|page| page.table_indices.iter().copied())
        .chain(tagged.unindexed_table_indices.iter().copied())
        .collect::<Vec<_>>();
    table_indices.sort_unstable();
    if table_indices != (0..tagged.result.tables.len()).collect::<Vec<_>>() {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "tagged table page index count mismatch",
        ));
    }
    Ok(())
}

fn extract_navigation_index(
    document: &PdfDocument,
    pages: &[PdfPage],
) -> PdfResult<(crate::navigation::NavigationIndex, bool, Vec<LayoutWarning>)> {
    match crate::navigation::extract_navigation(document, pages) {
        Ok(navigation) => Ok((navigation, true, Vec::new())),
        Err(error) if error.code == ErrorCode::LimitExceeded => Err(error),
        Err(error) => {
            let mut navigation = crate::navigation::NavigationIndex::default();
            navigation.page_links.resize_with(pages.len(), Vec::new);
            Ok((
                navigation,
                false,
                vec![LayoutWarning {
                    code: "navigation_target_invalid".to_owned(),
                    page_index: None,
                    font_resource: None,
                    node_id: None,
                    message: error.message,
                }],
            ))
        }
    }
}
fn apply_tagged_page_associations(
    page: &mut PageLayout,
    indexed_page: &crate::tagged_structure::PageTaggedStructureIndex,
    associations: &[crate::tagged_structure::TaggedAssociation],
) -> (bool, Vec<(usize, LayoutWarning)>) {
    let mut mcid_node_indices = BTreeMap::<i64, Vec<usize>>::new();
    for (node_index, node) in page.semantic_nodes.iter().enumerate() {
        for mcid in &node.provenance.mcids {
            mcid_node_indices.entry(*mcid).or_default().push(node_index);
        }
    }
    let mut associated_items = BTreeSet::new();
    let mut warning_keys = BTreeSet::new();
    let mut warnings = Vec::new();
    let mut any_tagged_order = false;
    for &association_index in &indexed_page.association_indices {
        let association = &associations[association_index];
        let matches = mcid_node_indices
            .get(&association.mcid)
            .map(Vec::as_slice)
            .unwrap_or_default();
        let image_matches = page
            .image_placements
            .iter()
            .enumerate()
            .filter(|(_, placement)| placement.provenance.mcids.contains(&association.mcid))
            .map(|(index, _)| index)
            .collect::<Vec<_>>();
        apply_tagged_figure_association(page, association, matches, &image_matches);
        if matches.is_empty() && image_matches.is_empty() {
            if warning_keys.insert(("tagged_mcid_missing", association.mcid)) {
                warnings.push((
                    association_index,
                    LayoutWarning {
                        code: "tagged_mcid_missing".to_owned(),
                        page_index: Some(page.page_index),
                        font_resource: None,
                        node_id: None,
                        message: "structure MCID has no collected page content".to_owned(),
                    },
                ));
            }
            continue;
        }
        if (!associated_items.insert(association.mcid) || matches.len() > 1)
            && warning_keys.insert(("tagged_mcid_ambiguous", association.mcid))
        {
            warnings.push((
                association_index,
                LayoutWarning {
                    code: "tagged_mcid_ambiguous".to_owned(),
                    page_index: Some(page.page_index),
                    font_resource: None,
                    node_id: None,
                    message: "MCID has multiple structure or content associations".to_owned(),
                },
            ));
        }
        for &index in matches {
            let node = &mut page.semantic_nodes[index];
            node.tag = Some(association.tag.clone());
            if association.alt_text.is_some() {
                node.alt_text.clone_from(&association.alt_text);
            }
            if association.actual_text.is_some() {
                node.actual_text.clone_from(&association.actual_text);
            }
            node.structure_object = association.structure_object;
            node.role = association
                .standard_role
                .as_deref()
                .map_or(LayoutNodeRole::Unclassified, role_for_tag);
            TAGGED_BLOCK_RULE_ID.clone_into(&mut node.rule_id);
            if !page.orders.tagged_order.contains(&node.id) {
                page.orders.tagged_order.push(node.id.clone());
                any_tagged_order = true;
            }
        }
    }
    (any_tagged_order, warnings)
}

fn apply_tagged_figure_association(
    page: &mut PageLayout,
    association: &crate::tagged_structure::TaggedAssociation,
    node_matches: &[usize],
    image_matches: &[usize],
) {
    if association.standard_role.as_deref() != Some("Figure") {
        return;
    }
    let linked_node_ids = node_matches
        .iter()
        .map(|index| page.semantic_nodes[*index].id.clone())
        .collect::<Vec<_>>();
    for index in image_matches {
        let placement = &mut page.image_placements[*index];
        for node_id in &linked_node_ids {
            if !placement.source_node_ids.contains(node_id) {
                placement.source_node_ids.push(node_id.clone());
            }
        }
        placement.tag = Some(association.tag.clone());
        placement.structure_object = association.structure_object;
        if association.alt_text.is_some() {
            placement.alt_text.clone_from(&association.alt_text);
        }
        placement.confidence = 1.0;
        "stage5b_tagged_figure_v1".clone_into(&mut placement.rule_id);
    }
}

fn build_page_layout<'a>(
    page: &PdfPage,
    text: &str,
    glyphs: impl Iterator<
        Item = (
            (&'a PositionedGlyph, &'a GlyphMarkedContent),
            &'a GlyphLayoutGeometry,
        ),
    >,
    include_debug_glyphs: bool,
) -> PdfResult<PageLayout> {
    let projected = glyphs
        .map(|((glyph, marked_content), geometry)| {
            project_glyph(page, glyph, marked_content, geometry)
        })
        .collect::<PdfResult<Vec<_>>>()?;
    let spans = merge_spans(page, &projected)?;
    let semantic_nodes = build_text_nodes(page, spans)?;
    let source_order = semantic_nodes.iter().map(|node| node.id.clone()).collect();
    Ok(PageLayout {
        page_index: page.index,
        page_number: page.index + 1,
        object: page.id,
        coordinate_space: CoordinateSpace::LayoutSpace,
        geometry: page.geometry.clone(),
        text: text.to_owned(),
        semantic_nodes,
        tables: Vec::new(),
        image_placements: Vec::new(),
        links: Vec::new(),
        orders: LayoutOrders {
            source_order,
            ..LayoutOrders::default()
        },
        visual_reading: None,
        debug_glyphs: include_debug_glyphs.then_some(projected),
    })
}

fn project_glyph(
    page: &PdfPage,
    glyph: &PositionedGlyph,
    marked_content: &GlyphMarkedContent,
    geometry: &GlyphLayoutGeometry,
) -> PdfResult<LayoutGlyph> {
    let pdf_origin = Point::try_new(glyph.origin[0], glyph.origin[1])?;
    let pdf_end = Point::try_new(
        glyph.origin[0] + glyph.advance[0],
        glyph.origin[1] + glyph.advance[1],
    )?;
    let pdf_baseline = Point::try_new(
        glyph.origin[0] + glyph.baseline[0],
        glyph.origin[1] + glyph.baseline[1],
    )?;
    let origin = checked_layout_point(&page.geometry, pdf_origin)?;
    let actual_end = checked_layout_point(&page.geometry, pdf_end)?;
    let baseline_end = checked_layout_point(&page.geometry, pdf_baseline)?;
    let baseline_vector = normalized_vector(origin, baseline_end)?;
    let advance = LayoutVector {
        x: actual_end.x - origin.x,
        y: actual_end.y - origin.y,
    };
    let extent = estimated_text_extent(page, glyph.font_size);
    let advance_length = advance.x.hypot(advance.y);
    let quad_end = if advance_length <= MIN_TEXT_EXTENT {
        Point::try_new(
            origin.x + baseline_vector.x * extent.mul_add(0.25, 0.0).max(MIN_TEXT_EXTENT),
            origin.y + baseline_vector.y * extent.mul_add(0.25, 0.0).max(MIN_TEXT_EXTENT),
        )?
    } else {
        actual_end
    };
    let quad = if glyph.writing_mode == WritingMode::Horizontal {
        transformed_text_quad(&page.geometry, origin, quad_end, geometry)?.unwrap_or(text_quad(
            origin,
            quad_end,
            baseline_vector,
            extent,
        )?)
    } else {
        text_quad(origin, quad_end, baseline_vector, extent)?
    };
    Ok(LayoutGlyph {
        source_ordinal: glyph.source_ordinal,
        text: glyph.unicode.clone(),
        text_origin: glyph.text_origin,
        mcid: glyph.mcid,
        tag: marked_content.tag.clone(),
        alt_text: marked_content.alt_text.clone(),
        actual_text: marked_content.actual_text.clone(),
        artifact: marked_content.artifact,
        font_resource: glyph.font_resource.clone(),
        font_size: glyph.font_size,
        writing_mode: glyph.writing_mode,
        rotation: glyph.rotation_bucket,
        origin,
        advance,
        baseline: LayoutSegment {
            start: origin,
            end: actual_end,
        },
        bbox: quad.bounding_box()?,
        quad,
        confidence: TEXT_GEOMETRY_CONFIDENCE,
        rule_id: GLYPH_RULE_ID.to_owned(),
    })
}

fn transformed_text_quad(
    page_geometry: &PageGeometry,
    origin: Point,
    end: Point,
    geometry: &GlyphLayoutGeometry,
) -> PdfResult<Option<Quad>> {
    let top_left = checked_layout_point(
        page_geometry,
        Point::try_new(geometry.top[0], geometry.top[1])?,
    )?;
    let bottom_left = checked_layout_point(
        page_geometry,
        Point::try_new(geometry.bottom[0], geometry.bottom[1])?,
    )?;
    let vertical_span = (top_left.x - bottom_left.x).hypot(top_left.y - bottom_left.y);
    if !vertical_span.is_finite() || vertical_span <= MIN_TEXT_EXTENT {
        return Ok(None);
    }
    let top_right = Point::try_new(end.x + top_left.x - origin.x, end.y + top_left.y - origin.y)?;
    let bottom_right = Point::try_new(
        end.x + bottom_left.x - origin.x,
        end.y + bottom_left.y - origin.y,
    )?;
    Ok(Some(Quad {
        top_left,
        top_right,
        bottom_right,
        bottom_left,
    }))
}

fn checked_layout_point(geometry: &PageGeometry, point: Point) -> PdfResult<Point> {
    let transformed = geometry.pdf_point_to_layout(point);
    Point::try_new(transformed.x, transformed.y)
}

fn normalized_vector(start: Point, end: Point) -> PdfResult<LayoutVector> {
    let x = end.x - start.x;
    let y = end.y - start.y;
    let length = x.hypot(y);
    if !length.is_finite() || length <= f64::EPSILON {
        return Err(PdfError::new(
            ErrorCode::InvalidPageGeometry,
            None,
            "projected text baseline is degenerate",
        ));
    }
    Ok(LayoutVector {
        x: x / length,
        y: y / length,
    })
}

fn estimated_text_extent(page: &PdfPage, font_size: f64) -> f64 {
    let page_extent = page
        .geometry
        .layout_bounds
        .width()
        .max(page.geometry.layout_bounds.height())
        .max(MIN_TEXT_EXTENT);
    (font_size.abs() * page.geometry.user_unit).clamp(MIN_TEXT_EXTENT, page_extent * 2.0)
}

fn text_quad(start: Point, end: Point, baseline: LayoutVector, extent: f64) -> PdfResult<Quad> {
    let normal = LayoutVector {
        x: -baseline.y,
        y: baseline.x,
    };
    let top = -0.8 * extent;
    let bottom = 0.2 * extent;
    Ok(Quad {
        top_left: offset_point(start, normal, top)?,
        top_right: offset_point(end, normal, top)?,
        bottom_right: offset_point(end, normal, bottom)?,
        bottom_left: offset_point(start, normal, bottom)?,
    })
}

fn offset_point(point: Point, vector: LayoutVector, scale: f64) -> PdfResult<Point> {
    Point::try_new(
        vector.x.mul_add(scale, point.x),
        vector.y.mul_add(scale, point.y),
    )
}

fn merge_spans(page: &PdfPage, glyphs: &[LayoutGlyph]) -> PdfResult<Vec<LayoutTextSpan>> {
    let mut spans = Vec::new();
    for glyph in glyphs {
        if let Some(previous) = spans.last_mut()
            && can_merge(previous, glyph)
        {
            merge_glyph(previous, glyph)?;
            continue;
        }
        spans.push(span_from_glyph(page, spans.len(), glyph));
    }
    Ok(spans)
}

fn span_from_glyph(page: &PdfPage, index: usize, glyph: &LayoutGlyph) -> LayoutTextSpan {
    LayoutTextSpan {
        id: format!("p{}-s{index}", page.index),
        text: glyph.text.clone(),
        tag: glyph.tag.clone(),
        alt_text: glyph.alt_text.clone(),
        actual_text: glyph.actual_text.clone(),
        artifact: glyph.artifact,
        font_resource: glyph.font_resource.clone(),
        font_size: glyph.font_size,
        writing_mode: glyph.writing_mode,
        rotation: glyph.rotation,
        origin: glyph.origin,
        advance: glyph.advance,
        baseline: glyph.baseline,
        bbox: glyph.bbox,
        quad: glyph.quad,
        confidence: glyph.confidence,
        rule_id: SPAN_RULE_ID.to_owned(),
        provenance: LayoutProvenance {
            page_object: page.id,
            source_ordinal_start: glyph.source_ordinal,
            source_ordinal_end: glyph.source_ordinal,
            mcids: glyph.mcid.into_iter().collect(),
            text_origins: vec![glyph.text_origin],
        },
    }
}

fn mcid_matches(span_mcids: &[i64], glyph_mcid: Option<i64>) -> bool {
    match (span_mcids, glyph_mcid) {
        ([], None) => true,
        ([span_mcid], Some(glyph_mcid)) => *span_mcid == glyph_mcid,
        _ => false,
    }
}

fn can_merge(span: &LayoutTextSpan, glyph: &LayoutGlyph) -> bool {
    let ordinal_matches = span
        .provenance
        .source_ordinal_end
        .checked_add(1)
        .is_some_and(|next| next == glyph.source_ordinal);
    let baseline = vector_between(span.baseline.start, span.baseline.end);
    let glyph_baseline = vector_between(glyph.baseline.start, glyph.baseline.end);
    let alignment = baseline
        .x
        .mul_add(glyph_baseline.x, baseline.y * glyph_baseline.y);
    let gap = distance(span.baseline.end, glyph.origin);
    let max_gap = span
        .bbox
        .height()
        .max(glyph.bbox.height())
        .mul_add(1.5, 1.0);
    ordinal_matches
        && span.font_resource == glyph.font_resource
        && (span.font_size - glyph.font_size).abs() <= 1.0e-6
        && span.writing_mode == glyph.writing_mode
        && span.rotation == glyph.rotation
        && span.tag == glyph.tag
        && span.alt_text == glyph.alt_text
        && span.actual_text == glyph.actual_text
        && span.artifact == glyph.artifact
        && mcid_matches(span.provenance.mcids.as_slice(), glyph.mcid)
        && alignment >= 0.999
        && gap <= max_gap
}

fn merge_glyph(span: &mut LayoutTextSpan, glyph: &LayoutGlyph) -> PdfResult<()> {
    span.text.push_str(&glyph.text);
    span.bbox = union_bbox(span.bbox, glyph.bbox)?;
    span.quad.top_right = glyph.quad.top_right;
    span.quad.bottom_right = glyph.quad.bottom_right;
    span.baseline.end = glyph.baseline.end;
    span.advance = LayoutVector {
        x: span.baseline.end.x - span.origin.x,
        y: span.baseline.end.y - span.origin.y,
    };
    span.confidence = span.confidence.min(glyph.confidence);
    span.provenance.source_ordinal_end = glyph.source_ordinal;
    if let Some(mcid) = glyph.mcid
        && !span.provenance.mcids.contains(&mcid)
    {
        span.provenance.mcids.push(mcid);
    }
    if !span.provenance.text_origins.contains(&glyph.text_origin) {
        span.provenance.text_origins.push(glyph.text_origin);
    }
    Ok(())
}

fn build_text_nodes(page: &PdfPage, spans: Vec<LayoutTextSpan>) -> PdfResult<Vec<LayoutNode>> {
    let mut groups = Vec::<Vec<LayoutTextSpan>>::new();
    for span in spans {
        if let Some(group) = groups.last_mut()
            && group
                .first()
                .is_some_and(|first| same_content_group(first, &span))
        {
            group.push(span);
        } else {
            groups.push(vec![span]);
        }
    }

    groups
        .into_iter()
        .enumerate()
        .map(|(index, group)| build_text_node(page, index, group))
        .collect()
}

fn same_content_group(left: &LayoutTextSpan, right: &LayoutTextSpan) -> bool {
    left.tag == right.tag
        && left.alt_text == right.alt_text
        && left.actual_text == right.actual_text
        && left.artifact == right.artifact
        && left.provenance.mcids == right.provenance.mcids
}

fn build_text_node(
    page: &PdfPage,
    index: usize,
    spans: Vec<LayoutTextSpan>,
) -> PdfResult<LayoutNode> {
    let first = spans.first().expect("group is non-empty");
    let mut bbox = first.bbox;
    let mut mcids = BTreeSet::new();
    let mut origins = Vec::new();
    let mut text = String::new();
    for span in &spans {
        bbox = union_bbox(bbox, span.bbox)?;
        text.push_str(&span.text);
        mcids.extend(span.provenance.mcids.iter().copied());
        for origin in &span.provenance.text_origins {
            if !origins.contains(origin) {
                origins.push(*origin);
            }
        }
    }
    let last = spans.last().expect("group is non-empty");
    let role = if first.artifact {
        LayoutNodeRole::Artifact
    } else {
        first
            .tag
            .as_deref()
            .map_or(LayoutNodeRole::Unclassified, role_for_tag)
    };
    let has_marked_metadata = first.tag.is_some()
        || first.alt_text.is_some()
        || first.actual_text.is_some()
        || first.artifact
        || !first.provenance.mcids.is_empty();
    Ok(LayoutNode {
        id: format!("p{}-n{index}", page.index),
        kind: LayoutNodeKind::TextBlock,
        role,
        tag: first.tag.clone(),
        alt_text: first.alt_text.clone(),
        actual_text: first.actual_text.clone(),
        artifact: first.artifact,
        structure_object: None,
        text,
        bbox,
        quad: None,
        confidence: BLOCK_CONFIDENCE,
        rule_id: if has_marked_metadata {
            MARKED_BLOCK_RULE_ID.to_owned()
        } else {
            BLOCK_RULE_ID.to_owned()
        },
        provenance: LayoutProvenance {
            page_object: page.id,
            source_ordinal_start: first.provenance.source_ordinal_start,
            source_ordinal_end: last.provenance.source_ordinal_end,
            mcids: mcids.into_iter().collect(),
            text_origins: origins,
        },
        spans,
    })
}

fn role_for_tag(tag: &str) -> LayoutNodeRole {
    match tag {
        "Document" => LayoutNodeRole::Document,
        "Part" => LayoutNodeRole::Part,
        "Sect" | "Div" | "Art" => LayoutNodeRole::Section,
        "H" | "H1" | "H2" | "H3" | "H4" | "H5" | "H6" => LayoutNodeRole::Heading,
        "P" => LayoutNodeRole::Paragraph,
        "L" => LayoutNodeRole::List,
        "LI" => LayoutNodeRole::ListItem,
        "Lbl" => LayoutNodeRole::Label,
        "LBody" => LayoutNodeRole::ListBody,
        "Table" => LayoutNodeRole::Table,
        "TR" => LayoutNodeRole::TableRow,
        "TH" => LayoutNodeRole::TableHeader,
        "TD" => LayoutNodeRole::TableCell,
        "Figure" => LayoutNodeRole::Figure,
        "Caption" => LayoutNodeRole::Caption,
        "Formula" => LayoutNodeRole::Formula,
        "Form" => LayoutNodeRole::Form,
        "Artifact" => LayoutNodeRole::Artifact,
        _ => LayoutNodeRole::Unclassified,
    }
}
fn union_bbox(left: BBox, right: BBox) -> PdfResult<BBox> {
    BBox::try_new(
        left.x0.min(right.x0),
        left.y0.min(right.y0),
        left.x1.max(right.x1),
        left.y1.max(right.y1),
    )
}

fn vector_between(start: Point, end: Point) -> LayoutVector {
    let x = end.x - start.x;
    let y = end.y - start.y;
    let length = x.hypot(y);
    if length <= f64::EPSILON {
        LayoutVector { x: 1.0, y: 0.0 }
    } else {
        LayoutVector {
            x: x / length,
            y: y / length,
        }
    }
}

fn distance(left: Point, right: Point) -> f64 {
    (right.x - left.x).hypot(right.y - left.y)
}

#[cfg(not(target_arch = "wasm32"))]
type LayoutTimer = Option<Instant>;

#[cfg(target_arch = "wasm32")]
type LayoutTimer = ();

#[cfg(not(target_arch = "wasm32"))]
#[allow(clippy::unnecessary_wraps)] // Keeps one fallible cross-target call site; wasm rejects timing.
fn start_layout_timer(include_timings: bool) -> PdfResult<LayoutTimer> {
    Ok(include_timings.then(Instant::now))
}

#[cfg(target_arch = "wasm32")]
fn start_layout_timer(include_timings: bool) -> PdfResult<LayoutTimer> {
    if include_timings {
        Err(PdfError::new(
            ErrorCode::UnsupportedFeature,
            None,
            "layout timings are unavailable on wasm32",
        ))
    } else {
        Ok(())
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn finish_layout_timer(timer: LayoutTimer) -> Option<LayoutTimings> {
    timer.map(|started| LayoutTimings {
        layout_ns: u64::try_from(started.elapsed().as_nanos()).unwrap_or(u64::MAX),
    })
}

#[cfg(target_arch = "wasm32")]
const fn finish_layout_timer((): LayoutTimer) -> Option<LayoutTimings> {
    None
}
fn parser_info() -> LayoutParserInfo {
    let info = version_info();
    LayoutParserInfo {
        name: "pdf-core".to_owned(),
        version: info.version.to_owned(),
        stage: info.stage.to_owned(),
    }
}

fn options_digest(options: LayoutExtractionOptions) -> String {
    let canonical = format!(
        "layout-ir-v{LAYOUT_IR_SCHEMA_VERSION}|normalize_unicode={}|quality={}|debug_glyphs={}|timings={}",
        u8::from(options.normalize_unicode),
        u8::from(options.include_quality_metadata),
        u8::from(options.include_debug_glyphs),
        u8::from(options.include_timings),
    );
    let digest = Sha256::digest(canonical.as_bytes());
    let mut output = String::with_capacity(64);
    for byte in digest {
        write!(output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
}

impl From<TextWarning> for LayoutWarning {
    fn from(warning: TextWarning) -> Self {
        Self {
            code: warning.code,
            page_index: Some(warning.page_index),
            font_resource: warning.font_resource,
            node_id: None,
            message: warning.message,
        }
    }
}

/// Stable public coordinate-space name used by Layout IR serializers.
#[must_use]
pub const fn layout_coordinate_space() -> &'static str {
    LAYOUT_SPACE
}
