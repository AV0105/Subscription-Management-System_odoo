import os
from dotenv import load_dotenv

# Load variables from .env file into environment
load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-fallback-secret-key')
    
    # DB connection string from .env or construct fallback
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL', 
        'mysql+pymysql://root:yourpassword@localhost:3306/subscription_db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Optional business defaults added by Person 2
    DEFAULT_PAYMENT_TERMS_DAYS = int(os.getenv('DEFAULT_PAYMENT_TERMS_DAYS', 30))
    DEFAULT_TAX_RATE = float(os.getenv('DEFAULT_TAX_RATE', 0.18))