use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::{Cursor, Read};
use std::path::{Component, Path};
use std::sync::{Mutex, OnceLock};

use office_core::{Asset, OfficeError, ParseLimits, Result};
use quick_xml::Reader;
use quick_xml::events::{BytesStart, Event};
use serde::{Deserialize, Serialize};
use zip::ZipArchive;

pub mod xml;
pub use xml::{Node, parse_xml};

#[derive(Debug)]
pub struct OoxmlPackage {
    entries: BTreeMap<String, PackagePart>,
    archive: Mutex<ZipArchive<Cursor<Vec<u8>>>>,
    limits: ParseLimits,
}
#[derive(Debug)]
struct PackagePart {
    index: usize,
    data: OnceLock<Vec<u8>>,
    hash: String,
    size: u64,
}

impl OoxmlPackage {
    pub fn open(bytes: &[u8], limits: &ParseLimits) -> Result<Self> {
        if bytes.len() as u64 > limits.max_input_bytes {
            return Err(OfficeError::new(
                "INPUT_LIMIT_EXCEEDED",
                "Office input exceeds max_input_bytes.",
                false,
            ));
        }
        let mut archive = ZipArchive::new(Cursor::new(bytes.to_vec())).map_err(|error| {
            OfficeError::new(
                "CORRUPT_PACKAGE",
                format!("Unable to open OOXML ZIP package: {error}"),
                false,
            )
        })?;
        if archive.len() > limits.max_parts {
            return Err(OfficeError::new(
                "ZIP_LIMIT_EXCEEDED",
                "OOXML package contains too many parts.",
                false,
            ));
        }
        let mut entries = BTreeMap::new();
        let mut total_uncompressed = 0_u64;
        for index in 0..archive.len() {
            let mut file = archive.by_index(index).map_err(|error| {
                OfficeError::new(
                    "CORRUPT_PACKAGE",
                    format!("Unable to read ZIP part {index}: {error}"),
                    false,
                )
            })?;
            if file.is_dir() {
                continue;
            }
            let name = file.name().replace('\\', "/");
            validate_part_name(&name)?;
            if entries.contains_key(&name) {
                return Err(OfficeError::new(
                    "CORRUPT_PACKAGE",
                    "Duplicate ZIP part name.",
                    false,
                ));
            }
            let size = file.size();
            let compressed = file.compressed_size().max(1);
            if size > limits.max_part_bytes {
                return Err(OfficeError::new(
                    "ZIP_LIMIT_EXCEEDED",
                    format!("OOXML part is too large: {name}"),
                    false,
                ));
            }
            if size > compressed.saturating_mul(limits.max_compression_ratio) {
                return Err(OfficeError::new(
                    "ZIP_BOMB_DETECTED",
                    format!("Suspicious compression ratio in part: {name}"),
                    false,
                ));
            }
            total_uncompressed = total_uncompressed.saturating_add(size);
            if total_uncompressed > limits.max_uncompressed_bytes {
                return Err(OfficeError::new(
                    "ZIP_LIMIT_EXCEEDED",
                    "OOXML expanded size exceeds the configured limit.",
                    false,
                ));
            }
            // Validate every part without retaining expanded media. XML is loaded on demand.
            let mut digest = Sha256::new();
            let mut actual = 0_u64;
            let mut chunk = [0_u8; 32 * 1024];
            loop {
                let count = file
                    .read(&mut chunk)
                    .map_err(|e| OfficeError::new("CORRUPT_PACKAGE", e.to_string(), false))?;
                if count == 0 {
                    break;
                }
                actual = actual.saturating_add(count as u64);
                if actual > size || actual > limits.max_part_bytes {
                    return Err(OfficeError::new(
                        "ZIP_LIMIT_EXCEEDED",
                        "Expanded part exceeds declared size or limit.",
                        false,
                    ));
                }
                digest.update(&chunk[..count]);
            }
            if actual != size {
                return Err(OfficeError::new(
                    "CORRUPT_PACKAGE",
                    "ZIP part size mismatch.",
                    false,
                ));
            }
            let hash = digest
                .finalize()
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
            entries.insert(
                name,
                PackagePart {
                    index,
                    data: OnceLock::new(),
                    hash,
                    size,
                },
            );
        }
        if !entries.contains_key("[Content_Types].xml") {
            return Err(OfficeError::new(
                "MISSING_REQUIRED_PART",
                "OOXML package is missing [Content_Types].xml.",
                false,
            ));
        }
        let package = Self {
            entries,
            archive: Mutex::new(archive),
            limits: limits.clone(),
        };
        let types = parse_xml(package.get("[Content_Types].xml")?, limits)?;
        if types.name != "Types" {
            return Err(OfficeError::new(
                "OOXML_CONTENT_TYPES_INVALID",
                "Expected Types root.",
                false,
            ));
        }
        if types
            .children
            .iter()
            .filter_map(|n| n.attr("ContentType"))
            .any(|v| {
                let v = v.to_ascii_lowercase();
                v.contains("macroenabled") || v.contains("vbaproject")
            })
        {
            return Err(OfficeError::new(
                "MACRO_ENABLED_OFFICE_NOT_SUPPORTED",
                "Macro-enabled content is not supported, even when renamed.",
                true,
            ));
        }
        Ok(package)
    }

    pub fn get(&self, name: &str) -> Result<&[u8]> {
        let part = self.entries.get(name).ok_or_else(|| {
            OfficeError::new(
                "MISSING_REQUIRED_PART",
                format!("OOXML package is missing {name}."),
                false,
            )
        })?;
        if let Some(data) = part.data.get() {
            return Ok(data);
        }
        let mut archive = self.archive.lock().map_err(|_| {
            OfficeError::new("CORRUPT_PACKAGE", "Package reader lock failed.", false)
        })?;
        let mut file = archive
            .by_index(part.index)
            .map_err(|e| OfficeError::new("CORRUPT_PACKAGE", e.to_string(), false))?;
        let mut data = Vec::new();
        file.read_to_end(&mut data)
            .map_err(|e| OfficeError::new("CORRUPT_PACKAGE", e.to_string(), false))?;
        let _ = part.data.set(data);
        Ok(part.data.get().expect("part initialized"))
    }
    pub fn get_optional(&self, name: &str) -> Option<&[u8]> {
        self.get(name).ok()
    }

    pub fn names(&self) -> impl Iterator<Item = &str> {
        self.entries.keys().map(String::as_str)
    }

    pub fn names_with_prefix<'a>(&'a self, prefix: &'a str) -> impl Iterator<Item = &'a str> + 'a {
        self.entries
            .keys()
            .filter(move |name| name.starts_with(prefix))
            .map(String::as_str)
    }

    pub fn media_assets(&self, prefix: &str) -> Vec<Asset> {
        self.names_with_prefix(prefix)
            .filter_map(|name| {
                let data = self.get_optional(name)?;
                let file_name = name.rsplit('/').next().unwrap_or(name);
                Some(Asset::from_bytes(
                    asset_id(name),
                    file_name,
                    mime_for_name(file_name),
                    format!("ooxml:part:{name}"),
                    data,
                ))
            })
            .collect()
    }

    pub fn relationships(&self, part_name: &str) -> Result<Vec<Relationship>> {
        let path = relationship_part_name(part_name);
        match self.get_optional(&path) {
            Some(bytes) => parse_relationships_with_limits(bytes, &self.limits),
            None => Ok(Vec::new()),
        }
    }

    pub fn related_part(&self, source: &str, id: &str) -> Result<Option<String>> {
        let relationship = self.relationships(source)?.into_iter().find(|r| r.id == id);
        relationship
            .filter(|r| !r.external())
            .map(|r| resolve_target(source, &r.target))
            .transpose()
    }

    pub fn related_by_type(&self, source: &str, kind: &str) -> Result<Vec<String>> {
        self.relationships(source)?
            .into_iter()
            .filter(|r| !r.external() && r.relationship_type.ends_with(&format!("/{kind}")))
            .map(|r| resolve_target(source, &r.target))
            .collect()
    }

    pub fn assets(&self, prefix: &str, options: &office_core::ParseOptions) -> Vec<Asset> {
        if !options.extract_images {
            return Vec::new();
        }
        if options.asset_mode == "inline" {
            return self.media_assets(prefix);
        }
        self.names_with_prefix(prefix)
            .filter_map(|name| {
                let part = self.entries.get(name)?;
                let file_name = name.rsplit('/').next().unwrap_or(name);
                Some(Asset {
                    id: asset_id(name),
                    mime_type: mime_for_name(file_name).into(),
                    file_name: file_name.into(),
                    data_base64: String::new(),
                    sha256: part.hash.clone(),
                    source_ref: format!("ooxml:part:{name}"),
                    alt_text: None,
                    metadata: BTreeMap::from([
                        ("data_omitted".into(), serde_json::json!(true)),
                        ("size_bytes".into(), serde_json::json!(part.size)),
                    ]),
                })
            })
            .collect()
    }

    pub fn graphics(
        &self,
        source: &str,
        options: &office_core::ParseOptions,
    ) -> Result<Vec<serde_json::Value>> {
        let mut graphics = Vec::new();
        for relationship in self.relationships(source)? {
            if relationship.external() {
                continue;
            }
            let kind = relationship
                .relationship_type
                .rsplit('/')
                .next()
                .unwrap_or("");
            if !matches!(kind, "chart" | "diagramData") {
                continue;
            }
            let part = resolve_target(source, &relationship.target)?;
            let node = parse_xml(self.get(&part)?, &options.limits)?;
            let content = if kind == "chart" {
                let series: Vec<_> = node.descendants("ser").iter().map(|s| {
                    let values = |name: &str| s.child(name).map(|n| n.descendants("pt").iter().map(|p| serde_json::json!({"index": p.attr("idx"), "value": p.child("v").map(Node::content)})).collect::<Vec<_>>()).unwrap_or_default();
                    serde_json::json!({"name": s.child("tx").map(|n| n.descendants("v").iter().map(|v| v.content()).collect::<Vec<_>>().join(" ")), "categories": values("cat"), "values": values("val"), "x_values": values("xVal"), "y_values": values("yVal"), "formulas": s.descendants("f").iter().map(|n| n.content()).collect::<Vec<_>>()})
                }).collect();
                serde_json::json!({"kind": "chart", "title": node.descendants("title").first().map(|n| n.drawing_text()), "series": series, "value_source": "stored_cache", "recalculated": false})
            } else {
                serde_json::json!({"kind": "smartart", "text": node.drawing_text()})
            };
            let mut content = content.as_object().unwrap().clone();
            content.insert("source_part".into(), serde_json::json!(part));
            content.insert("relationship_id".into(), serde_json::json!(relationship.id));
            graphics.push(serde_json::Value::Object(content));
        }
        Ok(graphics)
    }
}

pub fn resolve_target(source: &str, target: &str) -> Result<String> {
    let target = target.replace('\\', "/");
    let mut parts: Vec<&str> = if target.starts_with('/') {
        Vec::new()
    } else {
        source.split('/').collect()
    };
    if !target.starts_with('/') {
        parts.pop();
    }
    for segment in target.split('/') {
        match segment {
            "" | "." => {}
            ".." => {
                if parts.pop().is_none() {
                    return Err(OfficeError::new(
                        "ZIP_PATH_TRAVERSAL",
                        "Relationship escapes package root.",
                        false,
                    ));
                }
            }
            s if s.contains(':') => {
                return Err(OfficeError::new(
                    "ZIP_PATH_TRAVERSAL",
                    "Invalid internal relationship target.",
                    false,
                ));
            }
            s => parts.push(s),
        }
    }
    let result = parts.join("/");
    validate_part_name(&result)?;
    Ok(result)
}

pub fn asset_id(part: &str) -> String {
    format!(
        "asset_{}_{}",
        stable_fragment(part),
        &office_core::sha256_hex(part.as_bytes())[..16]
    )
}

fn validate_part_name(name: &str) -> Result<()> {
    let path = Path::new(name);
    if path.is_absolute()
        || path.components().any(|part| {
            matches!(
                part,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        return Err(OfficeError::new(
            "ZIP_PATH_TRAVERSAL",
            format!("Unsafe OOXML part path: {name}"),
            false,
        ));
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Relationship {
    pub id: String,
    pub relationship_type: String,
    pub target: String,
    pub target_mode: Option<String>,
}

impl Relationship {
    pub fn external(&self) -> bool {
        self.target_mode.as_deref() == Some("External")
    }
}

pub fn parse_relationships(bytes: &[u8]) -> Result<Vec<Relationship>> {
    parse_relationships_with_limits(bytes, &ParseLimits::default())
}
fn parse_relationships_with_limits(
    bytes: &[u8],
    limits: &ParseLimits,
) -> Result<Vec<Relationship>> {
    let root = parse_xml(bytes, limits)?;
    if root.name != "Relationships" {
        return Err(OfficeError::new(
            "INVALID_RELATIONSHIP",
            "Expected Relationships root.",
            false,
        ));
    }
    let mut seen = std::collections::BTreeSet::new();
    root.children
        .iter()
        .filter(|n| n.name == "Relationship")
        .map(|n| {
            let required = |name| {
                n.attr(name)
                    .filter(|v| !v.is_empty())
                    .map(str::to_owned)
                    .ok_or_else(|| {
                        OfficeError::new(
                            "INVALID_RELATIONSHIP",
                            format!("Missing relationship {name}."),
                            false,
                        )
                    })
            };
            let id = required("Id")?;
            if !seen.insert(id.clone()) {
                return Err(OfficeError::new(
                    "INVALID_RELATIONSHIP",
                    "Duplicate relationship ID.",
                    false,
                ));
            }
            let target_mode = n.attr("TargetMode").map(str::to_owned);
            if target_mode
                .as_deref()
                .is_some_and(|s| !matches!(s, "External" | "Internal"))
            {
                return Err(OfficeError::new(
                    "INVALID_RELATIONSHIP",
                    "Invalid TargetMode.",
                    false,
                ));
            }
            Ok(Relationship {
                id,
                relationship_type: required("Type")?,
                target: required("Target")?,
                target_mode,
            })
        })
        .collect()
}

pub fn attr<R: std::io::BufRead>(
    reader: &Reader<R>,
    element: &BytesStart<'_>,
    key: &[u8],
) -> Option<String> {
    element
        .attributes()
        .with_checks(false)
        .flatten()
        .find_map(|attribute| {
            if local_name(attribute.key.as_ref()) != key {
                return None;
            }
            attribute
                .decoded_and_normalized_value(quick_xml::XmlVersion::Implicit1_0, reader.decoder())
                .ok()
                .map(|value| value.into_owned())
        })
}

pub fn text_value(bytes: &[u8]) -> Result<String> {
    let mut reader = Reader::from_reader(Cursor::new(bytes));
    reader.config_mut().trim_text(false);
    let mut buffer = Vec::new();
    let mut out = String::new();
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Text(text)) => out.push_str(
                &text
                    .decode()
                    .map_err(|error| OfficeError::new("INVALID_XML", error.to_string(), false))?,
            ),
            Ok(Event::Eof) => break,
            Err(error) => return Err(xml_error(error)),
            _ => {}
        }
        buffer.clear();
    }
    Ok(out)
}

pub fn local_name(name: &[u8]) -> &[u8] {
    name.rsplit(|byte| *byte == b':').next().unwrap_or(name)
}

pub fn xml_error(error: quick_xml::Error) -> OfficeError {
    OfficeError::new(
        "INVALID_XML",
        format!("Unable to parse OOXML XML: {error}"),
        false,
    )
}

pub fn stable_fragment(value: &str) -> String {
    let mut hash = 0xcbf29ce484222325_u64;
    for byte in value.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}

pub fn mime_for_name(name: &str) -> &'static str {
    match name
        .rsplit('.')
        .next()
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "bmp" => "image/bmp",
        "tif" | "tiff" => "image/tiff",
        "svg" => "image/svg+xml",
        "emf" => "image/emf",
        "wmf" => "image/wmf",
        _ => "application/octet-stream",
    }
}

fn relationship_part_name(part_name: &str) -> String {
    let path = Path::new(part_name);
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or(part_name);
    let parent = path
        .parent()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .replace('\\', "/");
    if parent.is_empty() {
        format!("_rels/{file_name}.rels")
    } else {
        format!("{parent}/_rels/{file_name}.rels")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_traversal_is_rejected() {
        assert_eq!(
            validate_part_name("../evil.xml").unwrap_err().code,
            "ZIP_PATH_TRAVERSAL"
        );
    }

    #[test]
    fn external_relationship_is_only_metadata() {
        let xml = br#"<Relationships><Relationship Id="r1" Type="link" Target="https://example.com" TargetMode="External"/></Relationships>"#;
        let rels = parse_relationships(xml).unwrap();
        assert!(rels[0].external());
    }
}
