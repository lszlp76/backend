import os
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
import google.generativeai as genai
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from datetime import datetime # <--- YENİ EKLENEN SATIR

# Yeni oluşturduğumuz dosyaları çağırıyoruz
import models
from database import engine, SessionLocal

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

# Gemini Ayarları
if api_key:
    genai.configure(api_key=api_key)

   # model = genai.GenerativeModel("gemini-1.5-flash")

# Yeni hali (Daha zeki, daha derin analiz yapan "Pro" versiyon):
model = genai.GenerativeModel("gemini-2.0-flash")
# Veritabanı bağlantısı başarılı mı kontrol et
db_available = False
try:
    models.Base.metadata.create_all(bind=engine)
    db_available = True
    print("✅ Veritabanı bağlantısı başarılı!")
except Exception as e:
    print(f"⚠️ Veritabanı bağlantı hatası: {str(e)}")
    print("API sunucusu veritabanı olmadan başlatılıyor...")


# --- Yeni Eklenen CORS Middleware ---
# Bu blok, tarayıcı güvenliğini aşarak Flutter Web'in API'ye erişmesine izin verir.
# Geliştirme aşamasında tüm kaynaklara izin veriyoruz.
origins = ["*"] 

# Uygulama nesnesi oluşturuluyor (middleware eklemeden önce tanımlanmalı)
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, # '*' tüm kaynaklardan gelen isteklere izin verir (Geliştirme için ideal)
    allow_credentials=True,
    allow_methods=["*"],     # GET, POST, vb. tüm metodlara izin verir
    allow_headers=["*"],     # Tüm başlık tiplerine izin verir
)
# --- CORS Sonu ---

# Veritabanı Oturumu (Dependency Injection)
def get_db():
    if not db_available:
        raise HTTPException(status_code=503, detail="Veritabanı şu anda kullanılabilir değil")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Pydantic Model (Gelen Veri Formatı)
class RuyaIstegi(BaseModel):
    ruya_metni: str


# Health Check Endpoint
@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "database": "connected" if db_available else "disconnected"
    }

# 1. Rüyayı Analiz Et ve Kaydet
@app.post("/analiz-et")
def analiz_et(istek: RuyaIstegi, db: Session = Depends(get_db)):
    try:
        # B. Otomatik Tarih Oluştur
        # now() şu anki zamanı alır.
        # strftime() zamanı istediğimiz formatta yazıya çevirir.
        # Format: Gün.Ay.Yıl Saat:Dakika
        otomatik_tarih = datetime.now().strftime("%d.%m.%Y %H:%M")
        # A. Yapay Zekaya Sor
        chat = model.start_chat(history=[])
        prompt = f"Sen Carl Gustav Jung ekolünü benimsemiş, empatik ve uzman bir psikologsun. Kullanıcı sana rüyasını anlatacak. Sen bu rüyayı semboller, arketipler ve duygusal durum açısından analiz etmelisin. Cevabın yapıcı, içgörü dolu ve sohbet havasında olsun. Şmdi şu rüyayı yorumla: {istek.ruya_metni}"
        response = chat.send_message(prompt)
        ai_cevabi = response.text

        # B. MySQL'e Kaydet
        yeni_ruya = models.Ruya(
            ruya_metni=istek.ruya_metni, 
            yorum=ai_cevabi,
            tarih= otomatik_tarih # İstersen datetime ile otomatik tarih atabiliriz
        )
        db.add(yeni_ruya)
        db.commit() # Kaydı kesinleştir
        db.refresh(yeni_ruya) # Yeni ID'yi al

        return {"sonuc": ai_cevabi, "id": yeni_ruya.id}

    except Exception as e:
        return {"sonuc": f"Hata oluştu: {str(e)}"}

# 2. Geçmiş Rüyaları Listele (Yeni Özellik!)
@app.get("/gecmis")
def gecmis_getir(db: Session = Depends(get_db)):
    ruyalar = db.query(models.Ruya).all()
    return ruyalar


# Sunucuyu başlat
if __name__ == "__main__":
    print("\n🚀 API Sunucusu başlatılıyor...")
    print("📍 http://localhost:8000")
    print("📚 Docs: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)