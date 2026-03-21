# serialhavale.com - İş Kuralları ve Operasyonel Bilgi

## Temel Tanım
serialhavale.com bir **bahis altyapısı ödeme sağlayıcısıdır**. Bahis oynayacak müşterinin banka hesabıyla havale ile yatırım yapmasını sağlar. Bu yatırımları süzgeçten geçiren CRM altyapısıdır.

## İşlem Hayat Döngüsü (Payment Transaction Lifecycle)

### Yatırım (payment_type=1) — 2.3M+ işlem:
1. Bahis sitesi, müşteri adına yatırım talebi oluşturur (API) → payment_transactions kaydı (status=0, beklemede)
2. Müşteri gateway01.co'ya yönlendirilir → **IBAN + tutar gösterilir**
3. Müşteri kendi mobil bankasından belirtilen banka hesabına havale yapar
4. serialhavale.com panelindeki operatör (ekip üyesi) kendi bankasına bakarak paranın gelip gelmediğini kontrol eder
5. Para geldiyse → **ONAY** (status=1). Miktar farklıysa miktar değiştirilerek onaylanır
6. Para gelmediyse veya reject_time dolduysa → **RED** (status=3)
7. Onay/red → site'ye callback_url üzerinden bildirim gider (Laravel Queue)
8. Onaylar rapora yazılır (ekip bazlı, site bazlı)

### Çekim (payment_type=0) — 144K+ işlem:
1. Bahis sitesi, müşteri adına çekim talebi oluşturur → payment_transactions kaydı (status=0)
2. Onaylanan çekimin karşısında müşterinin IBAN'ı çıkar
3. Ekip, fiziksel telefonlardan (set) banka hesabından müşteriye parayı gönderir
4. Onay basılır → site'ye callback gider
5. **NOT: Çekim sayısı az çünkü genelde tether (kripto) teslimat tercih edilir**
6. **NOT: Çekim işlemlerinde komisyon ALINMAZ**

### Yatırım Gruba Atama Mantığı
- Siteye bağlı birden fazla grup var (site_group pivot)
- **Sıralı ve koşullu dağılım** ile gruplara atanır
- Koşullar: Ekibin yatırım durumunun açık olması, açık hesap olması, limitlere uygunluk
- Amaç: yükü dağıtmak, tek ekibin tüm yatırımı kaldıramaması

## Otomatik Onay (Auto Approve)
- Bazı ekipler otomatik onay botuna bağlı
- Bot phone_sms_log'daki SMS'leri gerçek işlemlerle eşleştirir
- **Eşleştirme kriteri: İSİM + TUTAR** (SMS'teki alıcı ismi + tutar ↔ işlemdeki isim + amount)
- Eşleşme varsa → otomatik onay (payment_transactions_state'te "auto approved" kaydı)
- Eşleşme yoksa → manuel kontrol bekler
- SMS eşleşme oranı: ~%75
- **uyuşmayansms** Telegram botu: Botun eşleştiremediği (isim veya miktar tutmuyor) işlemleri yakalar ve ekibe bildirir

## Otomatik Red (Auto Reject)
- Her sitenin reject_time süresi var (dakika cinsinden)
- **Real-time timer ile tetiklenir** (cron/scheduler değil)
- Süre dolunca → status=3 + "System Declined" action kaydı + RED callback
- Global default: AUTO_REJECTED_TIME=30 dakika (settings tablosu)

## Callback Sistemi
- Callback onay VE red durumunda tetiklenir
- **Laravel Queue** üzerinden dispatch edilir (jobs tablosu)
- callback_url: Site'nin belirlediği endpoint
- Her site'nin provider_type'ına göre callback formatı farklı olabilir

### Callback Retry Mekanizması
1. İlk gönderim başarısız → callback_retry_count=1
2. Max 5 retry denenir
3. 5. denemede hâlâ başarısızsa → callback_is_dead_letter=true
4. failed_jobs tablosuna da düşer

### KRİTİK SORUN: Dead Letter Callbacks
- **14,593+ callback_status=false** (toplam başarısız)
- **15,888 callback_is_dead_letter=true** (dead letter olarak işaretlenmiş)
- **2,374 ONAY + dead letter** = Site parasının geldiğini BİLMİYOR (en kritik!)
- **11,891 RED + dead letter** = Site red durumunu bilmiyor
- **328 BEKLEYEN + dead letter** = İşlem arafta
- En eski dead letter: 2025-12-12
- En sık hatalar:
  - 400 Bad Request: letspayments.com endpoint'leri (~12,800)
  - Connection refused: mani.pronetgaming.eu (~760) — BUGÜN AKTİF SORUN
  - 500 Internal Server Error: mani.pronetgaming.eu (~85)
- Panelde **"callback tekrar gönder" butonu VAR** (manuel recovery)
- Eksik: Otomatik dead letter uyarı sistemi (bu SERIAI'nin çözmesi gereken sorun)

## Roller ve Yetkiler (Spatie Permission Pattern)

### Administrator (10 kullanıcı)
- Her şeyi yapabilir: kullanıcı yönetimi, site ekleme, yetki verme, raporlar, onay/red, komisyon değiştirme

### Provider (4 kullanıcı)
- Sağlayıcı rolü. Pratikte admin gibi her şeyi yapabilir
- Kontrol yapar, dahil olmaz genelde

### User — Ekip Kullanıcısı (100 kullanıcı)
- Normal ekip operatörü
- Banka hesabı ekleyebilir/silebilir, onay/red verebilir
- Other transaction kullanabilir
- Ana sayfa ve rapor görüntüleyebilir

### Site User — Site Kullanıcısı (47 kullanıcı)
- SADECE kontrol amaçlı: raporlara, yatırımlara, çekimlere bakabilir
- Onay/red veremez, hiçbir şey yapamaz
- Panel: panelsh.com

### Personal — Ortacı/Aracı (50 kullanıcı)
- Ekipler ve siteler arasındaki iletişim köprüsü
- Sorun çözücü: "Bu işlem neden gecikti?", "Şu işlem onaylandı mı?"
- Ekibe ulaşır, siteye ulaşır, bilgi aktarır

## Telegram Bot Ekosistemi

### 1. banka_limit
- Banka hesaplarının alt limitlerini kontrol eder
- Eğer site minimum yatırımı (ör: 500 TL) kapsayan banka hesabı yoksa (en düşük alt limit 1000 TL ise) → personal'a uyarı atar
- "500-999 TL aralığında limit yok, ekiplere alt limitlerini düşürsünler"

### 2. bekleyen_cekim
- **10 dakikada bir** ortak bildirim kanalına mesaj atar
- Kaç tane bekleyen çekim var + en eski çekimin yaşı
- Ekipler görsün, eski çekimleri unutmasınlar diye

### 3. imha
- Ekiplerin ve sitelerin kaç tane bekleyen yatırımı olduğuna bakar
- **Riskli seviyenin üstündeyse** özel formatta uyarı mesajları atar

### 4. simplesorgu (tüm site telegramlarına ekli)
- `/sorgu [transaction_id]` — İşlemin bizdeki durumunu gösterir
- `/alici [İSİM]` — bank_accounts tablosunda bu isimde hesap var mı kontrol eder
- `/iban [IBAN]` — IBAN ile sorgulama yapar
- Site çalışanları personal'dan cevap beklemek yerine doğrudan sorgulama yapabilir

### 5. uyuşmayansms
- **ÖNEMLİ:** Auto-approve botunun eşleştiremediği işlemleri yakalar
- İsim veya miktar tutmuyorsa bildirim atar
- "Bu işlem şununla eşleşti ama miktar X TL farklı" şeklinde detaylı bildirir
- Ekip bu bilgiyle manuel onay verir

## Gruplar (Saha Ekipleri)
- Sitelerden gelen yatırımların round-robin dağıtıldığı saha ekipleri
- Her grubun kendi banka hesapları var (fiziksel telefonlarla yönetilir)
- Bir siteye birden fazla ekip eklenir ki yatırımlar dağıtılsın
- Hesaplar kolay bloke olduğundan mümkün olduğunca fazla ekip zorunlu
- site_group pivot tablosu ile eşleştirilir
- Grup komisyonu: %1-%3 arası (çoğunluk %2.25)

## Bank Account Lifecycle
- status: 0=Pasif, 1=Aktif, 2=Bloke
- **Bloke MANUEL yapılır** (operatör/admin — banka hesabı gerçekten bloke olmuştur veya risk var)
- deposit_status, withdrawal_status → **status'tan BAĞIMSIZ**
  - Aktif hesap (status=1) ama deposit_status=false olabilir (sadece çekim yapar)
- Bloke'dan geri dönüş: Admin tarafından tekrar aktif edilebilir

## Limit Hiyerarşisi
1. **Site limiti ÖNCELİKLİ** (ör: min 500 TL, max 100K TL)
2. Banka hesabı limiti (low_limit, high_limit, daily_total_process_limit)
3. Grup limiti (çoğunluk: low=100 TL, high=100K TL. 10 grup: 0/0 = limitsiz. Bazıları özel üst limitli: 3999, 4999, 5999, 8999)
- daily_total_process: Gece 12'de sıfırlanır (mekanizma belirsiz — muhtemelen cron)
- 0 = limitsiz anlamına gelir

## Raporlama ve Muhasebe
- Panelde günlük/aylık rapor sayfası var
- Filtreleme ve **Excel export** mümkün
- Ekip bazlı performans takibi (hangi ekip kaç işlem onayladı)
- Net kasa = yatırım - çekim - komisyon - şirket_teslimat + şirket_takviye

## Fraud/Dolandırıcılık Tespiti
- **Otomatik fraud tespiti YOK** — EKSİKLİK
- DB'de fraud/blacklist/block tablosu yok
- Tamamen **operatör tecrübesine dayalı manuel göz** ile yapılıyor
- Potansiyel SERIAI geliştirmesi: Aynı IBAN'dan yoğun işlem, aynı TC farklı site tespiti

## Platform URL'leri
- serialhavale.com: Admin panel (operatörler, adminler, ekipler, ortacılar)
- gateway01.co: Müşterinin karşısına çıkan yatırım ekranı (IBAN + tutar gösterilir)
- panelsh.com: Site kullanıcılarına verilen panel (ülke erişim kısıtlaması nedeniyle ayrı domain)

## IP Whitelist
- API entegrasyonu olan siteler için geçerli
- ip_whitelist tablosu: site_id + ip (inet)
- Site 3 ve 4 ağırlıklı, tümü root tarafından oluşturulmuş

## Bakım Modu
- MAINTENANCE_MODE=0 (şu an kapalı)
- Açılınca site bakım modunda görünür

## SORGU ZEKASI — Varsayılan Filtreler ve Akıl Yürütme

Bu kurallar, kullanıcı spesifik belirtmese bile sorguya otomatik uygulanmalıdır.
Brain bu kuralları bilmezse yanlış/eksik sonuç döner.

### Varsayılan Filtreler (kullanıcı aksini belirtmedikçe HER ZAMAN uygula)

| Kullanıcı dediğinde | Gerçek anlamı | SQL filtresi |
|----------------------|---------------|--------------|
| "toplam yatırım" / "yatırım tutarı" | Sadece ONAYLANMIŞ yatırımlar | `payment_type=1 AND status=1` |
| "toplam çekim" / "çekim tutarı" | Sadece ONAYLANMIŞ çekimler | `payment_type=0 AND status=1` |
| "bekleyen işlem" / "bekleyenler" | Henüz karar verilmemiş | `status=0` |
| "reddedilen" / "red" | Reddedilmiş işlemler | `status=3` |
| "bugünkü" / "bugün" | Bugünün tarihi (Türkiye saati) | `created_at >= CURRENT_DATE` |
| "bu hafta" | Pazartesiden bugüne | `created_at >= date_trunc('week', CURRENT_DATE)` |
| "bu ay" | Ayın 1'inden bugüne | `created_at >= date_trunc('month', CURRENT_DATE)` |
| "son 1 saat" | Son 60 dakika | `created_at >= NOW() - INTERVAL '1 hour'` |
| "aktif hesap" | Durumu aktif olan | `bank_accounts.status=1` |
| "aktif site" | Durumu aktif olan | `sites.status=true` (boolean!) |
| "aktif grup" | Durumu aktif olan | `groups.status=1` |

### KRİTİK: status=2 KULLANILMIYOR
payment_transactions'da status=2 değeri hiç yoktur. Sorguda ASLA kullanma:
- 0 = Beklemede
- 1 = Onaylandı
- 3 = Reddedildi (2 atlanmış, 3'e geçilmiş)

### KRİTİK: Tip Farkları
- `payment_transactions.status` → **smallint** (0, 1, 3)
- `sites.status` → **boolean** (true/false) — integer DEĞİL!
- `callback_status` → **boolean** (true/false) — integer DEĞİL!
- `bank_accounts.status` → **smallint** (0, 1, 2)

### Yaygın Sorular ve Doğru SQL Mantığı

**"Bugün toplam ne kadar yatırım geldi?"**
```sql
SELECT COALESCE(SUM(amount), 0) FROM payment_transactions
WHERE payment_type = 1 AND status = 1 AND created_at >= CURRENT_DATE
```
↑ status=1 ŞART. Yoksa bekleyen ve reddedilenler de dahil olur — YANLIŞ sonuç.

**"Bugün kaç işlem onaylandı?"**
```sql
SELECT COUNT(*) FROM payment_transactions
WHERE status = 1 AND created_at >= CURRENT_DATE
```

**"Bekleyen yatırım var mı?"**
```sql
SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM payment_transactions
WHERE payment_type = 1 AND status = 0
```

**"Şu sitenin bugünkü cirosu?"**
```sql
SELECT COALESCE(SUM(amount), 0) FROM payment_transactions
WHERE site_id = ? AND payment_type = 1 AND status = 1 AND created_at >= CURRENT_DATE
```

**"Dead letter sayısı?"**
```sql
SELECT COUNT(*) FROM payment_transactions
WHERE callback_is_dead_letter = true
```

**"Callback başarısız olan onaylı işlemler?"**
```sql
SELECT COUNT(*) FROM payment_transactions
WHERE status = 1 AND callback_status = false
```

**"Hangi banka hesabında en çok para var?"**
```sql
SELECT ba.id, ba.name, ba.surname, b.name as bank_name, ba.balance
FROM bank_accounts ba JOIN banks b ON ba.bank_id = b.id
WHERE ba.status = 1
ORDER BY ba.balance DESC LIMIT 10
```

**"Site komisyon karşılaştırması?"**
```sql
SELECT name, commission FROM sites WHERE status = true ORDER BY commission DESC
```
↑ sites.status = true (boolean), integer değil!

**"Ekip performansı — bugün en çok kimin grubu onaylamış?"**
```sql
SELECT g.name, COUNT(*) as islem_sayisi, SUM(pt.amount) as toplam
FROM payment_transactions pt
JOIN groups g ON pt.group_id = g.id
WHERE pt.status = 1 AND pt.created_at >= CURRENT_DATE
GROUP BY g.id, g.name ORDER BY toplam DESC
```

### Akıl Yürütme Kuralları

1. **"Toplam" dediğinde = onaylı demek.** Kimse reddedilen işlemi toplama dahil etmez. Kullanıcı "tüm işlemler dahil" veya "bekleyenlerle birlikte" demezse → status=1
2. **"Ciro" = onaylı yatırım toplamı.** Ciro hiçbir zaman çekim içermez.
3. **"Kâr" veya "kazanç" = toplam komisyon.** `SUM(provider_fee)` veya site bazlı `SUM(amount - company_amount)`
4. **"Net kasa" = yatırım - çekim - komisyon.** `SUM(company_amount)` zaten bunu verir (sadece yatırımlarda dolu).
5. **Deleted_at kontrolü:** Soft delete var. `deleted_at IS NULL` ekle — aksi belirtilmedikçe silinen kayıtları dahil etme.
6. **Tarih yoksa "bugün" varsay.** Kullanıcı "toplam yatırım ne kadar" derse bugünü kastetir, tüm zamanları değil.
7. **"Kaç tane" = COUNT, "ne kadar" = SUM(amount).** Ayrımı doğru yap.
8. **LIMIT koy.** Liste sorgularında LIMIT 20-50 koy, 2.4M satır döndürme.

## Manuel Onay ve Red→Onay Süreci

### Manuel Onay Nedir?
Bazı ekipler gelen yatırımı zamanında onaylamıyor. reject_time dolunca işlem otomatik reddediliyor ("System Declined"). Sonradan site sorgu yaptığında "bu yatırım geldi mi?" deniyor. Operasyon çalışanları ekiplere ulaşıp kontrol ediyor. Para gerçekten gelmişse, reddedilmiş işlem tekrar onaya çekiliyor.

### DB'de Manuel Onay Nasıl Görünür?
payment_transactions_state tablosunda şu action geçişleri oluşur:
1. `"Provider upon request."` (status=0) → İşlem oluşturuldu
2. `"System Declined"` (status=3) → Otomatik red (reject_time doldu)
3. `"Yatırımın durumu onaylandı olarak düzenlendi."` (status=1) → **MANUEL ONAY**

Yani **"Yatırımın durumu onaylandı olarak düzenlendi."** = manuel onay action string'i.

### Manuel Yatırım Ekleme
`"Manuel Yatırım Eklendi."` (status=0) → Sıfırdan elle eklenen işlem. Callback'siz olabilir.

### Audit Trail — Kim Yaptı?
`payment_transactions.created_by` → **JSON alanı**, içeriği:
```json
{"id": 42, "name": "Mia", "email": "mia@serial.com", "ip": "127.0.0.1", ...}
```
- `created_by->>'name'` → İşlemi yapan kişinin adı
- `created_by->>'email'` → @serial.com e-posta adresi
- `created_by_id` → users tablosuna FK (bigint)
- `updated_by` → JSON (genelde NULL, Laravel güncellemiyor)

**Manuel onayı KİM yaptı?** → `payment_transactions_state` tablosunda state kayıtlarında `created_by` NULL olabiliyor (Laravel Queue'dan yazıldığı için). Ama `payment_transactions` tablosundaki `created_by` JSON her zaman dolu — son güncellemeyi yapan kişiyi gösterir.

```sql
-- Manuel onayı kim yaptı? (son 7 gün)
SELECT pt.created_by->>'name' as yapan, pt.created_by->>'email' as email, COUNT(*) as cnt
FROM payment_transactions pt
WHERE pt.status = 1 AND pt.created_at >= CURRENT_DATE - INTERVAL '7 days'
AND EXISTS (SELECT 1 FROM payment_transactions_state pts WHERE pts.transaction_id = pt.id AND pts.status = 3)
GROUP BY pt.created_by->>'name', pt.created_by->>'email'
ORDER BY cnt DESC
```

### Diğer Action String'leri
- `"Yatırımın tutarı onaylandı."` → Normal onay (tutarla)
- `"Yatırımın reddedildi."` → Manuel red (operatör)
- `"Yatırımın tutarı güncellendi."` → Tutar değişikliği
- `"Yatırımın banka hesabı güncellendi."` → Hesap değişikliği
- `"Yatırımın grubu güncellendi."` → Grup değişikliği
- `"Yatırımın durumu iptal olarak düzenlendi."` → İptal
- `"auto approved"` → Otomatik onay (SMS eşleşmesi)
- `"Amount set."` → Tutar ayarlandı
- `"Professional Code Approved."` → Referans kodu onayı

### Manuel Onay Sorgu Örneği
```sql
-- Son 7 günde red→onay (manuel onay) sayısı
SELECT COUNT(DISTINCT pt.id)
FROM payment_transactions pt
WHERE pt.status = 1 AND pt.created_at >= CURRENT_DATE - INTERVAL '7 days'
AND EXISTS (
    SELECT 1 FROM payment_transactions_state pts
    WHERE pts.transaction_id = pt.id AND pts.status = 3
)
```

### Sorunlu Ekip Tespiti
Manuel onay oranı yüksek ekipler = işleyişi kötü etkileyen ekipler.
```sql
-- Ekip bazlı: red sayısı, manuel onay sayısı, red oranı
SELECT g.name,
    COUNT(*) FILTER (WHERE pt.status=1) as onayli,
    COUNT(*) FILTER (WHERE pt.status=3) as red,
    ROUND(COUNT(*) FILTER (WHERE pt.status=3)::numeric / NULLIF(COUNT(*),0) * 100, 1) as red_oran,
    (SELECT COUNT(DISTINCT pt2.id) FROM payment_transactions pt2
     WHERE pt2.group_id = g.id AND pt2.status = 1 AND pt2.created_at >= CURRENT_DATE - INTERVAL '7 days'
     AND EXISTS (SELECT 1 FROM payment_transactions_state pts WHERE pts.transaction_id = pt2.id AND pts.status = 3)
    ) as manuel_onay
FROM payment_transactions pt
JOIN groups g ON pt.group_id = g.id
WHERE pt.payment_type = 1 AND pt.created_at >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY g.id, g.name
ORDER BY red DESC
```

### Normal Kıyaslama Değerleri
- Platform geneli günlük red oranı: **~%28-30** (bu normal)
- Ekip bazlı red oranı %40+ = sorunlu (ortalamanın çok üstü)
- Manuel onay oranı %5+ = dikkat çekici, %10+ = sorunlu
- Gün: Gece 00:00 → Gece 00:00 (24 saat, sıfırlama gece 12)
- Çalışma saatleri: Ekibe göre değişken, sabit vardiya yok

## Muhasebe ve Finansal Hesaplamalar

### Temel Muhasebe Formülleri

**Günlük Ciro (Brüt):**
```sql
SELECT COALESCE(SUM(amount), 0) FROM payment_transactions
WHERE payment_type = 1 AND status = 1 AND created_at >= CURRENT_DATE
```

**Günlük Çekim Toplamı:**
```sql
SELECT COALESCE(SUM(amount), 0) FROM payment_transactions
WHERE payment_type = 0 AND status = 1 AND created_at >= CURRENT_DATE
```

**Net Kasa (company_amount):**
```
Net Kasa = Toplam Onaylı Yatırım - Toplam Komisyon - Çekim - Şirket Teslimat + Şirket Takviye
```
`company_amount` kolonu bunu otomatik hesaplar (sadece yatırımlarda dolu):
```sql
SELECT COALESCE(SUM(company_amount), 0) as net_kasa
FROM payment_transactions
WHERE payment_type = 1 AND status = 1 AND created_at >= CURRENT_DATE
```

**Toplam Komisyon Geliri:**
```sql
-- Provider komisyonu (serialhavale'nin kârı)
SELECT COALESCE(SUM(provider_fee), 0) FROM payment_transactions
WHERE payment_type = 1 AND status = 1 AND created_at >= CURRENT_DATE

-- Grup komisyonu (ekiplerin payı)
SELECT COALESCE(SUM(group_fee), 0) FROM payment_transactions
WHERE payment_type = 1 AND status = 1 AND created_at >= CURRENT_DATE
```

**Komisyon Formülü Detay:**
```
Yatırım: 100.000 TL
Site komisyonu: %5 (site.commission alanından)
Toplam komisyon: 100.000 × 0.05 = 5.000 TL
Grup payı (group_fee): grup.commission × yatırım → ör: %3 = 3.000 TL
Provider payı (provider_fee): toplam komisyon - grup payı = 2.000 TL
Site alacağı (company_amount): yatırım - toplam komisyon = 95.000 TL
ÇEKİMDE KOMİSYON YOK! provider_fee ve group_fee = 0
```

### other_transactions Muhasebe Kalemleri
| Tip | Kod | Açıklama | Kasa Etkisi |
|-----|-----|----------|-------------|
| Finans takviye | 0 | Ekibin çekimlere yetişmek için eklediği ek para | Kasayı ARTTIRIR |
| Finans takviye düş | 1 | Takviyeyi raporu bozmadan geri alma | Kasayı AZALTIR |
| Şirket takviye | 2 | Sitenin çekim çıkışı için gönderdiği para | Kasayı ARTTIRIR |
| Şirket teslimat | 3 | Kripto teslimat (raporda çekim gibi düşer) | Kasayı AZALTIR |
| Grup komisyon çekimi | 4 | Ekibin hakediş çekimi (group_fee) | Kasayı AZALTIR |
| Provider komisyon çekimi | 5 | Provider hakediş çekimi (provider_fee) | Kasayı AZALTIR |
| Bankalar arası transfer | 6 | Hesaplar arası para aktarma | NET ETKİSİ YOK (birinden düşer, diğerine eklenir) |

**Tümü revert edilebilir** (is_reverted=true → bakiye otomatik geri döner).

### Tam Kasa Hesaplama (Muhasebe Formülü)
```sql
SELECT
    -- Yatırım cirosu (brüt)
    COALESCE(SUM(CASE WHEN payment_type=1 AND status=1 THEN amount END), 0) as brut_yatirim,
    -- Çekim toplamı
    COALESCE(SUM(CASE WHEN payment_type=0 AND status=1 THEN amount END), 0) as toplam_cekim,
    -- Komisyon toplamı
    COALESCE(SUM(CASE WHEN payment_type=1 AND status=1 THEN provider_fee + group_fee END), 0) as toplam_komisyon,
    -- Net kasa (company_amount zaten bunu verir)
    COALESCE(SUM(CASE WHEN payment_type=1 AND status=1 THEN company_amount END), 0) as net_kasa
FROM payment_transactions
WHERE created_at >= CURRENT_DATE AND deleted_at IS NULL
```

### Banka Hesabı Bakiye Kontrolü
```sql
-- Toplam aktif hesap bakiyesi
SELECT SUM(balance) as toplam_bakiye, COUNT(*) as hesap_sayisi
FROM bank_accounts WHERE status = 1

-- Ekip bazlı bakiye
SELECT g.name, SUM(ba.balance) as bakiye, COUNT(ba.id) as hesap
FROM bank_accounts ba
JOIN groups g ON ba.group_id = g.id
WHERE ba.status = 1
GROUP BY g.id, g.name ORDER BY bakiye DESC
```

## Muhasebe Sorun Giderme ve Fark Analizi

### Panel vs Excel Farkı
- **Excel en doğru kaynak kabul edilir** — muhasebeciler Excel'e güvenir
- Panel bazen eksik kayıt gösterebilir (girilmeyi unutulan işlemler, teknik hatalar)
- Farklar eninde sonunda teknik ekip tarafından düzeltilir ama bazı köklü bozukluklar keşfedilmeden kalabiliyor
- MAZLUM'un rolü: DB'den gerçek rakamları hesaplayıp Excel/Panel ile karşılaştırma yapabilmek

### Net Kasa Farkı Çıktığında MAZLUM Ne Yapmalı?
1. DB'den doğru net kasayı hesapla (company_amount toplamı)
2. Fark varsa olası nedenleri kontrol et:

```sql
-- 1. Callback'siz onaylı işlemler (site bilmiyordur)
SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payment_transactions
WHERE status = 1 AND callback_status = false AND site_id = ?
AND created_at BETWEEN ? AND ?

-- 2. Revert edilmiş other_transactions
SELECT * FROM other_transactions
WHERE is_reverted = true AND created_at BETWEEN ? AND ?

-- 3. Manuel yatırımlar (callback'siz olabilir)
SELECT COUNT(*), SUM(amount) FROM payment_transactions pt
WHERE pt.status = 1 AND site_id = ?
AND EXISTS (
    SELECT 1 FROM payment_transactions_state pts
    WHERE pts.transaction_id = pt.id AND pts.action = 'Manuel Yatırım Eklendi.'
)

-- 4. Şirket teslimat toplamı (USDT olarak gönderilen)
SELECT COALESCE(SUM(amount),0) FROM other_transactions
WHERE transaction_process_type = 3 AND is_reverted = false
AND created_at BETWEEN ? AND ?

-- 5. Takviye giriş/çıkışları
SELECT
    SUM(CASE WHEN transaction_process_type=0 THEN amount ELSE 0 END) as finans_takviye,
    SUM(CASE WHEN transaction_process_type=1 THEN amount ELSE 0 END) as takviye_dus,
    SUM(CASE WHEN transaction_process_type=2 THEN amount ELSE 0 END) as sirket_takviye
FROM other_transactions WHERE is_reverted = false AND created_at BETWEEN ? AND ?
```

3. Bulunan farkları listele ve muhtemel nedeni açıkla
4. Bulamazsa → "Banka komisyonu, EFT gecikmesi veya sistem dışı manuel işlem olabilir" diye yönlendir

### Site Mutabakatı (Reconciliation)
- Site alacağı = `SUM(company_amount)` (onaylı yatırımlardan)
- Site borcu = Çekim toplamı + Şirket teslimatı (USDT gönderimi)
- **Teslimat genelde USDT (Tether) ile yapılır** — kripto olarak gönderilir, txid karşıya atılır
- Bazen nakit teslimat da yapılır (daha karmaşık)
- other_transactions tip=3 (şirket teslimat) = raporda çekim gibi düşer

```sql
-- Site mutabakat özeti
SELECT s.name,
    COALESCE(SUM(CASE WHEN pt.payment_type=1 AND pt.status=1 THEN pt.company_amount END), 0) as site_alacak,
    COALESCE(SUM(CASE WHEN pt.payment_type=0 AND pt.status=1 THEN pt.amount END), 0) as cekim_toplam,
    COALESCE((SELECT SUM(ot.amount) FROM other_transactions ot
        WHERE ot.transaction_process_type=3 AND ot.is_reverted=false
        AND ot.created_at BETWEEN ? AND ?), 0) as usdt_teslimat
FROM payment_transactions pt
JOIN sites s ON pt.site_id = s.id
WHERE pt.created_at BETWEEN ? AND ? AND pt.deleted_at IS NULL
GROUP BY s.id, s.name
ORDER BY site_alacak DESC
```

### Banka Bakiye Farkı ve Kayma Formülü
- **Sık oluyor** — sistemdeki balance ile teorik bakiye tutmayabiliyor
- Nedenler: EFT gecikmesi, banka komisyonu, manuel işlemler, hesap hataları
- Bu farkı tespit etmek için aşağıdaki formül kullanılır:

**Terimler:**
- `t_yatirim` = Toplam onaylı yatırım (SUM(amount) WHERE payment_type=1 AND status=1)
- `t_cekim` = Toplam onaylı çekim (SUM(amount) WHERE payment_type=0 AND status=1)
- `group_comission` = Grup komisyon oranı (groups.commission, ör: 0.0225)
- `site_comission` = Site komisyon oranı (sites.commission, ör: 0.05)
- `finans_takviye` = other_transactions tip=0 toplamı (revert edilmemiş)
- `cekilen_komisyon` = other_transactions tip=4 toplamı (grup hakediş çekimi)
- `p_cekilen_komisyon` = other_transactions tip=5 toplamı (provider hakediş çekimi)
- `sirket_takviye` = other_transactions tip=2 toplamı
- `yapilan_teslimat` = other_transactions tip=3 toplamı (USDT teslimat)
- `actual_balance` = bank_accounts.balance toplamı (aktif hesaplar)

**Hesaplanan Değerler (kayıtlı sütunlardan — oran değişse bile doğru kalır):**

KRİTİK: Komisyon oranları zamanla değişebilir. Bu yüzden `t_yatirim × oran` ile hesaplama YANLIŞ sonuç verir. Her işlemin kendi group_fee, provider_fee, company_amount değeri o anki oranla hesaplanmış ve kaydedilmiştir. HER ZAMAN kayıtlı sütun değerlerini kullan.

- `t_group_fee` = `SUM(group_fee)` WHERE payment_type=1 AND status=1
- `t_provider_fee` = `SUM(provider_fee)` WHERE payment_type=1 AND status=1
- `t_toplam_komisyon` = `t_group_fee + t_provider_fee`
- `t_company_amount` = `SUM(company_amount)` WHERE payment_type=1 AND status=1

1. **İçerde Kalan Komisyon** = `t_group_fee + finans_takviye − cekilen_komisyon`
2. **Provider Kalan Komisyon** = `t_provider_fee − p_cekilen_komisyon`
3. **Kalan Şirket Alacağı (KŞA)** = `t_company_amount − t_cekim + sirket_takviye − yapilan_teslimat`
4. **Hesapta Kalan (Teorik Bakiye)** = İçerde Kalan Komisyon + Provider Kalan Komisyon + KŞA

```
Teorik Bakiye =
    (t_group_fee + finans_takviye − cekilen_komisyon)
  + (t_provider_fee − p_cekilen_komisyon)
  + (t_company_amount − t_cekim + sirket_takviye − yapilan_teslimat)
```

5. **Fark (Kayma)** = `Teorik Bakiye − actual_balance`

Fark pozitifse → sistemde olması gerekenden az para var (kayıp/eksik)
Fark negatifse → sistemde olması gerekenden fazla para var (fazlalık)

```sql
-- Grup bazlı kayma analizi (kayıtlı sütunlardan — komisyon değişse bile doğru)
WITH grup_data AS (
    SELECT
        g.id as group_id, g.name,
        COALESCE(SUM(CASE WHEN pt.payment_type=1 AND pt.status=1 THEN pt.amount END), 0) as t_yatirim,
        COALESCE(SUM(CASE WHEN pt.payment_type=0 AND pt.status=1 THEN pt.amount END), 0) as t_cekim,
        COALESCE(SUM(CASE WHEN pt.payment_type=1 AND pt.status=1 THEN pt.group_fee END), 0) as t_group_fee,
        COALESCE(SUM(CASE WHEN pt.payment_type=1 AND pt.status=1 THEN pt.provider_fee END), 0) as t_provider_fee,
        COALESCE(SUM(CASE WHEN pt.payment_type=1 AND pt.status=1 THEN pt.company_amount END), 0) as t_company_amount
    FROM groups g
    LEFT JOIN payment_transactions pt ON pt.group_id = g.id AND pt.deleted_at IS NULL
    WHERE g.id = ?
    GROUP BY g.id, g.name
),
ot_data AS (
    SELECT
        COALESCE(SUM(CASE WHEN transaction_process_type=0 THEN amount END), 0) as finans_takviye,
        COALESCE(SUM(CASE WHEN transaction_process_type=4 THEN amount END), 0) as cekilen_komisyon,
        COALESCE(SUM(CASE WHEN transaction_process_type=5 THEN amount END), 0) as p_cekilen_komisyon,
        COALESCE(SUM(CASE WHEN transaction_process_type=2 THEN amount END), 0) as sirket_takviye,
        COALESCE(SUM(CASE WHEN transaction_process_type=3 THEN amount END), 0) as yapilan_teslimat
    FROM other_transactions
    WHERE is_reverted = false
    AND bank_account_id IN (SELECT id FROM bank_accounts WHERE group_id = ?)
),
bakiye AS (
    SELECT COALESCE(SUM(balance), 0) as actual_balance
    FROM bank_accounts WHERE group_id = ? AND status IN (0, 1, 2)
)
SELECT
    gd.name,
    gd.t_yatirim, gd.t_cekim,
    gd.t_group_fee, gd.t_provider_fee, gd.t_company_amount,
    (gd.t_group_fee + od.finans_takviye - od.cekilen_komisyon) as icerde_kalan_kom,
    (gd.t_provider_fee - od.p_cekilen_komisyon) as provider_kalan_kom,
    (gd.t_company_amount - gd.t_cekim + od.sirket_takviye - od.yapilan_teslimat) as ksa,
    (gd.t_group_fee + od.finans_takviye - od.cekilen_komisyon)
    + (gd.t_provider_fee - od.p_cekilen_komisyon)
    + (gd.t_company_amount - gd.t_cekim + od.sirket_takviye - od.yapilan_teslimat) as teorik_bakiye,
    b.actual_balance,
    (gd.t_group_fee + od.finans_takviye - od.cekilen_komisyon)
    + (gd.t_provider_fee - od.p_cekilen_komisyon)
    + (gd.t_company_amount - gd.t_cekim + od.sirket_takviye - od.yapilan_teslimat)
    - b.actual_balance as fark
FROM grup_data gd, ot_data od, bakiye b
```

### Ekip Hakediş Çekimi
- Periyot: ekibe göre değişken (günlük, haftalık, talep üzerine — sabit kural yok)
- other_transactions tip=4 ile kayıt altına alınır
- group_fee toplamı = ekibin hakediş alacağı
- Hakediş çekildikten sonra bakiyeden düşer

```sql
-- Ekibin birikmiş hakediş alacağı vs çekilmiş miktarı
SELECT g.name,
    COALESCE(SUM(pt.group_fee), 0) as toplam_hakedis,
    COALESCE((SELECT SUM(ot.amount) FROM other_transactions ot
        WHERE ot.transaction_process_type=4 AND ot.is_reverted=false
        AND ot.bank_account_id IN (SELECT id FROM bank_accounts WHERE group_id = g.id)
    ), 0) as cekilen_hakedis
FROM payment_transactions pt
JOIN groups g ON pt.group_id = g.id
WHERE pt.payment_type = 1 AND pt.status = 1
GROUP BY g.id, g.name
ORDER BY toplam_hakedis DESC
```

## Ekip Performans İstatistikleri ve Skorlama

### Panel İstatistik Sayfası Metrikleri
serialhavale.com panelinde İstatistikler sayfası var. Bu sayfadaki veriler DB'den şöyle üretilir:

```sql
-- Ekip istatistikleri (günlük veya tarih aralığına göre)
SELECT
    g.id, g.name,
    COUNT(*) as toplam_islem,
    COUNT(*) FILTER (WHERE pt.payment_type=1 AND pt.status=1) as basarili_yatirim,
    COUNT(*) FILTER (WHERE pt.payment_type=1 AND pt.status=3) as basarisiz_yatirim,
    COUNT(*) FILTER (WHERE pt.payment_type=0 AND pt.status=1) as basarili_cekim,
    COUNT(*) FILTER (WHERE pt.payment_type=0 AND pt.status=3) as basarisiz_cekim,
    COALESCE(SUM(CASE WHEN pt.payment_type=1 AND pt.status=1 THEN pt.amount END), 0) as yatirim_tutari,
    COALESCE(SUM(CASE WHEN pt.payment_type=0 AND pt.status=1 THEN pt.amount END), 0) as cekim_tutari,
    -- Ortalama onay süresi (saniye)
    ROUND(AVG(CASE WHEN pt.payment_type=1 AND pt.status=1
        THEN EXTRACT(EPOCH FROM (pt.updated_at - pt.created_at)) END)::numeric, 0) as ort_yatirim_sure_sn,
    ROUND(AVG(CASE WHEN pt.payment_type=0 AND pt.status=1
        THEN EXTRACT(EPOCH FROM (pt.updated_at - pt.created_at)) END)::numeric, 0) as ort_cekim_sure_sn,
    -- Başarı oranı
    ROUND(
        COUNT(*) FILTER (WHERE pt.payment_type=1 AND pt.status=1)::numeric /
        NULLIF(COUNT(*) FILTER (WHERE pt.payment_type=1), 0) * 100, 1
    ) as yatirim_basari_oran,
    -- Ortalama yatırım tutarı
    ROUND(AVG(CASE WHEN pt.payment_type=1 AND pt.status=1 THEN pt.amount END)::numeric, 0) as ort_yatirim_tutar
FROM payment_transactions pt
JOIN groups g ON pt.group_id = g.id
WHERE pt.created_at >= CURRENT_DATE AND pt.deleted_at IS NULL
GROUP BY g.id, g.name
ORDER BY basarili_yatirim DESC
```

### Ekip Performans Değerlendirme Kriterleri

**İYİ EKİP göstergeleri:**
- Yüksek yatırım hacmi (çok işlem alıyor = sistemi sırtlıyor)
- Yüksek başarı oranı (%85+ iyi, %90+ çok iyi)
- Düşük ortalama yatırım tutarı + yüksek adet = küçük işlemleri reddetmeden onaylıyor
- Yüksek çekim sayısı = çekim yükünü de taşıyor
- Düşük ortalama onay süresi = hızlı çalışıyor

**KÖTÜ EKİP göstergeleri:**
- Düşük başarı oranı (%80 altı = sorunlu)
- Yüksek ortalama tutar + düşük adet = sadece büyük işlemleri alıyor, küçükleri reddediyor (kolaya kaçma)
- Çekim sayısı sıfır veya çok düşük = çekim yükünü taşımıyor
- Yüksek ortalama onay süresi = yavaş çalışıyor, işlem bekletiyor
- Yüksek manuel onay oranı = zamanında onaylamıyor, sonradan düzeltiyor

**Yük Dağılımı (Sistem Sırtlama):**
```sql
-- Her ekibin toplam yükteki payı (%)
SELECT g.name,
    COUNT(*) FILTER (WHERE pt.payment_type=1 AND pt.status=1) as b_yatirim,
    ROUND(
        COUNT(*) FILTER (WHERE pt.payment_type=1 AND pt.status=1)::numeric /
        NULLIF((SELECT COUNT(*) FROM payment_transactions WHERE payment_type=1 AND status=1 AND created_at >= CURRENT_DATE), 0) * 100, 1
    ) as yuk_payi_yuzde
FROM payment_transactions pt
JOIN groups g ON pt.group_id = g.id
WHERE pt.created_at >= CURRENT_DATE AND pt.deleted_at IS NULL
GROUP BY g.id, g.name
ORDER BY b_yatirim DESC
```

**Kolaya Kaçan Ekip Tespiti:**
```sql
-- Ortalama tutarı yüksek AMA adet düşük ekipler (büyük işlem seçici)
SELECT g.name,
    COUNT(*) FILTER (WHERE pt.payment_type=1 AND pt.status=1) as adet,
    ROUND(AVG(CASE WHEN pt.payment_type=1 AND pt.status=1 THEN pt.amount END)::numeric, 0) as ort_tutar,
    COALESCE(SUM(CASE WHEN pt.payment_type=1 AND pt.status=1 THEN pt.amount END), 0) as toplam_tutar
FROM payment_transactions pt
JOIN groups g ON pt.group_id = g.id
WHERE pt.created_at >= CURRENT_DATE - INTERVAL '7 days' AND pt.deleted_at IS NULL
GROUP BY g.id, g.name
HAVING COUNT(*) FILTER (WHERE pt.payment_type=1) >= 50
ORDER BY ort_tutar DESC
```
Ort. tutarı çok yüksek ama adedi düşük olan ekipler = kolaya kaçıyor olabilir.

### Performans Skor Formülü
Ekipleri sıralamak için bileşik skor:
```
Skor = (başarı_oranı × 0.3) + (yük_payı × 0.25) + (çekim_katkısı × 0.2) + (hız_skoru × 0.15) + (düşük_tutar_bonusu × 0.1)
```
- başarı_oranı: %85 = 85 puan
- yük_payı: toplam yükteki yüzde (normalize 0-100)
- çekim_katkısı: çekim sayısı / toplam çekim × 100
- hız_skoru: 100 - (ort_süre_dakika × 10), min 0 (hızlı = yüksek puan)
- düşük_tutar_bonusu: ort_tutar platform ortalamasının altındaysa bonus (küçük işlem almak erdem)

### "En iyi ekip kim?" Sorusuna Cevap
Bu soruya tek metrikle cevap verilmez. Birden fazla boyutu değerlendir:
1. En çok yatırım onaylayan (hacim şampiyonu)
2. En yüksek başarı oranı (kalite şampiyonu)
3. En hızlı onay süresi (hız şampiyonu)
4. En çok çekim yapan (çekim yükünü taşıyan)
5. En düşük ortalama tutar ile en yüksek adet (emekçi)
Her birini ayrı göster, genel skor da ver.

## Önemli İş Kuralları Özeti
- Bahis altyapısı ödeme sağlayıcısı
- Çekim oranı düşüktür (tether teslimat tercih ediliyor)
- Komisyon SADECE yatırımda alınır
- Banka bakiyeleri otomatik güncellenir
- Site adlarındaki son harfler aracı kodları (rastgele, sistematik değil)
- Tüm siteler tek provider: ORBEYI (provider_id=2)
- Operatörler serialhavale.com + Telegram'ı yoğun kullanır
- Fraud tespiti manuel (otomatik yok — eksiklik)
- Dead letter callback = en kritik operasyonel sorun
