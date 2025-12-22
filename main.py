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
    models.Base.metadata.drop_all(bind=engine) 
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

# --- MODEL GÜNCELLEMESİ ---
class AvatarUpdate(BaseModel):
    user_id: str
    choice: str 
    zodiac: str | None = None
    interpreter_type: str | None = None # <--- YENİ: Yorumcu Tipi

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
# --- 1. PROFİL İŞLEMLERİ (GÜNCELLENDİ) ---
@app.get("/get-profile/{user_id}")
def get_profile(user_id: str, db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == user_id).first()
    if not profile:
        # Varsayılan değerler
        return {
            "choice": None, 
            "zodiac": None, 
            "interpreter_type": "psychological", # Varsayılan: Bilimsel/Psikolojik
            "is_premium": False, 
            "usage_count": 0
        }
    
    return {
        "choice": profile.avatar_choice, 
        "zodiac": profile.zodiac,
        "interpreter_type": profile.interpreter_type if profile.interpreter_type else "psychological",
        "is_premium": profile.is_premium,
        "usage_count": profile.lifetime_usage_count
    }

@app.post("/set-profile")
def set_profile(data: AvatarUpdate, db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == data.user_id).first()
    
    if not profile:
        new_profile = models.UserProfile(
            user_id=data.user_id, 
            avatar_choice=data.choice,
            zodiac=data.zodiac,
            interpreter_type=data.interpreter_type # <--- YENİ
        )
        db.add(new_profile)
    else:
        if data.choice: profile.avatar_choice = data.choice
        if data.zodiac: profile.zodiac = data.zodiac
        if data.interpreter_type: profile.interpreter_type = data.interpreter_type # <--- YENİ
    
    db.commit()
    return {"status": "success"}

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
# --- 2. RÜYA ANALİZ (DÜZELTİLMİŞ & GARANTİLİ VERSİYON) ---
# --- 2. RÜYA ANALİZ (YORUMCU MANTIĞI EKLENDİ) ---
@app.post("/analiz-et")
def analiz_et(istek: RuyaIstegi, db: Session = Depends(get_db)):
    try:
        # 1. KULLANICIYI BUL
        user_profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == istek.user_id).first()
        
        # Eğer profil yoksa oluştur (Fallback)
        if not user_profile:
            user_profile = models.UserProfile(
                user_id=istek.user_id, 
                is_premium=False,
                interpreter_type="psychological", # Varsayılan
                last_usage_date=date.today()
            )
            db.add(user_profile)
            db.commit()

        # ... (Limit ve Tarih kontrolleri aynı kalıyor) ...
        bugun = date.today()
        if user_profile.last_usage_date != bugun:
            user_profile.daily_usage_count = 0
            user_profile.last_usage_date = bugun
            db.commit()

        LIFETIME_LIMIT = 5
        if not user_profile.is_premium and user_profile.lifetime_usage_count >= LIFETIME_LIMIT:
            raise HTTPException(status_code=403, detail="LIMIT_REACHED")

        # --- YORUMCU SEÇİMİ VE PERSONA BELİRLEME ---
        secilen_yorumcu = user_profile.interpreter_type if user_profile.interpreter_type else "psychological"
        user_zodiac = user_profile.zodiac if user_profile.zodiac else "Unknown"

        system_persona = ""
        
        if secilen_yorumcu == "religious":
            # DİNİ / GELENEKSEL (İbn-i Sirin Tarzı)
            system_persona = """
            You are Ibn Sirin (Traditional Interpreter). 
            Interpret the dream as a divine message, omen, or warning based on traditional symbolism (like Ibn-i Sirin).
            Focus on destiny, moral warnings, and religious good tidings.
            Tone: Authoritative, wise, fatalistic, and sacred.
            """
        elif secilen_yorumcu == "spiritual":
            # SPİRİTÜEL / KOZMİK (Enerji, Çakra)
            system_persona = """
            You are an 'Star Reader' (Spiritual Mystic).
            Interpret the dream as a flow of cosmic energy, vibrations, and universal messages.
            Focus on chakras, spiritual alignment, aura, and the connection with the universe.
            Tone: Ethereal, soothing, magical, and uplifting.
            """
        else:
            # PSİKOLOJİK (Freud/Jung - Varsayılan)
            system_persona = """
            You are an 'Healer of the Soul' (Psychological Analyst).
            Interpret the dream using archetypes and subconscious analysis (like Jung/Freud).
            Focus on the user's hidden fears, repressed desires, shadow self, and inner conflicts.
            Tone: Intense, analytical, mysterious, and probing.
            """

        # --- PREMIUM / FREE TALİMATLARI (AYNI) ---
        if user_profile.is_premium:
            ozel_talimatlar = """
            - **Depth:** Provide a profound, multi-layered analysis based on your specific persona.
            - **Structure:**
                1. **Symbol Decoding:** Decode key symbols strictly through your persona's lens.
                2. **Personal Connection:** Connect the dream to the user's waking life.
                3. **Specific Advice:** Conclude with advice that fits your persona.
            - **Length:** Detailed and comprehensive.
            """
        else:
            ozel_talimatlar = """
            - **Constraint:** Keep the response STRICTLY under 50 words.
            - **Content:** Provide a "teaser" interpretation only. Identify the single most important symbol.
            - **Call to Action (CTA):** End by saying: "To hear the full wisdom, unlock Premium."
            """

        # --- ANA PROMPT BİRLEŞTİRME ---
        prompt = f"""
### SYSTEM ROLE (YOUR PERSONA)
{system_persona}

### USER CONTEXT
- **Zodiac Sign:** {user_zodiac}
- **Dream Content:** "{istek.ruya_metni}"

### INSTRUCTIONS
1. **Language Detection & Output:**
   - Detect the language of the "Dream Content".
   - **CRITICAL:** Your entire response must be in the **EXACT SAME LANGUAGE** as the dream.
   
2. **Analysis Instructions:**
{ozel_talimatlar}

### OUTPUT GENERATION
Speak now, wise one.
"""       
        # --- DÜZELTME BİTİŞİ ---

        # A. Gemini Sohbetini Başlat
        chat = model.start_chat(history=[])
        response = chat.send_message(prompt)
        ai_cevabi = response.text

        # B. Başlık ve Duygu (Aynı kalıyor)
        ek_bilgi_prompt = "Based on the dream above, create a mysterious title (3-5 words) and identify the dominant emotion. Use same  the **EXACT SAME LANGUAGE** as the dream. Output format strictly: Title | Emotion"
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

        # C. Görsel Prompt (Aynı kalıyor)
        gorsel_prompt_istegi = f"""Based on the dream above, create a highly detailed, mystical, and artistic image description suitable for an AI image generator.
        Describe the scene, lighting, and mood.
        CRITICAL: The output must be in English regardless of the dream language.
        """
        gorsel_response = chat.send_message(gorsel_prompt_istegi)
        gorsel_prompt = gorsel_response.text.strip()
        
        # D. Resim URL (Aynı kalıyor)
        encoded_prompt = urllib.parse.quote(gorsel_prompt)
        resim_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=768&height=1024&seed={datetime.now().microsecond}&nologo=true"

# --- EKSİK OLAN TANIMLAMA BURAYA EKLENDİ ---
        # Tarihi string formatında (Gün.Ay.Yıl) alıyoruz
        otomatik_tarih = datetime.now().strftime("%d.%m.%Y")
        # 4. KAYIT VE SAYAÇ
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
        
        user_profile.daily_usage_count += 1
        user_profile.lifetime_usage_count += 1
        
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
        print(f"Analiz Hatası: {e}")
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





