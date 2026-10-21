/* messages.js — the API's messages, in Turkish
 *
 * The server answers in English (`detail` on an error, `message` on success),
 * and hundreds of backend tests pin that wording, so the translation happens
 * here, at the edge: an explicit table of the exact messages a user actually
 * sees, plus a few patterns for messages that carry a name, a number or an ID.
 * Anything not listed comes back unchanged, so a new server message is shown
 * in English rather than lost.
 *
 * The result is plain text. Callers put it in textContent, or through
 * escapeHtml() into markup; a captured name is never trusted as HTML.
 *
 * No imports and no DOM: tests load this module in Node.
 */

const EXACT = {
  // Generic
  'An error occurred': 'Bir hata oluştu.',
  'Not Found': 'Bulunamadı.',
  'Method Not Allowed': 'Bu işlem desteklenmiyor.',
  'Internal Server Error': 'Sunucuda bir hata oluştu. Lütfen tekrar deneyin.',

  // Sign-in and session
  'Incorrect username or password': 'Kullanıcı adı veya şifre hatalı.',
  'Too many attempts. Please wait 1 minute.': 'Çok fazla deneme yapıldı. Lütfen 1 dakika bekleyin.',
  'Invalid 2FA verification code': 'İki adımlı doğrulama kodu geçersiz.',
  'This account has been disabled.': 'Bu hesap devre dışı bırakılmış.',
  'This account has been disabled': 'Bu hesap devre dışı bırakılmış.',
  'This account signs in with its passkey.': 'Bu hesap geçiş anahtarı (passkey) ile giriş yapar.',
  'Account is pending onboarding. Complete enrollment with your out-of-band token before signing in.':
    'Hesap henüz etkinleştirilmedi. Giriş yapmadan önce size verilen davet koduyla hesabınızı etkinleştirin.',
  'Session ended: the password or the account changed': 'Oturum sona erdi: şifre veya hesap değişti. Lütfen yeniden giriş yapın.',
  'Not authenticated — access token is missing': 'Oturum açık değil. Lütfen giriş yapın.',
  'Invalid token': 'Oturumunuz geçersiz. Lütfen yeniden giriş yapın.',
  'Invalid token — user not found': 'Oturumunuz geçersiz. Lütfen yeniden giriş yapın.',
  'Token expired': 'Oturumunuzun süresi doldu. Lütfen yeniden giriş yapın.',
  'Token has been revoked': 'Oturumunuz kapatıldı. Lütfen yeniden giriş yapın.',
  'CSRF token validation failed — missing or mismatched token':
    'Güvenlik doğrulaması başarısız oldu. Sayfayı yenileyip tekrar deneyin.',
  'Network Security Violation: IP address not authorized for PsikoNot access.':
    'Bu ağ adresinden PsikoNot’a erişim izni yok.',
  'Logout could not be completed, try again': 'Çıkış tamamlanamadı, tekrar deneyin.',
  'Logged out successfully': 'Çıkış yapıldı.',

  // Two-factor authentication
  'Invalid verification code. Please check your app and try again.':
    'Doğrulama kodu geçersiz. Uygulamanızı kontrol edip tekrar deneyin.',
  'Invalid verification code. Disable failed.': 'Doğrulama kodu geçersiz. İki adımlı doğrulama kapatılamadı.',
  '2FA setup has not been initiated. Call /api/v1/auth/2fa/setup first.':
    'İki adımlı doğrulama kurulumu başlatılmadı. Önce kurulumu başlatın.',
  '2FA is not enabled for this account.': 'Bu hesapta iki adımlı doğrulama açık değil.',
  '2FA is already enabled. Disable it with a current code first.':
    'İki adımlı doğrulama zaten açık. Önce geçerli bir kodla kapatın.',
  'Two-factor authentication enabled successfully': 'İki adımlı doğrulama açıldı.',
  'Two-factor authentication disabled successfully': 'İki adımlı doğrulama kapatıldı.',

  // Passkeys
  'Passkey / WebAuthn hardware credential registered successfully!': 'Geçiş anahtarı (passkey) kaydedildi.',
  'Invalid or unregistered Passkey credential.': 'Geçiş anahtarı (passkey) geçersiz veya kayıtlı değil.',
  'This passkey credential is already registered.': 'Bu geçiş anahtarı (passkey) zaten kayıtlı.',
  'Unknown, expired, or already used challenge': 'Doğrulama isteği bilinmiyor, süresi dolmuş ya da daha önce kullanılmış. Tekrar deneyin.',
  'Assertion origin is not authorized for this vault': 'Bu adresten geçiş anahtarıyla giriş yapılamaz.',
  'Assertion was issued for a different Relying Party': 'Geçiş anahtarı başka bir site için oluşturulmuş.',
  'Authenticator did not assert user presence': 'Cihaz kullanıcı varlığını doğrulamadı.',
  'Authenticator did not perform user verification': 'Cihaz kullanıcı doğrulaması yapmadı.',
  'Passkey signature could not be verified': 'Geçiş anahtarı imzası doğrulanamadı.',
  'Passkey signature verification failed': 'Geçiş anahtarı imzası doğrulanamadı.',
  'Unsupported credential algorithm: ES256 (secp256r1) required': 'Bu geçiş anahtarı türü desteklenmiyor (ES256 gerekir).',
  'Dual-Control Co-Signature Required to Revoke Passkey Credential.': 'Geçiş anahtarını kaldırmak için çift onay gerekir.',

  // Passwords
  'Password must be at least 12 characters long.': 'Şifre en az 12 karakter olmalıdır.',
  'Password must contain at least 1 uppercase letter.': 'Şifre en az 1 büyük harf içermelidir.',
  'Password must contain at least 1 lowercase letter.': 'Şifre en az 1 küçük harf içermelidir.',
  'Password must contain at least 1 digit.': 'Şifre en az 1 rakam içermelidir.',
  'Password must contain at least 1 special character (!@#$%^&* etc.).': 'Şifre en az 1 özel karakter (!@#$%^&* vb.) içermelidir.',
  'The current password is not correct.': 'Mevcut şifre doğru değil.',
  'The new password must differ from the current one.': 'Yeni şifre mevcut şifreden farklı olmalıdır.',
  'Password changed. Your other sessions have been signed out.': 'Şifre değiştirildi. Diğer oturumlarınız kapatıldı.',
  'Demo accounts keep their published passwords in demo mode.': 'Demo modunda demo hesapların şifreleri değiştirilemez.',
  'Demo accounts cannot be changed in demo mode.': 'Demo modunda demo hesaplar değiştirilemez.',

  // Invitation and reset codes
  'Invalid or unknown reset code': 'Sıfırlama kodu geçersiz veya bilinmiyor.',
  'This reset code has already been used': 'Bu sıfırlama kodu daha önce kullanılmış.',
  'This reset code has expired': 'Bu sıfırlama kodunun süresi dolmuş.',
  'Invalid or unknown enrollment token': 'Davet kodu geçersiz veya bilinmiyor.',
  'This enrollment token has already been used': 'Bu davet kodu daha önce kullanılmış.',
  'This enrollment token has expired': 'Bu davet kodunun süresi dolmuş.',
  'The account for this token no longer exists': 'Bu koda ait hesap artık yok.',
  'Password set. Sign in with your new password.': 'Şifre belirlendi. Yeni şifrenizle giriş yapın.',
  'Account activated. You can now sign in and enrol your passkey.':
    'Hesap etkinleştirildi. Artık giriş yapabilir ve geçiş anahtarınızı (passkey) kaydedebilirsiniz.',
  'Too many open invitations. Wait for some to be used or to expire.':
    'Çok fazla açık davet var. Bazılarının kullanılmasını veya süresinin dolmasını bekleyin.',
  'A user with this username already exists': 'Bu kullanıcı adı zaten kullanılıyor.',

  // Accounts
  'Your role cannot manage accounts.': 'Rolünüz hesap yönetimine izin vermiyor.',
  'You can only manage the secretaries who work for you.': 'Yalnızca sizin için çalışan sekreterleri yönetebilirsiniz.',
  'This secretary also works for other practitioners. You can remove them from your book; resetting or disabling the account is for an operator.':
    'Bu sekreter başka psikologlar için de çalışıyor. Onu kendi randevu defterinizden çıkarabilirsiniz; hesabı sıfırlamak veya devre dışı bırakmak merkez yöneticisinin işidir.',
  'No staff account with this username.': 'Bu kullanıcı adıyla bir personel hesabı yok.',
  'No secretary account with this username.': 'Bu kullanıcı adıyla bir sekreter hesabı yok.',
  'No practitioner account with this username.': 'Bu kullanıcı adıyla bir psikolog hesabı yok.',
  'User account not found.': 'Kullanıcı hesabı bulunamadı.',
  'This account is not disabled.': 'Bu hesap devre dışı değil.',
  'This account is already disabled.': 'Bu hesap zaten devre dışı.',
  'This account cannot be reset': 'Bu hesap sıfırlanamaz.',
  'Only an active account can be reset: re-enable a disabled account first; a pending account still has its invitation.':
    'Yalnızca etkin bir hesap sıfırlanabilir: devre dışı bir hesabı önce yeniden etkinleştirin; bekleyen bir hesabın daveti hâlâ geçerlidir.',
  'Give the reset code to the account holder in person.': 'Sıfırlama kodunu hesap sahibine yüz yüze verin.',
  'Account enabled. If they no longer know their password, issue a reset code.':
    'Hesap etkinleştirildi. Şifresini hatırlamıyorsa bir sıfırlama kodu verin.',

  // Dual control
  'Dual-Control Violation: The requesting user cannot self-approve their own request!':
    'Çift onay kuralı: Talebi oluşturan kişi kendi talebini onaylayamaz.',
  'Dual-control request has expired.': 'Çift onay talebinin süresi doldu.',
  'Dual-control request token not found.': 'Çift onay talebi bulunamadı.',
  'Only a Security Officer or System Administrator can co-sign dual-control requests.':
    'Çift onay taleplerini yalnızca güvenlik sorumlusu veya yönetici onaylayabilir.',
  'Only operators can initiate dual-control requests.': 'Çift onay talebini yalnızca operatörler başlatabilir.',
  'Only privileged operators can inspect dual-control requests.': 'Çift onay taleplerini yalnızca yetkili operatörler görebilir.',
  'Co-signature requires Security Officer or Administrator privileges.':
    'İkinci onay için güvenlik sorumlusu veya yönetici yetkisi gerekir.',
  'Access restricted to Security Officers and System Administrators.':
    'Bu alan yalnızca güvenlik sorumluları ve yöneticiler içindir.',
  'Alert ID not found.': 'Uyarı bulunamadı.',

  // Records
  "This client's file is not kept by you.": 'Bu danışanın dosyası sizde tutulmuyor.',
  "Only the client's practitioner can add records": 'Kaydı yalnızca danışanın psikoloğu ekleyebilir.',
  "Only the client's practitioner can correct records": 'Kaydı yalnızca danışanın psikoloğu düzeltebilir.',
  'This role has no access to client records.': 'Bu rolün danışan kayıtlarına erişimi yok.',
  'Access denied to this file.': 'Bu dosyaya erişim izniniz yok.',
  'Invalid patient_id format': 'Danışan numarası geçersiz.',
  'Record not found': 'Kayıt bulunamadı.',
  'Block not found': 'Blok bulunamadı.',
  'Password is required to decrypt this record': 'Bu kaydı açmak için şifre gerekli.',
  'Incorrect password — decryption failed': 'Şifre yanlış; kayıt açılamadı.',
  'Decryption failed: incorrect password': 'Şifre yanlış; kayıt açılamadı.',
  'Decryption failed: password required': 'Kayıt açılamadı: şifre gerekli.',
  'Decryption failed': 'Kayıt açılamadı.',
  'Password-protected records cannot be corrected here': 'Şifreli kayıtlar burada düzeltilemez.',
  'Only clinical records can be corrected': 'Yalnızca klinik kayıtlar düzeltilebilir.',
  'File not found or not stored off-chain': 'Dosya bulunamadı.',
  'Encrypted attachment not found': 'Şifreli ek bulunamadı.',
  'Off-chain download failed': 'Dosya indirilemedi.',
  'Record added to blockchain': 'Kayıt zincire eklendi.',
  'Correction appended. The original record remains on the chain.': 'Düzeltme eklendi. Asıl kayıt zincirde kalır.',

  // Clients
  'Client not found': 'Danışan bulunamadı.',
  "Not one of this practitioner's clients": 'Bu danışan bu psikoloğa kayıtlı değil.',

  // Appointments
  'The practitioner already has an appointment at that time': 'Psikoloğun bu saatte zaten bir randevusu var.',
  'An appointment must start in the future': 'Randevu ileri bir saatte başlamalıdır.',
  'An online appointment takes no room': 'Online randevuya oda atanmaz.',
  'No such room': 'Böyle bir oda yok.',
  'Only a scheduled appointment can be moved': 'Yalnızca planlanmış bir randevu taşınabilir.',
  'An appointment can be marked completed or no-show only after it starts':
    'Randevu ancak başladıktan sonra Geldi veya Gelmedi olarak işaretlenebilir.',
  'Status must be cancelled, completed or no_show': 'Geçersiz randevu durumu.',
  'Appointment not found': 'Randevu bulunamadı.',
  'This secretary account is not linked to a practitioner.': 'Bu sekreter hesabı hiçbir psikoloğa bağlı değil.',
  'Only a practitioner or their secretary can manage appointments.': 'Randevuları yalnızca psikolog veya sekreteri yönetebilir.',
  "You do not run this practitioner's appointment book.": 'Bu psikoloğun randevu defterini siz yönetmiyorsunuz.',
  "You run several practitioners' books: choose one (practitioner=<username>).":
    'Birden fazla psikoloğun randevu defterini yönetiyorsunuz: birini seçin.',
  'Name the practitioner whose book this is.': 'Defterin hangi psikoloğa ait olduğunu seçin.',
  'date must be YYYY-MM-DD': 'Tarih YYYY-AA-GG biçiminde olmalıdır.',
  'Nothing to change': 'Değiştirilecek bir şey yok.',

  // Rooms
  'Room not found': 'Oda bulunamadı.',
  'A room with that name already exists': 'Bu adla bir oda zaten var.',
  'Only those who book appointments, and operators, see the rooms.': 'Odaları yalnızca randevu verenler ve operatörler görebilir.',
  'The name cannot be empty': 'Ad boş olamaz.',

  // Staff links
  'This secretary does not work on this book.': 'Bu sekreter bu defterde çalışmıyor.',
  'This secretary already works on this book.': 'Bu sekreter zaten bu defterde çalışıyor.',
  'You can only change who works on your own book.': 'Yalnızca kendi defterinizde kimin çalıştığını değiştirebilirsiniz.',

  // KVKK
  'An erasure request is already open': 'Bu danışan için zaten açık bir silme talebi var.',
  'Status must be done or rejected': 'Geçersiz talep durumu.',
  'Request not found': 'Talep bulunamadı.',
  'Only practitioners and operators can see erasure requests': 'Silme taleplerini yalnızca psikologlar ve operatörler görebilir.',
  'Only an administrator or security officer may erase a patient': 'Danışan verisini yalnızca yönetici veya güvenlik sorumlusu silebilir.',
  'Erase the client first (POST /api/v1/erasure/{id}, needs dual control)': 'Önce danışanın verisini silin (çift onay gerekir).',

  // Form validation (backend/schemas/requests.py and Pydantic's own)
  'A confidential record needs a password': 'Şifreli kayıt için şifre gerekir.',
  'A protected record needs a protection password': 'Şifreli kayıt için şifre gerekir.',
  'A correction reason is required': 'Düzeltme nedeni zorunludur.',
  'Attachment size exceeds the 2MB limit': 'Ek dosya 2 MB sınırını aşıyor.',
  'Date cannot be in the future': 'Tarih ileri bir gün olamaz.',
  'Date must be a string': 'Tarih geçersiz.',
  'Date must be in ISO 8601 format (e.g., YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)': 'Tarih YYYY-AA-GG biçiminde olmalıdır.',
  'Date must be in ISO 8601 format (e.g., YYYY-MM-DD)': 'Tarih YYYY-AA-GG biçiminde olmalıdır.',
  'Use YYYY-MM-DD': 'Tarih YYYY-AA-GG biçiminde olmalıdır.',
  'Full name must be 2-100 characters': 'Ad soyad 2-100 karakter olmalıdır.',
  'Not a room id': 'Geçersiz oda.',
  'Not a valid e-mail address': 'Geçerli bir e-posta adresi değil.',
  'Phone may contain digits, spaces, ( ) - and a leading +': 'Telefon yalnızca rakam, boşluk, ( ) - ve baştaki + içerebilir.',
  'Planned sessions must be between 1 and 200': 'Planlanan seans sayısı 1 ile 200 arasında olmalıdır.',
  'Presenting problem is required': 'Başvuru nedeni zorunludur.',
  'Session duration must be between 1 and 300 minutes': 'Seans süresi 1 ile 300 dakika arasında olmalıdır.',
  'Session number must be a positive integer': 'Seans numarası pozitif bir tam sayı olmalıdır.',
  'Transcript text is required': 'Seans dökümü metni zorunludur.',
  'Username must be 3-50 chars: letters, digits, . _ -': 'Kullanıcı adı 3-50 karakter olmalı: harf, rakam, . _ -',
  'Username must contain only alphanumeric characters, underscores, dots, or hyphens.':
    'Kullanıcı adı yalnızca harf, rakam, alt çizgi, nokta veya tire içerebilir.',
  'file_data must be base64-encoded': 'Ek dosya okunamadı.',
  'file_name must be 1-255 printable characters': 'Dosya adı 1-255 karakter olmalıdır.',
  'file_type must be a MIME type such as image/png': 'Dosya türü geçersiz.',
  'patient_id must follow format CL-[0-9]{3,} (e.g., CL-001)': 'Danışan numarası CL-001 biçiminde olmalıdır.',
  'starts_at must include a timezone, e.g. 2026-10-01T10:00:00+03:00': 'Randevu saati geçersiz.',
  'Field required': 'Bu alan zorunludur.',
};

const STATUS_TR = {
  cancelled: 'iptal edilmiş', completed: 'Geldi olarak işaretlenmiş', no_show: 'Gelmedi olarak işaretlenmiş',
  scheduled: 'planlanmış', open: 'açık', done: 'tamamlanmış', rejected: 'reddedilmiş',
  PENDING: 'beklemede', APPROVED: 'onaylanmış', EXPIRED: 'süresi dolmuş', CONSUMED: 'kullanılmış',
};

const REQUEST_TYPE_TR = {
  DECRYPT_RAW_RECORD: 'kayıt okuma',
  RESET_ACCOUNT: 'şifre sıfırlama',
  ENABLE_ACCOUNT: 'hesabı yeniden etkinleştirme',
  ERASE_PATIENT: 'danışan verisini silme',
};

const OWN_ACCOUNT_TR = {
  reset: 'Kendi hesabınızı sıfırlayamazsınız.',
  disable: 'Kendi hesabınızı devre dışı bırakamazsınız.',
  enable: 'Kendi hesabınızı etkinleştiremezsiniz.',
};

/* Messages that carry a value. Each rule is [pattern, (match) => Turkish];
 * the captured values are put back as plain text. */
const PATTERNS = [
  [/^The room (.+) is already booked at that time$/, m => `${m[1]} bu saatte dolu.`],
  [/^The room (.+) is no longer in use: choose another room, or No room$/,
    m => `${m[1]} artık kullanılmıyor: başka bir oda seçin ya da “Oda yok”u seçin.`],
  [/^The room (.+) is no longer in use: move the appointment to another room, or choose No room to take it out of the room$/,
    m => `${m[1]} artık kullanılmıyor: randevuyu başka bir odaya taşıyın ya da odadan çıkarmak için “Oda yok”u seçin.`],
  [/^This appointment is already (\w+)$/, m => `Bu randevu zaten ${STATUS_TR[m[1]] || m[1]}.`],
  [/^Duration must be (\d+)-(\d+) minutes$/, m => `Süre ${m[1]}-${m[2]} dakika olmalıdır.`],
  [/^Format must be one of /, () => 'Görüşme biçimi Yüz yüze veya Online olmalıdır.'],
  // A session note's format is typed in, as the stored value.
  [/^Session format must be one of /, () => 'Görüşme biçimi “In-person” (yüz yüze) veya “Online” olmalıdır.'],
  [/^The range must be positive and at most (\d+) days$/, m => `Aralık en fazla ${m[1]} gün olmalıdır.`],
  [/^A room name is 1-(\d+) characters: letters, digits, spaces and \. - \/$/,
    m => `Oda adı 1-${m[1]} karakter olmalı: harf, rakam, boşluk ve . - /`],
  [/^This request is already (\w+)$/, m => `Bu talep zaten ${STATUS_TR[m[1]] || m[1]}.`],
  [/^You cannot (reset|disable|enable) your own account\.$/, m => OWN_ACCOUNT_TR[m[1]]],
  [/^Unauthorized action\. Required roles: /, () => 'Bu işlem için yetkiniz yok.'],
  [/^Role must be one of /, () => 'Geçersiz rol.'],
  [/^Invalid record type: (.+)$/, m => `Geçersiz kayıt türü: ${m[1]}`],
  [/^Unknown request type\. /, () => 'Bilinmeyen talep türü.'],
  [/^Dual-Control co-signature required: request (\S+) for (.+) under Dual-Control Access and have a second operator approve it\.$/,
    m => `Çift onay gerekli: Çift onay sayfasında ${m[2]} için ${REQUEST_TYPE_TR[m[1]] || m[1]} talebi oluşturun ve ikinci bir yetkiliye onaylatın.`],
  [/^Dual-Control Policy Violation: .* for patient (\S+)\. Open Dual-Control Access to request one\.$/,
    m => `Çift onay kuralı: Yetkili operatörler, ikinci onay almadan danışan kayıtlarını (${m[1]}) göremez veya açamaz. İzin istemek için Çift onay sayfasını açın.`],
  [/^Dual-control request created for patient (\S+)\. Awaiting co-signature by Security Officer\.$/,
    m => `${m[1]} için çift onay talebi oluşturuldu. İkinci onay bekleniyor.`],
  [/^Dual-Control request co-signed successfully by (.+)\. Access granted\.$/,
    m => `Çift onay talebi ${m[1]} tarafından onaylandı. Erişim verildi.`],
  [/^Dual-control request is in state (\w+) and cannot be co-signed\.$/,
    m => `Çift onay talebi ${STATUS_TR[m[1]] || m[1]} durumunda; onaylanamaz.`],
  [/^Block #(\d+) not found in chain for patient (.+)$/, m => `${m[2]} dosyasında #${m[1]} numaralı blok bulunamadı.`],
  [/^No blockchain record chain found for patient (.+)$/, m => `${m[1]} için kayıt zinciri bulunamadı.`],
  [/^Alert (.+) acknowledged by (.+)\.$/, m => `Uyarı ${m[2]} tarafından görüldü olarak işaretlendi.`],
  [/^Passkey authentication failed: (.+)$/, m => `Geçiş anahtarıyla (passkey) giriş başarısız: ${translateApiMessage(m[1])}`],
  [/^Passkey registration rejected: (.+)$/, m => `Geçiş anahtarı (passkey) kaydı reddedildi: ${translateApiMessage(m[1])}`],
  [/^Successfully authenticated via Passkey for (.+)$/, m => `${m[1]} geçiş anahtarıyla (passkey) giriş yaptı.`],
  [/^String should have at least (\d+) characters?$/, m => `En az ${m[1]} karakter girin.`],
  [/^String should have at most (\d+) characters?$/, m => `En fazla ${m[1]} karakter girebilirsiniz.`],
  [/^Input should be a valid integer/, () => 'Bir tam sayı girin.'],
  [/^Input should be greater than or equal to (\S+)$/, m => `Değer en az ${m[1]} olmalıdır.`],
  [/^Input should be less than or equal to (\S+)$/, m => `Değer en fazla ${m[1]} olmalıdır.`],
  [/^Validation failed: (.+)$/s, m => `Girilen bilgiler geçersiz: ${translateValidationList(m[1])}`],
];

// Pydantic prefixes a validator's message with "Value error, ".
function stripPydanticPrefix(text) {
  return text.replace(/^(Value|Assertion) error, /, '');
}

// "Validation failed: data.summary: Value error, X, title: Field required"
// — the field paths are code names, so only the messages are kept.
function translateValidationList(list) {
  const items = [...list.matchAll(/[A-Za-z0-9_.]+: (.*?)(?=, [A-Za-z0-9_.]+: |$)/gs)].map(m => m[1]);
  if (!items.length) return translateApiMessage(list);
  return items.map(item => {
    const text = translateApiMessage(item);
    return /[.!?]$/.test(text) ? text : `${text}.`;
  }).join(' ');
}

/* One API message (a `detail` or a `message` string) in Turkish, or the
 * original text when it is not in the table. */
export function translateApiMessage(text) {
  if (typeof text !== 'string' || !text) return text;
  const clean = stripPydanticPrefix(text.trim());
  if (Object.prototype.hasOwnProperty.call(EXACT, clean)) return EXACT[clean];
  for (const [pattern, render] of PATTERNS) {
    const m = clean.match(pattern);
    if (m) return render(m);
  }
  return text;
}

/* An error body's `detail` as one line of text: a string as it is, or
 * FastAPI's list of validation errors ([{loc, msg}, ...]) as their messages. */
export function apiDetailText(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map(item => (item && typeof item.msg === 'string') ? translateApiMessage(item.msg) : '')
      .filter(Boolean);
    return [...new Set(parts)].join(' ');
  }
  return '';
}

/* The Turkish text to show for an error body's `detail`; `fallback` when
 * the body carries none. */
export function apiErrorMessage(detail, fallback = 'Bir hata oluştu.') {
  const text = apiDetailText(detail);
  return text ? translateApiMessage(text) : fallback;
}
