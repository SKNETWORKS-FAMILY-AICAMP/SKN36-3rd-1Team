"""Issue #116: v1.3.2(data/handoff) 실데이터 테스트를 v1.3.2 데이터셋에서만 실행하는 조건.

기본 데이터셋은 v1.4.0(data/releases/v1.4.0-candidate)이다(PR #22). 아래 가드가 붙은 테스트는
v1.3.2 인계 패키지의 실제 파일·건수·해시를 기대값으로 삼으므로, 기본 데이터셋에서는 기대값을 바꾸지 않고
이유를 밝혀 건너뛴다.

데이터셋은 database.handoff_data를 import할 때 한 번 정해지므로(HANDOFF_DIR), 테스트 안에서
환경변수를 바꿔도 적용되지 않는다. v1.3.2 테스트는 pytest를 따로 실행해 확인한다.

    LIFEPOLICY_DATASET_DIR=data/handoff python -m pytest tests/unit
"""
import unittest

from database import handoff_data as hd

SKIP_REASON = ("NOT_APPLICABLE: v1.3.2(data/handoff) 실데이터 테스트 — 현재 데이터셋이 v1.3.2가 아님. "
               "LIFEPOLICY_DATASET_DIR=data/handoff로 따로 실행")

legacy_dataset_only = unittest.skipUnless(hd.IS_LEGACY_DATASET, SKIP_REASON)

# 반대 방향: legacy 파일 목록(tests/README.md 2번 명령)에 섞여 있는 v1.4.0 기대값 테스트.
# v1.3.2로 실행할 때 이 파일들을 함께 돌려도 실패하지 않도록 건너뛴다.
RELEASE_SKIP_REASON = ("NOT_APPLICABLE: v1.4.0 실데이터 테스트 — 현재 데이터셋이 v1.3.2(data/handoff)임. "
                       "기본 데이터셋으로 실행")

release_dataset_only = unittest.skipIf(hd.IS_LEGACY_DATASET, RELEASE_SKIP_REASON)
