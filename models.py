from sqlalchemy import Column, Integer, String, Text
from database import Base

class Ruya(Base):
    __tablename__ = 'ruyalar'

    id = Column(Integer, primary_key=True, index=True)
    ruya_metni = Column(Text, nullable=False)   # Kullanıcının girdiği rüya
    yorum = Column(Text, nullable=False)        # Yapay zekanın cevabı
    tarih = Column(String(50), nullable=True)   # Tarih bilgisi