import os
import urllib.parse
from datetime import datetime, date # <--- DÜZELTME 1: date buraya eklendi
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import Boolean, Date
import uvicorn
import google.generativeai as genai
from dotenv import load_dotenv

# --- Kendi oluşturduğumuz dosyalar ---
import models
from database import engine, SessionLocal

# --- Ayarlar ---
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)

# Model seçimi
model = genai.GenerativeModel("gemini-2.0-flash")

# --- Veritabanı Başlatma ---
db_available = False
try:
    # --- YENİ EKLENECEK SATIR (GEÇİCİ) ---
    # Bu satır mevcut tabloları siler, böylece yeni sütunlarla (burç vb.) tekrar oluşur.
    #models.Base.metadata.drop_all(bind=engine) 
    # -------------------------------------
    models.Base.metadata.create_all(bind=engine)
    db_available = True
    print("✅ Veritabanı bağlantısı başarılı!")
except Exception as e:
    print(f"⚠️ Veritabanı bağlantı hatası: {str(e)}")
    print("API sunucusu veritabanı olmadan başlatılıyor (Hata verebilir)...")

# --- Uygulama Başlatma ve CORS ---
app = FastAPI()

origins = ["*"] 

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Veritabanı Oturumu (Dependency) ---
def get_db():
    if not db_available:
        raise HTTPException(status_code=503, detail="Veritabanı şu anda kullanılabilir değil")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
#              VERİ MODELLERİ (Pydantic)
# ==========================================

class RuyaIstegi(BaseModel):
    ruya_metni: str
    user_id: str

class AvatarUpdate(BaseModel):
    user_id: str
    choice: str 
    zodiac: str | None = None # <--- YENİ: Burç bilgisi (Opsiyonel)

class PremiumUpdate(BaseModel):
    user_id: str
    is_premium: bool
# ==========================================
#                 ENDPOINTLER
# ==========================================

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "database": "connected" if db_available else "disconnected"
    }

# --- 1. AVATAR / PROFİL İŞLEMLERİ ---
# --- 1. PROFİL İŞLEMLERİ (GÜNCELLENDİ) ---
# --- GÜNCELLENEN: GET PROFILE (Premium bilgisini de gönderiyoruz) ---
@app.get("/get-profile/{user_id}")
def get_profile(user_id: str, db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == user_id).first()
    if not profile:
        return {"choice": None, "zodiac": None, "is_premium": False, "usage_count": 0}
    
    return {
        "choice": profile.avatar_choice, 
        "zodiac": profile.zodiac,
        "is_premium": profile.is_premium,
        "usage_count": profile.lifetime_usage_count # <--- Flutter'a toplam sayıyı gönderiyoruz
    }

@app.post("/set-profile") # İsmi set-avatar yerine set-profile yaptık (daha genel)
def set_profile(data: AvatarUpdate, db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == data.user_id).first()
    
    if not profile:
        new_profile = models.UserProfile(
            user_id=data.user_id, 
            avatar_choice=data.choice,
            zodiac=data.zodiac # <--- YENİ
        )
        db.add(new_profile)
    else:
        profile.avatar_choice = data.choice
        if data.zodiac: # Eğer burç gönderildiyse güncelle
            profile.zodiac = data.zodiac
    
    db.commit()
    return {"status": "success", "choice": data.choice, "zodiac": data.zodiac}

# --- IAP TAMAMLANDI ---
@app.post("/set-premium")
def set_premium(data: PremiumUpdate, db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == data.user_id).first()
    
    if not profile:
        # Profil yoksa oluştur ve Premium yap
        new_profile = models.UserProfile(
            user_id=data.user_id, 
            is_premium=data.is_premium,
            daily_usage_count=0,
            lifetime_usage_count=0,
            last_usage_date=date.today()
        )
        db.add(new_profile)
    else:
        # Profil varsa güncelle
        profile.is_premium = data.is_premium
    
    db.commit()
    return {"status": "success", "is_premium": data.is_premium}


# --- 2. RÜYA ANALİZ (GÜNCELLENDİ) ---
# --- 2. RÜYA ANALİZ (GÜNCELLENMİŞ VERSİYON) ---
@app.post("/analiz-et")
def analiz_et(istek: RuyaIstegi, db: Session = Depends(get_db)):
    try:
        # 1. KULLANICI PROFİLİNİ GETİR VEYA OLUŞTUR
        user_profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == istek.user_id).first()
        
        if not user_profile:
            user_profile = models.UserProfile(
                user_id=istek.user_id, 
                is_premium=False,           # Varsayılan: Ücretsiz
                daily_usage_count=0, 
                lifetime_usage_count=0,     # Yeni kullanıcı 0 rüya ile başlar
                last_usage_date=date.today()
            )
            db.add(user_profile)
            db.commit()

        # 2. GÜNLÜK TARİH KONTROLÜ (İstatistiksel temizlik için)
        # Gün değiştiyse günlük sayacı sıfırla (lifetime'a dokunma)
        bugun = date.today()
        if user_profile.last_usage_date != bugun:
            user_profile.daily_usage_count = 0
            user_profile.last_usage_date = bugun
            db.commit()

        # 3. KRİTİK KONTROL: LİMİT AŞIMI
        # Kullanıcı Premium DEĞİLSE ve Toplam Hakkı 5'e ulaşmışsa işlemi durdur.
        LIFETIME_LIMIT = 5
        
        if not user_profile.is_premium and user_profile.lifetime_usage_count >= LIFETIME_LIMIT:
            # Frontend bu hatayı yakalayıp Premium diyalogunu açacak
            raise HTTPException(status_code=403, detail="LIMIT_REACHED")

        # --- AI İŞLEMLERİ (BURADAN SONRASI YORUMLAMA MANTIĞI) ---
        user_profile_zodiac = user_profile.zodiac if user_profile.zodiac else "Unknown"
        otomatik_tarih = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        # A. Gemini Sohbetini Başlat
        chat = model.start_chat(history=[])
        
        # B. Rüya Yorumu İsteği
        prompt = f"""
### SYSTEM ROLE & OBJECTIVE
You are an empathetic, insightful counselor specialized in symbolic interpretation, archetypal psychology, and astrological correspondences and coming from Jungian school. Your goal is to analyze the user's dream by 
decoding symbols, emotional undertones, and latent meanings without explicitly mentioning "Jungian school".

### USER CONTEXT
- **Zodiac Sign:** {user_profile_zodiac}
- **Membership Status:** {"PREMIUM" if user_profile.is_premium else "FREE"}
- **Dream Content:** "{istek.ruya_metni}"

### INSTRUCTIONS

1. **Language Detection & Output:**
   - Analyze the "Dream Content" to detect its language. Do not mention you are interpreting user's dream.
   - **CRITICAL:** Your entire response must be in the **EXACT SAME LANGUAGE** as the dream.
   - Do NOT mention that you detected the language. Just start speaking in it.

2. **Astrological Integration:**
   - Subtly weave the user's Zodiac sign ({user_profile_zodiac}) into the analysis.
   - Mention traits associated with this sign only if they amplify the dream's meaning (e.g., "As a Scorpio, your natural intensity might be fueling this image..."). Do not force it if it doesn't fit.

3. **Analysis Logic (Conditional):**

   **IF USER IS PREMIUM:**
   - **Depth:** Provide a profound, multi-layered analysis. Explore the "shadow" aspects, emotional resonance, and archetypal imagery.
   - **Structure:**
     - Decode the key symbols.
     - Connect the dream to the user's waking life emotions.
     - **Behavioral Suggestion:** Conclude with a concrete, psychological exercise or thought pattern change to help the user feel better or integrate the dream's message.
   - **Tone:** Deep, therapeutic, and transformative.

   **IF USER IS FREE (Non-Premium):**
   - **Constraint:** Keep the response strictly under 50 words.
   - **Content:** Provide a "teaser" interpretation. Identify the *single most important symbol* and its surface-level meaning. Be intriguing but incomplete.
   - **Call to Action (CTA):** You MUST end the response by gently encouraging the user to upgrade to the Premium plan to unlock the full, detailed psychological analysis and behavioral advice.

### OUTPUT GENERATION
Based on the logic above, provide the response now:
"""
        response = chat.send_message(prompt)
        ai_cevabi = response.text

        # C. Başlık ve Duygu Analizi İsteği
        ek_bilgi_prompt = "Based on the dream above, create a mysterious title (3-5 words) and identify the dominant emotion. Output format strictly: Title | Emotion"
        ek_response = chat.send_message(ek_bilgi_prompt)
        ek_metin = ek_response.text.strip()
        
        ruya_basligi = "Bilinçaltı Mesajı"
        ruya_duygusu = "Nötr"
        try:
            if "|" in ek_metin:
                parts = ek_metin.split('|')
                if len(parts) >= 2:
                    ruya_basligi = parts[0].strip().replace('"', '')
                    ruya_duygusu = parts[1].strip().replace('.', '')
            else:
                ruya_basligi = ek_metin
        except:
            pass 

        # D. Görsel Prompt Oluşturma
        gorsel_prompt_istegi = f"""Based on the dream above, create a highly detailed, mystical, and artistic image description suitable for an AI image generator.
        Describe the scene, lighting, and mood.
        CRITICAL: The output must be in English regardless of the dream language.
        """
        gorsel_response = chat.send_message(gorsel_prompt_istegi)
        gorsel_prompt = gorsel_response.text.strip()
        
        # E. Pollinations.ai ile Resim URL'i Oluşturma
        encoded_prompt = urllib.parse.quote(gorsel_prompt)
        resim_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=768&height=1024&seed={datetime.now().microsecond}&nologo=true"

        # 4. VERİTABANINA KAYIT
        yeni_ruya = models.Ruya(
            user_id=istek.user_id,
            ruya_metni=istek.ruya_metni, 
            baslik=ruya_basligi,
            yorum=ai_cevabi,
            resim_url=resim_url,
            duygu=ruya_duygusu,
            tarih=otomatik_tarih
        )
        db.add(yeni_ruya)
        
        # 5. SAYAÇLARI GÜNCELLEME (EN ÖNEMLİ KISIM)
        user_profile.daily_usage_count += 1    # Günlük istatistik için
        user_profile.lifetime_usage_count += 1 # Kalıcı limit kontrolü için (+1 ekleniyor)
        
        db.commit()
        db.refresh(yeni_ruya)

        return {
            "baslik": ruya_basligi, 
            "sonuc": ai_cevabi, 
            "resim_url": resim_url,
            "duygu": ruya_duygusu,
            "id": yeni_ruya.id
        }

    except HTTPException as he:
        raise he 
    except Exception as e:
        print(f"Analiz Hatası: {e}") # Hata ayıklama için konsola yazdır
        raise HTTPException(status_code=500, detail=f"Sunucu hatası: {str(e)}")
# --- 3. GEÇMİŞ RÜYALAR ---

@app.get("/gecmis")
def gecmis_getir(user_id: str, db: Session = Depends(get_db)):
    ruyalar = db.query(models.Ruya).filter(models.Ruya.user_id == user_id).all()
    return ruyalar

# --- 4. RÜYA SİLME ---

@app.delete("/ruya-sil/{id}")
def ruya_sil(id: int, db: Session = Depends(get_db)):
    ruya = db.query(models.Ruya).filter(models.Ruya.id == id).first()
    if ruya is None:
        raise HTTPException(status_code=404, detail="Not Found")
    
    db.delete(ruya)
    db.commit()
    return {"mesaj": "Deleted"}

# --- SUNUCUYU BAŞLAT ---
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)





