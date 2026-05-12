import os

SECRET_KEY = os.environ.get('SECRET_KEY', 'cambiar-en-produccion-secret-key-aqui')

# Usar PostgreSQL en Railway, SQLite en local
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///academy.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
SQLALCHEMY_DATABASE_URI = _db_url
SQLALCHEMY_TRACK_MODIFICATIONS = False

STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

ACADEMY_NAME = os.environ.get('ACADEMY_NAME', 'Marca Atractora')
