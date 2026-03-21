# serialhavale.com — Payment Gateway API (V2)

## Platform URL'leri
- **Production:** https://netgateway.co
- **Test/Sandbox:** https://api.onlyp.co
- **Admin Panel:** https://serialhavale.com
- **Müşteri Gateway:** https://gateway01.co (eski/alternatif)
- **Site Panel:** https://panelsh.com

## Authentication
Tüm API istekleri `public-key` header'ı gerektirir.
```
public-key: SITE_PUBLIC_KEY
```
Her sitenin kendi public_key ve private_key'i var (sites tablosunda).

## API Endpoints

### 1. Deposit (Yatırım Başlatma)
```
POST /api/json/transaction/deposit
Content-Type: application/x-www-form-urlencoded
```

**Parametreler (hepsi zorunlu):**
| Parametre | Tip | DB Karşılığı |
|-----------|-----|-------------|
| customerUsername | String | customers.username |
| customerName | String | customers.name |
| customerSurname | String | customers.surname |
| customerReferenceId | String/Int | customers.reference_id |
| merchantTransactionId | String | payment_transactions.transaction_id |
| callbackUrl | URL | payment_transactions.callback_url |
| amount | Number | payment_transactions.amount |

Bu endpoint çağrıldığında → DB'de payment_transactions kaydı oluşur (status=0, beklemede).

### 2. Deposit — URL Only (Sadece Yönlendirme)
```
POST /api/transaction/deposit
```
Aynı parametreler, fark: amount opsiyonel. Sadece ödeme sayfası URL'sini döner.
Müşteri bu URL'ye yönlendirilir → gateway01.co'da IBAN + tutar görür.

### 3. Withdraw (Çekim Talebi)
```
POST /api/json/transaction/withdraw
```

**Ek parametre:**
| Parametre | Tip | DB Karşılığı |
|-----------|-----|-------------|
| customerPaymentAddress | String | IBAN (iban_prefix + iban_number) |

Çekim talebinde IBAN zorunlu — müşterinin parasının gönderileceği hesap.

## Callback Yapısı (KRİTİK)

İşlem onaylandığında veya reddedildiğinde, sistem sitenin callback_url'sine POST atar.

### Callback Parametreleri
| Parametre | Tip | Açıklama |
|-----------|-----|----------|
| status | Integer | 1=Başarılı (onay), 3=Başarısız (red) |
| amount | String | İşlem tutarı (ör: "100.00") |
| hash | String | SHA256 doğrulama hash'i |
| transaction_id | String | İşlem ID'si |
| update_date | String | ISO 8601 tarih |
| payment_type | Integer | 1=Yatırım, 0=Çekim |
| merchantTransactionId | String | Sitenin kendi işlem referansı |

### Hash Doğrulama Formülü
```php
hash('sha256', $public_api_key . $merchantTransactionId . $private_api_key . $amount . $status)
```
Sıralama: public_key + merchantTransactionId + private_key + amount + status
Site bu hash'i kontrol ederek callback'in gerçekten serialhavale'den geldiğini doğrular.

### Callback Formatları
İki formatta gönderilebilir:

**Format 1 — JSON:**
```json
{
  "status": 1,
  "amount": "100.00",
  "hash": "abc123...",
  "transaction_id": "TX123",
  "update_date": "2026-03-06T20:07:55.000000Z",
  "payment_type": 1,
  "merchantTransactionId": "12345"
}
```

**Format 2 — Form URL Encoded:**
```
status=1&amount=100.00&hash=abc123...&transaction_id=TX123&...
```

### Beklenen Callback Yanıtı
Site, callback'e HTTP 200 + `{"success": true}` ile cevap vermeli.
200 dışı yanıt → callback_status=false → retry mekanizması devreye girer.

## Callback Hata Nedenleri ve DB İlişkisi

### Neden Callback Başarısız Olur?
1. **Site endpoint'i cevap vermiyor** → Connection refused / timeout
   - DB'de: callback_error_message = "Connection refused" veya timeout
   - En sık: mani.pronetgaming.eu (~760 hata)

2. **Site 400 Bad Request dönüyor** → Parametre format uyuşmazlığı
   - DB'de: callback_error_message = "400 Bad Request"
   - En sık: letspayments.com endpoint'leri (~12,800 hata)

3. **Site 500 Internal Server Error** → Site tarafında bug
   - DB'de: callback_error_message = "500 Internal Server Error"

4. **Hash doğrulaması tutmuyor** → Site key değişmiş veya format farkı

5. **Site {"success": true} dönmüyor** → Farklı format döndüğünde de başarısız sayılabilir

### Callback Retry Akışı
1. İlk gönderim başarısız → callback_retry_count = 1
2. Tekrar dener (max 5 kez)
3. 5. denemede de başarısızsa → callback_is_dead_letter = true
4. failed_jobs tablosuna da kayıt düşer
5. Panelde "callback tekrar gönder" butonu var (manuel recovery)

### Dead Letter Analizi Sorguları
```sql
-- Hangi sitenin callback'leri en çok patlıyor?
SELECT s.name, s.provider_type,
    COUNT(*) FILTER (WHERE pt.callback_is_dead_letter = true) as dead_letter,
    COUNT(*) FILTER (WHERE pt.callback_status = false) as basarisiz_callback,
    LEFT(pt.callback_error_message, 50) as hata_ornegi
FROM payment_transactions pt
JOIN sites s ON pt.site_id = s.id
WHERE pt.callback_status = false AND pt.created_at >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY s.id, s.name, s.provider_type, LEFT(pt.callback_error_message, 50)
ORDER BY dead_letter DESC
LIMIT 20
```

```sql
-- Callback başarısız ama onaylı (KRİTİK — site parayı bilmiyor)
SELECT COUNT(*), COALESCE(SUM(amount), 0) as toplam_tutar
FROM payment_transactions
WHERE status = 1 AND callback_status = false
AND created_at >= CURRENT_DATE - INTERVAL '7 days'
```

## provider_type ve Callback Format İlişkisi
Her sitenin provider_type'ına göre callback formatı farklı olabilir:
- **LOCAL:** Doğrudan API entegrasyonu (netgateway.co API'si)
- **NOWGAMING, FINPAY, PRONET, BETCO, DIGITAIN:** Aracı üzerinden entegrasyon
  - Bu aracıların kendi callback format beklentileri olabilir
  - Dead letter çoğunlukla bu aracı entegrasyonlardan gelir

## API Parametre ↔ DB Kolon Eşleşmesi (Tam Harita)
| API Parametresi | DB Kolonu | Tablo |
|-----------------|-----------|-------|
| customerUsername | username | customers |
| customerName | name | customers |
| customerSurname | surname | customers |
| customerReferenceId | reference_id | customers |
| merchantTransactionId | transaction_id | payment_transactions |
| callbackUrl | callback_url | payment_transactions |
| amount | amount | payment_transactions |
| customerPaymentAddress | iban_number | payment_transactions |
| public-key (header) | public_key | sites |
| — (private, backend) | private_key | sites |

## HTTP Hata Kodları
| Kod | Açıklama |
|-----|----------|
| 200 | Başarılı |
| 400 | Geçersiz parametre |
| 401 | Yetkilendirme hatası (yanlış public-key) |
| 404 | İşlem bulunamadı |
| 500 | Sunucu hatası |
