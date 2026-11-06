"""신청 주소와 안내 주소를 구분한다. 이 함수는 웹 요청을 보내지 않는다."""

from urllib.parse import urlsplit


def review_application_url(
    value: object, source_url: str | None = None,
) -> tuple[str | None, str]:
    """원천의 신청 필드만 받고, 사용하지 못하는 주소는 사유와 함께 비운다.

    올바른 URL 형식은 실제 접수 가능 여부를 보증하지 않는다.
    참고 URL을 이 함수에 넘겨 신청 주소로 승격시키면 안 된다.
    """
    if not isinstance(value, str) or not value.strip():
        return None, "missing_application_url"

    url = value.strip()
    try:
        parsed = urlsplit(url)
        valid_host = parsed.hostname
        parsed.port  # 잘못된 포트 표기도 ValueError로 걸러 낸다.
    except ValueError:
        return None, "invalid_url"
    if (
        parsed.scheme not in {"http", "https"}
        or not valid_host
        or parsed.username is not None
        or parsed.password is not None
        or any(char.isspace() for char in url)
    ):
        return None, "invalid_http_url"
    if source_url and url.rstrip("/") == source_url.strip().rstrip("/"):
        return None, "source_page_only"

    # 아래 경로는 저장된 공식 원본에서 확인한 안내·공고 페이지다.
    # 도메인 전체를 막으면 같은 사이트의 실제 신청 화면까지 지울 수 있다.
    host = valid_host.lower()
    path = parsed.path.lower()
    guidance_page = (
        (host in {"bokjiro.go.kr", "www.bokjiro.go.kr"} and "wlfareinfo" in path)
        or (host in {"www.gov.kr", "plus.gov.kr"} and (
            "/dtlex/" in path or "benefitsrvcdtl" in path
        ))
        or (host == "nhuf.molit.go.kr" and path.startswith("/fp/"))
        or (host == "apply.lh.or.kr" and "selectwrtancinfo" in path)
        or (host == "www.work24.go.kr" and "selectissugudn" in path)
        or (host == "www.daegu.go.kr" and path.startswith("/youthpolicy/"))
        or (host == "www.kocca.kr" and "/bbs/view/" in path)
        or host in {"law.go.kr", "www.law.go.kr"}
    )
    if guidance_page:
        return None, "guidance_or_notice_page"
    return url, "application_field_present"


def application_url_or_none(
    value: object, source_url: str | None = None,
) -> str | None:
    """수집기에서는 검토 결과 중 주소 값만 사용한다."""
    return review_application_url(value, source_url)[0]


def first_application_url(values: list[str], source_url: str) -> str | None:
    """여러 원천 링크 중 검토를 통과한 첫 번째 신청 주소를 고른다."""
    for value in values:
        url = application_url_or_none(value, source_url)
        if url is not None:
            return url
    return None
