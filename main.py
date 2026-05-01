import json
import os
import requests
import urllib.parse
import random
import base64
from datetime import datetime, date
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
import uvicorn
from dotenv import load_dotenv
# --- YENİ GOOGLE SDK ---
from google import genai


# --- Kendi oluşturduğumuz dosyalar ---
import models
from database import engine, SessionLocal
import uuid # Benzersiz dosya isimleri oluşturmak için
import firebase_admin
from firebase_admin import credentials, storage
# --- Ayarlar ---
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

HF_API_KEY = os.getenv("HF_API_KEY") # Hugging Face Anahtarınız

# --- Firebase Admin Başlatma ---
# --- Firebase Admin Başlatma ---
if not firebase_admin._apps:
    try:
        # Render'daki gizli değişkenden JSON verisini çekmeye çalış
        firebase_json_str = os.getenv("FIREBASE_SERVICE_ACCOUNT")
        
        if firebase_json_str:
            # 1. Senaryo: Render sunucusundayız
            cred_dict = json.loads(firebase_json_str)
            cred = credentials.Certificate(cred_dict)
            print("✅ Firebase yetkileri gizli değişkenden alındı (Render).")
        else:
            # 2. Senaryo: Kendi bilgisayarımızdayız (Localhost)
            cred = credentials.Certificate("serviceAccountKey.json")
            print("✅ Firebase yetkileri yerel dosyadan alındı (Local).")

        # DİKKAT: Kendi bucket adresinizle değiştirmeyi unutmayın!
        firebase_admin.initialize_app(cred, {
            'storageBucket': 'ruyatabircisi-b0db2.firebasestorage.app' 
        })
    except Exception as e:
        print(f"⚠️ Firebase Admin başlatılamadı: {e}")



# --- GEMINI CLIENT BAŞLATMA ---
# --- GEMINI CLIENT BAŞLATMA ---
client = None

if api_key:
    # Yeni SDK'da Client bu şekilde başlatılır
    client = genai.Client(api_key=api_key)
else:
    print("⚠️ UYARI: GEMINI_API_KEY bulunamadı!")

# --- Veritabanı Başlatma ---
db_available = False
try:
    models.Base.metadata.create_all(bind=engine)
    db_available = True
    print("✅ Veritabanı bağlantısı başarılı!")
except Exception as e:
    print(f"⚠️ Veritabanı bağlantı hatası: {str(e)}")
    print("API sunucusu veritabanı olmadan başlatılıyor...")

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

# --- Veritabanı Oturumu ---
def get_db():
    if not db_available:
        raise HTTPException(status_code=503, detail="Veritabanı aktif değil")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
#              VERİ MODELLERİ
# ==========================================

class SembolIstegi(BaseModel):
    sembol: str
    user_id: str

class RuyaIstegi(BaseModel):
    ruya_metni: str
    user_id: str
    is_premium: bool = False # <--- YENİ EKLENEN (Varsayılan False)

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
    return {"status": "ok", "mode": "Pollinations Only"}

# --- 1. PROFİL İŞLEMLERİ ---
@app.get("/get-profile/{user_id}")
def get_profile(user_id: str, db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == user_id).first()
    
    bugun = date.today()
    current_daily_usage = 0
    if profile:
        if profile.last_usage_date != bugun:
            current_daily_usage = 0
        else:
            current_daily_usage = profile.daily_usage_count
   
    if not profile:
        return {
            "choice": None, "zodiac": None, "interpreter_type": "psychological",
            "is_premium": False, "usage_count": 0
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
            user_id=data.user_id, avatar_choice=data.choice,
            zodiac=data.zodiac, interpreter_type=data.interpreter_type
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
            user_id=data.user_id, is_premium=data.is_premium,
            daily_usage_count=0, lifetime_usage_count=0, last_usage_date=date.today()
        )
        db.add(new_profile)
    else:
        profile.is_premium = data.is_premium
    db.commit()
    return {"status": "success"}

#---User ID silme

@app.delete("/delete-account/{user_id}")
def delete_account(user_id: str, db: Session = Depends(get_db)):
    try:
        # --- 1. FIREBASE STORAGE'DAKİ RESİMLERİ SİLME (YENİ) ---
        try:
            bucket = storage.bucket()
            # Sadece bu kullanıcıya ait resimleri bul
            # (Örn: ruya_resimleri/KullaniciID_ ile başlayan tüm dosyalar)
            prefix = f"ruya_resimleri/{user_id}_"
            blobs = bucket.list_blobs(prefix=prefix)
            
            silinen_resim_sayisi = 0
            for blob in blobs:
                blob.delete()
                silinen_resim_sayisi += 1
                
            print(f"✅ Firebase: {user_id} kullanıcısının {silinen_resim_sayisi} resmi başarıyla silindi.")
        except Exception as e:
            # Resim silinirken hata olsa bile hesap silme işlemini durdurmamak için except içine alıyoruz
            print(f"⚠️ Firebase resim silme hatası: {e}")
        # --------------------------------------------------------

        # --- 2. SQL VERİTABANINDAN SİLME (MEVCUT KOD) ---
        # Önce kullanıcının rüyalarını sil
        db.query(models.Ruya).filter(models.Ruya.user_id == user_id).delete()
        
        # Sonra kullanıcı profilini sil
        db.query(models.UserProfile).filter(models.UserProfile.user_id == user_id).delete()
        
        db.commit()
        return {"status": "success", "message": f"User data and {silinen_resim_sayisi} images deleted permanently"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
# --- 2. RÜYA ANALİZ (SADELEŞTİRİLMİŞ) ---
@app.post("/analiz-et")
def analiz_et(istek: RuyaIstegi, db: Session = Depends(get_db)):
    try:
        if not client:
             raise HTTPException(status_code=500, detail="Gemini API Key eksik.")

        # --- Kullanıcı Kontrolleri ---
        user_profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == istek.user_id).first()
        if not user_profile:
            # Kullanıcı yoksa oluştururken gelen 'is_premium' bilgisiyle oluştur
            user_profile = models.UserProfile(
                user_id=istek.user_id, 
               is_premium=istek.is_premium, # <--- GÜNCELLENDİ
                interpreter_type="psychological", 
                last_usage_date=date.today()
            )
            db.add(user_profile)
            db.commit()
        else:
            # Kullanıcı varsa, DURUMUNU GÜNCELLE (Aboneliği bitmiş olabilir veya yeni almış olabilir)
            # Bu satır sayesinde veritabanı her zaman Flutter/RevenueCat ile senkronize kalır.
            if user_profile.is_premium != istek.is_premium:
                user_profile.is_premium = istek.is_premium
                db.commit()
                
        bugun = date.today()
        if user_profile.last_usage_date != bugun:
            user_profile.daily_usage_count = 0
            user_profile.last_usage_date = bugun
            db.commit()

        if not user_profile.is_premium and user_profile.daily_usage_count >= 3:
            raise HTTPException(status_code=403, detail="LIMIT_REACHED")

        # --- Yorumcu Ayarları ---
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
                1. **Symbol Decoding:** Decode key symbols strictly through your persona's lens.CRITICAL: The heading "**Symbol Decoding:**" SHOULD NOT BE SHOWN AND this specific phrase MUST be translated into the **EXACT SAME LANGUAGE** as the dream content.
                2. **Personal Connection:** Connect the dream to the user's waking life.CRITICAL: The heading "**Personal Connection:**" SHOULD NOT BE SHOWN AND this specific phrase MUST be translated into the **EXACT SAME LANGUAGE** as the dream content.
                3. **Specific Advice:** Conclude with advice that fits your persona.CRITICAL: The heading "**Specific Advice:**" SHOULD NOT BE SHOWN AND this specific phrase MUST be translated into the **EXACT SAME LANGUAGE** as the dream content.
            - **Length:** Detailed and comprehensive.
            """
        else:
            ozel_talimatlar = """
            - **Constraint:** Keep the response STRICTLY under 50 words.
            - **Content:** Provide a "teaser" interpretation only. Identify the single most important symbol.
            - **Call to Action (CTA):** You MUST end the response with a sentence inviting them to premium. 
              - The sentence meaning: "To hear the full wisdom, unlock Premium."
              - **CRITICAL RULE:** This sentence MUST be in the **EXACT SAME LANGUAGE** as the rest of your interpretation. Never leave it in English if the dream is not English.
            """

        # --- ANA PROMPT BİRLEŞTİRME (GÜNCELLENDİ) ---
        prompt = f"""
            ### SYSTEM ROLE (YOUR PERSONA)
            {system_persona}

            ### USER CONTEXT
            - **Zodiac Sign:** {user_zodiac}
            - **Dream Content:** "{istek.ruya_metni}"

            ### INSTRUCTIONS

            #### 1. SAFETY & MODERATION (PRIORITY #1 - CRITICAL)
            **Before performing any analysis, you MUST evaluate the "Dream Content".**
            
            **Refuse to interpret if the content contains:**
            - Profanity, insults, or vulgar language.
            - Sexual content, erotic fantasies, or explicit themes.
            - Hate speech, extreme violence, or self-harm.
            - Any content unsuitable for a user under 13 years old.

            **IF A VIOLATION IS DETECTED:**
            - **DO NOT** interpret the dream.
            - **DO NOT** mention zodiac signs or symbols.
            - **Start your response EXACTLY with this structure:** `[VIOLATION] (REASON: <write specific keywords or reason here>) || <Warning Message>`
            
            - **Example:** `[VIOLATION] (REASON: sexual content, keyword: naked) || Please use appropriate language.`
            
            - **Warning Message Guideline:** "Please use appropriate language. I cannot interpret content containing profanity or unsuitable themes." (Translate this sentiment naturally to the target language).

            #### 2. Language Detection & Tone
            - If content is safe: Detect the language of the "Dream Content".
            - **Tone:** Your output must be **Family-Friendly (PG-13)** at all times. Even if the dream is scary, keep the interpretation constructive and safe for a younger audience.
            - **Language:** Your entire response must be in the **EXACT SAME LANGUAGE** as the dream.

            #### 3. Analysis Instructions (Only if Safe)
            {ozel_talimatlar}

            #### 4. OUTPUT FORMAT RESTRICTIONS (STRICT)
            - **DO NOT** repeat the user's dream text.
            - **DO NOT** state which language you detected (e.g., "You wrote in Turkish...").
            - **DO NOT** include introductory filler phrases like "Here is your interpretation," "Based on your dream," or "Greetings user."
            - **START DIRECTLY** with the first hwith the first sentence of the interpretation.
            - **ONLY** provide the analysis content requested in step #3.

            ### OUTPUT GENERATION
            Start analysis now.
            ### OUTPUT GENERATION
            Speak now, wise one.
        """

       # A. Gemini Sohbetini Başlat
        chat = client.chats.create(model="gemini-2.0-flash")
        response = chat.send_message(prompt)
        ai_cevabi_raw= response.text
       # --- GÜVENLİK KONTROLÜ (GÜNCELLENDİ) ---
        is_violation = "[VIOLATION]" in ai_cevabi_raw
        
        # Kullanıcıya gidecek temiz cevap varsayılan olarak ham cevaptır
        ai_cevabi = ai_cevabi_raw 

        if is_violation:
            # Formatımız: [VIOLATION] (REASON: ...) || Kullanıcı Mesajı
            violation_reason = "Belirtilmemiş"
            
            if "||" in ai_cevabi_raw:
                parts = ai_cevabi_raw.split("||")
                
                # Sol taraf (Sebep kısmı): "[VIOLATION] (REASON: xyz)" -> Temizle
                violation_part = parts[0].strip()
                violation_reason = violation_part.replace("[VIOLATION]", "").replace("(", "").replace(")", "").strip()
                
                # Sağ taraf (Kullanıcı Mesajı): "Lütfen uygun dil kullanın..."
                if len(parts) > 1:
                    ai_cevabi = parts[1].strip()
                else:
                    # Ayırıcı var ama sağ taraf boşsa
                    ai_cevabi = "Lütfen uygun bir dille rüyanızı tekrar anlatın."
            else:
                # Eğer AI formatı tutturamazsa (|| koymazsa) manuel temizlik yap
                ai_cevabi = ai_cevabi_raw.replace("[VIOLATION]", "").strip()
            
            # KONSOLA DETAYLI YAZDIR
            print(f"\n🚨 [GÜVENLİK UYARISI] İçerik Engellendi!")
            print(f"👉 Sebep/Kelimeler: {violation_reason}")
            print(f"📝 Gelen Rüya: {istek.ruya_metni[:50]}...\n")
        # Eğer ihlal varsa etiketi temizle ki kullanıcı görmesin
        if is_violation:
            ai_cevabi = ai_cevabi.replace("[VIOLATION]", "").strip()

        # Varsayılan Değerler
        ruya_basligi = "Bilinçaltı Mesajı"
        ruya_duygusu = "Nötr"
        resim_url = "https://placehold.co/768x1024/png?text=Uygunsuz+Icerik" # Güvenli varsayılan

        # SADECE İHLAL YOKSA Diğer İşlemleri Yap
        if not is_violation:
            # B. Başlık ve Duygu
            try:
                ek_bilgi_prompt = "Based on the dream above, create a mysterious title (3-5 words) and identify the dominant emotion. Use same the **EXACT SAME LANGUAGE** as the dream. Output format strictly: Title | Emotion"
                ek_response = chat.send_message(ek_bilgi_prompt)
                ek_metin = ek_response.text.strip()
                
                if "|" in ek_metin:
                    parts = ek_metin.split('|')
                    if len(parts) >= 2:
                        ruya_basligi = parts[0].strip().replace('"', '')
                        ruya_duygusu = parts[1].strip().replace('.', '')
                else:
                    ruya_basligi = ek_metin
            except:
                pass 

         
           # C. RESİM ÜRETİMİ (TOGETHER AI + FLUX + FIREBASE)
            resim_url = "https://placehold.co/768x1024/png?text=Ruya+Gunlugu" # Varsayılan

            try:
                # a. Prompt Oluştur (Bu kısım aynı kalıyor)
                gorsel_prompt = "mystic surreal dream art"
                try:
                    img_prompt_req = (
                        f"Create a short, surreal art prompt (max 8 words) for: '{istek.ruya_metni}'. "
                        "English only. IMPORTANT SAFETY RULES: The image must be suitable for people under 13. "
                        "STRICTLY NO horror, blood, gore, violence, nightmares, monsters, or disturbing imagery. "
                        "Use keywords like: ethereal, soft lighting, whimsical, fantasy."
                    )
                    img_resp = client.models.generate_content(model="gemini-2.0-flash", contents=img_prompt_req)
                    gorsel_prompt = img_resp.text.strip().replace('"', '').replace('\n', ' ')
                except Exception as e_prompt:
                    print(f"⚠️ Prompt oluşturma hatası: {e_prompt}")

                # b. Together AI'ye İstek At (YENİ VE SAĞLAM MOTOR)
                print(f"🎨 Together AI (FLUX) resim üretiyor. Prompt: {gorsel_prompt}")
                
                together_api_key = os.getenv("TOGETHER_API_KEY")
                url = "https://api.together.xyz/v1/images/generations"
                headers = {
                    "Authorization": f"Bearer {together_api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "black-forest-labs/FLUX.1-schnell", # Dünyanın en iyi ve hızlı açık kaynak modeli
                    "prompt": gorsel_prompt,
                    "width": 768,
                    "height": 1024,
                    "steps": 4,
                    "n": 1,
                    "response_format": "b64_json"
                }

                response = requests.post(url, headers=headers, json=payload, timeout=45)

                if response.status_code == 200:
                    # Gelen resmi çöz ve byte formatına getir
                    b64_data = response.json()["data"][0]["b64_json"]
                    image_bytes = base64.b64decode(b64_data)

                    # c. Firebase Storage'a Yükle (Bu kısım aynı)
                    dosya_adi = f"ruya_resimleri/{istek.user_id}_{uuid.uuid4().hex[:8]}.png"
                    bucket = storage.bucket()
                    blob = bucket.blob(dosya_adi)

                    blob.upload_from_string(image_bytes, content_type='image/png')

                    encoded_dosya_adi = urllib.parse.quote(dosya_adi, safe='')
                    resim_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{encoded_dosya_adi}?alt=media"
                    
                    print(f"✅ Resim başarıyla Firebase'e yüklendi: {resim_url}")

                else:
                    print(f"❌ Together AI Hata Döndü: {response.status_code} - {response.text}")

            except Exception as e:
                print(f"⚠️ Resim oluşturma sürecinde genel hata: {e}")
        # D. Kayıt
        otomatik_tarih = datetime.now().strftime("%d.%m.%Y")
        yeni_ruya = models.Ruya(
            user_id=istek.user_id, ruya_metni=istek.ruya_metni, baslik=ruya_basligi.strip(),
            yorum=ai_cevabi, resim_url=resim_url, duygu=ruya_duygusu.strip(), tarih=otomatik_tarih
        )
        db.add(yeni_ruya)
        
        # Hak düşümü her durumda yapılır (Kötüye kullanımı engellemek için) veya yapılmayabilir.
        # Burada engellemek adına hakkı düşüyoruz.
        user_profile.daily_usage_count += 1
        user_profile.lifetime_usage_count += 1
        db.commit()
        db.refresh(yeni_ruya)

        return {
            "baslik": ruya_basligi.strip(), "sonuc": ai_cevabi,
            "resim_url": resim_url, "duygu": ruya_duygusu.strip(), "id": yeni_ruya.id
        }

    except HTTPException as he:
        raise he 
    except Exception as e:
        print(f"Genel Hata: {e}")
        raise HTTPException(status_code=500, detail=str(e))
      #çalışan versiyon. resimlere küfür etksini engellemez

    #    # Yeni SDK'da 'start_chat' yerine 'chats.create' kullanılır.
    #     chat = client.chats.create(model="gemini-2.0-flash")
    #     response = chat.send_message(prompt)
    #     ai_cevabi = response.text

    #     # B. Başlık ve Duygu
    #     ek_bilgi_prompt = "Based on the dream above, create a mysterious title (3-5 words) and identify the dominant emotion. Use same the **EXACT SAME LANGUAGE** as the dream. Output format strictly: Title | Emotion"
    #     ek_response = chat.send_message(ek_bilgi_prompt)
    #     ek_metin = ek_response.text.strip()
        
    #     ruya_basligi = "Bilinçaltı Mesajı"
    #     ruya_duygusu = "Nötr"
    #     try:
    #         if "|" in ek_metin:
    #             parts = ek_metin.split('|')
    #             if len(parts) >= 2:
    #                 ruya_basligi = parts[0].strip().replace('"', '')
    #                 ruya_duygusu = parts[1].strip().replace('.', '')
    #         else:
    #             ruya_basligi = ek_metin
    #     except:
    #         pass 
    #     # -----------------------------------------------------------------------
    #     # C. RESİM ÜRETİMİ (HATA KONTROLLÜ VE LOGLAMALI)
    #     # -----------------------------------------------------------------------
        
    #     # 1. Varsayılan güvenli URL
    #     resim_url = "https://placehold.co/768x1024/png?text=Dream+Journal"

    #     try:
    #         # a. Prompt Oluştur
    #         gorsel_prompt = "mystic surreal dream art"
    #         try:
    #             img_prompt_req = img_prompt_req = (
    #                 f"Create a short, surreal art prompt (max 8 words) for: '{istek.ruya_metni}'. "
    #                 "English only. "
    #                 "IMPORTANT SAFETY RULES: The image must be suitable for people under 13. "
    #                 "STRICTLY NO horror, blood, gore, violence, nightmares, monsters, or disturbing imagery. "
    #                 "If the dream is scary, convert it into a soft, magical, or abstract representation. "
    #                 "Use keywords like: ethereal, soft lighting, whimsical, fantasy."
    #             )
    #             img_resp = client.models.generate_content(model="gemini-2.0-flash", contents=img_prompt_req)
    #             gorsel_prompt = img_resp.text.strip().replace('"', '').replace('\n', ' ')
    #         except Exception as e_prompt:
    #             print(f"⚠️ Prompt oluşturma hatası: {e_prompt}")

    #         # b. URL'yi Oluştur
    #         encoded_prompt = urllib.parse.quote(gorsel_prompt)
    #         random_seed = random.randint(1, 99999)
            
    #         # Oluşturulan Aday URL
    #         aday_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?model=turbo&width=384&height=512&nologo=true&seed={random_seed}"
            
            
    #         print(f"🔍 URL Kontrol Ediliyor: {aday_url}")

    #         # c. URL'ye İstek Atıp Cevabı Kontrol Et (Validation)
    #         # Not: timeout=10 veriyoruz ki sunucu yanıt vermezse sonsuza kadar beklemesin.
    #         kontrol = requests.get(aday_url, timeout=30)

    #         # Cevabın Tipi JSON ise bu bir HATADIR.
    #         if "application/json" in kontrol.headers.get("Content-Type", ""):
    #             print(f"❌ Pollinations Hata Döndü: {kontrol.text}")
    #             # Hata olduğu için resim_url varsayılan (placehold.co) kalır.
            
    #         elif kontrol.status_code == 200:
    #             print("✅ Resim Başarıyla Oluşturuldu (Sunucu Yanıtı OK).")
    #             resim_url = aday_url
            
    #         else:
    #             print(f"⚠️ Beklenmedik Durum (Kod {kontrol.status_code}): {kontrol.text}")
    #             # Güvenlik için varsayılanda kalabilir veya risk alıp url atanabilir.
    #             # Biz varsayılanda kalmasını tercih ediyoruz.

    #     except Exception as e:
    #         print(f"⚠️ Resim oluşturma sürecinde genel hata: {e}")
    #         # Hata durumunda varsayılan resim_url kullanılır.

    #     # -----------------------------------------------------------------------
       
    #     # -----------------------------------------------------------------------

    #     # D. Kayıt
    #     otomatik_tarih = datetime.now().strftime("%d.%m.%Y")
    #     yeni_ruya = models.Ruya(
    #         user_id=istek.user_id, ruya_metni=istek.ruya_metni, baslik=ruya_basligi.strip(),
    #         yorum=ai_cevabi, resim_url=resim_url, duygu=ruya_duygusu.strip(), tarih=otomatik_tarih
    #     )
    #     db.add(yeni_ruya)
        
    #     user_profile.daily_usage_count += 1
    #     user_profile.lifetime_usage_count += 1
    #     db.commit()
    #     db.refresh(yeni_ruya)

    #     return {
    #         "baslik": ruya_basligi.strip(), "sonuc": ai_cevabi,
    #         "resim_url": resim_url, "duygu": ruya_duygusu.strip(), "id": yeni_ruya.id
    #     }

    # except HTTPException as he:
    #     raise he 
    # except Exception as e:
    #     print(f"Genel Hata: {e}")
    #     raise HTTPException(status_code=500, detail=str(e))

# --- 3. DİĞER ENDPOINTLER ---
@app.get("/gecmis")
def gecmis_getir(user_id: str, db: Session = Depends(get_db)):
    return db.query(models.Ruya).filter(models.Ruya.user_id == user_id).all()

@app.delete("/ruya-sil/{id}")
def ruya_sil(id: int, db: Session = Depends(get_db)):
    ruya = db.query(models.Ruya).filter(models.Ruya.id == id).first()
    if not ruya: raise HTTPException(status_code=404, detail="Bulunamadı")
    db.delete(ruya)
    db.commit()
    return {"mesaj": "Silindi"}

@app.post("/sembol-ara")
def sembol_ara(istek: SembolIstegi, db: Session = Depends(get_db)):
    try:
        # Yeni ve Güvenli Hali:
        prompt = f"Define the dream symbol '{istek.sembol}' in 2 sentences. Content must be strictly family-friendly and safe for children under 13. Avoid all sexual, violent, or offensive descriptions. Respond in the user's language."
        resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return {"sembol": istek.sembol, "anlam": resp.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
