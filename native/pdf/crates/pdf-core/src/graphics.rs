use crate::{ErrorCode, PdfDictionary, PdfDocument, PdfError, PdfObject, PdfResult};

#[derive(Debug, Clone, Copy)]
pub(crate) struct Matrix {
    pub a: f64,
    pub b: f64,
    pub c: f64,
    pub d: f64,
    pub e: f64,
    pub f: f64,
}

impl Matrix {
    pub const IDENTITY: Self = Self {
        a: 1.0,
        b: 0.0,
        c: 0.0,
        d: 1.0,
        e: 0.0,
        f: 0.0,
    };

    pub const fn multiply(self, other: Self) -> Self {
        Self {
            a: self.a * other.a + self.b * other.c,
            b: self.a * other.b + self.b * other.d,
            c: self.c * other.a + self.d * other.c,
            d: self.c * other.b + self.d * other.d,
            e: self.e * other.a + self.f * other.c + other.e,
            f: self.e * other.b + self.f * other.d + other.f,
        }
    }

    pub const fn is_finite(self) -> bool {
        self.a.is_finite()
            && self.b.is_finite()
            && self.c.is_finite()
            && self.d.is_finite()
            && self.e.is_finite()
            && self.f.is_finite()
    }

    pub fn transform(self, x: f64, y: f64) -> (f64, f64) {
        (
            x * self.a + y * self.c + self.e,
            x * self.b + y * self.d + self.f,
        )
    }
}

pub(crate) fn resolve_dictionary(
    document: &PdfDocument,
    object: &PdfObject,
) -> PdfResult<PdfDictionary> {
    let value = if let Some(id) = object.as_reference() {
        document.object(id)?.value
    } else {
        object.clone()
    };
    value.as_dictionary().cloned().ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "expected resource dictionary",
        )
    })
}

#[allow(clippy::cast_precision_loss)]
pub(crate) fn parse_matrix(object: &PdfObject) -> PdfResult<Matrix> {
    let PdfObject::Array(values) = object else {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "Form Matrix must be an array",
        ));
    };
    if values.len() != 6 {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "Form Matrix must contain six numbers",
        ));
    }
    let mut numbers = [0.0_f64; 6];
    for (index, value) in values.iter().enumerate() {
        numbers[index] = match value {
            PdfObject::Integer(value) => *value as f64,
            PdfObject::Real(value) => *value,
            _ => {
                return Err(PdfError::new(
                    ErrorCode::InvalidObject,
                    None,
                    "Form Matrix entries must be numbers",
                ));
            }
        };
    }
    Ok(Matrix {
        a: numbers[0],
        b: numbers[1],
        c: numbers[2],
        d: numbers[3],
        e: numbers[4],
        f: numbers[5],
    })
}
