use image::{ExtendedColorType, codecs::jpeg::JpegEncoder};
use pdf_core::{ImageDataFormat, PdfDocument};

fn jpeg_fixture() -> Vec<u8> {
    let mut jpeg = Vec::new();
    JpegEncoder::new_with_quality(&mut jpeg, 90)
        .encode(&[255, 0, 0, 0, 255, 0], 2, 1, ExtendedColorType::Rgb8)
        .expect("encode JPEG fixture");
    jpeg
}

fn image_pdf() -> Vec<u8> {
    let jpeg = jpeg_fixture();
    let objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << \
          /XObject << /Im1 4 0 R >> >> >>"
            .to_vec(),
        {
            let mut stream = format!(
                "<< /Type /XObject /Subtype /Image /Width 2 /Height 1 \
                 /ColorSpace /DeviceRGB /BitsPerComponent 8 \
                 /Filter /DCTDecode /Length {} >>\nstream\n",
                jpeg.len()
            )
            .into_bytes();
            stream.extend_from_slice(&jpeg);
            stream.extend_from_slice(b"\nendstream");
            stream
        },
    ];
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::new();
    for (index, object) in objects.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n", index + 1).as_bytes());
        pdf.extend_from_slice(object);
        pdf.extend_from_slice(b"\nendobj\n");
    }
    let xref_offset = pdf.len();
    pdf.extend_from_slice(format!("xref\n0 {}\n", objects.len() + 1).as_bytes());
    pdf.extend_from_slice(b"0000000000 65535 f\n");
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n\n").as_bytes());
    }
    pdf.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n",
            objects.len() + 1
        )
        .as_bytes(),
    );
    pdf
}

#[test]
fn extracts_and_validates_original_jpeg_bitstream() {
    let document = PdfDocument::parse(&image_pdf()).expect("valid image PDF");
    let images = document.extract_images().expect("image extraction");
    assert_eq!(images.len(), 1);
    assert_eq!(images[0].resource_name, "Im1");
    assert_eq!(images[0].format, ImageDataFormat::Jpeg);
    assert_eq!((images[0].width, images[0].height), (2, 1));
    assert_eq!(images[0].color_space.as_deref(), Some("DeviceRGB"));
    assert!(images[0].data.starts_with(&[0xff, 0xd8]));
    assert!(images[0].warnings.is_empty());
}
