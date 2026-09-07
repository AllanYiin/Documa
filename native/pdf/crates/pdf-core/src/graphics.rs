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

    /// Compose a new user-space transform inside the current CTM:
    /// `self.multiply(other).transform(p) == self.transform(other.transform(p))`.
    pub const fn multiply(self, other: Self) -> Self {
        Self {
            a: self.a * other.a + self.c * other.b,
            b: self.b * other.a + self.d * other.b,
            c: self.a * other.c + self.c * other.d,
            d: self.b * other.c + self.d * other.d,
            e: self.a * other.e + self.c * other.f + self.e,
            f: self.b * other.e + self.d * other.f + self.f,
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

#[cfg(test)]
mod tests {
    use super::Matrix;

    #[test]
    fn general_affine_composition_preserves_nested_transform_order() {
        let parent = Matrix {
            a: 0.5,
            b: 0.1,
            c: -0.2,
            d: 0.8,
            e: 10.0,
            f: 20.0,
        };
        let child = Matrix {
            a: 1.0,
            b: 0.2,
            c: 0.3,
            d: 1.5,
            e: 4.0,
            f: -2.0,
        };
        let result = parent.multiply(child).transform(3.0, 7.0);
        assert!((result.0 - 12.73).abs() < 1e-9);
        assert!((result.1 - 28.19).abs() < 1e-9);
    }
}
