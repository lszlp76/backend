import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

# 1. Ortamdan Veritabanı URL'ini almaya çalış (Render'da bu dolu gelir)
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

# 2. Render bazen 'postgres://' verir, SQLAlchemy 'postgresql://' ister. Düzeltme:
if SQLALCHEMY_DATABASE_URL and SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 3. Eğer URL yoksa (Yani Localdeysen) SQLite kullan
if not SQLALCHEMY_DATABASE_URL:
    SQLALCHEMY_DATABASE_URL = "sqlite:///./ruyalar.db"

# 4. Motoru (Engine) Başlat
if "sqlite" in SQLALCHEMY_DATABASE_URL:
    # SQLite için özel ayar (check_same_thread)
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    # PostgreSQL için (Render)
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()