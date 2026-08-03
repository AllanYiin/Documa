use std::collections::BTreeMap;
use std::io::{Cursor, Read};
use std::path::{Component, Path};

use office_core::{Asset, OfficeError, ParseLimits, Result};
use quick_xml::Reader;
use quick_xml::events::{BytesStart, Event};
use serde::{Deserialize, Serialize};
use zip::ZipArchive;

#[derive(Debug)]
pub struct OoxmlPackage {
    entries: BTreeMap<String, Vec<u8>>,
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
        let mut archive = ZipArchive::new(Cursor::new(bytes)).map_err(|error| {
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
            let size = file.size();
            let compressed = file.compressed_size().max(1);
            if size > limits.max_part_bytes {
                return Err(OfficeError::new(
                    "ZIP_LIMIT_EXCEEDED",
                    format!("OOXML part is too large: {name}"),
                    false,
                ));
            }
            if size / compressed > limits.max_compression_ratio {
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
            let mut data = Vec::with_capacity(size.min(usize::MAX as u64) as usize);
            file.read_to_end(&mut data).map_err(|error| {
                OfficeError::new(
                    "CORRUPT_PACKAGE",
                    format!("Unable to inflate OOXML part {name}: {error}"),
                    false,
                )
            })?;
            entries.insert(name, data);
        }
        if !entries.contains_key("[Content_Types].xml") {
            return Err(OfficeError::new(
                "MISSING_REQUIRED_PART",
                "OOXML package is missing [Content_Types].xml.",
                false,
            ));
        }
        Ok(Self { entries })
    }

    pub fn get(&self, name: &str) -> Result<&[u8]> {
        self.entries.get(name).map(Vec::as_slice).ok_or_else(|| {
            OfficeError::new(
                "MISSING_REQUIRED_PART",
                format!("OOXML package is missing {name}."),
                false,
            )
        })
    }

    pub fn get_optional(&self, name: &str) -> Option<&[u8]> {
        self.entries.get(name).map(Vec::as_slice)
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
                    format!("asset_{}", stable_fragment(name)),
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
            Some(bytes) => parse_relationships(bytes),
            None => Ok(Vec::new()),
        }
    }
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
    let mut reader = Reader::from_reader(Cursor::new(bytes));
    reader.config_mut().trim_text(true);
    let mut buffer = Vec::new();
    let mut relationships = Vec::new();
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Empty(element)) | Ok(Event::Start(element))
                if local_name(element.name().as_ref()) == b"Relationship" =>
            {
                relationships.push(Relationship {
                    id: attr(&reader, &element, b"Id").unwrap_or_default(),
                    relationship_type: attr(&reader, &element, b"Type").unwrap_or_default(),
                    target: attr(&reader, &element, b"Target").unwrap_or_default(),
                    target_mode: attr(&reader, &element, b"TargetMode"),
                });
            }
            Ok(Event::Eof) => break,
            Err(error) => return Err(xml_error(error)),
            _ => {}
        }
        buffer.clear();
    }
    Ok(relationships)
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
