from app import create_app
from extensions import db

app = create_app()

with app.app_context():
    print("Dropping all existing tables...")
    db.drop_all()
    print("Recreating tables with the latest schema...")
    db.create_all()
    print("✅ Done! Database schema is now fully up to date.")