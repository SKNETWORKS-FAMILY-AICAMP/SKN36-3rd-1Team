#!/usr/bin/env python
"""SSOK 정책 상세 SSR(Django) 관리 명령. web_ssr 폴더에서 실행한다."""
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
