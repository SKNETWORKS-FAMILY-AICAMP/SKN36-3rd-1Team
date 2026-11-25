"""Issue #105: r2 실데이터 테스트를 r2 릴리스에만 적용하는 조건.

#91 r2 도구의 기대값(manifest d48ef11b…, B2 1,139, 노드 1,126 등)은 r2 릴리스의 사실이다.
최신 데이터(#95·#96·#98·#104)가 병합되면 현재 체크아웃 릴리스가 r2가 아니므로, 실제 릴리스 파일을 읽는 r2 테스트는
기대값을 바꾸지 않고 이유를 밝혀 건너뛴다. 최신 릴리스 검증은 r3 테스트(test_*_r3*, test_plan_r3_vector_reuse)가 맡고,
r2 번들의 실물 호환성은 r2 ZIP에 동봉된 r2 소스(027f40b)로 확인한다. mock·fixture 기반 r2 테스트는 계속 실행된다.
"""
import unittest

from database import full_index as fi
from scripts import restore_profiles_v140 as profiles

R2_MANIFEST = profiles.R2_PROFILE["manifest_lf_sha256"]
SKIP_REASON = ("NOT_APPLICABLE: 현재 릴리스 manifest가 r2 기준(d48ef11b…)이 아님 — r2 실데이터 검증은 r2 ZIP 동봉 소스에서, "
               "최신 릴리스 검증은 r3 테스트에서 수행")


def r2_release_is_current() -> bool:
    try:
        return fi.manifest_sha256() == R2_MANIFEST
    except OSError:
        return False


r2_release_only = unittest.skipUnless(r2_release_is_current(), SKIP_REASON)
