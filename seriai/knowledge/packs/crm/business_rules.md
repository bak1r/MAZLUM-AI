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
