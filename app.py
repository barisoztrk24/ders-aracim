import os

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

# .env dosyasındaki anahtarı okur
load_dotenv()
API_KEY = os.getenv("NVIDIA_API_KEY")

BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL = "mistralai/mistral-large-4-1t-a52b"

st.set_page_config(page_title="Ders Notu Asistanı", page_icon="📚")
st.title("📚 Ders Notu Asistanı")
st.write("Ders notunu yapıştır, sana özet ve test soruları hazırlayayım.")

if not API_KEY:
    st.error(
        "API anahtarı bulunamadı. Proje klasöründe `.env` dosyası olduğundan "
        "ve içinde NVIDIA_API_KEY=... satırı bulunduğundan emin ol."
    )
    st.stop()

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)


def yapay_zekaya_sor(sistem_mesaji, kullanici_mesaji):
    """Modele bir istek gönderir ve cevabın metnini döndürür."""
    cevap = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": sistem_mesaji},
            {"role": "user", "content": kullanici_mesaji},
        ],
        temperature=0.3,
        max_tokens=4096,
    )
    secim = cevap.choices[0]
    metin = secim.message.content

    if not metin:
        raise ValueError(
            f"Model boş cevap döndürdü (sebep: {secim.finish_reason}). "
            "Lütfen tekrar dene."
        )
    return metin


# --- Arayüz ---
notlar = st.text_area("Ders notunu buraya yapıştır:", height=300)

soru_sayisi = st.slider("Kaç test sorusu olsun?", min_value=3, max_value=15, value=5)

if st.button("Özet ve soru üret"):
    if len(notlar.strip()) < 50:
        st.warning("Lütfen biraz daha uzun bir ders notu yapıştır.")
    else:
        try:
            # 1) Özet
            with st.spinner("Özet hazırlanıyor..."):
                ozet = yapay_zekaya_sor(
                    "Sen yardımcı bir ders asistanısın. Türkçe yanıt ver. "
                    "Sadece verilen nottaki bilgileri kullan.",
                    "Aşağıdaki ders notunu maddeler halinde, kısa ve anlaşılır "
                    "şekilde özetle. Önemli kavramları kalın yaz.\n\n" + notlar,
                )

            # 2) Test soruları
            with st.spinner("Test soruları hazırlanıyor..."):
                sorular = yapay_zekaya_sor(
                    "Sen deneyimli bir öğretmensin. Türkçe yanıt ver. "
                    "Sadece verilen nottaki bilgilerden soru hazırla.",
                    f"Aşağıdaki ders notundan {soru_sayisi} adet çoktan seçmeli "
                    "test sorusu hazırla. Her sorunun A, B, C, D şıkları olsun. "
                    "Önce tüm soruları yaz. Sonra tek başına bir satıra "
                    "---CEVAP ANAHTARI--- yaz ve altına her sorunun doğru "
                    "cevabını kısa bir açıklamayla ver.\n\n" + notlar,
                )

            st.session_state["ozet"] = ozet
            st.session_state["sorular"] = sorular

        except Exception as hata:
            st.error(f"Bir hata oluştu: {hata}")

# --- Sonuçları göster ---
if "ozet" in st.session_state:
    st.subheader("📝 Özet")
    st.markdown(st.session_state["ozet"])

    st.subheader("❓ Test Soruları")
    parcalar = st.session_state["sorular"].split("---CEVAP ANAHTARI---")
    st.markdown(parcalar[0])

    if len(parcalar) > 1:
        with st.expander("Cevap anahtarını göster"):
            st.markdown(parcalar[1])