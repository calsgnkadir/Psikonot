"""
tests/test_frontend_messages.py — the API's English messages, shown in Turkish
==============================================================================
The interface is Turkish, the API answers in English. The frontend module
backend/static/js/modules/messages.js maps the messages a user actually sees:
an exact table, patterns for messages that carry a name, and the original text
for anything unknown. These tests load the module in Node (as an ES module, the
same way test_frontend_syntax.py checks it) and run it on real API messages.

Also checked statically: the page declares lang="tr", and every API error the
UI shows passes through the mapping (apiFetch builds its Error from it).

The Node part is skipped when Node.js is not installed (it is on CI).
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "backend" / "static"
MESSAGES_JS = STATIC / "js" / "modules" / "messages.js"
NODE = shutil.which("node")

# Each case: (function, argument, expected result).
CASES = [
    # exact matches
    ("translateApiMessage", "Incorrect username or password", "Kullanıcı adı veya şifre hatalı."),
    ("translateApiMessage", "Too many attempts. Please wait 1 minute.",
     "Çok fazla deneme yapıldı. Lütfen 1 dakika bekleyin."),
    ("translateApiMessage", "The practitioner already has an appointment at that time",
     "Psikoloğun bu saatte zaten bir randevusu var."),
    ("translateApiMessage", "This client's file is not kept by you.", "Bu danışanın dosyası sizde tutulmuyor."),
    ("translateApiMessage", "CSRF token validation failed — missing or mismatched token",
     "Güvenlik doğrulaması başarısız oldu. Sayfayı yenileyip tekrar deneyin."),
    # a message that carries a name
    ("translateApiMessage", "The room Oda 3 is already booked at that time", "Oda 3 bu saatte dolu."),
    ("translateApiMessage", "You cannot disable your own account.", "Kendi hesabınızı devre dışı bırakamazsınız."),
    # a name that looks like markup stays plain text (the caller escapes it)
    ("translateApiMessage", "The room <b>X</b> is already booked at that time", "<b>X</b> bu saatte dolu."),
    # intake
    ("translateApiMessage", "An application cannot go from waitlisted to contacted",
     "Başvuru “Bekleme listesinde” durumundan “Arandı” durumuna geçemez."),
    ("translateApiMessage", "Assign the application to a practitioner first", "Önce başvuruyu bir psikoloğa atayın."),
    ("translateApiMessage", "Value error, A name may contain only letters, spaces and . ' -",
     "Ad soyad yalnızca harf, boşluk ve . ' - içerebilir."),
    ("translateApiMessage", "Unknown time of day", "Geçersiz seçim."),
    # weekly series
    ("translateApiMessage", "A series has 1-52 sessions", "Bir seri en az 1, en fazla 52 seanstan oluşur."),
    ("translateApiMessage",
     "Some sessions of the series are no longer free: 2031-03-11 10:00, 2031-03-18 10:00. Preview the series again.",
     "Serinin şu seansları artık boş değil: 2031-03-11 10:00, 2031-03-18 10:00. "
     "Hiçbir randevu verilmedi; seriyi yeniden önizleyin."),
    ("translateApiMessage", "This appointment is not part of a series", "Bu randevu bir serinin parçası değil."),
    # unknown messages come back unchanged
    ("translateApiMessage", "Some brand-new server message", "Some brand-new server message"),
    # Pydantic's prefix and list form
    ("translateApiMessage", "Value error, Full name must be 2-100 characters", "Ad soyad 2-100 karakter olmalıdır."),
    ("apiErrorMessage", [{"loc": ["body", "full_name"], "msg": "Value error, Full name must be 2-100 characters"},
                         {"loc": ["body", "phone"], "msg": "Field required"}],
     "Ad soyad 2-100 karakter olmalıdır. Bu alan zorunludur."),
    ("apiErrorMessage", None, "Bir hata oluştu."),
    ("translateApiMessage", "Validation failed: data.session_number: Value error, Session number must be a positive integer",
     "Girilen bilgiler geçersiz: Seans numarası pozitif bir tam sayı olmalıdır."),
    # messages a user can see that the first table missed
    ("translateApiMessage", "Value error, Session format must be one of In-person, Online",
     "Görüşme biçimi Yüz yüze veya Online olmalıdır."),
    ("translateApiMessage", "Dual-Control Violation: An account cannot approve a request about itself.",
     "Çift onay kuralı: Bir hesap kendisiyle ilgili bir talebi onaylayamaz."),
    ("translateApiMessage", "Validity must be between 5 and 120 minutes.",
     "Geçerlilik süresi 5 ile 120 dakika arasında olmalıdır."),
    ("translateApiMessage", "Passkey credential abc123 revoked successfully for user psk.elif.",
     "psk.elif kullanıcısının geçiş anahtarı (passkey) kaldırıldı."),
    ("translateApiMessage", "Deliver the enrollment token to the account holder out of band.",
     "Davet kodunu hesap sahibine yüz yüze veya ayrı bir güvenli kanaldan iletin."),
]


@unittest.skipUnless(NODE, "Node.js is not installed")
class TestMessageMapping(unittest.TestCase):
    def test_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copyfile(MESSAGES_JS, Path(tmp) / "messages.mjs")
            script = Path(tmp) / "run.mjs"
            script.write_text(
                "import * as m from './messages.mjs';\n"
                f"const cases = {json.dumps([[f, a] for f, a, _ in CASES])};\n"
                "process.stdout.write(JSON.stringify(cases.map(([f, a]) => m[f](a ?? undefined))));\n",
                encoding="utf-8")
            result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = json.loads(result.stdout)
        for (func, arg, expected), got in zip(CASES, outputs):
            with self.subTest(func=func, arg=arg):
                self.assertEqual(got, expected)


class TestTurkishPage(unittest.TestCase):
    def test_page_language_is_turkish(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="tr">', html)

    def test_api_errors_go_through_the_mapping(self):
        utils = (STATIC / "js" / "modules" / "utils.js").read_text(encoding="utf-8")
        self.assertIn("from './messages.js'", utils)
        self.assertIn("apiErrorMessage(json.detail)", utils)

    def test_dates_are_formatted_in_turkish(self):
        """No view formats a date or time in an English locale any more."""
        for path in sorted((STATIC / "js").rglob("*.js")):
            if "vendor" in path.parts:
                continue
            src = path.read_text(encoding="utf-8")
            for locale in ("'en-GB'", "'en-US'"):
                with self.subTest(file=path.name, locale=locale):
                    self.assertNotIn(locale, src)


if __name__ == "__main__":
    unittest.main()
