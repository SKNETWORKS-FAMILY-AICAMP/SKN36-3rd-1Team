"""테스트 전용 설정: python manage.py test --settings=config.settings_test

실제 FastAPI를 호출하지 않는다(테스트에서 HTTP를 mock). 비밀 키는 실행마다 임의로 만든다.
"""
import os

from django.core.management.utils import get_random_secret_key

os.environ.setdefault("DJANGO_SECRET_KEY", get_random_secret_key())

from .settings import *  # noqa: E402,F401,F403

DEBUG = False
ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]
SSR_FASTAPI_BASE_URL = "http://fastapi.internal.test:8000"
SSR_REACT_BASE_URL = "http://127.0.0.1:5173/"
SSR_CATALOG_CACHE_SECONDS = 0
