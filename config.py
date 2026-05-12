import os

SECRET_KEY              = os.environ.get('SECRET_KEY', 'cambiar-en-produccion-secret-key-aqui')
SQLALCHEMY_DATABASE_URI = 'sqlite:///academy.db'
SQLALCHEMY_TRACK_MODIFICATIONS = False

STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

ACADEMY_NAME = os.environ.get('ACADEMY_NAME', 'Mi Academia')
