from .settings import *
from dotenv import load_dotenv

load_dotenv()

DEBUG = True
ALLOWED_HOSTS = ["172.20.10.5", "127.0.0.1", "localhost"]

# Optional: if you want dev to always use the fallback key
# (better: set DJANGO_SECRET_KEY locally)
