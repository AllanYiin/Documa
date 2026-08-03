#![forbid(unsafe_code)]
#![doc = "From-scratch PDF parsing and text extraction core."]

mod cmap;
mod content;
mod decode_budget;
mod document;
mod error;
mod figure_flow;
mod filter;
mod font;
mod font_metrics;
mod geometry;
mod graphics;
mod images;
mod layout;
mod layout_ir;
mod lexer;
mod limits;
mod marked_content;
mod navigation;
mod object;
mod object_stream;
mod object_stream_cache;
mod page;
mod parser;
mod reading_order;
mod table_reconstruction;
mod tagged_structure;
mod text;
mod text_model;
mod vector_paths;
mod xref;

#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};

pub use content::{ContentOperation, parse_content};
pub use document::{DecodeMetrics, DocumentSummary, PdfDocument, PdfVersion};
pub use error::{ErrorCode, PdfError, PdfResult};
pub use filter::{decode_stream, decode_stream_with_limits};
pub use geometry::{
    AffineMatrix, BBox, CoordinateSpace, DISPLAY_SPACE, LAYOUT_SPACE, PDF_USER_SPACE, PageGeometry,
    PageGeometryWarning, Point, Quad,
};
pub use images::{ExtractedImage, ImageDataFormat, ImageWarning};
pub use layout_ir::{
    DocumentLayout, LAYOUT_IR_SCHEMA_VERSION, LayoutCapabilities, LayoutDocumentFinalize,
    LayoutDocumentStart, LayoutEvent, LayoutEventItem, LayoutEventProducer, LayoutEventStream,
    LayoutExtractionOptions, LayoutGlyph, LayoutImagePlacement, LayoutLinkAnnotation,
    LayoutNamedDestination, LayoutNavigationTarget, LayoutNavigationTargetKind, LayoutNode,
    LayoutNodeFinalization, LayoutNodeKind, LayoutNodeRole, LayoutOrders, LayoutOutlineItem,
    LayoutPageFinalization, LayoutParserInfo, LayoutProvenance, LayoutSegment, LayoutTable,
    LayoutTableCell, LayoutTableCellRole, LayoutTableEvidence, LayoutTextSpan, LayoutTimings,
    LayoutVector, LayoutVisualBlock, LayoutVisualBlockOrder, LayoutVisualCue, LayoutVisualFocus,
    LayoutVisualReading, LayoutVisualTransition, LayoutVisualTransitionKind, LayoutWarning,
    PageLayout, collect_layout_events, layout_coordinate_space,
};
pub use lexer::{Lexer, SpannedToken, Token};
pub use limits::ParseLimits;
pub use object::{ObjectId, PdfDictionary, PdfName, PdfObject, PdfStream, PdfString};
pub use page::PdfPage;
pub use parser::{IndirectObject, parse_object, parse_object_with_limits};
pub use text::{
    ExtractedText, ExtractedTextV2, ExtractionMode, PageText, TextExtractionOptions,
    TextExtractionOptionsV2, TextQuality, TextSpan, TextWarning,
};
pub use text_model::{PositionedGlyph, SeparatorOrigin, TextOrigin, TextSeparator, WritingMode};
pub use xref::{XrefEntry, XrefKind};

/// Version information shared by all language bindings.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct VersionInfo {
    /// Workspace package version.
    pub version: &'static str,
    /// Current implementation stage.
    pub stage: &'static str,
}

/// Returns build-independent package information.
#[must_use]
pub const fn version_info() -> VersionInfo {
    VersionInfo {
        version: env!("CARGO_PKG_VERSION"),
        stage: "stage-11",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exposes_stage_eleven_version() {
        let info = version_info();
        assert_eq!(info.stage, "stage-11");
        assert_eq!(info.version, "0.2.0");
        assert!(!info.version.is_empty());
    }
}
