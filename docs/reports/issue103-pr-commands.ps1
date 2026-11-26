# 검토/승인 후 사용자가 직접 실행. Codex는 실행하지 않음.
Set-Location 'C:\Users\Playdata\Documents\ChatGPT\4th 단위 프로젝트\issue103-pr-ready-20261126'
git status --short
git branch --show-current
git diff --check
# 아래 브랜치인지 확인: fix/103-sejong-apply-url-final
git add -- 'ai/prompts/system_prompt.py' 'backend/api/policy_catalog.py' 'backend/rag/pipeline.py' 'backend/rag/application_links.py' 'tests/integration/test_issue103_guidance.py' 'tests/integration/test_issue103_actual_response_replay.py' 'tests/fixtures/issue103-r3d-mixed-url-response.json' 'tests/manual/verify_issue103_r3d_readonly.py' 'tests/unit/test_issue103_readonly_verifier.py' 'docs/reports/issue103-api-verification-20261126.md' 'docs/reports/issue103-api-verification-20261126.json' 'docs/reports/issue103-final-readiness-20261126.md' 'docs/reports/issue103-final-readiness-20261126.json' 'docs/reports/issue103-pr-description.md' 'docs/reports/issue103-pr-commands.ps1'
git diff --cached --check
git diff --cached --stat
git diff --cached
# staged 내용을 확인한 다음 실행
git commit -m 'fix: correct Sejong application guidance and K-pass catalog search (#103)'
git push -u origin fix/103-sejong-apply-url-final
# 웹 PR: base=dev, compare=fix/103-sejong-apply-url-final
Start-Process 'https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN36-3rd-1Team/compare/dev...fix/103-sejong-apply-url-final?expand=1'
# PR 본문: docs/reports/issue103-pr-description.md
