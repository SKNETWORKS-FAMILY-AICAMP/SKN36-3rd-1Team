"""web_ssr/.env 읽기 회귀 테스트: .env.example을 그대로 복사해도 기본값이 유지되어야 한다."""
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from config.settings import BASE_DIR, _load_local_env

KEYS = ("DJANGO_DEBUG", "DJANGO_ALLOWED_HOSTS", "SSR_FASTAPI_BASE_URL", "SSR_REACT_BASE_URL",
        "SSR_FASTAPI_TIMEOUT_SECONDS", "SSR_CATALOG_CACHE_SECONDS")


class LocalEnvTests(SimpleTestCase):
    def clean_env(self):
        return {k: v for k, v in os.environ.items() if k not in KEYS}

    def test_copied_env_example_does_not_blank_out_defaults(self):
        with mock.patch.dict(os.environ, self.clean_env(), clear=True):
            _load_local_env(BASE_DIR / ".env.example")
            # 실패해도 환경 변수 전체(비밀값 포함 가능)를 출력하지 않도록 키 이름만 보고한다.
            leaked = [key for key in KEYS if key in os.environ]
            self.assertEqual(leaked, [], "빈 값이 환경 변수로 설정되면 settings 기본값을 덮어쓴다")
            self.assertEqual(os.environ.get("SSR_FASTAPI_BASE_URL", "http://127.0.0.1:8000"), "http://127.0.0.1:8000")

    def test_filled_values_are_read_and_os_env_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("DJANGO_DEBUG=true\nSSR_FASTAPI_BASE_URL='http://127.0.0.1:8120'\n"
                                "SSR_REACT_BASE_URL=http://127.0.0.1:5120/\n", encoding="utf-8")
            env = self.clean_env() | {"SSR_REACT_BASE_URL": "http://os.example/"}
            with mock.patch.dict(os.environ, env, clear=True):
                _load_local_env(env_file)
                self.assertEqual(os.environ["DJANGO_DEBUG"], "true")
                self.assertEqual(os.environ["SSR_FASTAPI_BASE_URL"], "http://127.0.0.1:8120")
                self.assertEqual(os.environ["SSR_REACT_BASE_URL"], "http://os.example/")
