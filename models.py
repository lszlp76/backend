from sqlalchemy import Column, Integer, String, Text
from database import Base

class Ruya(Base):
    __tablename__ = 'ruyalar'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(100), index=True) # <--- YENİ EKLENDİ
    baslik = Column(String(255), nullable=True)
    ruya_metni = Column(Text, nullable=False)
    yorum = Column(Text, nullable=False)
    tarih = Column(String(50), nullable=True)