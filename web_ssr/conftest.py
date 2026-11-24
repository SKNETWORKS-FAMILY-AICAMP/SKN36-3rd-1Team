"""저장소 루트에서 pytest를 실행해도 web_ssr 테스트가 Django 테스트 설정으로 동작하게 한다.

기본 실행 방법은 `cd web_ssr; python manage.py test policies --settings=config.settings_test`이다.
"""
import os
import sys
from pathlib import Path

WEB_SSR_DIR = Path(__file__).resolve().parent
if str(WEB_SSR_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_SSR_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_test")

import django  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

django.setup()
# manage.py test와 같이 템플릿 사용 기록(assertTemplateUsed) 등 테스트 환경을 켠다.
setup_test_environment()
