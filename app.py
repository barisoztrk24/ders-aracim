import os
import re
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
from dotenv import load_dotenv
from fpdf import FPDF
from openai import OpenAI
from pypdf import PdfReader

# .env dosyasındaki anahtarı okur
load_dotenv()
API_KEY = os.getenv("NVIDIA_API_KEY")
BASE_URL = "https://integrate.api.nvidia.com/v1"

# Çok uzun notlar yavaşlatır, bu yüzden bir üst sınır koyuyoruz
MAX_KARAKTER = 15000

# Listede en başa gelecek modeller (listede varsa)
TERCIH_EDILENLER = [
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "meta/muse-glimmer-30b",
]

# Sohbet için uygun olmayan modelleri listeden çıkarmak için
CHAT_DISI = (
    "embed", "rerank", "parse", "guard", "safety",
    "reward", "clip", "riva", "tts", "asr", "diffusion",
)

# Çıktı dilleri: modele verilen talimat + PDF başlıkları
DILLER = {
    "Notla aynı dil (otomatik)": {
        "talimat": "Write everything in the same language as the student's "
        "notes. Do not translate the notes into another language.",
        "basliklar": ("Study Guide", "Quiz Questions", "Answer Key"),
    },
    "Türkçe": {
        "talimat": "Write everything in Turkish.",
        "basliklar": ("Ders Özeti", "Test Soruları", "Cevap Anahtarı"),
    },
    "English": {
        "talimat": "Write everything in English.",
        "basliklar": ("Study Guide", "Quiz Questions", "Answer Key"),
    },
    "Deutsch": {
        "talimat": "Write everything in German.",
        "basliklar": ("Zusammenfassung", "Testfragen", "Lösungen"),
    },
    "Français": {
        "talimat": "Write everything in French.",
        "basliklar": ("Résumé", "Questions du test", "Corrigé"),
    },
    "Español": {
        "talimat": "Write everything in Spanish.",
        "basliklar": ("Resumen", "Preguntas de prueba", "Respuestas"),
    },
}

TONLAR = {
    "Eğlenceli ve motive edici": "warm, playful and encouraging; light humor, "
    "vivid analogies, talk directly to the student, but stay accurate",
    "Standart": "clear, friendly and neutral",
    "Sınav odaklı": "serious and exam-focused; precise terminology, "
    "emphasize what is most likely to be tested",
}

CEVAP_ISARETI = "===ANSWER_KEY==="

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

st.set_page_config(page_title="Ders Notu Asistanı", page_icon="📚")

if not API_KEY:
    st.error(
        "API anahtarı bulunamadı. Proje klasöründe `.env` dosyası olduğundan "
        "ve içinde NVIDIA_API_KEY=... satırı bulunduğundan emin ol."
    )
    st.stop()

client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=180.0)
@st.cache_data(ttl=3600, show_spinner=False)
def modelleri_getir():
    """NVIDIA'dan şu an kullanılabilen modellerin listesini alır."""
    idler = [m.id for m in client.models.list()]
    idler = [i for i in idler if not any(k in i.lower() for k in CHAT_DISI)]
    oncelikli = [i for i in TERCIH_EDILENLER if i in idler]
    digerleri = sorted(i for i in idler if i not in oncelikli)
    return oncelikli + digerleri


# ---------------- Yardımcı fonksiyonlar ----------------
def pdf_oku(dosya):
    """PDF dosyasındaki yazıyı çıkarır."""
    okuyucu = PdfReader(dosya)
    sayfalar = [sayfa.extract_text() or "" for sayfa in okuyucu.pages]
    return "\n".join(sayfalar).strip()


def sor(model, sistem_mesaji, kullanici_mesaji):
    """Modele bir istek gönderir ve cevabın metnini döndürür."""
    cevap = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sistem_mesaji},
            {"role": "user", "content": kullanici_mesaji},
        ],
        temperature=0.6,
        max_tokens=4096,
    )
    secim = cevap.choices[0]
    metin = secim.message.content
    if not metin:
        raise ValueError(
            f"Model boş cevap döndürdü (sebep: {secim.finish_reason}). "
            "Tekrar dene ya da başka bir model seç."
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


def soru_istemi(ton, dil, notlar, adet):
    sistem = (
        "You are an experienced, creative teacher who writes great exam "
        f"questions. {dil} Questions must be based only on the student's notes."
    )
    kullanici = (
        f"Create {adet} multiple-choice questions (options A-D, exactly one "
        "correct) from the notes below.\n"
        f"Tone: {ton}.\n\n"
        "Requirements:\n"
        "- Order from easy to hard.\n"
        "- Mix question types: definition recall, 'which statement is FALSE', "
        "short real-life scenario / application questions, cause-and-effect.\n"
        "- Wrong options must be plausible, based on common misconceptions.\n"
        "- Vary which letter is correct.\n"
        "- Format: the question number and text in bold, then each option on "
        "its own line starting with 'A)', 'B)', 'C)', 'D)'.\n"
        "- Do not use tables.\n\n"
        "After all questions, write exactly this on its own line, without "
        f"translating it: {CEVAP_ISARETI}\n"
        "Then, for each question, give the number, the correct letter, and a "
        "1-2 sentence explanation that teaches: why it is correct and why the "
        "most tempting wrong option is wrong.\n\n"
        f"NOTES:\n{notlar}"
    )
    return sistem, kullanici


def sorulari_ayir(metin):
    """Cevabı 'sorular' ve 'cevap anahtarı' olarak ikiye böler."""
    if CEVAP_ISARETI in metin:
        sorular, cevaplar = metin.split(CEVAP_ISARETI, 1)
        return sorular.strip(), cevaplar.strip()
    return metin.strip(), ""


def goster(metin):
    """Satır sonlarını koruyarak markdown gösterir (şıklar alt alta çıksın)."""
    st.markdown(metin.replace("\n", "  \n"))


# ---------------- PDF oluşturma ----------------
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


# ---------------- Yan menü (ayarlar) ----------------
with st.sidebar:
    st.header("⚙️ Ayarlar")

    try:
        modeller = modelleri_getir()
    except Exception:
        modeller = []

    if modeller:
        model = st.selectbox(
            "Yapay zekâ modeli (yazarak arayabilirsin)", modeller
        )
    else:
        model = ""
        st.warning("Model listesi alınamadı, model adını aşağıya yaz.")

    elle_model = st.text_input(
        "Model adını kendin yazmak istersen (isteğe bağlı)"
    ).strip()
    if elle_model:
        model = elle_model

    dil_adi = st.selectbox("Çıktı dili", list(DILLER.keys()))
    ton_adi = st.selectbox("Anlatım tarzı", list(TONLAR.keys()))
    soru_sayisi = st.slider("Test sorusu sayısı", 3, 15, 5)

# ---------------- Ana ekran ----------------
st.title("📚 Ders Notu Asistanı")
st.write("PDF yükle ya da notunu yapıştır, sana özet ve test soruları hazırlayayım.")

sekme_pdf, sekme_metin = st.tabs(["📄 PDF yükle", "✍️ Metin yapıştır"])

pdf_metni = ""
with sekme_pdf:
    pdf_dosyasi = st.file_uploader("Ders notu PDF'ini seç", type="pdf")
    if pdf_dosyasi is not None:
        try:
            pdf_metni = pdf_oku(pdf_dosyasi)
            if pdf_metni:
                st.success(f"PDF okundu ({len(pdf_metni)} karakter).")
            else:
                st.warning(
                    "PDF'ten yazı çıkarılamadı. Taranmış (fotoğraf gibi) bir "
                    "PDF olabilir, bu uygulama sadece yazı içeren PDF'leri okur."
                )
        except Exception as hata:
            st.error(f"PDF okunamadı: {hata}")

with sekme_metin:
    yazilan_metin = st.text_area("Ders notunu buraya yapıştır:", height=300)

# PDF yüklendiyse o kullanılır, yoksa yapıştırılan metin
notlar = pdf_metni if pdf_metni else yazilan_metin

if st.button("Özet ve soru üret", type="primary"):
    if not model:
        st.warning("Lütfen soldaki menüden bir model seç ya da model adı yaz.")
    elif len(notlar.strip()) < 50:
        st.warning("Lütfen bir PDF yükle ya da biraz daha uzun bir not yapıştır.")
    else:
        if len(notlar) > MAX_KARAKTER:
            st.info(
                f"Not çok uzun, hız için ilk {MAX_KARAKTER} karakteri kullanıyorum. "
                "Daha fazlası için PDF'i bölümlere ayırıp tek tek dene."
            )
            notlar = notlar[:MAX_KARAKTER]

        dil = DILLER[dil_adi]
        ton = TONLAR[ton_adi]
        ozet_baslik, soru_baslik, cevap_baslik = dil["basliklar"]

        try:
            with st.spinner(
                "Özet ve sorular aynı anda hazırlanıyor, biraz sürebilir..."
            ):
                ozet_sistem, ozet_kullanici = ozet_istemi(ton, dil["talimat"], notlar)
                soru_sistem, soru_kullanici = soru_istemi(
                    ton, dil["talimat"], notlar, soru_sayisi
                )
                with ThreadPoolExecutor(max_workers=2) as havuz:
                    is_ozet = havuz.submit(sor, model, ozet_sistem, ozet_kullanici)
                    is_soru = havuz.submit(sor, model, soru_sistem, soru_kullanici)
                    ozet = is_ozet.result()
                    sorular_ham = is_soru.result()

            sorular, cevaplar = sorulari_ayir(sorular_ham)

            # PDF'leri hazırla (başarısız olursa sadece indirme butonu çıkmaz)
            ozet_pdf = sorular_pdf = None
            try:
                ozet_pdf = pdf_olustur(ozet_baslik, [(None, ozet)])
                bolumler = [(None, sorular)]
                if cevaplar:
                    bolumler.append((cevap_baslik, cevaplar))
                sorular_pdf = pdf_olustur(soru_baslik, bolumler)
            except Exception as hata:
                st.warning(f"PDF hazırlanamadı: {hata}")

            st.session_state["sonuc"] = {
                "ozet": ozet,
                "sorular": sorular,
                "cevaplar": cevaplar,
                "ozet_pdf": ozet_pdf,
                "sorular_pdf": sorular_pdf,
            }

        except Exception as hata:
            if "410" in str(hata) or "404" in str(hata):
                st.error(
                    "Bu model artık kullanılamıyor ya da adı yanlış. "
                    "Soldaki listeden başka bir model seç."
                )
            else:
                st.error(f"Bir hata oluştu: {hata}")

# ---------------- Sonuçları göster ----------------
sonuc = st.session_state.get("sonuc")
if sonuc:
    st.subheader("📝 Özet")
    goster(sonuc["ozet"])

    st.subheader("❓ Test Soruları")
    goster(sonuc["sorular"])

    if sonuc["cevaplar"]:
        with st.expander("Cevap anahtarını göster"):
            goster(sonuc["cevaplar"])

    st.divider()
    st.subheader("📥 PDF olarak indir")
    if sonuc["ozet_pdf"] and sonuc["sorular_pdf"]:
        sol, sag = st.columns(2)
        sol.download_button(
            "📄 Özeti indir",
            data=sonuc["ozet_pdf"],
            file_name="ozet.pdf",
            mime="application/pdf",
        )
        sag.download_button(
            "📄 Soruları indir (cevap anahtarı son sayfada)",
            data=sonuc["sorular_pdf"],
            file_name="sorular.pdf",
            mime="application/pdf",
        )
    else:
        st.info(
            "PDF için Türkçe karakterleri destekleyen bir yazı tipi bulunamadı. "
            "Windows'ta Arial otomatik bulunur. Bulunamazsa DejaVuSans.ttf ve "
            "DejaVuSans-Bold.ttf dosyalarını proje klasörüne koyabilirsin."
        )