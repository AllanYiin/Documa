//! 分詞吞吐量 benchmark：短句 / 段落 / 長文三組。
//! 需先跑 lingxi-convert 產生 assets（與 real_assets 整合測試同一套）。

use criterion::{criterion_group, criterion_main, Criterion, Throughput};
use lingxi_core::{CustomLexiconEntry, CustomLexiconSpec, Segmenter, SegmenterOptions};

const SHORT: &str = "金管會前主委參加台北市政府的記者會"; // ~17 字
const PARA: &str = "行政院主計總處今天公布最新經濟成長率預測，全年經濟成長率上修至百分之三點二，主因出口表現優於預期，半導體產業受惠人工智慧需求暢旺，帶動相關供應鏈出貨動能強勁。不過內需方面，民間消費成長動能趨緩，房市交易量縮，加上國際地緣政治風險仍高，主計總處提醒下半年不確定性因素仍多。學者分析，台灣經濟結構高度依賴科技產業出口，若終端需求反轉，恐衝擊整體成長表現，建議政府持續推動產業多元化，並強化服務業附加價值，以分散風險。此外勞動市場方面，失業率維持低檔，實質薪資成長仍然有限，物價漲幅雖趨緩但民生必需品價格居高不下，民眾對經濟的實際感受與總體數據之間仍有落差。";

fn bench_segment(c: &mut Criterion) {
    let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/../../assets");
    let seg = match Segmenter::from_asset_dir(dir) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("assets 不存在（{e}），先跑 lingxi-convert");
            return;
        }
    };
    let custom_lexicons: Vec<_> = (0..10)
        .map(|lexicon_index| CustomLexiconSpec {
            schema_version: 1,
            id: format!("benchmark-{lexicon_index}"),
            domain: format!("domain-{lexicon_index}"),
            priority: 0,
            enabled: true,
            entries: (0..5_000)
                .map(|entry_index| {
                    let index = lexicon_index * 5_000 + entry_index;
                    let word: String = [
                        char::from_u32(0x4e00 + (index / 400) as u32).unwrap(),
                        char::from_u32(0x5200 + ((index / 20) % 200) as u32).unwrap(),
                        char::from_u32(0x6000 + (index % 20) as u32).unwrap(),
                    ]
                    .into_iter()
                    .collect();
                    CustomLexiconEntry {
                        word,
                        pos: Some("Na".into()),
                        affect: None,
                    }
                })
                .collect(),
        })
        .collect();
    let custom_seg =
        Segmenter::from_asset_dir_with_options(dir, SegmenterOptions { custom_lexicons })
            .expect("50,000 詞 benchmark 辭典應可載入");
    let long_10k: String = PARA.chars().cycle().take(10_000).collect();
    let long_20k: String = PARA.chars().cycle().take(20_000).collect();
    let long_40k: String = PARA.chars().cycle().take(40_000).collect();

    let mut group = c.benchmark_group("cut");
    for (name, text) in [
        ("short_17chars", SHORT),
        ("para_250chars", PARA),
        ("long_10k_chars", long_10k.as_str()),
        ("long_20k_chars", long_20k.as_str()),
        ("long_40k_chars", long_40k.as_str()),
    ] {
        group.throughput(Throughput::Bytes(text.len() as u64));
        group.bench_function(name, |b| b.iter(|| seg.cut(std::hint::black_box(text))));
    }
    group.finish();

    let mut group = c.benchmark_group("custom_lexicon_50k");
    group.throughput(Throughput::Bytes(PARA.len() as u64));
    group.bench_function("baseline", |b| {
        b.iter(|| seg.cut(std::hint::black_box(PARA)))
    });
    group.bench_function("ten_domains_50000_words", |b| {
        b.iter(|| custom_seg.cut(std::hint::black_box(PARA)))
    });
    group.finish();

    let mut group = c.benchmark_group("tokenize");
    group.throughput(Throughput::Bytes(PARA.len() as u64));
    group.bench_function("para_250chars", |b| {
        b.iter(|| seg.tokenize(std::hint::black_box(PARA)))
    });
    group.finish();
}

criterion_group!(benches, bench_segment);
criterion_main!(benches);
