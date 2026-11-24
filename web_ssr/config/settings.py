"""SSOK 정책 상세 SSR 설정.

DB·로그인·Admin을 쓰지 않는 읽기 전용 페이지다. 정책 데이터는 기존 FastAPI(GET /api/policies)에서 받는다.
값은 OS 환경 변수 또는 web_ssr/.env에서만 읽는다(저장소 루트 .env의 API 키를 이 프로세스로 가져오지 않는다).
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_local_env(path: Path) -> None:
    """web_ssr/.env의 KEY=VALUE 줄을 읽는다. 이미 설정된 OS 환경 변수는 덮어쓰지 않는다."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_local_env(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        number = float(value)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name}는 숫자여야 합니다.") from exc
    if not minimum <= number <= maximum:
        raise ImproperlyConfigured(f"{name}는 {minimum}~{maximum} 사이여야 합니다.")
    return number


DEBUG = _env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_DEBUG=false이면 DJANGO_SECRET_KEY를 설정해야 합니다.")
    # 개발 모드에서만 프로세스마다 임시 키를 만든다(세션·로그인을 쓰지 않으므로 재시작해도 영향 없음).
    SECRET_KEY = get_random_secret_key()

ALLOWED_HOSTS = [host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
                 if host.strip()]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "policies",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    },
]

# 정책 데이터는 FastAPI가 소유한다. Django는 DB를 쓰지 않는다.
DATABASES: dict = {}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "ssok-ssr"}}

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

# React(Vite)·FastAPI 경로(/, /api/)와 겹치지 않는 접두사. 같은 도메인 프록시로 묶어도 충돌하지 않는다.
STATIC_URL = "/ssr-static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

X_FRAME_OPTIONS = "DENY"
# HTTPS로 배포할 때만 켠다(로컬 http 개발에는 끈다). 프록시가 TLS를 끝내면 X-Forwarded-Proto를 신뢰한다.
if _env_bool("DJANGO_SECURE_HTTPS", False):
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = int(_env_float("DJANGO_HSTS_SECONDS", 3600, minimum=0, maximum=31536000))
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# --- SSOK 연동 설정 ---
# Django 서버가 호출하는 FastAPI 주소(서버→서버). 사용자 입력으로 바꾸지 않는다.
SSR_FASTAPI_BASE_URL = os.environ.get("SSR_FASTAPI_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")
# 브라우저가 이동할 React 주소(관계 지도·상담 화면 링크용).
SSR_REACT_BASE_URL = os.environ.get("SSR_REACT_BASE_URL", "http://127.0.0.1:5173/").strip()
SSR_FASTAPI_CONNECT_TIMEOUT_SECONDS = _env_float("SSR_FASTAPI_CONNECT_TIMEOUT_SECONDS", 3.0, minimum=0.5, maximum=30)
SSR_FASTAPI_READ_TIMEOUT_SECONDS = _env_float("SSR_FASTAPI_TIMEOUT_SECONDS", 10.0, minimum=0.5, maximum=60)
# 정책 목록 메모리 캐시(초). 0이면 매 요청마다 FastAPI를 호출한다. 날짜가 바뀌면 캐시 키도 바뀐다.
SSR_CATALOG_CACHE_SECONDS = int(_env_float("SSR_CATALOG_CACHE_SECONDS", 60, minimum=0, maximum=3600))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"policies": {"handlers": ["console"], "level": "INFO"}},
}
