"""저장소 루트에서 pytest를 실행해도 web_ssr 테스트가 Django 테스트 설정으로 동작하게 한다.

기본 실행 방법은 `cd web_ssr; python manage.py test policies --settings=config.settings_test`이다.

Django는 web_ssr/requirements.txt로 따로 설치한다. 루트 requirements.txt만 설치한 환경에서는
Django 테스트(policies/)만 수집에서 빼고 이유를 출력한다. 다른 프로젝트 테스트 수집에는 영향이 없다.
Django가 설치돼 있으면 아래 import·setup을 그대로 실행하므로 Django 내부 오류는 숨기지 않는다.
"""
import importlib.util
import os
import sys
from pathlib import Path

WEB_SSR_DIR = Path(__file__).resolve().parent

if importlib.util.find_spec("django") is None:
    # 설치 여부만 확인한다(find_spec은 모듈을 실행하지 않음). 설치된 Django의 import 오류는 이 분기에 오지 않는다.
    # config/settings_test.py도 pytest 기본 패턴(*_test.py)에 걸려 Django를 import하므로 함께 뺀다.
    collect_ignore = ["policies", "config"]
    DJANGO_SKIP_MESSAGE = ("web_ssr: Django가 설치되지 않아 Django 테스트(web_ssr/policies)를 수집하지 않았습니다. "
                           "실행하려면: python -m pip install -r web_ssr/requirements.txt")

    def pytest_terminal_summary(terminalreporter):
        terminalreporter.write_line(DJANGO_SKIP_MESSAGE, yellow=True)
else:
    if str(WEB_SSR_DIR) not in sys.path:
        sys.path.insert(0, str(WEB_SSR_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_test")

    import django
    from django.test.utils import setup_test_environment

    django.setup()
    # manage.py test와 같이 템플릿 사용 기록(assertTemplateUsed) 등 테스트 환경을 켠다.
    setup_test_environment()
