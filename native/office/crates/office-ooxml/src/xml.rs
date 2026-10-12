//! Bounded XML tree for small OOXML structural parts, not worksheet cell data.
use office_core::{OfficeError, ParseBudget, ParseLimits, Result};
use quick_xml::{Reader, events::Event};
use std::collections::BTreeMap;

#[derive(Debug, Default, Clone)]
pub struct Node {
    pub name: String,
    pub attrs: BTreeMap<String, String>,
    pub children: Vec<Node>,
    pub text: String,
}
impl Node {
    pub fn attr(&self, name: &str) -> Option<&str> {
        self.attrs
            .get(name)
            .or_else(|| {
                self.attrs
                    .iter()
                    .find(|(k, _)| k.rsplit(':').next() == Some(name))
                    .map(|(_, v)| v)
            })
            .map(String::as_str)
    }
    pub fn child(&self, name: &str) -> Option<&Node> {
        self.children.iter().find(|n| n.name == name)
    }
    pub fn descendants<'a>(&'a self, name: &str) -> Vec<&'a Node> {
        let mut found = Vec::new();
        self.collect(name, &mut found);
        found
    }
    fn collect<'a>(&'a self, name: &str, out: &mut Vec<&'a Node>) {
        for child in &self.children {
            if child.name == name {
                out.push(child);
            }
            child.collect(name, out);
        }
    }
    pub fn content(&self) -> String {
        let mut value = self.text.clone();
        for child in &self.children {
            value.push_str(&child.content());
        }
        value
    }
    pub fn drawing_text(&self) -> String {
        self.descendants("p")
            .iter()
            .map(|p| {
                p.descendants("t")
                    .iter()
                    .map(|t| t.content())
                    .collect::<String>()
            })
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join("\n")
    }
}

pub fn parse_xml(bytes: &[u8], limits: &ParseLimits) -> Result<Node> {
    let mut reader = Reader::from_reader(bytes);
    let mut stack = vec![Node::default()];
    let mut budget = ParseBudget::default();
    loop {
        let event = reader.read_event().map_err(super::xml_error)?;
        let empty = matches!(&event, Event::Empty(_));
        match event {
            Event::Start(e) | Event::Empty(e) => {
                if stack.len() > limits.max_xml_depth {
                    return Err(OfficeError::new(
                        "PARSE_LIMIT_EXCEEDED",
                        "XML nesting limit exceeded.",
                        false,
                    ));
                }
                let mut node = Node {
                    name: String::from_utf8_lossy(super::local_name(e.name().as_ref()))
                        .into_owned(),
                    ..Node::default()
                };
                for a in e.attributes() {
                    let a = a.map_err(|e| OfficeError::new("INVALID_XML", e.to_string(), false))?;
                    node.attrs.insert(
                        String::from_utf8_lossy(a.key.as_ref()).into_owned(),
                        a.decoded_and_normalized_value(
                            quick_xml::XmlVersion::Implicit1_0,
                            reader.decoder(),
                        )
                        .map_err(super::xml_error)?
                        .into_owned(),
                    );
                }
                if empty {
                    stack.last_mut().unwrap().children.push(node);
                } else {
                    stack.push(node);
                }
            }
            Event::End(_) => {
                if stack.len() <= 1 {
                    return Err(OfficeError::new(
                        "INVALID_XML",
                        "Unexpected closing tag.",
                        false,
                    ));
                }
                let node = stack.pop().unwrap();
                stack.last_mut().unwrap().children.push(node);
            }
            Event::Text(e) => {
                let text = e
                    .decode()
                    .map_err(|e| OfficeError::new("INVALID_XML", e.to_string(), false))?;
                budget.add_text(&text, limits)?;
                stack.last_mut().unwrap().text.push_str(&text);
            }
            Event::CData(e) => {
                let text = e
                    .decode()
                    .map_err(|e| OfficeError::new("INVALID_XML", e.to_string(), false))?;
                budget.add_text(&text, limits)?;
                stack.last_mut().unwrap().text.push_str(&text);
            }
            Event::GeneralRef(e) => {
                let entity = format!("&{};", String::from_utf8_lossy(e.as_ref()));
                let text = quick_xml::escape::unescape(&entity)
                    .map_err(|e| OfficeError::new("INVALID_XML", e.to_string(), false))?;
                budget.add_text(&text, limits)?;
                stack.last_mut().unwrap().text.push_str(&text);
            }
            Event::DocType(_) => {
                return Err(OfficeError::new(
                    "INVALID_XML",
                    "DTD declarations are not supported.",
                    false,
                ));
            }
            Event::Eof => break,
            _ => {}
        }
    }
    let root = stack.pop().unwrap();
    if !stack.is_empty() || root.children.len() != 1 || !root.text.trim().is_empty() {
        return Err(OfficeError::new(
            "INVALID_XML",
            "Expected one complete XML root.",
            false,
        ));
    }
    Ok(root.children.into_iter().next().unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn entities_and_cdata_are_decoded() {
        let n = parse_xml(
            b"<root>A&amp;B<![CDATA[<C>]]>&#x4E2D;</root>",
            &ParseLimits::default(),
        )
        .unwrap();
        assert_eq!(n.content(), "A&B<C>中");
    }
    #[test]
    fn incomplete_multiple_and_dtd_roots_are_rejected() {
        for xml in ["<root>", "<a/><b/>", "<!DOCTYPE a><a/>"] {
            assert!(parse_xml(xml.as_bytes(), &ParseLimits::default()).is_err());
        }
    }
}
