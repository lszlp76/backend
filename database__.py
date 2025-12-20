import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

# Ortam değişkenlerini al
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "3306") # Bulamazsa varsayılan 3306 olsun
DB_NAME = os.getenv("DB_NAME")

# Bağlantı adresini dinamik olarak oluştur
# Format: mysql+pymysql://user:password@host:port/database_name
URL_DATABASE = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
print("Database URL:", URL_DATABASE)

# Engine oluşturulurken 2 kritik ayar ekliyoruz:
engine = create_engine(
    URL_DATABASE,
    pool_recycle=60,    # Her 60 saniyede bir bağlantıyı tazele (Sunucu koparmadan biz yenileyelim)
    pool_pre_ping=True  # Her sorgudan önce "Orada mısın?" diye kontrol et. Cevap yoksa yeni bağlan.
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()