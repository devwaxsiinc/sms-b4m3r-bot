# Bot Kurulum ve Kullanım Kılavuzu

## Gereksinimler
- Python 3.8 veya üzeri
- Discord Bot Token

## Kurulum Adımları

### 1. Python Kontrolü
```bash
python --version
```
Python 3.8+ olmalı. Değilse [python.org](https://www.python.org/downloads/) adresinden indirin.

### 2. Gerekli Paketleri Kurun
```bash
pip install -r requirements.txt
```

### 3. Config Dosyasını Düzenleyin
`config.json` dosyasında:
- `bot_token`: Discord bot token'ınızı girin
- `allowed_channel_id`: İzin verilen kategori ID (1434979655450099753)
- `allowed_role_id`: İzin verilen rol ID (1434564418079035504)

### 4. Botu Çalıştırın
```bash
python discord_bot.py
```

## Discord'da Kullanım

### Komut Formatı
```
/sms numara:5551234567 sayi:50 mod:normal
```

### Parametreler
- **numara**: 10 haneli telefon numarası (zorunlu)
  - Örnek: `5551234567`
- **sayi**: Gönderilecek SMS sayısı (1-100 arası, zorunlu)
  - Örnek: `50`
- **mod**: Gönderme modu (opsiyonel, varsayılan: normal)
  - `normal`: Sırayla gönderir
  - `turbo`: Paralel gönderir (daha hızlı)

### Örnekler
```
/sms numara:5551234567 sayi:50 mod:normal
/sms numara:5551234567 sayi:100 mod:turbo
/sms numara:5551234567 sayi:25
```

### Önemli Notlar
- Bot sadece belirtilen kategorideki kanallarda çalışır
- Sadece belirtilen role sahip kullanıcılar komutu kullanabilir
- Sayı parametresi 1-100 arasında olmalıdır
- Bot 7/24 çalışır, hata vermez

## Sorun Giderme

### Bot çalışmıyor
- Python versiyonunu kontrol edin (3.8+)
- Tüm paketlerin kurulu olduğundan emin olun
- Bot token'ının doğru olduğundan emin olun

### Komut görünmüyor
- Botun sunucuda olduğundan emin olun
- Botun izinlerini kontrol edin
- Birkaç saniye bekleyin (komutlar senkronize olmalı)

### Hata mesajları
- Kategori kontrolü: Sadece belirtilen kategorideki kanallarda çalışır
- Rol kontrolü: Gerekli role sahip olmalısınız
- Telefon numarası: 10 haneli olmalı (örn: 5551234567)

