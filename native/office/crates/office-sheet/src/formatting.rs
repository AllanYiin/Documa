use calamine::{ExcelDateTime, ExcelDateTimeType};
use serde_json::Value;

pub(super) fn builtin(id: u32) -> String {
    match id {
        0 => "General",
        1 => "0",
        2 => "0.00",
        3 => "#,##0",
        4 => "#,##0.00",
        9 => "0%",
        10 => "0.00%",
        11 => "0.00E+00",
        14 => "mm-dd-yy",
        15 => "d-mmm-yy",
        16 => "d-mmm",
        17 => "mmm-yy",
        18 => "h:mm AM/PM",
        19 => "h:mm:ss AM/PM",
        20 => "h:mm",
        21 => "h:mm:ss",
        22 => "m/d/yy h:mm",
        45 => "mm:ss",
        46 => "[h]:mm:ss",
        49 => "@",
        _ => return format!("builtin:{id}"),
    }
    .into()
}
pub(super) fn is_date(format: &str) -> bool {
    let lower = format.to_ascii_lowercase();
    !lower.starts_with("builtin:")
        && !lower.contains('[')
        && (lower.contains('y')
            || lower.contains('d')
            || lower.contains("h:")
            || lower.contains("mm:ss"))
}
pub(super) fn display(
    raw: &Value,
    kind: &str,
    format: &str,
    date1904: bool,
) -> (String, bool, Option<String>) {
    let basic = match raw {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "true" } else { "false" }.into(),
        _ => raw
            .as_f64()
            .map(|n| n.to_string())
            .unwrap_or_else(|| raw.to_string()),
    };
    let Some(n) = raw.as_f64() else {
        return (basic.clone(), true, (kind == "datetime").then_some(basic));
    };
    if is_date(format) || kind == "datetime" {
        if !n.is_finite() || !(0.0..=2_958_465.999_99).contains(&n) {
            return (basic, false, None);
        }
        let (y, m, d, h, mi, s, ms) =
            ExcelDateTime::new(n, ExcelDateTimeType::DateTime, date1904).to_ymd_hms_milli();
        let iso = format!("{y:04}-{m:02}-{d:02}T{h:02}:{mi:02}:{s:02}.{ms:03}");
        let display = match format.to_ascii_lowercase().as_str() {
            "yyyy-mm-dd" => Some(format!("{y:04}-{m:02}-{d:02}")),
            "mm-dd-yy" => Some(format!("{m:02}-{d:02}-{:02}", y % 100)),
            "yyyy-mm-dd hh:mm:ss" => Some(format!("{y:04}-{m:02}-{d:02} {h:02}:{mi:02}:{s:02}")),
            "h:mm" => Some(format!("{h}:{mi:02}")),
            "h:mm:ss" => Some(format!("{h}:{mi:02}:{s:02}")),
            _ => None,
        };
        return (
            display.clone().unwrap_or_else(|| iso.clone()),
            display.is_some(),
            Some(iso),
        );
    }
    if matches!(format, "General" | "@") {
        return (basic, true, None);
    }
    let percent = format.ends_with('%');
    let pattern = format.trim_end_matches('%');
    if !pattern.is_empty()
        && pattern.chars().all(|c| matches!(c, '0' | '#' | ',' | '.'))
        && pattern.matches('.').count() <= 1
        && pattern
            .split_once('.')
            .is_none_or(|(_, fraction)| fraction.bytes().all(|c| c == b'0'))
    {
        let decimals = pattern.split_once('.').map(|(_, s)| s.len()).unwrap_or(0);
        if decimals > 15 {
            return (basic, false, None);
        }
        let value = if percent { n * 100.0 } else { n };
        let mut result = format!("{value:.decimals$}");
        if pattern.contains(',') {
            let (integer, fraction) = result.split_once('.').unwrap_or((&result, ""));
            let negative = integer.starts_with('-');
            let digits = integer.trim_start_matches('-');
            let mut grouped = String::new();
            for (i, c) in digits.chars().enumerate() {
                if i > 0 && (digits.len() - i) % 3 == 0 {
                    grouped.push(',');
                }
                grouped.push(c);
            }
            result = format!(
                "{}{}{}",
                if negative { "-" } else { "" },
                grouped,
                if fraction.is_empty() {
                    String::new()
                } else {
                    format!(".{fraction}")
                }
            );
        } else if decimals == 0 && pattern.bytes().all(|c| c == b'0') {
            let width = pattern.len();
            result = format!("{:0>width$}", result);
        }
        if percent {
            result.push('%');
        }
        return (result, true, None);
    }
    (basic, false, None)
}
