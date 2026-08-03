#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};

/// Resource limits applied to every input-derived parser operation.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct ParseLimits {
    pub max_file_bytes: usize,
    pub max_object_depth: usize,
    pub max_array_items: usize,
    pub max_dictionary_entries: usize,
    pub max_string_bytes: usize,
    pub max_name_bytes: usize,
    pub max_xref_entries: usize,
    pub max_incremental_updates: usize,
    pub max_stream_bytes: usize,
    pub max_decoded_stream_bytes: usize,
    pub max_total_decoded_bytes: usize,
    pub max_cached_object_stream_bytes: usize,
    pub max_cached_object_streams: usize,
    pub max_filter_chain_depth: usize,
    pub max_stream_expansion_ratio: usize,
    pub max_pages: usize,
    pub max_content_operations: usize,
    pub max_cmap_mappings: usize,
    pub max_text_spans: usize,
    pub max_structure_elements: usize,
    pub max_structure_kids: usize,
    pub max_parent_tree_entries: usize,
    pub max_role_map_entries: usize,
    pub max_path_segments: usize,
    pub max_table_candidates: usize,
    pub max_tables: usize,
    pub max_table_cells: usize,
    pub max_images: usize,
    pub max_image_pixels: usize,
    pub max_annotations: usize,
    pub max_named_destinations: usize,
    pub max_outline_items: usize,
}

impl Default for ParseLimits {
    fn default() -> Self {
        Self {
            max_file_bytes: 256 * 1024 * 1024,
            max_object_depth: 64,
            max_array_items: 1_000_000,
            max_dictionary_entries: 100_000,
            max_string_bytes: 64 * 1024 * 1024,
            max_name_bytes: 4 * 1024,
            max_xref_entries: 5_000_000,
            max_incremental_updates: 128,
            max_stream_bytes: 128 * 1024 * 1024,
            max_decoded_stream_bytes: 256 * 1024 * 1024,
            max_total_decoded_bytes: 512 * 1024 * 1024,
            max_cached_object_stream_bytes: 64 * 1024 * 1024,
            max_cached_object_streams: 256,
            max_filter_chain_depth: 8,
            max_stream_expansion_ratio: 200,
            max_pages: 100_000,
            max_content_operations: 5_000_000,
            max_cmap_mappings: 2_000_000,
            max_text_spans: 5_000_000,
            max_structure_elements: 1_000_000,
            max_structure_kids: 2_000_000,
            max_parent_tree_entries: 1_000_000,
            max_role_map_entries: 100_000,
            max_path_segments: 5_000_000,
            max_table_candidates: 100_000,
            max_tables: 100_000,
            max_table_cells: 1_000_000,
            max_images: 1_000_000,
            max_image_pixels: 250_000_000,
            max_annotations: 100_000,
            max_named_destinations: 100_000,
            max_outline_items: 100_000,
        }
    }
}
