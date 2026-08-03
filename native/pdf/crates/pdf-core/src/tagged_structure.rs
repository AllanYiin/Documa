use std::collections::{BTreeMap, BTreeSet};

use crate::{
    ErrorCode, ObjectId, PdfDictionary, PdfDocument, PdfError, PdfName, PdfObject, PdfPage,
    PdfResult,
    marked_content::{resolve_text, resolve_value},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TaggedAssociation {
    pub page_index: usize,
    pub mcid: i64,
    pub tag: String,
    pub standard_role: Option<String>,
    pub alt_text: Option<String>,
    pub actual_text: Option<String>,
    pub structure_object: Option<ObjectId>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum TaggedTableCellKind {
    Data,
    Header,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum TaggedTableScope {
    Row,
    Column,
    Both,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TaggedTableCell {
    pub structure_object: Option<ObjectId>,
    pub kind: TaggedTableCellKind,
    pub row_span: usize,
    pub column_span: usize,
    pub scope: Option<TaggedTableScope>,
    pub associations: Vec<(usize, i64)>,
    pub valid: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TaggedTableRow {
    pub cells: Vec<TaggedTableCell>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TaggedTable {
    pub structure_object: Option<ObjectId>,
    pub page_index: Option<usize>,
    pub rows: Vec<TaggedTableRow>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct TaggedStructureWarning {
    pub code: String,
    pub page_index: Option<usize>,
    pub message: String,
}

#[derive(Debug, Default)]
pub(crate) struct TaggedStructureResult {
    pub associations: Vec<TaggedAssociation>,
    pub tables: Vec<TaggedTable>,
    pub warnings: Vec<TaggedStructureWarning>,
}

#[derive(Debug, Default)]
pub(crate) struct PageTaggedStructureIndex {
    pub association_indices: Vec<usize>,
    pub table_indices: Vec<usize>,
}

#[derive(Debug)]
pub(crate) struct TaggedStructureIndex {
    pub result: TaggedStructureResult,
    pub pages: Vec<PageTaggedStructureIndex>,
    pub unindexed_association_indices: Vec<usize>,
    pub unindexed_table_indices: Vec<usize>,
}

impl TaggedStructureResult {
    pub(crate) fn into_page_index(self, page_count: usize) -> TaggedStructureIndex {
        let mut pages = (0..page_count)
            .map(|_| PageTaggedStructureIndex::default())
            .collect::<Vec<_>>();
        let mut unindexed_association_indices = Vec::new();
        for (association_index, association) in self.associations.iter().enumerate() {
            if let Some(page) = pages.get_mut(association.page_index) {
                page.association_indices.push(association_index);
            } else {
                unindexed_association_indices.push(association_index);
            }
        }
        let mut unindexed_table_indices = Vec::new();
        for (table_index, table) in self.tables.iter().enumerate() {
            match tagged_table_page_index(table) {
                Ok(Some(page_index)) if page_index < pages.len() => {
                    pages[page_index].table_indices.push(table_index);
                }
                Ok(_) | Err(()) => unindexed_table_indices.push(table_index),
            }
        }
        TaggedStructureIndex {
            result: self,
            pages,
            unindexed_association_indices,
            unindexed_table_indices,
        }
    }
}

pub(crate) fn tagged_table_page_index(table: &TaggedTable) -> Result<Option<usize>, ()> {
    let mut pages = table
        .rows
        .iter()
        .flat_map(|row| &row.cells)
        .flat_map(|cell| cell.associations.iter().map(|association| association.0))
        .collect::<BTreeSet<_>>();
    if let Some(page_index) = table.page_index {
        pages.insert(page_index);
    }
    if pages.len() > 1 {
        Err(())
    } else {
        Ok(pages.into_iter().next())
    }
}

#[derive(Debug, Clone, Default)]
struct StructureContext {
    page_id: Option<ObjectId>,
    tag: Option<String>,
    standard_role: Option<String>,
    alt_text: Option<String>,
    actual_text: Option<String>,
    structure_object: Option<ObjectId>,
    table_index: Option<usize>,
    row_index: Option<usize>,
    cell_index: Option<usize>,
}

struct StructureWalker<'a> {
    document: &'a PdfDocument,
    pages: &'a [PdfPage],
    page_indices: BTreeMap<ObjectId, usize>,
    role_map: BTreeMap<String, String>,
    structure_stack: BTreeSet<ObjectId>,
    structure_elements: usize,
    structure_kids: usize,
    table_cells: usize,
    warning_keys: BTreeSet<(String, Option<usize>, String)>,
    result: TaggedStructureResult,
}

pub(crate) fn extract_tagged_structure(
    document: &PdfDocument,
    pages: &[PdfPage],
) -> PdfResult<TaggedStructureResult> {
    let catalog = document.catalog()?;
    let catalog_dictionary = catalog.value.as_dictionary().expect("catalog validated");
    let Some(root_object) = catalog_dictionary.get(&PdfName(b"StructTreeRoot".to_vec())) else {
        return Ok(TaggedStructureResult::default());
    };
    let root = resolve_dictionary(document, root_object, "StructTreeRoot")?;
    let page_indices = pages.iter().map(|page| (page.id, page.index)).collect();
    let mut walker = StructureWalker {
        document,
        pages,
        page_indices,
        role_map: BTreeMap::new(),
        structure_stack: BTreeSet::new(),
        structure_elements: 0,
        structure_kids: 0,
        table_cells: 0,
        warning_keys: BTreeSet::new(),
        result: TaggedStructureResult::default(),
    };
    walker.load_role_map(&root)?;
    if let Some(kids) = root.get(&PdfName(b"K".to_vec())) {
        walker.walk_kid(kids, None, &StructureContext::default(), 0)?;
    }
    walker.validate_parent_tree(&root)?;
    Ok(walker.result)
}

impl StructureWalker<'_> {
    fn load_role_map(&mut self, root: &PdfDictionary) -> PdfResult<()> {
        let Some(role_map) = root.get(&PdfName(b"RoleMap".to_vec())) else {
            return Ok(());
        };
        let dictionary = resolve_dictionary(self.document, role_map, "RoleMap")?;
        if dictionary.len() > self.document.limits.max_role_map_entries {
            return Err(limit("RoleMap entry limit exceeded"));
        }
        for (custom, mapped) in dictionary {
            let mapped = resolve_value(self.document, &mapped)?;
            let PdfObject::Name(mapped) = mapped else {
                self.warn(
                    "tagged_structure_invalid",
                    None,
                    String::from_utf8_lossy(custom.as_bytes()).into_owned(),
                    "RoleMap value is not a name",
                );
                continue;
            };
            self.role_map.insert(
                String::from_utf8_lossy(custom.as_bytes()).into_owned(),
                String::from_utf8_lossy(mapped.as_bytes()).into_owned(),
            );
        }
        Ok(())
    }

    fn walk_kid(
        &mut self,
        object: &PdfObject,
        object_id: Option<ObjectId>,
        context: &StructureContext,
        depth: usize,
    ) -> PdfResult<()> {
        if depth > self.document.limits.max_object_depth {
            return Err(limit("structure nesting depth limit exceeded"));
        }
        self.structure_kids = self
            .structure_kids
            .checked_add(1)
            .ok_or_else(|| limit("structure kid count overflow"))?;
        if self.structure_kids > self.document.limits.max_structure_kids {
            return Err(limit("structure kid limit exceeded"));
        }
        match object {
            PdfObject::Null => Ok(()),
            PdfObject::Reference(id) => {
                if !self.structure_stack.insert(*id) {
                    self.warn(
                        "tagged_structure_cycle",
                        context
                            .page_id
                            .and_then(|page| self.page_indices.get(&page).copied()),
                        format!("{}:{}", id.number, id.generation),
                        "cyclic structure reference was skipped",
                    );
                    return Ok(());
                }
                let value = self.document.object(*id)?.value;
                let result = self.walk_kid(&value, Some(*id), context, depth + 1);
                self.structure_stack.remove(id);
                result
            }
            PdfObject::Integer(mcid) => self.push_association(*mcid, context),
            PdfObject::Array(values) => {
                if values.len() > self.document.limits.max_structure_kids {
                    return Err(limit("structure K array limit exceeded"));
                }
                for value in values {
                    self.walk_kid(value, None, context, depth + 1)?;
                }
                Ok(())
            }
            PdfObject::Dictionary(dictionary) => {
                let object_type = dictionary
                    .get(&PdfName(b"Type".to_vec()))
                    .and_then(|value| match value {
                        PdfObject::Name(name) => Some(name.as_bytes()),
                        _ => None,
                    });
                if object_type == Some(b"OBJR") {
                    self.warn(
                        "tagged_object_reference_unsupported",
                        context
                            .page_id
                            .and_then(|page| self.page_indices.get(&page).copied()),
                        "objr".to_owned(),
                        "structure object-reference kid is deferred to Stage 5",
                    );
                    return Ok(());
                }
                if object_type == Some(b"MCR")
                    || (dictionary.contains_key(&PdfName(b"MCID".to_vec()))
                        && !dictionary.contains_key(&PdfName(b"S".to_vec())))
                {
                    return self.walk_mcr(dictionary, context);
                }
                if object_type == Some(b"StructElem")
                    || dictionary.contains_key(&PdfName(b"S".to_vec()))
                {
                    return self.walk_element(dictionary, object_id, context, depth);
                }
                if let Some(kids) = dictionary.get(&PdfName(b"K".to_vec())) {
                    return self.walk_kid(kids, None, context, depth + 1);
                }
                self.warn(
                    "tagged_structure_invalid",
                    context
                        .page_id
                        .and_then(|page| self.page_indices.get(&page).copied()),
                    "kid".to_owned(),
                    "unsupported structure kid dictionary was skipped",
                );
                Ok(())
            }
            _ => {
                self.warn(
                    "tagged_structure_invalid",
                    context
                        .page_id
                        .and_then(|page| self.page_indices.get(&page).copied()),
                    "kid".to_owned(),
                    "structure kid has an unsupported type",
                );
                Ok(())
            }
        }
    }

    fn walk_element(
        &mut self,
        dictionary: &PdfDictionary,
        object_id: Option<ObjectId>,
        parent: &StructureContext,
        depth: usize,
    ) -> PdfResult<()> {
        self.structure_elements = self
            .structure_elements
            .checked_add(1)
            .ok_or_else(|| limit("structure element count overflow"))?;
        if self.structure_elements > self.document.limits.max_structure_elements {
            return Err(limit("structure element limit exceeded"));
        }
        let Some(PdfObject::Name(tag_name)) = dictionary.get(&PdfName(b"S".to_vec())) else {
            self.warn(
                "tagged_structure_invalid",
                parent
                    .page_id
                    .and_then(|page| self.page_indices.get(&page).copied()),
                "role".to_owned(),
                "structure element has no role name",
            );
            return Ok(());
        };
        let tag = String::from_utf8_lossy(tag_name.as_bytes()).into_owned();
        let standard_role = self.resolve_role(&tag, parent.page_id);
        let page_id = if let Some(page) = dictionary.get(&PdfName(b"Pg".to_vec())) {
            if let Some(page) = page.as_reference() {
                Some(page)
            } else {
                self.warn(
                    "tagged_structure_invalid",
                    parent
                        .page_id
                        .and_then(|page| self.page_indices.get(&page).copied()),
                    tag.clone(),
                    "structure element Pg is not an indirect page reference",
                );
                parent.page_id
            }
        } else {
            parent.page_id
        };
        let alt_text = self.optional_text(dictionary, b"Alt", "Alt", page_id, &tag);
        let actual_text =
            self.optional_text(dictionary, b"ActualText", "ActualText", page_id, &tag);
        let (table_index, row_index, cell_index) = self.enter_table_structure(
            standard_role.as_deref(),
            dictionary,
            object_id,
            page_id,
            parent,
        )?;
        let context = StructureContext {
            page_id,
            tag: Some(tag),
            standard_role,
            alt_text: alt_text.or_else(|| parent.alt_text.clone()),
            actual_text: actual_text.or_else(|| parent.actual_text.clone()),
            structure_object: object_id,
            table_index,
            row_index,
            cell_index,
        };
        if let Some(kids) = dictionary.get(&PdfName(b"K".to_vec())) {
            self.walk_kid(kids, None, &context, depth + 1)?;
        }
        Ok(())
    }

    fn walk_mcr(&mut self, dictionary: &PdfDictionary, parent: &StructureContext) -> PdfResult<()> {
        if dictionary.contains_key(&PdfName(b"Stm".to_vec())) {
            self.warn(
                "tagged_object_reference_unsupported",
                parent
                    .page_id
                    .and_then(|page| self.page_indices.get(&page).copied()),
                "mcr-stream".to_owned(),
                "stream-associated marked-content reference is deferred",
            );
            return Ok(());
        }
        let page_id = dictionary
            .get(&PdfName(b"Pg".to_vec()))
            .and_then(PdfObject::as_reference)
            .or(parent.page_id);
        let Some(PdfObject::Integer(mcid)) = dictionary.get(&PdfName(b"MCID".to_vec())) else {
            self.warn(
                "tagged_structure_invalid",
                page_id.and_then(|page| self.page_indices.get(&page).copied()),
                "mcr".to_owned(),
                "marked-content reference has no integer MCID",
            );
            return Ok(());
        };
        let mut context = parent.clone();
        context.page_id = page_id;
        self.push_association(*mcid, &context)
    }

    fn push_association(&mut self, mcid: i64, context: &StructureContext) -> PdfResult<()> {
        if mcid < 0 {
            self.warn(
                "tagged_structure_invalid",
                context
                    .page_id
                    .and_then(|page| self.page_indices.get(&page).copied()),
                mcid.to_string(),
                "structure MCID must be non-negative",
            );
            return Ok(());
        }
        let Some(page_id) = context.page_id else {
            self.warn(
                "tagged_structure_invalid",
                None,
                mcid.to_string(),
                "structure MCID has no inherited Pg",
            );
            return Ok(());
        };
        let Some(page_index) = self.page_indices.get(&page_id).copied() else {
            self.warn(
                "tagged_structure_invalid",
                None,
                format!("{}:{}", page_id.number, page_id.generation),
                "structure Pg does not reference a document page",
            );
            return Ok(());
        };
        let Some(tag) = context.tag.clone() else {
            self.warn(
                "tagged_structure_invalid",
                Some(page_index),
                mcid.to_string(),
                "structure MCID has no owning role",
            );
            return Ok(());
        };
        if self.result.associations.len() >= self.document.limits.max_structure_elements {
            return Err(limit("tagged association limit exceeded"));
        }
        self.result.associations.push(TaggedAssociation {
            page_index,
            mcid,
            tag,
            standard_role: context.standard_role.clone(),
            alt_text: context.alt_text.clone(),
            actual_text: context.actual_text.clone(),
            structure_object: context.structure_object,
        });
        if let (Some(table_index), Some(row_index), Some(cell_index)) =
            (context.table_index, context.row_index, context.cell_index)
        {
            let cell = self
                .result
                .tables
                .get_mut(table_index)
                .and_then(|table| table.rows.get_mut(row_index))
                .and_then(|row| row.cells.get_mut(cell_index))
                .ok_or_else(|| limit("tagged table context is inconsistent"))?;
            cell.associations.push((page_index, mcid));
        }
        Ok(())
    }

    fn enter_table_structure(
        &mut self,
        standard_role: Option<&str>,
        dictionary: &PdfDictionary,
        object_id: Option<ObjectId>,
        page_id: Option<ObjectId>,
        parent: &StructureContext,
    ) -> PdfResult<(Option<usize>, Option<usize>, Option<usize>)> {
        let page_index = page_id.and_then(|page| self.page_indices.get(&page).copied());
        match standard_role {
            Some("Table") => {
                if self.result.tables.len() >= self.document.limits.max_tables {
                    return Err(limit("tagged table limit exceeded"));
                }
                let table_index = self.result.tables.len();
                self.result.tables.push(TaggedTable {
                    structure_object: object_id,
                    page_index,
                    rows: Vec::new(),
                });
                Ok((Some(table_index), None, None))
            }
            Some("TR") => {
                let Some(table_index) = parent.table_index else {
                    self.warn(
                        "tagged_table_invalid",
                        page_index,
                        "row-without-table".to_owned(),
                        "tagged table row has no Table ancestor",
                    );
                    return Ok((None, None, None));
                };
                let table = self
                    .result
                    .tables
                    .get_mut(table_index)
                    .ok_or_else(|| limit("tagged table row context is inconsistent"))?;
                let row_index = table.rows.len();
                table.rows.push(TaggedTableRow { cells: Vec::new() });
                Ok((Some(table_index), Some(row_index), None))
            }
            Some(role @ ("TH" | "TD")) => {
                let (Some(table_index), Some(row_index)) = (parent.table_index, parent.row_index)
                else {
                    self.warn(
                        "tagged_table_invalid",
                        page_index,
                        "cell-without-row".to_owned(),
                        "tagged table cell has no TR ancestor",
                    );
                    return Ok((parent.table_index, parent.row_index, None));
                };
                if self.table_cells >= self.document.limits.max_table_cells {
                    return Err(limit("tagged table cell limit exceeded"));
                }
                let (row_span, column_span, scope, valid) =
                    self.table_cell_attributes(dictionary, page_index)?;
                self.table_cells = self
                    .table_cells
                    .checked_add(1)
                    .ok_or_else(|| limit("tagged table cell count overflow"))?;
                let row = self
                    .result
                    .tables
                    .get_mut(table_index)
                    .and_then(|table| table.rows.get_mut(row_index))
                    .ok_or_else(|| limit("tagged table cell context is inconsistent"))?;
                let cell_index = row.cells.len();
                row.cells.push(TaggedTableCell {
                    structure_object: object_id,
                    kind: if role == "TH" {
                        TaggedTableCellKind::Header
                    } else {
                        TaggedTableCellKind::Data
                    },
                    row_span,
                    column_span,
                    scope,
                    associations: Vec::new(),
                    valid,
                });
                Ok((Some(table_index), Some(row_index), Some(cell_index)))
            }
            _ => Ok((parent.table_index, parent.row_index, parent.cell_index)),
        }
    }

    fn table_cell_attributes(
        &mut self,
        dictionary: &PdfDictionary,
        page_index: Option<usize>,
    ) -> PdfResult<(usize, usize, Option<TaggedTableScope>, bool)> {
        let mut row_span = 1;
        let mut column_span = 1;
        let mut scope = None;
        let mut valid = true;
        if let Some(attributes) = dictionary.get(&PdfName(b"A".to_vec())) {
            self.walk_table_attributes(
                attributes,
                page_index,
                0,
                &mut row_span,
                &mut column_span,
                &mut scope,
                &mut valid,
            )?;
        }
        Ok((row_span, column_span, scope, valid))
    }

    #[allow(clippy::too_many_arguments)]
    fn walk_table_attributes(
        &mut self,
        object: &PdfObject,
        page_index: Option<usize>,
        depth: usize,
        row_span: &mut usize,
        column_span: &mut usize,
        scope: &mut Option<TaggedTableScope>,
        valid: &mut bool,
    ) -> PdfResult<()> {
        if depth > self.document.limits.max_object_depth {
            return Err(limit("tagged table attribute depth limit exceeded"));
        }
        let value = match resolve_value(self.document, object) {
            Ok(value) => value,
            Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
            Err(_) => {
                *valid = false;
                self.warn(
                    "tagged_table_invalid",
                    page_index,
                    "attribute-reference".to_owned(),
                    "tagged table attribute reference is invalid",
                );
                return Ok(());
            }
        };
        match value {
            PdfObject::Dictionary(attributes) => {
                let owner_is_table = match attributes.get(&PdfName(b"O".to_vec())) {
                    None => true,
                    Some(owner) => matches!(
                        resolve_value(self.document, owner),
                        Ok(PdfObject::Name(name)) if name.as_bytes() == b"Table"
                    ),
                };
                if !owner_is_table {
                    return Ok(());
                }
                self.read_positive_table_span(
                    &attributes,
                    b"RowSpan",
                    page_index,
                    row_span,
                    valid,
                )?;
                self.read_positive_table_span(
                    &attributes,
                    b"ColSpan",
                    page_index,
                    column_span,
                    valid,
                )?;
                if let Some(value) = attributes.get(&PdfName(b"Scope".to_vec())) {
                    match resolve_value(self.document, value) {
                        Ok(PdfObject::Name(name)) if name.as_bytes() == b"Row" => {
                            *scope = Some(TaggedTableScope::Row);
                        }
                        Ok(PdfObject::Name(name)) if name.as_bytes() == b"Column" => {
                            *scope = Some(TaggedTableScope::Column);
                        }
                        Ok(PdfObject::Name(name)) if name.as_bytes() == b"Both" => {
                            *scope = Some(TaggedTableScope::Both);
                        }
                        Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
                        _ => {
                            *valid = false;
                            self.warn(
                                "tagged_table_invalid",
                                page_index,
                                "scope".to_owned(),
                                "tagged table Scope must be Row, Column, or Both",
                            );
                        }
                    }
                }
            }
            PdfObject::Array(values) => {
                if values.len() > self.document.limits.max_array_items {
                    return Err(limit("tagged table attribute array limit exceeded"));
                }
                for value in values {
                    self.walk_table_attributes(
                        &value,
                        page_index,
                        depth + 1,
                        row_span,
                        column_span,
                        scope,
                        valid,
                    )?;
                }
            }
            PdfObject::Integer(_) | PdfObject::Null => {}
            _ => {
                *valid = false;
                self.warn(
                    "tagged_table_invalid",
                    page_index,
                    "attributes".to_owned(),
                    "tagged table A entry has an unsupported type",
                );
            }
        }
        Ok(())
    }

    fn read_positive_table_span(
        &mut self,
        attributes: &PdfDictionary,
        key: &[u8],
        page_index: Option<usize>,
        output: &mut usize,
        valid: &mut bool,
    ) -> PdfResult<()> {
        let Some(value) = attributes.get(&PdfName(key.to_vec())) else {
            return Ok(());
        };
        let span_valid = match resolve_value(self.document, value) {
            Ok(PdfObject::Integer(value)) if value > 0 => {
                if let Ok(converted) = usize::try_from(value) {
                    *output = converted;
                    true
                } else {
                    false
                }
            }
            Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
            _ => false,
        };
        if !span_valid {
            *valid = false;
            self.warn(
                "tagged_table_invalid",
                page_index,
                String::from_utf8_lossy(key).into_owned(),
                "tagged table span must be a positive bounded integer",
            );
        }
        Ok(())
    }
    fn resolve_role(&mut self, tag: &str, page_id: Option<ObjectId>) -> Option<String> {
        let mut current = tag.to_owned();
        let mut visited = BTreeSet::new();
        for _ in 0..self.document.limits.max_object_depth {
            if !visited.insert(current.clone()) {
                self.warn(
                    "tagged_structure_cycle",
                    page_id.and_then(|page| self.page_indices.get(&page).copied()),
                    tag.to_owned(),
                    "RoleMap cycle leaves the role unclassified",
                );
                return None;
            }
            let Some(next) = self.role_map.get(&current).cloned() else {
                return Some(current);
            };
            current = next;
        }
        self.warn(
            "tagged_structure_invalid",
            page_id.and_then(|page| self.page_indices.get(&page).copied()),
            tag.to_owned(),
            "RoleMap depth limit leaves the role unclassified",
        );
        None
    }

    fn optional_text(
        &mut self,
        dictionary: &PdfDictionary,
        key: &[u8],
        field: &str,
        page_id: Option<ObjectId>,
        warning_key: &str,
    ) -> Option<String> {
        let value = dictionary.get(&PdfName(key.to_vec()))?;
        if let Ok(text) = resolve_text(self.document, value, field) {
            Some(text)
        } else {
            self.warn(
                "tagged_structure_invalid",
                page_id.and_then(|page| self.page_indices.get(&page).copied()),
                warning_key.to_owned(),
                "structure text string is invalid",
            );
            None
        }
    }

    fn validate_parent_tree(&mut self, root: &PdfDictionary) -> PdfResult<()> {
        let Some(parent_tree) = root.get(&PdfName(b"ParentTree".to_vec())) else {
            return Ok(());
        };
        let mut entries = BTreeMap::new();
        let mut stack = BTreeSet::new();
        self.walk_number_tree(parent_tree, &mut stack, &mut entries, 0)?;
        let associated_pages = self
            .result
            .associations
            .iter()
            .map(|association| association.page_index)
            .collect::<BTreeSet<_>>();
        let mut parent_arrays = BTreeMap::new();
        for page_index in associated_pages {
            parent_arrays.insert(page_index, self.resolve_parent_array(page_index, &entries));
        }
        for association in self.result.associations.clone() {
            let Some(parent_result) = parent_arrays.get(&association.page_index) else {
                continue;
            };
            let parents = match parent_result {
                Ok(parents) => parents,
                Err(message) => {
                    self.warn_parent_mismatch(&association, message);
                    continue;
                }
            };
            let Ok(index) = usize::try_from(association.mcid) else {
                self.warn_parent_mismatch(&association, "MCID cannot index ParentTree array");
                continue;
            };
            let Some(parent) = parents.get(index) else {
                self.warn_parent_mismatch(&association, "ParentTree array has no MCID slot");
                continue;
            };
            if matches!(parent, PdfObject::Null) {
                self.warn_parent_mismatch(&association, "ParentTree MCID slot is null");
                continue;
            }
            if let Some(expected) = association.structure_object {
                if let Some(actual) = parent.as_reference() {
                    if expected != actual {
                        self.warn_parent_mismatch(
                            &association,
                            "ParentTree points to another element",
                        );
                    }
                } else {
                    self.warn_parent_mismatch(
                        &association,
                        "ParentTree MCID slot is not an indirect structure element",
                    );
                }
            }
        }
        Ok(())
    }

    fn resolve_parent_array(
        &self,
        page_index: usize,
        entries: &BTreeMap<i64, PdfObject>,
    ) -> Result<Vec<PdfObject>, &'static str> {
        let page = &self.pages[page_index];
        let struct_parents = page
            .dictionary
            .get(&PdfName(b"StructParents".to_vec()))
            .ok_or("page has no StructParents key")?;
        let PdfObject::Integer(key) = resolve_value(self.document, struct_parents)
            .map_err(|_| "page StructParents cannot be resolved")?
        else {
            return Err("page StructParents is not an integer");
        };
        let parent_value = entries.get(&key).ok_or("ParentTree has no page entry")?;
        let value = resolve_value(self.document, parent_value)
            .map_err(|_| "ParentTree page entry cannot be resolved")?;
        let PdfObject::Array(parents) = value else {
            return Err("ParentTree page entry is not an array");
        };
        Ok(parents)
    }
    fn walk_number_tree(
        &mut self,
        object: &PdfObject,
        stack: &mut BTreeSet<ObjectId>,
        entries: &mut BTreeMap<i64, PdfObject>,
        depth: usize,
    ) -> PdfResult<()> {
        if depth > self.document.limits.max_object_depth {
            return Err(limit("ParentTree nesting depth limit exceeded"));
        }
        let value = if let PdfObject::Reference(id) = object {
            if !stack.insert(*id) {
                self.warn(
                    "tagged_structure_cycle",
                    None,
                    format!("parent-tree-{}", id.number),
                    "cyclic ParentTree node was skipped",
                );
                return Ok(());
            }
            let value = self.document.object(*id)?.value;
            let result = self.walk_number_tree(&value, stack, entries, depth + 1);
            stack.remove(id);
            return result;
        } else {
            object.clone()
        };
        let Some(dictionary) = value.as_dictionary() else {
            self.warn(
                "tagged_structure_invalid",
                None,
                "parent-tree".to_owned(),
                "ParentTree node is not a dictionary",
            );
            return Ok(());
        };
        if let Some(PdfObject::Array(numbers)) = dictionary.get(&PdfName(b"Nums".to_vec())) {
            if !numbers.len().is_multiple_of(2) {
                self.warn(
                    "tagged_structure_invalid",
                    None,
                    "parent-tree-nums".to_owned(),
                    "ParentTree Nums array has odd length",
                );
            }
            for pair in numbers.chunks_exact(2) {
                let PdfObject::Integer(key) = pair[0] else {
                    self.warn(
                        "tagged_structure_invalid",
                        None,
                        "parent-tree-key".to_owned(),
                        "ParentTree key is not an integer",
                    );
                    continue;
                };
                if entries.len() >= self.document.limits.max_parent_tree_entries {
                    return Err(limit("ParentTree entry limit exceeded"));
                }
                if entries.insert(key, pair[1].clone()).is_some() {
                    self.warn(
                        "tagged_mcid_ambiguous",
                        None,
                        key.to_string(),
                        "duplicate ParentTree key used the last value",
                    );
                }
            }
        }
        if let Some(PdfObject::Array(kids)) = dictionary.get(&PdfName(b"Kids".to_vec())) {
            if kids.len() > self.document.limits.max_structure_kids {
                return Err(limit("ParentTree Kids limit exceeded"));
            }
            for kid in kids {
                self.walk_number_tree(kid, stack, entries, depth + 1)?;
            }
        }
        Ok(())
    }

    fn warn_parent_mismatch(&mut self, association: &TaggedAssociation, message: &str) {
        self.warn(
            "parent_tree_mismatch",
            Some(association.page_index),
            association.mcid.to_string(),
            message,
        );
    }

    fn warn(&mut self, code: &str, page_index: Option<usize>, key: String, message: &str) {
        if self.warning_keys.insert((code.to_owned(), page_index, key)) {
            self.result.warnings.push(TaggedStructureWarning {
                code: code.to_owned(),
                page_index,
                message: message.to_owned(),
            });
        }
    }
}

fn resolve_dictionary(
    document: &PdfDocument,
    object: &PdfObject,
    field: &str,
) -> PdfResult<PdfDictionary> {
    let value = resolve_value(document, object)?;
    value.as_dictionary().cloned().ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidObject,
            None,
            format!("{field} must resolve to a dictionary"),
        )
    })
}

fn limit(message: &str) -> PdfError {
    PdfError::new(ErrorCode::LimitExceeded, None, message)
}
#[cfg(test)]
mod page_index_tests {
    use super::*;

    #[test]
    fn compact_page_index_preserves_global_order_and_unassigned_tables() {
        let association = |page_index, mcid| TaggedAssociation {
            page_index,
            mcid,
            tag: "P".to_owned(),
            standard_role: Some("P".to_owned()),
            alt_text: None,
            actual_text: None,
            structure_object: None,
        };
        let valid_table = TaggedTable {
            structure_object: None,
            page_index: Some(0),
            rows: Vec::new(),
        };
        let cross_page_table = TaggedTable {
            structure_object: None,
            page_index: Some(0),
            rows: vec![TaggedTableRow {
                cells: vec![TaggedTableCell {
                    structure_object: None,
                    kind: TaggedTableCellKind::Data,
                    row_span: 1,
                    column_span: 1,
                    scope: None,
                    associations: vec![(1, 7)],
                    valid: true,
                }],
            }],
        };
        let index = TaggedStructureResult {
            associations: vec![association(1, 2), association(0, 1), association(1, 3)],
            tables: vec![valid_table, cross_page_table],
            warnings: Vec::new(),
        }
        .into_page_index(2);

        assert_eq!(index.pages[0].association_indices, vec![1]);
        assert_eq!(index.pages[1].association_indices, vec![0, 2]);
        assert_eq!(index.pages[0].table_indices, vec![0]);
        assert!(index.pages[1].table_indices.is_empty());
        assert!(index.unindexed_association_indices.is_empty());
        assert_eq!(index.unindexed_table_indices, vec![1]);
        assert_eq!(index.result.associations[2].mcid, 3);
    }
}
