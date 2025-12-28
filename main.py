import os
import urllib.parse
from datetime import datetime, date
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

import requests
import cloudinary
import cloudinary.uploader

# --- Ayarlar ---
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
cloudinary.config( 
  cloud_name =os.getenv("CLOUD_NAME"), 
  cloud_api_key = os.getenv("CLOUD_API_KEY"), 
  cloud_api_secret = os.getenv("CLOUD_API_SECRET") 
)
HF_TOKEN = os.getenv("HF_TOKEN")
if api_key:
    genai.configure(api_key=api_key)

# Model seçimi
model = genai.GenerativeModel("gemini-2.0-flash")

# --- Veritabanı Başlatma ---
db_available = False
try:
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

class SembolIstegi(BaseModel):
    sembol: str
    user_id: str

class RuyaIstegi(BaseModel):
    ruya_metni: str
    user_id: str

class AvatarUpdate(BaseModel):
    user_id: str
    choice: str | None = None
    zodiac: str | None = None
    interpreter_type: str | None = None

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

# --- 1. PROFİL İŞLEMLERİ ---
@app.get("/get-profile/{user_id}")
def get_profile(user_id: str, db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == user_id).first()
    
    # Bugünün tarihini kontrol et, eskiyse sıfırla
    bugun = date.today()
    current_daily_usage = 0
    if profile:
        if profile.last_usage_date != bugun:
            current_daily_usage = 0
        else:
            current_daily_usage = profile.daily_usage_count
   
    if not profile:
        return {
            "choice": None, 
            "zodiac": None, 
            "interpreter_type": "psychological",
            "is_premium": False, 
            "usage_count": 0
        }
    
    return {
        "choice": profile.avatar_choice, 
        "zodiac": profile.zodiac,
        "interpreter_type": profile.interpreter_type if profile.interpreter_type else "psychological",
        "is_premium": profile.is_premium,
        "usage_count": current_daily_usage
    }

@app.post("/set-profile")
def set_profile(data: AvatarUpdate, db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == data.user_id).first()
    
    if not profile:
        new_profile = models.UserProfile(
            user_id=data.user_id, 
            avatar_choice=data.choice,
            zodiac=data.zodiac,
            interpreter_type=data.interpreter_type
        )
        db.add(new_profile)
    else:
        if data.choice: profile.avatar_choice = data.choice
        if data.zodiac: profile.zodiac = data.zodiac
        if data.interpreter_type: profile.interpreter_type = data.interpreter_type
    
    db.commit()
    return {"status": "success"}

@app.post("/set-premium")
def set_premium(data: PremiumUpdate, db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == data.user_id).first()
    
    if not profile:
        new_profile = models.UserProfile(
            user_id=data.user_id, 
            is_premium=data.is_premium,
            daily_usage_count=0,
            lifetime_usage_count=0,
            last_usage_date=date.today()
        )
        db.add(new_profile)
    else:
        profile.is_premium = data.is_premium
    
    db.commit()
    return {"status": "success", "is_premium": data.is_premium}


# --- 2. RÜYA ANALİZ (DÜZELTİLMİŞ & GARANTİLİ VERSİYON) ---
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
                interpreter_type="psychological",
                last_usage_date=date.today()
            )
            db.add(user_profile)
            db.commit()

        # Tarih Kontrolü ve Sıfırlama
        bugun = date.today()
        if user_profile.last_usage_date != bugun:
            user_profile.daily_usage_count = 0
            user_profile.last_usage_date = bugun
            db.commit()

        # --- GÜNLÜK LİMİT KONTROLÜ ---
        DAILY_LIMIT = 3 
        
        if not user_profile.is_premium and user_profile.daily_usage_count >= DAILY_LIMIT:
            raise HTTPException(status_code=403, detail="LIMIT_REACHED")

        # --- YORUMCU SEÇİMİ VE PERSONA BELİRLEME ---
        secilen_yorumcu = user_profile.interpreter_type if user_profile.interpreter_type else "psychological"
        user_zodiac = user_profile.zodiac if user_profile.zodiac else "Unknown"

        system_persona = ""
        
        if secilen_yorumcu == "religious":
            system_persona = """
            You are Ibn Sirin (Traditional Interpreter). 
            Interpret the dream as a divine message, omen, or warning based on traditional symbolism.
            Focus on destiny, moral warnings, and religious good tidings.
            Tone: Authoritative, wise, fatalistic, and sacred.
            """
        elif secilen_yorumcu == "spiritual":
            system_persona = """
            You are an 'Star Reader' (Spiritual Mystic).
            Interpret the dream as a flow of cosmic energy, vibrations, and universal messages.
            Focus on chakras, spiritual alignment, aura, and the connection with the universe.
            Tone: Ethereal, soothing, magical, and uplifting.
            """
        else:
            system_persona = """
            You are an 'Healer of the Soul' (Psychological Analyst).
            Interpret the dream using archetypes and subconscious analysis (like Jung/Freud).
            Focus on the user's hidden fears, repressed desires, shadow self, and inner conflicts.
            Tone: Intense, analytical, mysterious, and probing.
            """

        # --- PREMIUM / FREE TALİMATLARI ---
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
            - **Call to Action (CTA):** End by stating: "To hear the full wisdom, unlock Premium." -> CRITICAL: This specific phrase MUST be translated into the **EXACT SAME LANGUAGE** as the dream content.
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
        - Detect the language of the "Dream Content".** DO NOT SAY WHAT LANGUAGE IT IS.**
        - **CRITICAL:** Your entire response must be in the **EXACT SAME LANGUAGE** as the dream.
   
        2. **Analysis Instructions:**
        {ozel_talimatlar}

        ### OUTPUT GENERATION
        Speak now, wise one.
        """       

        # A. Gemini Sohbetini Başlat
        chat = model.start_chat(history=[])
        response = chat.send_message(prompt)
        ai_cevabi = response.text

        # B. Başlık ve Duygu
        ek_bilgi_prompt = "Based on the dream above, create a mysterious title (3-5 words) and identify the dominant emotion. Use same the **EXACT SAME LANGUAGE** as the dream. Output format strictly: Title | Emotion"
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
        # C 
      # 1. Gemini'den Kısa Prompt İste
        gorsel_prompt_istegi = f"""
        Create a vivid, cinematic, surreal art prompt based on: "{istek.ruya_metni}".
        Max 10 words. English only. No quotes.
        """
        gorsel_response = chat.send_message(gorsel_prompt_istegi)
        gorsel_prompt = gorsel_response.text.strip().replace('"', '').replace('\n', ' ')
        
        # URL için promptu encode et
        encoded_prompt = urllib.parse.quote(gorsel_prompt)

        resim_url = "" 
        
        try:
            # --- MODEL DENEME ZİNCİRİ ---
            # 1. Deneme: FLUX (En Kaliteli)
            models_to_try = ["turbo", "turbo", "turbo"] 
            response = None
            
            for model_name in models_to_try:
                try:
                    current_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?model={model_name}&width=768&height=1024&nologo=true"
                    print(f"Resim Deneniyor ({model_name}): {current_url}")
                    
                    # İstek at
                    resp = requests.get(current_url, timeout=50)
                    
                    # Eğer başarılıysa ve içerik resimse döngüden çık
                    if resp.status_code == 200 and "image" in resp.headers.get("content-type", ""):
                        response = resp
                        print(f"✅ Başarılı Model: {model_name}")
                        break
                    else:
                        print(f"⚠️ {model_name} başarısız (Status: {resp.status_code}), sıradakine geçiliyor...")
                        
                except Exception as ex:
                    print(f"⚠️ {model_name} hatası: {ex}")
                    continue

            # Eğer hiçbir model çalışmadıysa response boş kalır
            if response and response.status_code == 200:
                print("Cloudinary'e yükleniyor...")
                upload_result = cloudinary.uploader.upload(
                    response.content, 
                    folder="ruya_alemi_resimler"
                )
                resim_url = upload_result.get("secure_url")
                print(f"Yükleme Başarılı: {resim_url}")
            else:
                print("❌ Tüm modeller başarısız oldu. Varsayılan resim kullanılıyor.")
                resim_url = "https://images.unsplash.com/photo-1444703686981-a3abbc4d4fe3?q=80&w=1000&auto=format&fit=crop"

        except Exception as e:
            print(f"Genel Resim Hatası: {e}")
            resim_url = "https://images.unsplash.com/photo-1444703686981-a3abbc4d4fe3?q=80&w=1000&auto=format&fit=crop"
        # 4. KAYIT VE SAYAÇ
        otomatik_tarih = datetime.now().strftime("%d.%m.%Y")
        
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

# ----5. SEMBOL İŞLEMLERİ ----
@app.post("/sembol-ara")
def sembol_ara(istek: SembolIstegi, db: Session = Depends(get_db)):
    try:
        user_profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == istek.user_id).first()
        secilen_yorumcu = user_profile.interpreter_type if user_profile and user_profile.interpreter_type else "psychological"
        
        persona_role = ""
        if secilen_yorumcu == "religious":
            persona_role = "Islamic Dream Interpreter (Ibn Sirin style). Focus on divine signs."
        elif secilen_yorumcu == "spiritual":
            persona_role = "Spiritual Mystic. Focus on energy and universal symbols."
        else:
            persona_role = "Psychologist (Jungian). Focus on archetypes."

        prompt = f"""
        Role: {persona_role}
        Task: Define the dream symbol "{istek.sembol}" strictly in 2-3 sentences.
        Constraint: Output MUST be in the same language as the symbol provided by the user.
        Direct answer only, no intros.
        """

        chat = model.start_chat(history=[])
        response = chat.send_message(prompt)
        
        return {"sembol": istek.sembol, "anlam": response.text}

    except Exception as e:
        print(f"Sembol Arama Hatası: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- SUNUCUYU BAŞLAT ---
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)