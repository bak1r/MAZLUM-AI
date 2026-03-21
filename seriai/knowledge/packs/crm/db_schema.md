# serialhavale.com - Database Schema

## Platform: Bahis Altyapısı Ödeme Sağlayıcısı
Bahis oynayacak müşterinin banka hesabıyla havale ile yatırım yapmasını sağlayan CRM altyapısı.
Site: serialhavale.com (admin panel)
Gateway: gateway01.co (müşteri yatırım ekranı — IBAN + tutar gösterilir)
Site Panel: panelsh.com (site kullanıcıları için, ülke erişim kısıtlaması nedeniyle ayrı domain)
DB: PostgreSQL (alpha_database)
Provider: ORBEYI (provider_id=2, tek sağlayıcı)

## Tam Tablo Listesi (37 tablo)
Ana: payment_transactions, payment_transactions_state, bank_accounts, banks, customers, groups, sites, users
Pivot: site_group, site_user, group_user, model_has_roles, model_has_permissions
Yardımcı: other_transactions, phone_sms_log, settings, ip_whitelist, notifications, audit_logs
Roller: roles, permissions
Queue: jobs, failed_jobs, job_batches (Laravel Queue — callback retry için kullanılır)
Boş/Kullanılmıyor: crypto_transactions, crypto_wallets, pratik_gateway_transactions, audit_logs (boş), notifications (boş)

## Ana Tablolar

### payment_transactions (2.4M+ kayıt)
Ana işlem tablosu. Her havale/ödeme işlemi burada.
- id: İşlem PK (bigint)
- transaction_id: Dış işlem referans ID (integer)
- payment_type: smallint — 0=Çekim (withdrawal), 1=Yatırım (deposit)
- status: smallint — 0=Beklemede, 1=Onaylandı, 3=Reddedildi (status=2 KULLANILMIYOR)
- amount: numeric — İşlem tutarı (TL)
- group_id → groups: Hangi ekip/grup işlemi yönetiyor (bigint)
- provider_id → users: Sağlayıcı (bigint)
- site_id → sites: Hangi site üzerinden geldi (bigint)
- customer_id → customers: Müşteri (bigint)
- bank_account_id → bank_accounts: Kullanılan banka hesabı (bigint)
- iban_prefix, iban_number: varchar — IBAN bilgisi
- **Callback kolonları (KRİTİK):**
  - callback_url: varchar — İşlem sonucu site'ye gönderilecek URL (5,152 eski/test kaydında boş)
  - callback_status: **boolean** — true=başarılı, false=başarısız
  - callback_body: text — Gönderilen callback içeriği
  - callback_response: text — Site'den gelen yanıt
  - callback_error_message: text — Hata mesajı (ör: "400 Bad Request", "Connection refused")
  - callback_retry_count: integer — Deneme sayısı (max 5, sonra dead letter)
  - callback_next_retry_at: timestamp — Sonraki deneme zamanı
  - callback_is_dead_letter: boolean — 4 retry sonrası true olur (15,888 kayıt)
  - callback_last_attempted_at: timestamp — Son deneme zamanı
- provider_fee: numeric — Sağlayıcı komisyonu (SADECE yatırımda hesaplanır)
- group_fee: numeric — Ekip komisyon payı
- company_amount: numeric — Sitenin net alacağı
- ip_address: inet
- created_at, updated_at, deleted_at: timestamp

**İstatistikler (canlı):**
- payment_type=1 (yatırım): 2,333,742 kayıt
- payment_type=0 (çekim): 144,495 kayıt
- status=1 (onaylı): 1,798,724 | status=3 (red): 678,230 | status=0 (bekleyen): 262
- callback_status=true: 2,462,693 | callback_status=false: 14,521
- callback_is_dead_letter=true: 15,888 (dead letter — 2,374 onaylı ama callback gitmemiş = KRİTİK)

### customers (1.1M+ kayıt)
- id, name, surname, username, identity_number (TC), reference_id
- provider_id, site_id → sites
- status: Aktif/pasif
- Top siteler müşteri sayısına göre: NET P (191K), REST P (134K), NGS P (117K)

### bank_accounts (2,987 kayıt — 1,776 aktif, 515 pasif, 696 bloke)
Ekiplerin fiziksel olarak yönettikleri banka hesapları.
- id, name, surname: Hesap sahibi
- bank_id → banks: Banka
- group_id → groups: Hangi gruba/ekibe ait
- provider_id: Sağlayıcı
- iban_prefix, iban_number: IBAN
- balance: numeric — Bakiye (otomatik güncellenir: yatırım gelirse artar, çekim giderse azalır)
- low_limit, high_limit: numeric — Bu aralıkta yatırım kabul eder
- daily_total_process_limit: integer — Günlük toplam işlem limiti
- daily_total_process: integer — Bugün yapılan işlem toplamı (gece 12'de cron ile sıfırlanır, limite ulaşınca hesap otomatik devre dışı kalır)
- **status: smallint — 0=Pasif, 1=Aktif, 2=Bloke (MANUEL bloke — operatör/admin hesabı bloke eder)**
- **deposit_status: Yatırım kabul ediyor mu (status'tan BAĞIMSIZ)**
- **withdrawal_status: Çekim kabul ediyor mu (status'tan BAĞIMSIZ)**
- NOT: Bir hesap aktif (status=1) ama deposit_status=false olabilir (sadece çekim yapar)

### sites (89 kayıt)
İş ortağı bahis siteleri. Yatırım/çekim talepleri bunlardan gelir.
- id, name: Site adı (son harfler aracı kodları — rastgele, sistematik değil)
- commission: numeric — Komisyon oranı (site bazlı, %0-%6 arası)
- **provider_type: varchar — API entegrasyon tipi. Her tipin callback formatı farklı olabilir:**
  - LOCAL: Doğrudan API entegrasyonu
  - NOWGAMING, FINPAY, PRONET, BETCO, DIGITAIN: Aracı üzerinden entegrasyon
- provider_id: Sağlayıcı (tamamı provider_id=2 = ORBEYI)
- **status: boolean (true/false, integer DEĞİL!)**
- low_limit, high_limit: integer — **Site limiti ÖNCELİKLİ** (diğer limitlerden önce kontrol edilir)
- reject_time: integer — Otomatik red süresi (dakika). **Real-time timer ile tetiklenir** (cron değil).
- public_key, private_key: API anahtarları
- theme: varchar

### groups (52 kayıt, 37 aktif)
Saha ekipleri. Banka hesaplarını koyar, yatırımları kontrol eder, onay/red verir.
- id, name: Grup/ekip adı — ŞEHİR BAZLI OPERATÖR EKİPLERİ (ör: "214 F", "57 Grup", "Diyarbakır 1-6", "Adana01", "Fox", "Speed")
- commission: numeric — Grup komisyonu (%1-%3 arası, çoğunluğu %2.25)
- provider_id: Sağlayıcı (hepsi provider_id=2, test grup hariç)
- status: Aktif/pasif
- low_limit, high_limit: integer — Çoğu grup: low=100, high=100000. 10 grup: 0/0 (limitsiz). Bazı gruplar özel üst limitli (3999, 4999, 5999, 8999)

### phone_sms_log (637K+ kayıt)
Ekiplerin fiziksel telefonlarından çekilen banka SMS'leri.
- id, token: SMS kaynak telefon/script token'ı
- message: text — SMS içeriği (isimler maskeli)
- time: timestamp
- payment_transaction_id → payment_transactions: Eşleşen işlem (null ise eşleşmemiş)
- status: İşlenmiş mi
- Auto-approve bot bu SMS'leri **isim + tutar** ile eşleştirir

### users (211 kayıt)
Sistem kullanıcıları.
- id, name, surname, email, password
- provider_id: Sağlayıcı (integer)
- two_fa, two_fa_token: İki faktörlü doğrulama
- **Roller users tablosunda DEĞİL → Spatie permission pattern:**
  - roles tablosu: administrator (10), provider (4), user (100), site_user (47), personal (50)
  - model_has_roles: pivot tablo (user ↔ role)
  - model_has_permissions: pivot tablo (user/role ↔ permission)

### banks (20 kayıt)
Banka tanımları (Akbank, Garanti, Ziraat, İş, Yapı Kredi, vb).

### other_transactions (11,700 kayıt)
Banka hesaplarında yapılan muhasebe ayarlamaları.
- transaction_type: smallint (tümü 0)
- transaction_process_type kodları:
  - 0: Finans takviye (663 kayıt — ekibin çekimlere yetişmek için eklediği ek para)
  - 1: Finans takviye düş (7 kayıt — takviyeyi raporu bozmadan geri alma)
  - 2: Şirket takviye (90 kayıt — sitenin çekim çıkışı için gönderdiği para)
  - 3: Şirket teslimat (1,461 kayıt — kripto teslimat, raporda çekim gibi düşer)
  - 4: Çekilen grup komisyonu (210 kayıt — group_fee hakediş çekimi)
  - 5: Çekilen provider komisyonu (663 kayıt — provider_fee çekimi)
  - 6: Bankalar arası transfer (9,220 kayıt — hesaplar arası para aktarma)
- bank_account_id, target_bank_account_id: Kaynak ve hedef hesap
- bank_account_balance_before/after: Kaynak hesap bakiye değişimi
- target_bank_account_balance_before/after: Hedef hesap bakiye değişimi
- **is_reverted: boolean — Tüm other_transaction tipleri revert edilebilir, bakiye otomatik geri döner**

### payment_transactions_state (5.6M+ kayıt)
İşlem audit logu. Her durum değişikliği loglanır.
- id: bigint PK
- transaction_id → payment_transactions
- amount: numeric
- status: smallint — 0 (3.3M), 1 (1.8M), 3 (523K)
- **action: varchar — Sabit kodlu state transition string'leri:**
  - "Provider upon request." (2.3M) — İşlem ilk oluşturulduğunda
  - "Yatirim tutari onaylandi" (1.2M) — Yatırım onaylandığında
  - "Amount set." (680K) — Tutar ayarlandığında
  - "Professional Code Approved." (456K) — Referans kodu onaylandığında
  - "System Declined" (236K) — Otomatik red (reject_time doldu)
  - Diğerleri: "auto approved", miktar değişiklikleri, manuel red, vb.
- ip_address: inet
- created_by_id, created_by (JSON): Kim yapmış (audit trail)
- created_at, updated_at, deleted_at

### settings (4 kayıt)
- AUTO_REJECTED_TIME: 30 (dakika — global default)
- SITE_URL: https://gateway01.co
- THEME_COLOR: green
- MAINTENANCE_MODE: 0 (kapalı)

### ip_whitelist
- site_id → sites: Hangi site için
- ip: inet — Beyaz listeye alınan IP
- Site 3 ve 4 ağırlıklı, tümü root tarafından oluşturulmuş

## İlişkiler
- payment_transactions → customers, sites, groups, bank_accounts (FK)
- bank_accounts → banks, groups (FK)
- **site_group: sites ↔ groups çoklu ilişki (M:N pivot)**
  - Bir siteye birden fazla ekip bağlanır, yatırımlar sırayla ve koşullara bağlı dağılır (açık hesap, deposit_status, limit)
  - Top: site 20 (4,952 eşleşme), site 31 (4,632 eşleşme)
- group_user: groups ↔ users çoklu ilişki
- site_user: sites ↔ users çoklu ilişki
- model_has_roles: users ↔ roles (Spatie)
- model_has_permissions: users/roles ↔ permissions (Spatie)

## Komisyon Formülü (SADECE YATIRIMDA)
```
Yatırım tutarı: 100.000 TL
Site komisyonu: %5 (site.commission)
Toplam komisyon: 100.000 * 0.05 = 5.000 TL
Grup komisyonu (group_fee): %3 (group.commission) → 3.000 TL
Provider komisyonu (provider_fee): 5.000 - 3.000 = 2.000 TL
Şirket alacağı (company_amount): 100.000 - 5.000 = 95.000 TL

NOT: Çekim işlemlerinde komisyon ALINMAZ.

Net kasa hesaplama:
company_amount = yatırım - çekim - komisyon - şirket_teslimat + şirket_takviye
```

## Callback Retry Mekanizması (Laravel Queue)
1. İşlem onay/red → callback dispatch (Laravel Queue job)
2. Başarısız → callback_retry_count++ (max 5 deneme)
3. 5. denemede hâlâ başarısızsa → callback_is_dead_letter = true
4. Panelde "callback tekrar gönder" butonu var (manuel recovery)
5. En sık hatalar: 400 Bad Request (letspayments), Connection refused, 500 Internal Server Error
6. KRİTİK: 2,374 onaylı işlemin callback'i gitmemiş — site parasının geldiğini bilmiyor!

## Limit Hiyerarşisi
1. **Site limiti ÖNCELİKLİ** (low_limit, high_limit)
2. Banka hesabı limiti (low_limit, high_limit, daily_total_process_limit)
3. Grup limiti (çoğunluk: low=100, high=100000. 10 grup limitsiz: 0/0. Bazıları özel üst limitli)
NOT: 0 = limitsiz anlamına gelir

## Not
- Sadece SELECT sorguları çalıştırılabilır
- Tablo adları İngilizce, kolon adları İngilizce
- callback_status boolean (true/false), integer değil
- sites.status boolean (true/false), integer değil
- Crypto tabloları aktif değil
- pratik_gateway_transactions boş
- audit_logs boş iskelet
