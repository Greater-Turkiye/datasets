# Greater Türkiye — Datasets

[![validate](https://github.com/Greater-Turkiye/datasets/actions/workflows/validate.yml/badge.svg)](https://github.com/Greater-Turkiye/datasets/actions/workflows/validate.yml)
![data: CC BY 4.0](https://img.shields.io/badge/data-CC%20BY%204.0-blue) ![code: MIT](https://img.shields.io/badge/code-MIT-green)

**TR** · Türkiye'nin çevresindeki askeri ve güvenlik gelişmelerine dair **açık kaynaklı, doğrulanmış, iki dilli** kayıtlar.
**EN** · **Open-source, verified, bilingual** records of military and security developments in Türkiye's neighbourhood.

> Kapsam dışarıya dönüktür: Türk kuvvetlerinin konum/hareketleri kaydedilmez. Kırmızı çizgiler: [handbook/tr/02-red-lines.md](https://github.com/Greater-Turkiye/handbook/blob/main/tr/02-red-lines.md)
> The scope is outward-looking: positions/movements of Turkish forces are never recorded.

## Yapı / Layout

```
schemas/v1/     JSON Schema (2020-12) — kayıt türleri / record types
vocab/          kontrollü sözlükler (bölge, olay türü, ülke...) / controlled vocabularies
policy.yaml     CI ile zorlanan içerik güvenliği kuralları / content-safety rules enforced by CI
data/<tür>/<yyyy>/<mm>/<id>.yaml   her kayıt bir dosya / one file per record
examples/       kurgusal örnek kayıtlar (yayınlanmaz) / fictional examples (not exported)
tools/gt.py     validate | build | fmt | new | id
tools/data/     Türkiye coğrafi çiti (Natural Earth, kamu malı) / Türkiye geofence (Natural Earth, public domain)
tests/          gt.py birim testleri (pytest) / unit tests for gt.py
```

| Tür / Kind | Önek / Prefix | Açıklama / Description |
|---|---|---|
| `event` | `evt_` | Olay: tatbikat, konuşlanma, saldırı, hava/deniz olayı, tedarik, açıklama... |
| `actor` | `act_` | Devlet, ordu, birlik, bakanlık, silahlı grup, şirket |
| `site` | `sit_` | Türkiye dışındaki kamuya açık bilinen tesis (üs, liman, radar) |
| `equipment` | `eqp_` | Platform/silah **tipi** (F-16, S-300...) |
| `source` | `src_` | Kaynak sicili: güvenilirlik (A–F) ve kullanım şartları |
| `tombstone` | (any) | Geri çekilmiş kayıt — ID ve dosya kalır, içerik silinir |

## Hızlı başlangıç / Quick start

```bash
pip install -r requirements.txt
python tools/gt.py new event        # data/events/2026/09/evt_....yaml oluşturur / creates a skeleton
python tools/gt.py fmt              # kanonik sıra + referans etiketleri / canonical order + "# label" on references
python tools/gt.py validate         # tüm kontroller / all checks
python tools/gt.py build            # dist/: *.jsonl, events.csv, events.geojson, vocab.json
pip install pytest && python -m pytest tests   # araç testleri / tooling tests
```

CI bunların hepsini çalıştırır; `fmt --check` biçimsiz kaydı reddeder. / CI runs all of these; `fmt --check` rejects unformatted records.

Katkı adımları için / how to contribute: [CONTRIBUTING.md](CONTRIBUTING.md).

## Temel kurallar / Core rules

- **ID'ler kalıcıdır** — değişmez, silinmez, taşınmaz; dosya yolu ID'den türetilir. / IDs are permanent; the path is derived from the ID.
- **Tartışmalı nitelendirmeler atfedilir** (`claims[]`): "X'e göre ihlal". Proje egemenlik iddia etmez; `countries` = olaya karışan devletler. / Contested labels are attributed, never asserted.
- **Doğrulama ölçeği** / Verification: Admiralty — kaynak güvenilirliği A–F, bilgi doğruluğu 1–6, `assessment.status`.
- `verified` için: doğruluk ≤ 2, İngilizce metin, her kaynağa arşiv linki, 2 bağımsız kaynak **veya** geolocation/chronolocation/uydu.
- Kişisel veri, gizlilik dereceli belge, URL kısaltıcı → CI reddeder. / Personal data, classified markings, URL shorteners → rejected by CI.
- **Coğrafi çit:** Türkiye kara toprakları, iç suları veya kıyıdan 12 deniz mili içindeki koordinat → Türk kuvvetleri kapısı (koordinat kaldırılır). / **Geofence:** coordinates on Türkiye's land, internal waters or within 12 nm of its coast trigger the Turkish forces gate. Ayrıntı / details: [tools/data/README.md](tools/data/README.md).

## Sürümler / Releases

Veri sürümleri CalVer: `v2026.09.0`. Her etikette `dist/` dosyaları GitHub Release'e eklenir. Şema sürümü kayıtlardaki `schema: event/1` alanıdır; kırıcı değişiklik → `schemas/v2/` + migration.

## Lisans / License

Veri, sözlük ve şemalar **CC BY 4.0** ([LICENSE-DATA](LICENSE-DATA)); kod **MIT** ([LICENSE](LICENSE)). Atıf: [CITATION.cff](CITATION.cff).
Üçüncü taraf içerik kopyalanmaz: kayıtlar kendi özetimiz + bağlantı + arşivden oluşur.
