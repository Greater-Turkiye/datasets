# Veriye katkı / Contributing data

Önce genel rehber: [Greater-Turkiye/.github CONTRIBUTING](https://github.com/Greater-Turkiye/.github/blob/main/CONTRIBUTING.md) ve **kırmızı çizgiler**.
Read the org-wide guide and the **red lines** first.

## Kod yazmadan / Without code

[Veri önerisi formu](https://github.com/Greater-Turkiye/datasets/issues/new?template=01-data-submission.yml) ile olay/kaynak önerin. Formu eksiksiz doldurun: zaman UTC, bölge açılır listeden, her satıra bir kaynak bağlantısı, mümkünse aynı sırada arşiv bağlantıları.
Open a data-submission issue and fill the form completely: UTC time, a region from the dropdown, one source URL per line and, if you can, archive links in the same order.

### Etiketten kayda / From label to record

1. Bir maintainer veya triyajcı öneriyi inceler ve uygun bulursa **`kayda-gec`** etiketini ekler. Bu etiket "bu aday kayda değer" demektir, "bu doğrudur" demek değildir.
2. [`record-from-issue`](.github/workflows/record-from-issue.yml) iş akışı formu ayrıştırır, `python tools/gt.py new event` ile kaydı üretir, `fmt` + `validate` çalıştırır ve **taslak bir pull request** açar; issue'ya PR bağlantısını yorumlar. Doğrudan `main`'e hiçbir şey yazılmaz.
3. Alan eşlemesi ve boş bırakılanlar: [README](README.md#öneriden-kayda--from-proposal-to-record). Kayıt `assessment.status: unverified` ile gelir ve `countries`, `actors`, `equipment`, `sites`, `claims` boştur.
4. İnceleyen kişi PR'daki kontrol listesini tamamlar: aktör/kaynak kayıtlarını bağlar, arşivleri tamamlar, `en` metinleri yazar, tartışmalı nitelendirmeleri `claims[]` altına atfeder ve ancak kendi kontrolünden sonra `assessment` değerlerini yükseltir.
5. `validate` reddederse (Türk kuvvetleri kapısı, kişisel veri, gizlilik damgası, kaynaksızlık, işaretlenmemiş kırmızı çizgi kutusu) hiçbir dal veya PR açılmaz; iş akışı sebebi issue'ya yorumlar ve kırmızı biter. Kontrol kaldırılmaz — öneri düzeltilip etiket yeniden uygulanır.

A maintainer or triager applies the **`kayda-gec`** label to an approved proposal; the `record-from-issue` workflow
then opens a draft pull request with an `unverified` record and comments the link on the issue. It never commits to
`main` and never marks anything verified. The reviewer of that pull request completes the checklist — actor and
source records, archives, English text, attributed `claims[]` — and only then raises `assessment`. If the policy
gate rejects the draft, nothing is opened at all and the workflow says why on the issue.

### PR'ı elle açmak / Opening the pull request by hand

Bu kuruluş şu anda GitHub Actions'ın pull request açmasına izin vermiyor. Böyle bir durumda iş akışı **yeşil biter**:
kayıt üretilir, `fmt` + `validate` geçer, dal itilir ve issue'ya şunları içeren bir yorum düşer — tek tıklık
`…/compare/main...record/issue-<n>?expand=1` bağlantısı, inceleme kontrol listesi ve PR açıklamasına yapıştırılacak
hazır gövde. Yapmanız gereken tek şey bağlantıya tıklayıp gövdeyi yapıştırmak; kayıt zaten kontrollerden geçmiştir.

This organisation currently does not allow Actions to open pull requests. When that happens the workflow **ends
green**: the record is generated, `fmt` and `validate` pass, the branch is pushed, and the issue gets a comment with
a one-click `…/compare/main...record/issue-<n>?expand=1` link, the reviewer checklist and a ready-made pull request
body. Click the link, paste the body, review as usual. The run log carries a `::warning::` explaining why. The
fallback is chosen on `gh pr create`'s exit status, not on its wording.

Kalıcı çözüm / To remove the fallback permanently, one of:

- **Organisation settings → Actions → General → Workflow permissions → "Allow GitHub Actions to create and approve
  pull requests"** (kuruluş sahibi gerekir / needs an organisation owner); veya / or
- depoya bir `GT_BOT_TOKEN` sırrı ekleyin / add a `GT_BOT_TOKEN` secret — contents / pull-requests / issues yazma
  yetkisi olan bir makine hesabı / a machine account with contents / pull-requests / issues write.

> Bir pull request iş akışı jetonuyla açıldığında `validate` kontrolü kendiliğinden başlamaz; incelerken bir commit
> itin veya PR'ı kapatıp yeniden açın ki zorunlu kontrol raporlansın. / A pull request opened with the workflow
> token does not start the `validate` check on its own: push a commit while reviewing, or close and reopen the PR,
> so the required check reports before merge.

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

`tools/` değişiklikleri için testler: `pip install pytest && python -m pytest tests`. Testler reponun geçici bir kopyasında çalışır, gerçek kayıtlara dokunmaz.
Yeni bir politika kuralı eklerseniz `tests/test_gt.py` içindeki `CASES` listesine reddedilen bir örnek ekleyin.
Issue formunu veya `tools/issue_to_record.py` eşlemesini değiştirirseniz `tests/fixtures/issues/` altına gerçekçi bir issue gövdesi ekleyin ve `tests/test_issue_to_record.py` içinde doğrulayın — kötü niyetli (kabuk metakarakterleri) ve politika kapısına takılan örnekler dahil.
Tests for `tools/` run on a temporary copy of the repo. When you add a policy rule, add a failing example to `CASES`.
When you change the issue form or the mapping in `tools/issue_to_record.py`, add a realistic issue body under
`tests/fixtures/issues/` and cover it in `tests/test_issue_to_record.py` — including a hostile one and one the
policy gate must reject.

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
