from sqlalchemy import Column, Integer, String, Text
from database import Base

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
    user_id = Column(String(100), unique=True, index=True) # Her kullanıcının 1 profili olur
    avatar_choice = Column(String(50), nullable=True) # 'female' (Kleopatra) veya 'male' (Akhenaton)