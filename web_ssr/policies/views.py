"""정책 상세 View. 데이터 조회는 services, 화면 구성은 Template이 맡는다."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from . import services

logger = logging.getLogger(__name__)

# React(frontend/src/lib/applicationSchedule.ts receptionLabels, TurnView RECEPTION_LABEL)와 같은 문구·색.
RECEPTION_LABELS = {"before": "접수 예정", "open": "접수 중", "closed": "접수 마감", "unknown": "모집 상태 미확인"}
RECEPTION_BADGE = {"before": "b-muted", "open": "b-ok", "closed": "b-bad", "unknown": "b-muted"}


def _days_between(start: str, end: str) -> int:
    return (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days


def schedule_view(reception: str, schedule: dict[str, Any] | None, today: str,
                  evaluated_on: str | None = None) -> dict[str, str]:
    """React scheduleView와 같은 규칙. 일정이 오늘 기준이 아니거나 접수 상태와 어긋나면 날짜를 단정하지 않는다."""
    if (evaluated_on is not None and evaluated_on != today) or (schedule and schedule["evaluated_on"] != today):
        return {"status": "unknown", "label": "일정 재확인 필요", "badge": "",
                "period": "이전 기준의 접수 안내예요. 공식 공고에서 현재 일정을 확인해 주세요."}
    if schedule is None:
        label = "일정 확인 필요" if reception == "unknown" else RECEPTION_LABELS[reception]
        return {"status": reception, "label": label, "badge": "", "period": "공식 원문에서 확인해 주세요."}

    def matches(date: str | None, days: int | None) -> bool:
        return days is None if date is None else days == _days_between(schedule["evaluated_on"], date)

    start, end = schedule["days_to_start"], schedule["days_to_end"]
    consistent = matches(schedule["start_date"], start) and matches(schedule["end_date"], end) and (
        (reception == "before" and start is not None and start > 0)
        or (reception == "open" and (start is None or start <= 0)
            and (schedule["is_ongoing"] or (end is not None and end >= 0)))
        or (reception == "closed" and end is not None and end < 0))
    if not consistent:
        return {"status": "unknown", "label": "일정 확인 필요", "badge": "",
                "period": "접수 상태와 일정이 일치하지 않아 공식 공고 확인이 필요해요."}
    if reception == "before":
        badge = f"시작 D-{start}"
    elif reception == "closed":
        badge = ""
    elif schedule["is_ongoing"]:
        badge = "상시 접수"
    else:
        badge = "오늘 마감" if end == 0 else f"마감 D-{end}"
    s, e = schedule["start_date"], schedule["end_date"]
    if s and e:
        period = f"{s} ~ {e}"
    elif schedule["is_ongoing"]:
        period = f"{s}부터 · 상시 접수" if s else "상시 접수"
    elif e:
        period = f"{e}까지 (시작일 미확인)"
    elif s:
        period = f"{s}부터 (종료일 미확인)"
    else:
        period = "공식 원문에서 확인해 주세요."
    return {"status": reception, "label": RECEPTION_LABELS[reception], "badge": badge, "period": period}


def react_base() -> str | None:
    """브라우저용 React 주소(끝에 /). 설정이 안전한 http(s) 주소가 아니면 React 링크를 숨긴다."""
    base = services.safe_http_url(settings.SSR_REACT_BASE_URL)
    if not base:
        return None
    return base if base.endswith("/") else base + "/"


def react_links(policy_id: str) -> dict[str, str | None]:
    base = react_base()
    if not base:
        return {"home": None, "map": None}
    return {"home": f"{base}#/", "map": f"{base}#/map?policy={quote(policy_id, safe='')}"}


def _error(request: HttpRequest, status: int) -> HttpResponse:
    messages = {
        404: ("정책을 찾을 수 없어요", "주소가 올바른지 확인해 주세요. 정책이 목록에서 빠졌을 수도 있어요."),
        503: ("정책 정보를 잠시 불러올 수 없어요", "잠시 후 다시 시도해 주세요. 상담 화면에서는 계속 정책을 둘러볼 수 있어요."),
        500: ("일시적인 문제가 생겼어요", "잠시 후 다시 시도해 주세요."),
    }
    title, message = messages[status]
    return render(request, "policies/error.html",
                  {"status": status, "title": title, "message": message, "react_base": react_base()}, status=status)


@require_GET
def policy_detail(request: HttpRequest, policy_id: str) -> HttpResponse:
    try:
        result = services.fetch_policy(policy_id)
    except services.PolicyNotFound:
        return _error(request, 404)
    except services.PolicyServiceUnavailable as exc:
        # 서버 로그에는 사유만 남기고(주소·응답 본문 제외) 사용자에게는 일반 안내만 보여 준다.
        logger.warning("정책 상세 조회 실패: policy_id=%s reason=%s", policy_id, exc)
        return _error(request, 503)

    policy = result.policy
    today = datetime.now(services.KST).date().isoformat()
    schedule = schedule_view(policy["reception_status"], policy["application_schedule"], today, result.evaluated_on)
    context = {
        "policy": policy,
        "schedule": schedule,
        "reception_badge": RECEPTION_BADGE[schedule["status"]],
        "links": react_links(policy["policy_id"]),
        "react_base": react_base(),
        "dataset_version": result.dataset_version,
        "evaluated_on": result.evaluated_on,
    }
    return render(request, "policies/detail.html", context)


def not_found(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    return _error(request, 404)


def server_error(request: HttpRequest) -> HttpResponse:
    return _error(request, 500)
