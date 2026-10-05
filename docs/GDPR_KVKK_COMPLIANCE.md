# KVKK & GDPR Uyumluluk Beyanı
# PsikoNot — Veri Koruma Çerçevesi (v6.0.0)

> **Belge Türü:** Kişisel Veri İşleme Envanteri ve Uyum Beyanı  
> **Kapsam:** PsikoNot'ta işlenen kişisel veriler ve özel nitelikli sağlık verileri (psikolojik danışmanlık kayıtları)  
> **Güncelleme:** 2026-07-28  
> **Referans Mevzuat:** 6698 sayılı KVKK · GDPR (AB) 2016/679 · ISO/IEC 27701:2019 · ISO 27001

---

## 1. Veri Sorumlusu Bilgileri

| Alan | Bilgi |
|------|-------|
| **Ünvan** | PsikoNot'u kullanan psikoloji muayenehanesi (veri sorumlusu) |
| **Teknik Mimarisi** | Isolated Single-Tenant Architecture, Clean Architecture, CQRS |
| **Veri İşleme Modeli** | Off-chain şifreli depolama + Local Signed Merkle Hash-Chain |

> ⚠️ **Kritik Mimari Notu:** Ham Kişisel Sağlık Verisi (PHI) **hiçbir zaman kamuya açık ağlara yazılmaz**;
> izole tek-kiracılı (single-tenant) dağıtımın dışına çıkmaz.
>
> ✅ **Uygulama durumu:** Pseudonymization yazma yoluna bağlanmıştır — klinik zincir
> deposu ham `patient_id` ile değil, deterministik `anon_id` ile anahtarlanır. Anahtar
> imhası ile silme (Art. 17) de canlıdır. Kalan tek madde dış Merkle çıpalamasıdır.

---

## 2. İşlenen Kişisel Veri Kategorileri

### 2.1 Sıradan Kişisel Veriler
- Kimlik (Ad, soyad, kullanıcı adı)
- Dijital Kimlik (FIDO2 Passkey credential ID, IP Allowlist)
- Güvenlik (Argon2id şifre hash, TOTP sırrı)

- Fatura bilgileri (danışan adı ve numarası, seans tarihi, tutar). Hizmet satırı sabit bir metindir ve serbest metin alanı yoktur; faturaya teşhis veya not yazılamaz. Ödeme takibi yapılmaz. Bir faturanın varlığı, kişinin psikolojik danışmanlık aldığını gösterdiği için faturalar da uzmanın defteriyle sınırlı tutulur.

### 2.2 Özel Nitelikli Kişisel Veriler (Sağlık Verileri)
- Danışan profili (özellikler, başvuru nedeni, sorunlar, geçmiş)
- Seans notları, seans dökümleri (her zaman yalnızca uzman görür) ve uzmanın süreç notları
- Tedavi planları
- Ödevler, danışanın kişisel günlüğü ve ekler (ör. taranmış onam formu)

---

## 3. Güvenlik ve Uyum Mekanizmaları

1. **Pseudonymization (KVKK M.7 & GDPR Art. 32)** — ✅ *canlı, yazma yoluna bağlı*:
   - Klinik zincir deposu, ham `patient_id` yerine deterministik `anon_id` (HMAC) ile anahtarlanır (`core/pseudonymization/service.py::project_name_for`); diskteki depo yalnızca opak takma kimlikler tutar.
   - Yetkili yönetici `patient_id ↔ anon_id` eşlemesini çözebilir; yazma yolu bu eşlemeyi kalıcılaştırır.
2. **Çift Onaylı Yetki İlkesi (Dual-Control)**:
   - Sistem Yöneticisi (Admin) dahi danışanın kayıtlarını tek başına okuyamaz. Güvenlik Görevlisi (`security_officer`) co-signature (çift onay) şarttır.
3. **Dosyayı yalnızca danışanın uzmanı açar**:
   - Danışan uygulamaya girmez; her danışan bir uzmana ait bir karttır. Danışanın dosyasını (seans notları, dökümler, profil) yalnızca o uzman açar; sekreter, başka bir uzman ya da tek başına bir yönetici açamaz.
   - Aydınlatma metni ve açık rıza kâğıt üzerinde imzalanır; imza tarihi danışan kartında tutulur. Veri kopyası (M.11) ve silme talebi (M.17) danışanın isteğiyle uzman tarafından uygulamadan yapılır.
4. **Network Level Isolation (Ağ İzolasyonu)**:
   - `IPAllowlistMiddleware` ile varsayılan olarak kamuya kapalıdır; sadece kurum VPN ve yetkili IP bloklarına açık tutulur.
5. **Diskte Şifreleme (KVKK M.12 & GDPR Art. 32)**:
   - Her klinik yük, KMS'ten türeyen ve danışana özel bir anahtarla AES-256-GCM ile diskte şifrelenir (`core/services/record_service.py::_encrypt_at_rest`).
   - Zincir deposu yalnızca şifreli metin tutar; imzalama anahtarı zincir deposunun dışında (ortam değişkeni / OS keyring) yaşadığından, tek başına çalınan bir `projects/` yedeği çözülemez.
6. **Tamper-Evident Erişim Defteri (ISO 27001 A.12.4 & KVKK M.12)**:
   - Her okuma ve klinisyen görüntülemesi, `seq` + `prev_hash` + `hash` taşıyan hash-bağlı bir kayıttır (`database/audit_storage.py`).
   - Geçmiş bir erişim olayını silmek veya değiştirmek zinciri kırar ve `verify_access_log_integrity` tarafından sıra numarasıyla raporlanır. Uzman, danışanının dosyasına kimin eriştiğini ve defterin bütünlük durumunu **Access Ledger** ekranından görür.
7. **Anahtar İmhası ile Silme — Unutulma Hakkı (GDPR Art. 17 & KVKK M.7)** — ✅ *canlı*:
   - At-rest anahtarı, KMS kökü **ve** danışana özel bir gizli anahtardan türetilir. `POST /api/v1/erasure/{patient_id}` bu gizli anahtarı imha eder; onun altında şifrelenmiş her kayıt kalıcı olarak çözülemez hale gelir (crypto-shredding).
   - Append-only zincir ve imzaları **bozulmaz** (bütünlük kanıtı korunur); işlem yetkili rol + Dual-Control ile korunur ve geri döndürülemezdir.
   - **Silme talebi:** danışan isterse uzmanı, **Clients** sayfasından silme talebi oluşturur. Talep kendi başına hiçbir şey silmez: yönetici veya KVKK sorumlusu silmeyi dual-control ile uygular, talep ancak anahtar gerçekten imha edildikten sonra "tamamlandı" olarak kapatılabilir. Saklama yükümlülüğü varsa talep gerekçeyle reddedilebilir.
8. **Aydınlatma Metni ve Açık Rıza (KVKK M.5, M.6, M.10)**:
   - Danışan uygulamaya girmez: aydınlatma metni ve açık rıza formu kâğıt üzerinde imzalanır, imza tarihi danışan kartına yazılır. Kartta tarih yoksa liste bunu "KVKK forms missing" olarak gösterir.
9. **Veriye Erişim ve Kopya Alma (KVKK M.11)** — ✅ *canlı*:
   - Danışan isterse uzmanı, **Clients** sayfasındaki "Export data" ile danışanın verisinin bir kopyasını tek bir JSON dosyası olarak indirip danışana verir: kart, kayıtlar (kilitli kayıtlar kilitli kalır), randevular ve dosyaya kimin eriştiği.
10. **Dışarıda Tutulan İmza Anahtarı (GDPR Art. 32)** — ✅ *canlı (opsiyonel)*:
   - `KMS_PROVIDER=vault` ile imza anahtarı HashiCorp Vault Transit içinde yaşar ve uygulamaya hiç girmez; host + `projects/` deposunu ele geçiren bir operatör dahi imza veya at-rest anahtarı üretemez.
11. **Band-Dışı Hesap Onboarding'i** — ✅ *canlı*:
   - Hiçbir hesap self-registration ile oluşmaz. Yetkili operatör kimliği doğrulanmış hesabı `PENDING_ONBOARDING` olarak açar; hesap, band-dışı teslim edilen tek-kullanımlık enrollment token redeem edilene kadar giriş yapamaz.

---

## 4. Yol Haritası (Planlanan Uyum Çalışmaları)

Bu bölüm, beyanın kod tabanının **önünde** iddia içermemesi için, henüz
uygulanmamış uyum mekanizmalarını dürüstçe listeler. Her madde bağımsız olarak
teslim edilebilecek şekilde sıralanmıştır.

| Durum | Mekanizma | Karşılık |
| :---: | :--- | :--- |
| 📋 planlandı | **Merkle kökünün dış çıpalanması** — RFC 3161 zaman damgası veya imzalı günlük kök ile operatör-değiştiremez bütünlük kanıtı | ISO 27001 A.12.4 |

> Pseudonymization'ın yazma yoluna bağlanması, anahtar imhası ile silme (Art. 17) ve
> dışarıda tutulan imza anahtarı **tamamlanmış** olup Bölüm 3'e taşınmıştır.

> Bu tablo, güvenlik denetiminde "beyan edilen ≠ uygulanan" boşluğunu ortadan
> kaldırmak için tutulur. Bir madde uygulandığında Bölüm 3'e taşınır.
