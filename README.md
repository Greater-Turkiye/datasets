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
tools/issue_to_record.py   onaylı öneriyi taslak kayda çevirir / turns an approved proposal into a draft record
tools/data/     Türkiye coğrafi çiti (Natural Earth, kamu malı) / Türkiye geofence (Natural Earth, public domain)
tests/          araç birim testleri (pytest) / unit tests for the tooling
tests/fixtures/issues/     örnek issue gövdeleri / sample issue bodies used by the tests
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
python tools/gt.py build            # dist/: *.jsonl, events.csv, events.geojson, vocab.json, feed.xml, feed.json, feed.md
pip install pytest && python -m pytest tests   # araç testleri / tooling tests
```

CI bunların hepsini çalıştırır; `fmt --check` biçimsiz kaydı reddeder. / CI runs all of these; `fmt --check` rejects unformatted records.

Katkı adımları için / how to contribute: [CONTRIBUTING.md](CONTRIBUTING.md).

## Öneriden kayda / From proposal to record

Bir [veri önerisi](https://github.com/Greater-Turkiye/datasets/issues/new?template=01-data-submission.yml) issue'suna bir maintainer veya triyajcı **`kayda-gec`** etiketini eklediğinde, [`record-from-issue`](.github/workflows/record-from-issue.yml) iş akışı formu okur, `python tools/gt.py new event` ile bir kayıt üretir, `fmt` + `validate` çalıştırır ve **taslak bir pull request** açar; sonra issue'ya PR bağlantısını yorumlar. Etiket yalnızca "bu aday kayda değer" demektir; kaydın doğru olduğuna karar veren hâlâ PR'ı inceleyen insandır.

When a maintainer or triager applies the **`kayda-gec`** label to a data-submission issue, the
[`record-from-issue`](.github/workflows/record-from-issue.yml) workflow parses the form, creates a record with
`python tools/gt.py new event`, runs `fmt` and `validate`, opens a **draft pull request** and comments the link on
the issue. The label only says "this candidate is worth recording"; whether the record is *true* stays a human
decision made on that pull request.

| Form alanı / Form field | Kayıt / Record |
|---|---|
| issue başlığı / issue title (`[Veri / Data]` öneki atılır) | `title.tr` |
| Ne oldu? TR / EN | `summary.tr`, `summary.en` |
| Ne zaman? (UTC) | `time.start`; yalnız tarih → `precision: day`, saat de varsa → `minute`; `basis: reported` |
| Bölge / Region | `regions[0]` (`vocab/regions.yaml` kodu değilse reddedilir / rejected if not a vocabulary code) |
| Yer / Place | `location.place_name.tr` + `precision: locality`, `method: reported` — **koordinat asla üretilmez / coordinates are never generated** |
| Olay türü / Event type | `event_type`; serbest metin sözlüğe eşlenir, eşleşmezse `other` / mapped to the vocabulary, else `other` |
| Kaynak bağlantıları / Source URLs | `sources[].url` (+ `lang` alan adından tahmin / guessed from the domain) — en az bir kaynak zorunlu / at least one is required |
| Arşiv bağlantıları / Archive URLs | `sources[].archives[]`, yalnızca sayılar birebir eşleşirse / only when the counts line up one-to-one |
| Güven düzeyiniz / Your confidence | `assessment.credibility` — 4'ten iyisi asla / never better than 4 |
| Kırmızı çizgi kutuları / Red-line boxes | hepsi işaretli değilse reddedilir / all must be ticked |

`assessment.status` daima `unverified`'dır; `countries`, `actors`, `equipment`, `sites` ve `claims` bilerek boş bırakılır — kimin karıştığını bir makine çıkarsamaz. Provenans, dosyanın başındaki yorum bloğunda ve PR gövdesinde issue bağlantısı olarak durur. /
`assessment.status` is always `unverified`, and `countries`, `actors`, `equipment`, `sites` and `claims` are left
empty on purpose — a machine does not infer who was involved. Provenance is the comment header of the record file
and the issue link in the pull request.

**Kapı kapanırsa / When the gate closes.** `validate` kaydı reddederse (Türk kuvvetleri kapısı, kişisel veri,
gizlilik damgası, kaynaksızlık, işaretlenmemiş kırmızı çizgi kutusu) iş akışı **hiçbir şey açmaz**: dal yok, commit
yok, PR yok. Sebebi issue'ya yorumlar ve kırmızı biter; kontrol asla zayıflatılmaz. /
If `validate` rejects the record — TUR forces gate, personal data, classification markers, no source, an unticked
red-line box — the workflow opens **nothing**: no branch, no commit, no pull request. It comments why on the issue
and fails the run. The gate is never stripped to make a record pass.

**PR açılamazsa / When the pull request cannot be opened.** Bu kuruluş şu anda Actions'ın pull request açmasına izin
vermiyor. Kayıt yine üretilir, `fmt` + `validate` yine çalışır ve dal yine itilir; `gh pr create` başarısız olursa
iş akışı **yeşil biter** ve issue'ya tek tıklık bir bağlantı (`…/compare/main...record/issue-<n>?expand=1`), inceleme
kontrol listesi ve PR açıklaması için hazır gövde yorumlanır — koşu günlüğünde bir `::warning::` ile. /
This organisation currently does not allow Actions to open pull requests. The record is still generated, `fmt` and
`validate` still run and the branch is still pushed; if `gh pr create` fails, the workflow **ends green** and
comments a one-click `…/compare/main...record/issue-<n>?expand=1` link, the reviewer checklist and a ready-made body
on the issue, with a `::warning::` in the run log. The fallback is chosen on `gh`'s exit status; its message is only
a hint used to word the warning.

Bunu tamamen otomatik hâle getirmek için / To make it fully automatic again, either:

- bir kuruluş sahibi şunu açar / an organisation owner enables **Organisation settings → Actions → General →
  Workflow permissions → "Allow GitHub Actions to create and approve pull requests"**; veya / or
- depoya `GT_BOT_TOKEN` sırrı eklenir (contents / pull-requests / issues yazma yetkisi olan bir makine hesabı) — bu
  aynı zamanda üretilen PR'da `validate` kontrolünü de kendiliğinden başlatır. / add a `GT_BOT_TOKEN` secret (a
  machine account with contents / pull-requests / issues write), which also makes the generated PR start the
  `validate` check on its own.

## Temel kurallar / Core rules

- **ID'ler kalıcıdır** — değişmez, silinmez, taşınmaz; dosya yolu ID'den türetilir. / IDs are permanent; the path is derived from the ID.
- **Tartışmalı nitelendirmeler atfedilir** (`claims[]`): "X'e göre ihlal". Proje egemenlik iddia etmez; `countries` = olaya karışan devletler. / Contested labels are attributed, never asserted.
- **Doğrulama ölçeği** / Verification: Admiralty — kaynak güvenilirliği A–F, bilgi doğruluğu 1–6, `assessment.status`.
- `verified` için: doğruluk ≤ 2, İngilizce metin, her kaynağa arşiv linki, 2 bağımsız kaynak **veya** geolocation/chronolocation/uydu.
- Kişisel veri, gizlilik dereceli belge, URL kısaltıcı → CI reddeder. / Personal data, classified markings, URL shorteners → rejected by CI.
- **Coğrafi çit:** Türkiye kara toprakları, iç suları veya kıyıdan 12 deniz mili içindeki koordinat → Türk kuvvetleri kapısı (koordinat kaldırılır). / **Geofence:** coordinates on Türkiye's land, internal waters or within 12 nm of its coast trigger the Turkish forces gate. Ayrıntı / details: [tools/data/README.md](tools/data/README.md).

## Akış / Feed

`build`, `dist/` içine **hesap gerektirmeyen** bir kamu akışı da yazar; `pages.yml` bunu GitHub Pages'e taşır.
`build` also writes a **public, account-free** feed into `dist/`; `pages.yml` publishes it to GitHub Pages.

| Dosya / File | Biçim / Format | İçerik / Contents |
|---|---|---|
| `feed.xml` | RSS 2.0 (+ `atom:self`) | Yayımlanan her kayıt: başlık, iki dilli özet, bölge, doğrulama durumu, kalıcı bağlantı, kaynak ve arşiv bağlantıları / every published record: title, bilingual summary, region, verification status, permalink, source and archive links |
| `feed.json` | JSON Feed 1.1 | Aynı maddeler; `_gt` uzantısı durumu, güvenilirliği, yöntemi, bölgeleri ve kaynakları makine okunur biçimde taşır / the same items; the `_gt` extension carries status, credibility, method, regions and sources machine-readably |
| `feed.md` | Markdown | Son 7 günün özeti — kanal gönderisine yapıştırmaya uygun / a digest of the last 7 days, ready to paste into a channel post |

Abonelik (birleştikten sonra) / Subscribing (once merged): akış okuyucunuza
`https://greater-turkiye.github.io/datasets/feed.xml` veya `.../feed.json` adresini ekleyin; dizin sayfası
`<link rel="alternate">` ile otomatik keşfi de destekler. Hesap, anahtar veya kayıt gerekmez.
Add `https://greater-turkiye.github.io/datasets/feed.xml` (or `.../feed.json`) to any reader; the index page
also advertises them with `<link rel="alternate">`. No account, key or sign-up.

Kurallar / Rules:

- **Yalnızca `data/` altındaki `verified` ve `partially_verified` olay kayıtları yayımlanır.** `examples/` kurgusaldır ve akışa hiç girmez; `unverified`, `disputed`, `false` ve geri çekilmiş kayıtlar da girmez. / Only `verified` and `partially_verified` event records from `data/` are published; fictional `examples/`, unverified, disputed, false and withdrawn records never appear.
- **Her madde doğrulama durumunu ve atfını taşır:** başlıkta `[DOĞRULANMIŞ / VERIFIED]` ya da `[KISMEN DOĞRULANMIŞ / PARTIALLY VERIFIED]`, gövdede Admiralty doğruluğu, değerlendirme notu, düzeltmeler, her kaynak ve arşivi, kayda kalıcı bağlantı. Hiçbir madde atıfsız gerçek gibi okunmaz. / Every item repeats its verification status and attribution, so nothing reads as an unattributed fact.
- **Zamanlar kayıttan türer, saatten değil:** yayın zamanı `reported_at`, yoksa `time.start`; güncelleme zamanı en son `corrections[].date`; kanal zamanı en yeni maddenin zamanı. Sıralama yeniden eskiye, eşitlikte ID'ye göredir ve özet penceresi en yeni maddeye sabitlenir — bu yüzden değişmeyen veriyi yeniden derlemek bayt bayt aynı dosyaları üretir. / All times come from the records (`reported_at`, else `time.start`; `corrections[].date`), never from the clock, and ordering is newest-first with the ID breaking ties, so a rebuild of unchanged data is byte-identical.
- Kalıcı bağlantı kaydın kendisidir: `https://github.com/Greater-Turkiye/datasets/blob/main/<kayıt yolu>`. / The permalink is the record itself.
- Akış yalnızca `dist/` içindekini yansıtır; metin kayıtlardan gelir, akış üretiminde hiçbir içerik yazılmaz. / The feed only reflects `dist/`; it never writes content of its own.

## Sürümler / Releases

Veri sürümleri CalVer: `v2026.09.0`. Her etikette `dist/` dosyaları GitHub Release'e eklenir. Şema sürümü kayıtlardaki `schema: event/1` alanıdır; kırıcı değişiklik → `schemas/v2/` + migration.

## Lisans / License

Veri, sözlük ve şemalar **CC BY 4.0** ([LICENSE-DATA](LICENSE-DATA)); kod **MIT** ([LICENSE](LICENSE)). Atıf: [CITATION.cff](CITATION.cff).
Üçüncü taraf içerik kopyalanmaz: kayıtlar kendi özetimiz + bağlantı + arşivden oluşur.

## Depo kurallari / Repository rules

Yapay zeka araclari ve yeni katkicilar icin kisa calisma kurallari: [CLAUDE.md](CLAUDE.md). Bu kurallarin ilki, her degisiklikte README dosyasini ayni PR icinde guncel tutmaktir.
Short working rules for AI agents and new contributors: [CLAUDE.md](CLAUDE.md). The first of them is keeping the README true in the same pull request as the change.
