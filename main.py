import os
import urllib.parse
from datetime import datetime
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
        # Profil yoksa varsayılan değerler
        return {"choice": None, "zodiac": None, "is_premium": False}
    
    return {
        "choice": profile.avatar_choice, 
        "zodiac": profile.zodiac,
        "is_premium": profile.is_premium # <--- EKLENDİ
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

# --- 2. RÜYA ANALİZ (GÜNCELLENDİ) ---
# --- GÜNCELLENEN: ANALİZ ET (Limit Kontrolü Eklendi) ---
@app.post("/analiz-et")
def analiz_et(istek: RuyaIstegi, db: Session = Depends(get_db)):
    try:
        # 1. Kullanıcı Profilini Getir veya Oluştur
        user_profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == istek.user_id).first()
        
        if not user_profile:
            user_profile = models.UserProfile(
                user_id=istek.user_id, 
                is_premium=False, # Varsayılan Ücretsiz
                daily_usage_count=0, 
                last_usage_date=date.today()
            )
            db.add(user_profile)
            db.commit()

        # 2. Tarih Kontrolü (Gün değiştiyse sayacı sıfırla)
        bugun = date.today()
        if user_profile.last_usage_date != bugun:
            user_profile.daily_usage_count = 0
            user_profile.last_usage_date = bugun
            db.commit()

        # 3. KISITLAMA MANTIĞI: Premium değilse ve 1 hakkı dolduysa
        if not user_profile.is_premium and user_profile.daily_usage_count >= 1:
            raise HTTPException(status_code=403, detail="LIMIT_REACHED")

        # --- BURADAN SONRASI ESKİ KODLA AYNI (AI İşlemleri) ---
        user_profile_zodiac = user_profile.zodiac if user_profile.zodiac else "Unknown"
        otomatik_tarih = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        chat = model.start_chat(history=[])
        
        prompt = f"""
        Act as an expert psychologist following the Jungian school and also consider astrological archetypes.
        
        CONTEXT: The user's zodiac sign is: {user_zodiac}. 
        If the zodiac sign is known, subtly weave this into the interpretation (e.g., mention traits associated with {user_zodiac} if relevant to the dream).
        
        CRITICAL INSTRUCTION: Detect the language of the dream text provided below. 
        Provide your response (the analysis) STRICTLY IN THAT SAME LANGUAGE.
        Keep the tone constructive, insightful, and conversational.

        Dream Text: {istek.ruya_metni}
        """
        response = chat.send_message(prompt)
        ai_cevabi = response.text

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

        gorsel_prompt_istegi = f"""Based on the dream above, create a mysterious title (3-5 words) and identify the dominant emotion.
        CRITICAL INSTRUCTION: Write the title and emotion IN THE SAME LANGUAGE as the dream.
        
        Output format strictly: Title | Emotion
        (Do NOT use labels like 'Title:', 'Baslik:', 'Emotion:'. Just the values separated by a vertical bar)."""
        gorsel_response = chat.send_message(gorsel_prompt_istegi)
        gorsel_prompt = gorsel_response.text.strip()
        
        encoded_prompt = urllib.parse.quote(gorsel_prompt)
        resim_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=768&height=1024&seed={datetime.now().microsecond}&nologo=true"

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
        
        # --- ÖNEMLİ: Kullanım Sayacını Artır ---
        user_profile.daily_usage_count += 1
        
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
        return {"sonuc": f"Error: {str(e)}"}
    
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





