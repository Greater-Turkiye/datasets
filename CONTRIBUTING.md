# Veriye katkı / Contributing data

Önce genel rehber: [Greater-Turkiye/.github CONTRIBUTING](https://github.com/Greater-Turkiye/.github/blob/main/CONTRIBUTING.md) ve **kırmızı çizgiler**.
Read the org-wide guide and the **red lines** first.

## Kod yazmadan / Without code

[Veri önerisi formu](https://github.com/Greater-Turkiye/datasets/issues/new?template=01-data-submission.yml) ile olay/kaynak önerin. Bir reviewer kayda dönüştürür.
Open a data-submission issue; a reviewer turns it into a record.

## PR ile / Via pull request

1. Repoyu **fork**'layın, dal açın: `git checkout -b evt-aegean-incident`
2. `pip install -r requirements.txt`
3. `python tools/gt.py new event` → oluşan dosyadaki `TODO`'ları doldurun.
   - Bölge, olay türü, ülke kodları: `vocab/` klasörü. Uygun kod yoksa önce **kod önerisi** issue'su açın.
   - Aktör/ekipman/kaynak kaydı yoksa `new actor`, `new equipment`, `new source` ile oluşturun.
   - Her web kaynağı için arşiv alın: `https://web.archive.org/save/<url>` → `archives:` altına ekleyin.
4. `python tools/gt.py fmt` → anahtarları kanonik sıraya dizer, `act_/sit_/eqp_/src_` referanslarına `# English label` yorumu ekler.
   Dosya başındaki yorum bloğu korunur, diğer yorumlar silinir. CI `fmt --check` ile denetler.
   Run `fmt` to canonicalise key order and annotate references; CI runs `fmt --check`.
5. `python tools/gt.py validate` hatasız geçmeli (uyarılar olabilir).
6. PR açın, şablondaki kontrol listesini işaretleyin.

## Araçlar / Tooling

`tools/gt.py` değişiklikleri için testler: `pip install pytest && python -m pytest tests`. Testler reponun geçici bir kopyasında çalışır, gerçek kayıtlara dokunmaz.
Yeni bir politika kuralı eklerseniz `tests/test_gt.py` içindeki `CASES` listesine reddedilen bir örnek ekleyin.
Tests for `tools/gt.py` run on a temporary copy of the repo. When you add a policy rule, add a failing example to `CASES`.

## Yazım kuralları / Style

- Türkçe kaynak dildir; `en` yayından önce zorunludur. Makine çevirisi ise `i18n: {source: tr, machine: [en]}` ekleyin.
- İddiayı atfedin: "MSB'ye göre…", "According to the Hellenic NDGS…". Tartışmalı nitelendirme → `claims[]`.
- Zaman daima **UTC** (`2026-09-10T11:00Z`); hassasiyeti abartmayın (`precision`).
- Koordinat en çok 5 ondalık; bilinmiyorsa `geometry` koymayın, `precision` yeterli.
- Hedef gösterme dili, nefret söylemi, esir/ceset görüntüsü yok.

## Silme ve düzeltme / Deletion and corrections

Dosya asla silinmez ve taşınmaz. Hata → `corrections[]` girdisi ekleyin. Geri çekme → içerik `schema: tombstone/1` ile değiştirilir.
Files are never deleted or moved. Fix via `corrections[]`; withdraw via a `tombstone/1` record.

## Lisans / License

PR açarak katkınızın **CC BY 4.0** (veri) / **MIT** (kod) altında yayınlanmasını kabul edersiniz. Başkasının içeriğini kopyalamayın.
By opening a PR you license your contribution under CC BY 4.0 (data) / MIT (code). Do not copy others' content.
