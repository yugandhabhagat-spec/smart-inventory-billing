import os
from datetime import timedelta
from decimal import Decimal

from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or (
        "sqlite:///" + os.path.join(basedir, "instance", "app.db").replace("\\", "/")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_MINUTES = 15
    BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "My Retail Shop")
    BUSINESS_ADDRESS = os.environ.get("BUSINESS_ADDRESS", "")
    BUSINESS_PHONE = os.environ.get("BUSINESS_PHONE", "")
    RETURN_POLICY = os.environ.get("RETURN_POLICY", "")
    CURRENCY = os.environ.get("CURRENCY", "Rs.")
    TIMEZONE = os.environ.get("TIMEZONE", "Asia/Kolkata")
    TAX_RATE = Decimal(os.environ.get("TAX_RATE", "0"))


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-not-for-production"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    TAX_RATE = Decimal("0")
    TIMEZONE = "UTC"
