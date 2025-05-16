from decouple import config
from sqlalchemy.engine import URL

TELEGRAM_TOKEN = config('TELEGRAM_TOKEN')
DATABASE_URL = config('DATABASE_URL')