import io
import json
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
from dotenv import load_dotenv
from fpdf import FPDF
from json_repair import repair_json
from openai import OpenAI
from pypdf import PdfReader

# .env dosyasındaki anahtarı okur
load_dotenv()
API_KEY = os.getenv("NVIDIA_API_KEY")
BASE_URL = "https://integrate.api.nvidia.com/v1"

# Kullanılan model (değiştirmek istersen sadece bu satırı düzenle)
MODEL = "nvidia/nemotron-3.5-bolt-e4b"

# Çok uzun notlar yavaşlatır, bu yüzden bir üst sınır koyuyoruz
MAX_KARAKTER = 15000

SECENEK_OZET = "📝 Sadece özet"
SECENEK_SORU = "❓ Sadece quiz"
SECENEK_IKISI = "📚 İkisi birden"

HARFLER = "ABCD"

TUR_ETIKET = {
    "mc": "🔤 Çoktan seçmeli",
    "dy": "⚖️ Doğru mu, yanlış mı?",
    "acik": "✍️ Klasik soru",
}

DOGRU_MESAJLARI = [
    "🎉 Harika!",
    "💪 Tam isabet!",
    "🔥 Süpersin!",
    "✅ Doğru!",
    "🧠 Bildin!",
]
YANLIS_MESAJLARI = [
    "😅 Bu sefer olmadı.",
    "🤔 Az kalsın!",
    "📌 Not al, bir dahaki sefere sende!",
]

# Sohbet bölümündeki hazır butonlar: (buton yazısı, modele gidecek soru)
SOHBET_HIZLI = [
    ("🧒 Daha basit anlat", "Bu konuyu daha basit, günlük hayattan örneklerle anlat."),
    ("💡 Örnek ver", "Bu notlardaki en önemli kavram için somut bir örnek ver."),
    (
        "🎯 Sınavda ne çıkar?",
        "Bu notlara göre sınavda en çok hangi konuların çıkması muhtemel? "
        "Kısaca listele.",
    ),
]

# Çıktı dilleri: modele verilen talimat + PDF başlıkları ve etiketleri
DILLER = {
    "Notla aynı dil (otomatik)": {
        "talimat": "Write everything in the same language as the student's "
        "notes. Do not translate the notes into another language.",
        "basliklar": ("Study Guide", "Quiz Questions", "Answer Key"),
        "bolumler": ("Multiple Choice", "True / False", "Open-Ended Questions"),
        "dy": ("True", "False"),
    },
    "Türkçe": {
        "talimat": "Write everything in Turkish.",
        "basliklar": ("Ders Özeti", "Test Soruları", "Cevap Anahtarı"),
        "bolumler": ("Çoktan Seçmeli", "Doğru / Yanlış", "Klasik Sorular"),
        "dy": ("Doğru", "Yanlış"),
    },
    "English": {
        "talimat": "Write everything in English.",
        "basliklar": ("Study Guide", "Quiz Questions", "Answer Key"),
        "bolumler": ("Multiple Choice", "True / False", "Open-Ended Questions"),
        "dy": ("True", "False"),
    },
    "Deutsch": {
        "talimat": "Write everything in German.",
        "basliklar": ("Zusammenfassung", "Testfragen", "Lösungen"),
        "bolumler": ("Multiple Choice", "Richtig / Falsch", "Offene Fragen"),
        "dy": ("Richtig", "Falsch"),
    },
    "Français": {
        "talimat": "Write everything in French.",
        "basliklar": ("Résumé", "Questions du test", "Corrigé"),
        "bolumler": ("QCM", "Vrai / Faux", "Questions ouvertes"),
        "dy": ("Vrai", "Faux"),
    },
    "Español": {
        "talimat": "Write everything in Spanish.",
        "basliklar": ("Resumen", "Preguntas de prueba", "Respuestas"),
        "bolumler": ("Opción múltiple", "Verdadero / Falso", "Preguntas abiertas"),
        "dy": ("Verdadero", "Falso"),
    },
}

TONLAR = {
    "Eğlenceli ve motive edici": "warm, playful and encouraging; light humor, "
    "vivid analogies, talk directly to the student, but stay accurate",
    "Standart": "clear, friendly and neutral",
    "Sınav odaklı": "serious and exam-focused; precise terminology, "
    "emphasize what is most likely to be tested",
}

# PDF için Türkçe karakterleri destekleyen yazı tipleri (sırayla aranır)
FONT_ADAYLARI = [
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
]

EMOJI = re.compile("[\U00010000-\U0010FFFF\u2600-\u27BF\uFE0F\u200d]")

# "Eğim veya eğim" gibi aynı kelimenin tekrarlandığı ifadeleri yakalar
TEKRARLI_IFADE = re.compile(
    r"\b(\w+)\s+(?:veya|ya da|ve|or|and|oder|und|ou|et|o|y)\s+\1\b",
    re.IGNORECASE,
)

st.set_page_config(page_title="Ders Notu Asistanı", page_icon="📚")

if not API_KEY:
    st.error(
        "API anahtarı bulunamadı. Proje klasöründe `.env` dosyası olduğundan "
        "ve içinde NVIDIA_API_KEY=... satırı bulunduğundan emin ol."
    )
    st.stop()

client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=180.0)


# ---------------- PDF okuma ve sayfa aralığı ----------------
@st.cache_data(show_spinner="PDF okunuyor...")
def pdf_sayfalarini_oku(veri):
    """PDF'in her sayfasındaki yazıyı çıkarır (bir kez okunur, hafızada kalır)."""
    okuyucu = PdfReader(io.BytesIO(veri))
    return [sayfa.extract_text() or "" for sayfa in okuyucu.pages]


def sayfa_araligi_coz(metin, toplam):
    """'5-15, 20' -> sayfa numaralarının listesi (0'dan başlayan). Boşsa hepsi."""
    metin = metin.strip()
    if not metin:
        return list(range(toplam))

    hata = ValueError(
        f"Sayfa aralığı geçersiz. 1 ile {toplam} arasında olmalı, "
        "örneğin 5-15 ya da 1-3, 8."
    )
    sayfalar = set()
    for parca in metin.split(","):
        parca = parca.strip()
        if not parca:
            continue
        try:
            if "-" in parca:
                bas_metin, son_metin = parca.split("-", 1)
                bas, son = int(bas_metin), int(son_metin)
            else:
                bas = son = int(parca)
        except ValueError:
            raise hata
        if bas < 1 or son < bas or son > toplam:
            raise hata
        sayfalar.update(range(bas - 1, son))

    if not sayfalar:
        raise hata
    return sorted(sayfalar)


# ---------------- Yapay zekâ yardımcıları ----------------
def sor(model, sistem_mesaji, kullanici_mesaji, sicaklik=0.6):
    """Modele bir istek gönderir ve cevabın metnini döndürür."""
    cevap = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sistem_mesaji},
            {"role": "user", "content": kullanici_mesaji},
        ],
        temperature=sicaklik,
        max_tokens=4096,
    )
    secim = cevap.choices[0]
    metin = secim.message.content
    if not metin:
        raise ValueError(
            f"Model boş cevap döndürdü (sebep: {secim.finish_reason}). "
            "Lütfen tekrar dene."
        )
    return metin.strip()


def ozet_istemi(ton, dil, notlar):
    sistem = (
        "You are a friendly, energetic tutor who makes studying enjoyable. "
        f"{dil} Facts must come only from the student's notes; never invent "
        "facts. Analogies and everyday examples may be your own."
    )
    kullanici = (
        "Turn the notes below into an engaging study guide.\n"
        f"Tone: {ton}.\n\n"
        "Use exactly this structure, with markdown headings (##) that each "
        "start with a fitting emoji:\n"
        "## The Big Idea - 2-3 sentences: a hook explaining why this topic "
        "matters.\n"
        "## Key Concepts - bullet list. For each concept: bold name, a "
        "plain-language explanation, and a real-life analogy or example.\n"
        "## Memory Tricks - 2-3 mnemonics, mental images or comparisons for "
        "the hardest points.\n"
        "## Common Mistakes - 2-3 misconceptions students often have, each "
        "with the correction.\n"
        "## Quick Recap - 3-5 one-line takeaways.\n\n"
        "Do not use tables. Translate the section titles into the output "
        "language. No filler.\n\n"
        f"NOTES:\n{notlar}"
    )
    return sistem, kullanici


def soru_istemi(ton, dil, notlar, coktan, dogru_yanlis, klasik):
    """Modelden soruları JSON olarak ister (etkileşimli quiz için)."""
    sistem = (
        "You are an experienced, creative teacher who writes great exam "
        f"questions. {dil} Questions must be based only on the student's "
        "notes. You reply with valid JSON only."
    )

    sema = []
    kurallar = []

    if coktan:
        sema.append(
            '"multiple_choice": [{"question": "...", "options": ["...", '
            '"...", "...", "..."], "correct_index": 0, "explanation": "..."}]'
        )
        kurallar.append(
            f"- multiple_choice: exactly {coktan} items. 'options' has "
            "exactly 4 strings WITHOUT letter prefixes. Wrong options must be "
            "plausible, based on common misconceptions. Every option must be "
            "clearly different from the others. Vary correct_index "
            "between 0 and 3. 'explanation' is 1-2 sentences: why the answer "
            "is right and why the most tempting wrong option is wrong."
        )

    if dogru_yanlis:
        sema.append(
            '"true_false": [{"statement": "...", "answer": true, '
            '"explanation": "..."}]'
        )
        kurallar.append(
            f"- true_false: exactly {dogru_yanlis} items. Each statement is "
            "judged true or false by the student; never reveal the answer in "
            "the statement. About half should be true and half false. False "
            "statements must be subtle, plausible distortions of facts in the "
            "notes, not absurd. 'answer' is the JSON boolean true or false. "
            "'explanation' is 1 sentence (for false statements, give the "
            "correct fact)."
        )

    if klasik:
        sema.append(
            '"open_ended": [{"question": "...", "model_answer": "..."}]'
        )
        kurallar.append(
            f"- open_ended: exactly {klasik} items. Questions that require "
            "explaining, comparing or applying a concept in the student's own "
            "words. 'model_answer' is 2-4 sentences covering the key points."
        )

    sema_metni = "{" + ", ".join(sema) + "}"

    kullanici = (
        "Create a quiz from the notes below.\n"
        f"Tone: {ton}.\n\n"
        "Return ONLY one valid JSON object with exactly this structure. The "
        "keys stay in English; all text values must be in the output "
        f"language:\n{sema_metni}\n\n"
        "Rules:\n"
        + "\n".join(kurallar)
        + "\n"
        "- Within each list, order from easy to hard.\n"
        "- Mix levels: recall, understanding, short real-life scenarios, "
        "cause-and-effect.\n"
        "- Never write the same word twice as a pair like 'X or X'. If two "
        "terms from the notes mean the same thing in the output language, "
        "use only one of them.\n"
        "- Never use the straight double-quote character inside text values; "
        "use single quotes or typographic quotes instead.\n"
        "- Keep every explanation short.\n"
        "- No markdown, no code fences, no text before or after the JSON.\n\n"
        f"NOTES:\n{notlar}"
    )
    return sistem, kullanici


def json_ayikla(metin):
    """Model cevabından JSON'u çıkarır; bozuksa onarmayı dener."""
    bas = metin.find("{")
    if bas == -1:
        raise ValueError("Cevapta JSON bulunamadı.")
    son = metin.rfind("}")
    parca = metin[bas : son + 1] if son > bas else metin[bas:]

    try:
        return json.loads(parca)
    except json.JSONDecodeError:
        veri = repair_json(parca, return_objects=True)
        if not isinstance(veri, dict) or not veri:
            raise ValueError("JSON okunamadı.")
        return veri


def tekrar_temizle(metin):
    """'Eğim veya eğim' gibi tekrarlı ifadeleri tek kelimeye indirir."""
    return TEKRARLI_IFADE.sub(r"\1", metin)


def sorulari_duzenle(veri, coktan, dogru_yanlis, klasik):
    """JSON'daki soruları kontrol eder ve uygulamanın kullanacağı biçime çevirir."""
    if not isinstance(veri, dict):
        return []
    sorular = []

    for k in (veri.get("multiple_choice") or [])[:coktan]:
        try:
            siklar = [
                tekrar_temizle(re.sub(r"^[A-Da-d][\)\.]\s*", "", str(s).strip()))
                for s in k["options"]
            ][:4]
            dogru = int(k["correct_index"])
            soru = tekrar_temizle(str(k["question"]).strip())
            if len(siklar) < 2 or not 0 <= dogru < len(siklar) or not soru:
                continue
            # İki şık birbirinin aynıysa soruyu alma
            if len({s.lower() for s in siklar}) < len(siklar):
                continue
            sorular.append(
                {
                    "tur": "mc",
                    "soru": soru,
                    "siklar": siklar,
                    "dogru": dogru,
                    "aciklama": str(k.get("explanation", "")).strip(),
                }
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            continue

    for k in (veri.get("true_false") or [])[:dogru_yanlis]:
        try:
            cevap = k["answer"]
            if isinstance(cevap, str):
                cevap = cevap.strip().lower() in ("true", "doğru", "dogru", "evet")
            soru = tekrar_temizle(str(k["statement"]).strip())
            if not soru:
                continue
            sorular.append(
                {
                    "tur": "dy",
                    "soru": soru,
                    "dogru": bool(cevap),
                    "aciklama": str(k.get("explanation", "")).strip(),
                }
            )
        except (KeyError, TypeError, AttributeError):
            continue

    for k in (veri.get("open_ended") or [])[:klasik]:
        try:
            soru = tekrar_temizle(str(k["question"]).strip())
            ornek = str(k["model_answer"]).strip()
            if not soru or not ornek:
                continue
            sorular.append({"tur": "acik", "soru": soru, "ornek": ornek})
        except (KeyError, TypeError, AttributeError):
            continue

    return sorular


def soru_uret(ton, dil, notlar, coktan, dogru_yanlis, klasik):
    """Bir soru türünü üretir; JSON bozuk gelirse bir kez daha dener."""
    sistem, kullanici = soru_istemi(ton, dil, notlar, coktan, dogru_yanlis, klasik)
    son_hata = None
    for _ in range(2):
        try:
            ham = sor(MODEL, sistem, kullanici, sicaklik=0.3)
            veri = json_ayikla(ham)
            sorular = sorulari_duzenle(veri, coktan, dogru_yanlis, klasik)
            if sorular:
                return sorular
            son_hata = ValueError("Modelden geçerli soru gelmedi.")
        except Exception as hata:
            son_hata = hata
            if "410" in str(hata) or "404" in str(hata):
                raise
    raise ValueError(f"Sorular hazırlanamadı, lütfen tekrar dene. ({son_hata})")


def goster(metin):
    """Satır sonlarını koruyarak markdown gösterir."""
    st.markdown(metin.replace("\n", "  \n"))


# ---------------- Notuna soru sor (sohbet) ----------------
def sohbet_akisi(gecmis, notlar):
    """Sohbet cevabını yazıldıkça (parça parça) döndürür."""
    sistem = (
        "You are a patient, friendly tutor helping a student understand "
        "their lecture notes. Answer mainly from the notes below. If the "
        "answer is not in the notes, say so clearly, then you may help with "
        "general knowledge and mark it as outside the notes. Explain "
        "simply, use short examples, and keep answers concise (under about "
        "200 words unless asked for more). Reply in the language of the "
        "student's latest message.\n\n"
        f"NOTES:\n{notlar}"
    )
    stream = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": sistem}] + gecmis[-10:],
        temperature=0.5,
        max_tokens=2048,
        stream=True,
    )
    for parca in stream:
        if not parca.choices:
            continue
        metin = parca.choices[0].delta.content
        if metin:
            yield metin


def hizli_soru(metin):
    st.session_state["bekleyen_soru"] = metin


# ---------------- Etkileşimli quiz ----------------
def quiz_baslat(sorular, tum=None):
    sayac = st.session_state.get("quiz_sayac", 0) + 1
    st.session_state["quiz_sayac"] = sayac
    st.session_state["quiz"] = {
        "sorular": sorular,
        "tum": tum or sorular,
        "i": 0,
        "puan": 0,
        "asama": "soru",  # "soru" = cevap bekleniyor, "geri" = geri bildirim
        "son_dogru": None,
        "mesaj": "",
        "yanlis_idx": [],
        "tur": sayac,
        "balon": False,
    }


def dogru_cevap_metni(q):
    if q["tur"] == "mc":
        return f"{HARFLER[q['dogru']]}) {q['siklar'][q['dogru']]}"
    if q["tur"] == "dy":
        return "Doğru" if q["dogru"] else "Yanlış"
    return q["ornek"]


def kontrol_et():
    quiz = st.session_state["quiz"]
    i = quiz["i"]
    q = quiz["sorular"][i]
    if q["tur"] != "acik":
        secim = st.session_state.get(f"cevap_{quiz['tur']}_{i}")
        dogru = secim == q["dogru"]
        quiz["son_dogru"] = dogru
        quiz["mesaj"] = random.choice(DOGRU_MESAJLARI if dogru else YANLIS_MESAJLARI)
        if dogru:
            quiz["puan"] += 1
        else:
            quiz["yanlis_idx"].append(i)
    quiz["asama"] = "geri"


def ileri(ekle=0):
    quiz = st.session_state["quiz"]
    i = quiz["i"]
    if quiz["sorular"][i]["tur"] == "acik":
        if ekle:
            quiz["puan"] += 1
        else:
            quiz["yanlis_idx"].append(i)
    quiz["i"] += 1
    quiz["asama"] = "soru"


def tekrar_hepsi():
    quiz = st.session_state["quiz"]
    quiz_baslat(quiz["tum"])


def tekrar_yanlislar():
    quiz = st.session_state["quiz"]
    yanlislar = [quiz["sorular"][k] for k in quiz["yanlis_idx"]]
    quiz_baslat(yanlislar, quiz["tum"])


def sonuc_ekrani(quiz, n):
    puan = quiz["puan"]
    yuzde = round(100 * puan / n)

    if yuzde >= 90:
        mesaj = "🏆 Muhteşem! Bu konuda tam bir ustasın."
    elif yuzde >= 70:
        mesaj = "🎯 Çok iyi! Birkaç noktayı tekrar edersen mükemmel olur."
    elif yuzde >= 50:
        mesaj = "💪 Fena değil! Özeti bir kez daha oku ve yanlışlarına bak."
    else:
        mesaj = "📚 Başlangıç böyle olur! Özeti tekrar okuyup yeniden dene, yapabilirsin."

    if yuzde >= 80 and not quiz["balon"]:
        st.balloons()
        quiz["balon"] = True

    st.markdown(f"## 🏁 Sonuç: {puan} / {n}  (%{yuzde})")
    st.progress(puan / n)
    st.write(mesaj)

    if quiz["yanlis_idx"]:
        with st.expander(
            f"🔍 Tekrar etmen gereken {len(quiz['yanlis_idx'])} soru", expanded=True
        ):
            for k in quiz["yanlis_idx"]:
                q = quiz["sorular"][k]
                st.markdown(f"**{q['soru']}**")
                st.write(f"✅ Doğru cevap: {dogru_cevap_metni(q)}")
                if q.get("aciklama"):
                    st.caption(f"💡 {q['aciklama']}")
                st.divider()

    sol, sag = st.columns(2)
    sol.button("🔁 Baştan çöz", on_click=tekrar_hepsi)
    if quiz["yanlis_idx"]:
        sag.button("🎯 Sadece yanlışlarımı çöz", on_click=tekrar_yanlislar)


def quiz_goster():
    quiz = st.session_state["quiz"]
    sorular = quiz["sorular"]
    n = len(sorular)
    i = quiz["i"]

    if i >= n:
        sonuc_ekrani(quiz, n)
        return

    q = sorular[i]
    geri = quiz["asama"] == "geri"
    anahtar = f"cevap_{quiz['tur']}_{i}"

    st.progress(i / n, text=f"Soru {i + 1} / {n}  •  Puan: {quiz['puan']}")
    st.caption(TUR_ETIKET[q["tur"]])
    st.markdown(f"#### {q['soru']}")

    secim = None
    if q["tur"] == "mc":
        secim = st.radio(
            "Cevabını seç:",
            options=list(range(len(q["siklar"]))),
            format_func=lambda k: f"{HARFLER[k]}) {q['siklar'][k]}",
            index=None,
            key=anahtar,
            disabled=geri,
        )
    elif q["tur"] == "dy":
        secim = st.radio(
            "Sence bu ifade:",
            options=[True, False],
            format_func=lambda v: "✅ Doğru" if v else "❌ Yanlış",
            index=None,
            key=anahtar,
            disabled=geri,
        )
    else:
        st.text_area(
            "Cevabını yaz (istersen kafandan da cevaplayabilirsin):",
            key=anahtar,
            disabled=geri,
            height=120,
        )

    if not geri:
        if q["tur"] == "acik":
            st.button("👀 Örnek cevabı göster", on_click=kontrol_et, type="primary")
        else:
            st.button(
                "Kontrol et ✔️",
                on_click=kontrol_et,
                type="primary",
                disabled=secim is None,
            )
        return

    # Geri bildirim aşaması
    son_soru = i == n - 1
    if q["tur"] == "acik":
        st.info(f"📖 **Örnek cevap:** {q['ornek']}")
        st.write("Cevabın örnek cevaba yakın mıydı?")
        s1, s2 = st.columns(2)
        s1.button(
            "✅ Evet, yaptım", on_click=ileri, args=(1,), key=f"evet_{anahtar}"
        )
        s2.button(
            "🔄 Eksiğim vardı", on_click=ileri, args=(0,), key=f"hayir_{anahtar}"
        )
    else:
        if quiz["son_dogru"]:
            st.success(quiz["mesaj"])
        else:
            st.error(f"{quiz['mesaj']} Doğru cevap: **{dogru_cevap_metni(q)}**")
        if q.get("aciklama"):
            st.info(f"💡 {q['aciklama']}")
        st.button(
            "Sonuçları gör 🏁" if son_soru else "Sonraki soru →",
            on_click=ileri,
            type="primary",
            key=f"ileri_{anahtar}",
        )


# ---------------- PDF oluşturma ----------------
def sorulari_metne_cevir(sorular, dil):
    """Quiz sorularını PDF için düz metne (soru + cevap anahtarı) çevirir."""
    mc_ad, dy_ad, acik_ad = dil["bolumler"]
    dogru_et, yanlis_et = dil["dy"]
    tur_ad = {"mc": mc_ad, "dy": dy_ad, "acik": acik_ad}

    soru_satirlari = []
    cevap_satirlari = []

    for tur in ("mc", "dy", "acik"):
        grup = [q for q in sorular if q["tur"] == tur]
        if not grup:
            continue
        soru_satirlari += ["", f"## {tur_ad[tur]}"]
        cevap_satirlari += ["", f"## {tur_ad[tur]}"]

        for no, q in enumerate(grup, 1):
            soru_satirlari.append(f"**{no}.** {q['soru']}")
            if tur == "mc":
                for k, sik in enumerate(q["siklar"]):
                    soru_satirlari.append(f"{HARFLER[k]}) {sik}")
                cevap = f"{HARFLER[q['dogru']]}) {q['siklar'][q['dogru']]}"
            elif tur == "dy":
                soru_satirlari.append(f"( ) {dogru_et}      ( ) {yanlis_et}")
                cevap = dogru_et if q["dogru"] else yanlis_et
            else:
                soru_satirlari += ["", "", ""]
                cevap = q["ornek"]

            aciklama = q.get("aciklama")
            cevap_satirlari.append(
                f"**{no}.** {cevap}" + (f" - {aciklama}" if aciklama else "")
            )
            soru_satirlari.append("")

    return "\n".join(soru_satirlari).strip(), "\n".join(cevap_satirlari).strip()


def font_bul():
    for normal, kalin in FONT_ADAYLARI:
        if os.path.exists(normal) and os.path.exists(kalin):
            return normal, kalin
    return None


def temizle(metin):
    """PDF'in gösteremeyeceği emojileri ve sorun çıkaran işaretleri temizler."""
    metin = EMOJI.sub("", metin)
    return metin.replace("--", "–").replace("__", "_")


def satir_yaz(pdf, satir, girinti=0):
    # Tek yıldızlı italikleri düz yazıya çevir (**kalın** olanlara dokunma)
    satir = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)", r"\1", satir)
    pdf.set_x(pdf.l_margin + girinti)
    try:
        pdf.multi_cell(0, 6, satir, markdown=True, new_x="LMARGIN", new_y="NEXT")
    except Exception:
        pdf.set_x(pdf.l_margin + girinti)
        pdf.multi_cell(
            0, 6, satir.replace("**", ""), new_x="LMARGIN", new_y="NEXT"
        )


def markdown_yaz(pdf, metin):
    for satir in temizle(metin).splitlines():
        satir = satir.rstrip()
        if not satir.strip():
            pdf.ln(3)
            continue
        if re.fullmatch(r"\s*[-*_]{3,}\s*", satir):
            pdf.ln(2)
            continue

        baslik = re.match(r"^\s*#{1,6}\s*(.*)$", satir)
        if baslik:
            pdf.ln(2)
            pdf.set_font("Govde", "B", 13)
            pdf.multi_cell(
                0, 7, baslik.group(1).replace("**", ""),
                new_x="LMARGIN", new_y="NEXT",
            )
            pdf.set_font("Govde", "", 11)
            continue

        madde = re.match(r"^\s*[-*•]\s+(.*)$", satir)
        if madde:
            satir_yaz(pdf, "• " + madde.group(1), girinti=5)
        else:
            satir_yaz(pdf, satir)


def pdf_olustur(baslik, bolumler):
    """bolumler: [(alt_baslik veya None, metin), ...]. PDF baytlarını döndürür."""
    fontlar = font_bul()
    if not fontlar:
        return None
    normal, kalin = fontlar

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_font("Govde", "", normal)
    pdf.add_font("Govde", "B", kalin)
    pdf.add_font("Govde", "I", normal)
    pdf.add_font("Govde", "BI", kalin)
    pdf.add_page()

    pdf.set_font("Govde", "B", 18)
    pdf.multi_cell(0, 10, temizle(baslik), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Govde", "", 11)

    for i, (alt_baslik, metin) in enumerate(bolumler):
        if i > 0:
            pdf.add_page()
        if alt_baslik:
            pdf.set_font("Govde", "B", 16)
            pdf.multi_cell(
                0, 9, temizle(alt_baslik), new_x="LMARGIN", new_y="NEXT"
            )
            pdf.ln(3)
            pdf.set_font("Govde", "", 11)
        markdown_yaz(pdf, metin)

    return bytes(pdf.output())


def pdf_paketi(ozet, sorular, dil):
    """Özet PDF'i ve soru PDF'ini (cevap anahtarı son sayfada) hazırlar."""
    ozet_baslik, soru_baslik, cevap_baslik = dil["basliklar"]
    ozet_pdf = sorular_pdf = None
    if ozet:
        ozet_pdf = pdf_olustur(ozet_baslik, [(None, ozet)])
    if sorular:
        soru_metni, cevap_metni = sorulari_metne_cevir(sorular, dil)
        sorular_pdf = pdf_olustur(
            soru_baslik, [(None, soru_metni), (cevap_baslik, cevap_metni)]
        )
    return ozet_pdf, sorular_pdf


# ---------------- Çalışmayı kaydet / yükle ----------------
def yedek_olustur():
    """Mevcut çalışmayı (özet, sorular, notlar, sohbet) JSON olarak paketler."""
    sonuc = st.session_state["sonuc"]
    quiz = st.session_state.get("quiz")
    veri = {
        "surum": 1,
        "dil_adi": sonuc.get("dil_adi"),
        "ozet": sonuc.get("ozet"),
        "sorular": quiz["tum"] if quiz else [],
        "notlar": st.session_state.get("sohbet_notlari", ""),
        "sohbet": st.session_state.get("sohbet", []),
    }
    return json.dumps(veri, ensure_ascii=False, indent=2).encode("utf-8")


def soru_gecerli_mi(q):
    """Yedek dosyasındaki bir sorunun bozuk olup olmadığını kontrol eder."""
    try:
        if not isinstance(q, dict) or not str(q["soru"]).strip():
            return False
        if q["tur"] == "mc":
            return (
                isinstance(q["siklar"], list)
                and 2 <= len(q["siklar"]) <= 4
                and isinstance(q["dogru"], int)
                and 0 <= q["dogru"] < len(q["siklar"])
            )
        if q["tur"] == "dy":
            return isinstance(q["dogru"], bool)
        if q["tur"] == "acik":
            return bool(str(q["ornek"]).strip())
    except (KeyError, TypeError, ValueError):
        return False
    return False


def yedekten_yukle(dosya):
    """Kaydedilmiş çalışmayı geri yükler."""
    veri = json.loads(dosya.getvalue().decode("utf-8"))
    if not isinstance(veri, dict):
        raise ValueError("Geçersiz yedek dosyası.")

    dil_adi_yedek = veri.get("dil_adi")
    if dil_adi_yedek not in DILLER:
        dil_adi_yedek = "Notla aynı dil (otomatik)"
    dil = DILLER[dil_adi_yedek]

    ozet = veri.get("ozet") or None
    sorular = [q for q in (veri.get("sorular") or []) if soru_gecerli_mi(q)]
    if not ozet and not sorular:
        raise ValueError("Yedekte özet ya da soru bulunamadı.")

    try:
        ozet_pdf, sorular_pdf = pdf_paketi(ozet, sorular, dil)
    except Exception:
        ozet_pdf = sorular_pdf = None

    sohbet = [
        {"role": m["role"], "content": m["content"]}
        for m in (veri.get("sohbet") or [])
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
    ]

    st.session_state["sonuc"] = {
        "ozet": ozet,
        "n_sorular": len(sorular),
        "ozet_pdf": ozet_pdf,
        "sorular_pdf": sorular_pdf,
        "dil_adi": dil_adi_yedek,
    }
    st.session_state["sohbet_notlari"] = str(veri.get("notlar") or "")
    st.session_state["sohbet"] = sohbet
    if sorular:
        quiz_baslat(sorular)
    else:
        st.session_state.pop("quiz", None)


# ---------------- Yan menü (ayarlar) ----------------
with st.sidebar:
    st.header("⚙️ Ayarlar")
    dil_adi = st.selectbox("Çıktı dili", list(DILLER.keys()))
    ton_adi = st.selectbox("Anlatım tarzı", list(TONLAR.keys()))
    st.caption(f"Model: {MODEL}")

    st.divider()
    st.subheader("💾 Kayıtlı çalışma")
    yedek = st.file_uploader(
        "Daha önce kaydettiğin çalışmayı yükle", type="json"
    )
    if yedek is not None:
        kimlik = (yedek.name, yedek.size)
        if st.session_state.get("yedek_kimlik") != kimlik:
            st.session_state["yedek_kimlik"] = kimlik
            try:
                yedekten_yukle(yedek)
                st.success("Çalışma yüklendi!")
            except Exception as hata:
                st.error(f"Yedek dosyası okunamadı: {hata}")
    else:
        st.session_state.pop("yedek_kimlik", None)

# ---------------- Ana ekran ----------------
st.title("📚 Ders Notu Asistanı")
st.write("PDF yükle ya da notunu yapıştır, sana özet ve etkileşimli quiz hazırlayayım.")

sekme_pdf, sekme_metin = st.tabs(["📄 PDF yükle", "✍️ Metin yapıştır"])

pdf_metni = ""
with sekme_pdf:
    pdf_dosyasi = st.file_uploader("Ders notu PDF'ini seç", type="pdf")
    if pdf_dosyasi is not None:
        try:
            sayfalar = pdf_sayfalarini_oku(pdf_dosyasi.getvalue())
            toplam = len(sayfalar)
            aralik = st.text_input(
                f"Hangi sayfalar kullanılsın? (PDF {toplam} sayfa)",
                placeholder="örn. 5-15  ya da  1-3, 8, 10-12  (boş bırakırsan hepsi)",
            )
            secili = sayfa_araligi_coz(aralik, toplam)
            pdf_metni = "\n".join(sayfalar[k] for k in secili).strip()

            if not pdf_metni:
                st.warning(
                    "Seçtiğin sayfalardan yazı çıkarılamadı. Taranmış (fotoğraf "
                    "gibi) bir PDF olabilir, bu uygulama sadece yazı içeren "
                    "PDF'leri okur."
                )
            else:
                st.success(
                    f"{len(secili)} sayfa okundu ({len(pdf_metni)} karakter)."
                )
                if len(pdf_metni) > MAX_KARAKTER:
                    kullanilan = 0
                    son_sayfa = secili[-1] + 1
                    for k in secili:
                        kullanilan += len(sayfalar[k]) + 1
                        if kullanilan > MAX_KARAKTER:
                            son_sayfa = k + 1
                            break
                    st.info(
                        "Seçtiğin kısım uzun. Hız için yaklaşık "
                        f"{son_sayfa}. sayfaya kadar olan bölüm kullanılacak. "
                        "Geri kalanı için sayfa aralığını değiştirip tekrar dene."
                    )
        except ValueError as hata:
            st.error(str(hata))
        except Exception as hata:
            st.error(f"PDF okunamadı: {hata}")

with sekme_metin:
    yazilan_metin = st.text_area("Ders notunu buraya yapıştır:", height=300)

# PDF yüklendiyse o kullanılır, yoksa yapıştırılan metin
notlar = pdf_metni if pdf_metni else yazilan_metin

st.subheader("Ne istiyorsun?")
istek = st.radio(
    "Ne üretilsin?",
    [SECENEK_OZET, SECENEK_SORU, SECENEK_IKISI],
    index=2,
    horizontal=True,
    label_visibility="collapsed",
)
ozet_ister = istek in (SECENEK_OZET, SECENEK_IKISI)
soru_ister = istek in (SECENEK_SORU, SECENEK_IKISI)

coktan_sayisi = dy_sayisi = klasik_sayisi = 0
if soru_ister:
    st.write("Her türden kaç soru olsun? (İstemediğin türü 0 yap)")
    sutun1, sutun2, sutun3 = st.columns(3)
    coktan_sayisi = sutun1.number_input("Çoktan seçmeli", 0, 10, 5)
    dy_sayisi = sutun2.number_input("Doğru / Yanlış", 0, 10, 3)
    klasik_sayisi = sutun3.number_input("Klasik", 0, 5, 2)

if st.button("Hazırla", type="primary"):
    if len(notlar.strip()) < 50:
        st.warning("Lütfen bir PDF yükle ya da biraz daha uzun bir not yapıştır.")
    elif soru_ister and coktan_sayisi + dy_sayisi + klasik_sayisi == 0:
        st.warning("En az bir soru türünde 1 ya da daha fazla soru seç.")
    else:
        if len(notlar) > MAX_KARAKTER:
            st.info(
                f"Not çok uzun, hız için ilk {MAX_KARAKTER} karakteri kullanıyorum. "
                "Daha fazlası için notu bölümlere ayırıp tek tek dene."
            )
            notlar = notlar[:MAX_KARAKTER]

        dil = DILLER[dil_adi]
        ton = TONLAR[ton_adi]

        try:
            with st.spinner("Hazırlanıyor, biraz sürebilir..."):
                isler = {}
                with ThreadPoolExecutor(max_workers=4) as havuz:
                    if ozet_ister:
                        isler["ozet"] = havuz.submit(
                            sor, MODEL, *ozet_istemi(ton, dil["talimat"], notlar)
                        )
                    if soru_ister:
                        # Her soru türü ayrı (küçük) bir istek olarak, aynı anda üretilir
                        tur_sayilari = {
                            "mc": (coktan_sayisi, 0, 0),
                            "dy": (0, dy_sayisi, 0),
                            "acik": (0, 0, klasik_sayisi),
                        }
                        for tur, (c, d, k) in tur_sayilari.items():
                            if c or d or k:
                                isler[tur] = havuz.submit(
                                    soru_uret, ton, dil["talimat"], notlar, c, d, k
                                )

                    ozet = isler["ozet"].result() if "ozet" in isler else None

                    sorular = []
                    basarisiz = []
                    son_hata = None
                    for tur in ("mc", "dy", "acik"):
                        if tur in isler:
                            try:
                                sorular += isler[tur].result()
                            except Exception as tur_hatasi:
                                basarisiz.append(TUR_ETIKET[tur])
                                son_hata = tur_hatasi

            if soru_ister and not sorular:
                raise son_hata

            # PDF'leri hazırla (başarısız olursa sadece indirme butonu çıkmaz)
            try:
                ozet_pdf, sorular_pdf = pdf_paketi(ozet, sorular, dil)
            except Exception as hata:
                st.warning(f"PDF hazırlanamadı: {hata}")
                ozet_pdf = sorular_pdf = None

            st.session_state["sonuc"] = {
                "ozet": ozet,
                "n_sorular": len(sorular),
                "ozet_pdf": ozet_pdf,
                "sorular_pdf": sorular_pdf,
                "dil_adi": dil_adi,
            }
            st.session_state["sohbet_notlari"] = notlar
            st.session_state["sohbet"] = []
            if sorular:
                quiz_baslat(sorular)
            else:
                st.session_state.pop("quiz", None)

            if basarisiz:
                st.warning(
                    "Şu soru türleri hazırlanamadı: "
                    + ", ".join(basarisiz)
                    + ". Hazırla'ya tekrar basarak yeniden deneyebilirsin."
                )

        except Exception as hata:
            if "410" in str(hata) or "404" in str(hata):
                st.error(
                    "Model artık kullanılamıyor ya da adı değişmiş olabilir. "
                    "app.py içindeki MODEL satırını güncelle."
                )
            else:
                st.error(f"Bir hata oluştu: {hata}")

# ---------------- Sonuçları göster ----------------
sonuc = st.session_state.get("sonuc")
if sonuc:
    st.caption(
        "⚠️ Yapay zekâ hata yapabilir. Önemli bilgileri kendi notunla karşılaştır."
    )

    if sonuc["ozet"]:
        st.subheader("📝 Özet")
        goster(sonuc["ozet"])

    if "quiz" in st.session_state:
        st.subheader("🎮 Quiz zamanı!")
        with st.container(border=True):
            quiz_goster()

    if sonuc["ozet"] or sonuc["n_sorular"]:
        st.divider()
        st.subheader("📥 PDF olarak indir")

        if sonuc["ozet_pdf"] or sonuc["sorular_pdf"]:
            sol, sag = st.columns(2)
            if sonuc["ozet_pdf"]:
                sol.download_button(
                    "📄 Özeti indir",
                    data=sonuc["ozet_pdf"],
                    file_name="ozet.pdf",
                    mime="application/pdf",
                )
            if sonuc["sorular_pdf"]:
                sag.download_button(
                    "📄 Soruları indir (cevap anahtarı son sayfada)",
                    data=sonuc["sorular_pdf"],
                    file_name="sorular.pdf",
                    mime="application/pdf",
                )
        else:
            st.info(
                "PDF için Türkçe karakterleri destekleyen bir yazı tipi "
                "bulunamadı. Windows'ta Arial otomatik bulunur. Bulunamazsa "
                "DejaVuSans.ttf ve DejaVuSans-Bold.ttf dosyalarını proje "
                "klasörüne koyabilirsin."
            )

    # ---------- Notuna soru sor ----------
    if st.session_state.get("sohbet_notlari"):
        st.divider()
        st.subheader("💬 Notuna soru sor")
        st.caption("Anlamadığın bir yeri sor, istersen 'daha basit anlat' de.")

        gecmis = st.session_state.setdefault("sohbet", [])
        for mesaj in gecmis:
            with st.chat_message(mesaj["role"]):
                st.markdown(mesaj["content"])

        hizli_sutunlar = st.columns(len(SOHBET_HIZLI))
        for sutun, (etiket, metin) in zip(hizli_sutunlar, SOHBET_HIZLI):
            sutun.button(
                etiket, on_click=hizli_soru, args=(metin,), key=f"hizli_{etiket}"
            )

        yeni_soru = st.chat_input("Notunla ilgili bir şey sor...")
        if not yeni_soru:
            yeni_soru = st.session_state.pop("bekleyen_soru", None)

        if yeni_soru:
            gecmis.append({"role": "user", "content": yeni_soru})
            with st.chat_message("user"):
                st.markdown(yeni_soru)
            with st.chat_message("assistant"):
                try:
                    cevap = st.write_stream(
                        sohbet_akisi(gecmis, st.session_state["sohbet_notlari"])
                    )
                except Exception as hata:
                    cevap = ""
                    st.error(f"Cevap alınamadı: {hata}")
            if cevap:
                gecmis.append({"role": "assistant", "content": cevap})
            else:
                gecmis.pop()  # cevapsız kalan soruyu geçmişten çıkar

    # ---------- Çalışmayı kaydet ----------
    st.divider()
    st.subheader("💾 Çalışmayı kaydet")
    st.caption(
        "Sayfayı yenilersen sonuçlar silinir. Bu dosyayı indirirsen soldaki "
        "menüden tekrar yükleyip kaldığın yerden devam edebilirsin "
        "(quiz baştan başlar). Dosyanın içinde notun da bulunur."
    )
    st.download_button(
        "💾 Çalışmayı indir (.json)",
        data=yedek_olustur(),
        file_name="calisma.json",
        mime="application/json",
    )