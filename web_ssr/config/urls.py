"""루트 URL. 정책 상세(/policies/<policy_id>/)만 Django가 담당한다."""
from django.urls import include, path

urlpatterns = [
    path("policies/", include("policies.urls")),
]

# DEBUG=false일 때 형식이 틀린 주소 등 URL 단계 404도 같은 안내 화면을 쓴다.
handler404 = "policies.views.not_found"
handler500 = "policies.views.server_error"
