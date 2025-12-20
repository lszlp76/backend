from sqlalchemy import Column, Integer, String, Text
from database import Base
from sqlalchemy import Boolean, Date
import datetime # <--- Bu satır eklendi (Tarih işlemleri için)


class Ruya(Base):
    __tablename__ = 'ruyalar'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(100), index=True) # <--- YENİ EKLENDİ
    baslik = Column(String(255), nullable=True)
    ruya_metni = Column(Text, nullable=False)
    yorum = Column(Text, nullable=False)
    resim_url = Column(Text, nullable=True) # <--- YENİ EKLENDİ
    duygu = Column(Text, nullable=True)  # <--- YENİ EKLENDİ
    tarih = Column(String(50), nullable=True)

class UserProfile(Base):
    __tablename__ = 'user_profiles'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(100), unique=True, index=True)
    avatar_choice = Column(String(50), nullable=True)
    zodiac = Column(String(50), nullable=True)
    is_premium = Column(Boolean, default=False)
    
    # --- YENİ EKLENEN SAYAÇ (Silme işleminden etkilenmez) ---
    lifetime_usage_count = Column(Integer, default=0) 
    
    # (Eski günlük sayaçları isterseniz tutabilir veya silebilirsiniz, şimdilik kalsın)
    daily_usage_count = Column(Integer, default=0) 
    last_usage_date = Column(Date, default=datetime.date.today)