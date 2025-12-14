import os
import urllib.parse
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
import uvicorn
import google.generativeai as genai
from dotenv import load_dotenv

# --- Kendi oluşturduğumuz dosyalar ---
import models
from ruyatabiri.databae import engine, SessionLocal

# --- Ayarlar ---
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    genai.configure(api_key=api_key)

# Model seçimi (Daha gelişmiş versiyon)
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

class RuyaIstegi(BaseModel):
    ruya_metni: str
    user_id: str

# YENİ: Avatar seçimi için veri modeli
class AvatarUpdate(BaseModel):
    user_id: str
    choice: str # 'female' veya 'male'

# ==========================================
#                 ENDPOINTLER
# ==========================================

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "database": "connected" if db_available else "disconnected"
    }

# --- 1. AVATAR / PROFİL İŞLEMLERİ (YENİ) ---

@app.get("/get-profile/{user_id}")
def get_profile(user_id: str, db: Session = Depends(get_db)):
    """Kullanıcının avatar seçimini getirir (female/male)."""
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == user_id).first()
    if not profile:
        return {"choice": None} # Henüz seçim yapmamış
    return {"choice": profile.avatar_choice}

@app.post("/set-avatar")
def set_avatar(data: AvatarUpdate, db: Session = Depends(get_db)):
    """Kullanıcının avatar seçimini kaydeder veya günceller."""
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == data.user_id).first()
    
    if not profile:
        # Profil yoksa yeni oluştur
        new_profile = models.UserProfile(user_id=data.user_id, avatar_choice=data.choice)
        db.add(new_profile)
    else:
        # Varsa güncelle
        profile.avatar_choice = data.choice
    
    db.commit()
    return {"status": "success", "choice": data.choice}


# --- 2. RÜYA ANALİZ VE KAYIT ---

@app.post("/analiz-et")
def analiz_et(istek: RuyaIstegi, db: Session = Depends(get_db)):
    try:
        otomatik_tarih = datetime.now().strftime("%d.%m.%Y %H:%M")
        chat = model.start_chat(history=[])

        # A) YORUM İSTEĞİ
        prompt = f"Sen Jung ekolünü benimsemiş uzman bir psikologsun. Kullanıcın sana ilettiği rüyayı semboller, arketipler ve duygusal durum açısından analiz etmelisin. Cevabın yapıcı, içgörü dolu ve sohbet havasında olsun. Şu rüyayı yorumla: {istek.ruya_metni}"
        response = chat.send_message(prompt)
        ai_cevabi = response.text

        # B) BAŞLIK VE DUYGU İSTEĞİ
        ek_bilgi_prompt = f"Bu rüya için 3-5 kelimelik gizemli bir başlık ve rüyadaki baskın duyguyu (tek kelime, örn: Korku, Huzur, Kaygı) bul. Format şöyle olsun: 'BAŞLIK: [Başlık] | DUYGU: [Duygu]'. Sadece bunu yaz."
        ek_response = chat.send_message(ek_bilgi_prompt)
        ek_metin = ek_response.text.strip()
        
        # Basit metin parçalama (Parsing)
        ruya_basligi = "Bilinçaltı Mesajı"
        ruya_duygusu = "Nötr"
        
        try:
            parts = ek_metin.split('|')
            if len(parts) >= 2:
                ruya_basligi = parts[0].replace("BAŞLIK:", "").strip().replace('"', '')
                ruya_duygusu = parts[1].replace("DUYGU:", "").strip()
        except:
            pass 

        # C) GÖRSEL PROMPT İSTEĞİ
        gorsel_prompt_istegi = f"Based on this dream: '{istek.ruya_metni}', create a short, vivid, surrealist art style image prompt in English. Maximum 15 words. Just the prompt, no explanation."
        gorsel_response = chat.send_message(gorsel_prompt_istegi)
        gorsel_prompt = gorsel_response.text.strip()
        
        # D) URL OLUŞTURMA (Pollinations.ai)
        encoded_prompt = urllib.parse.quote(gorsel_prompt)
        resim_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=768&height=1024&seed={datetime.now().microsecond}&nologo=true"

        # E) VERİTABANINA KAYIT
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
        db.commit()
        db.refresh(yeni_ruya)

        return {
            "baslik": ruya_basligi, 
            "sonuc": ai_cevabi, 
            "resim_url": resim_url,
            "duygu": ruya_duygusu,
            "id": yeni_ruya.id
        }

    except Exception as e:
        return {"sonuc": f"Hata oluştu: {str(e)}"}

# --- 3. GEÇMİŞ RÜYALAR ---

@app.get("/gecmis")
def gecmis_getir(user_id: str, db: Session = Depends(get_db)):
    # .filter() komutu ile sadece o kullanıcıya ait verileri süzüyoruz
    # Tersten sıralama (en yeni en üstte) için .order_by(models.Ruya.id.desc()) eklenebilir
    ruyalar = db.query(models.Ruya).filter(models.Ruya.user_id == user_id).all()
    return ruyalar

# --- 4. RÜYA SİLME ---

@app.delete("/ruya-sil/{id}")
def ruya_sil(id: int, db: Session = Depends(get_db)):
    ruya = db.query(models.Ruya).filter(models.Ruya.id == id).first()
    if ruya is None:
        raise HTTPException(status_code=404, detail="Rüya bulunamadı")
    
    db.delete(ruya)
    db.commit()
    return {"mesaj": "Rüya başarıyla silindi"}

# --- SUNUCUYU BAŞLAT ---
if __name__ == "__main__":
    print("\n🚀 API Sunucusu başlatılıyor...")
    print("📍 http://localhost:8000")
    print("📚 Docs: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)