# Organizasyon Bilgileri

## Platform
- **Temel tanım: Bahis altyapısı ödeme sağlayıcısı** — müşterilerin banka havale ile yatırım yapmasını sağlayan CRM
- Admin Panel: serialhavale.com
- Müşteri Yatırım Ekranı: gateway01.co (IBAN + tutar gösterilir)
- Site Kullanıcı Paneli: panelsh.com (ülke erişim kısıtlaması nedeniyle ayrı domain)
- Sağlayıcı: ORBEYI (tek provider, provider_id=2)

## Yapı
- ~55 aktif site (bahis iş ortakları)
- ~37 aktif grup (saha ekipleri)
- 211 kullanıcı: admin (10), provider (4), operatör (100), site_user (47), personal (50)
- 20 banka tanımlı
- 2,987 banka hesabı (1,776 aktif, 515 pasif, 696 bloke)
- 2.3M+ yatırım, 144K+ çekim işlemi

## Temel Kavramlar
- Yatırım (deposit): Müşteriden platforma para girişi (payment_type=1) — komisyon alınır
- Çekim (withdrawal): Platformdan müşteriye para çıkışı (payment_type=0) — komisyon alınmaz
- Grup: Saha ekibi, fiziksel banka hesaplarını yönetir, yatırımları kontrol eder
- Site: İş ortağı bahis sitesi, API entegrasyon tipi (LOCAL, PRONET, FINPAY, vb.) farklı olabilir
- Operatör: İşlemleri onaylayan/reddeden ekip üyesi
- Ortacı (Personal): Ekip-site arası iletişim köprüsü, sorun çözücü

## İşlem Durumları
- 0: Beklemede (pending) — 262 aktif
- 1: Onaylandı (approved) — 1.8M
- 3: Reddedildi (rejected) — 678K

## Kullanıcı Rolleri (Spatie Permission Pattern)
- Administrator (10): Tam yetki
- Provider (4): Sağlayıcı, pratikte admin gibi
- User (100): Ekip operatörü (onay/red, banka hesabı yönetimi)
- Site User (47): Salt okunur kontrol (panelsh.com)
- Personal (50): Ortacı/aracı, iletişim köprüsü

## Telegram Bot Ekosistemi (5 bot)
1. **banka_limit**: Site min yatırımını kapsayan banka hesabı yoksa → personal'a uyarı
2. **bekleyen_cekim**: 10 dk'da bir ortak kanala bekleyen çekim sayısı + yaşı bildirir
3. **imha**: Riskli seviyede bekleyen yatırım sayısı uyarısı (40+ bilgi, 70+ uyarı, 85+ kritik, 150+ nükleer)
4. **simplesorgu**: Tüm site telegramlarında /sorgu, /alici, /iban komutları
5. **uyuşmayansms**: Auto-approve botunun atladığı eşleşmeleri yakalar, ekibe bildirir

## Kritik Sorunlar
1. **Dead letter callback**: 15,888 dead letter, 2,374'ü ONAYLI (site parasını bilmiyor!) — otomatik uyarı sistemi YOK
2. **Fraud tespiti**: Otomatik fraud/blacklist yok, tamamen manuel göz
3. **Callback hatalar**: letspayments 400 Bad Request (~12,800), pronetgaming Connection refused (~760) — aktif sorun
