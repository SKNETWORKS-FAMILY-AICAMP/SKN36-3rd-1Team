"""
SSOK - 데이터 정제·정규화 및 중복 병합 모듈 (collectors/preprocessing/normalize.py)

역할:
1. HTML/PDF 원문에서 UI/메뉴/자동로그아웃/뷰어 노이즈를 제거하고 순수 정책 본문만 추출
2. 온통청년(YouthCenter), 정부24(Gov24), 복지로(Bokjiro) Open API 응답을 공통 Policy/Document 스키마로 변환
3. 다중 출처(API 간 및 기존 수동 검증 정책 간) 중복 정책 탐지 및 대표 policy_id 기준 통합
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from pypdf import PdfReader

from collectors.preprocessing.application_urls import application_url_or_none, first_application_url

UI_NOISE_EXACT_LINES = {
    "공유하기",
    "페이스북",
    "트위터",
    "네이버블로그",
    "카카오톡",
    "URL복사",
    "글자크기",
    "크게",
    "작게",
    "초기화",
    "인쇄",
    "목록",
    "점자다운로드",
    "뷰어다운로드",
    "전체다운로드",
    "미리보기",
    "다운로드",
    "닫기",
    "레이어 닫기",
    "홈",
    "고객서비스",
    "공지사항",
    "개인상품",
    "주택전세자금대출",
    "주택구입자금대출",
    "주택월세자금대출",
    "도시재생상품",
    "기업상품",
    "기금소개",
    "제도소개",
    "이용안내",
    "신청안내",
    "자주하는 질문",
    "자주하는질문",
    "대출안내",
    "이용절차 및 제출서류",
    "온라인신청",
    "계산하기",
    "조회된 데이터가 없습니다.",
    "첨부파일이 없습니다.",
}

UI_NOISE_SUBSTRINGS = [
    "센스리더를 사용하시는 분은",
    "자동 로그아웃 안내",
    "로그인 시간을 연장하시겠습니까",
    "이용에 불편을 드려 죄송합니다",
    "새창으로 열림",
    "클립보드에 복사",
]


def compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_lines(raw_text: str) -> str:
    """불필요한 공백, 중복 빈 줄, UI 버튼 단독 라인을 제거한다."""
    cleaned: List[str] = []
    prev_line = None
    for line in raw_text.splitlines():
        s = re.sub(r"[ \t]+", " ", line).strip()
        if not s:
            continue
        if s in UI_NOISE_EXACT_LINES:
            continue
        if any(sub in s for sub in UI_NOISE_SUBSTRINGS):
            continue
        if s == prev_line:
            continue
        cleaned.append(s)
        prev_line = s
    return "\n".join(cleaned)


def extract_pdf_text(path: Path) -> Tuple[str, List[Tuple[int, str]]]:
    """PDF에서 전체 텍스트와 페이지별 텍스트 리스트를 반환한다."""
    reader = PdfReader(str(path))
    pages: List[Tuple[int, str]] = []
    full_parts: List[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        txt = (page.extract_text() or "").strip()
        if txt:
            pages.append((idx, txt))
            full_parts.append(f"--- [Page {idx}] ---\n{txt}")
    return "\n\n".join(full_parts), pages


def extract_nhuf_html(path: Path) -> str:
    """주택도시기금(NHUF) HTML에서 브레드크럼·탭·버튼 노이즈를 제거하고 본문만 추출한다."""
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    container = (
        soup.select_one(".contents")
        or soup.select_one("#contArea")
        or soup.select_one("#container")
    )
    if not container:
        return ""

    for tag in container.find_all(["script", "style", "nav", "button"]):
        tag.decompose()
    for selector in [
        ".lnb",
        ".location",
        ".page_Path",
        ".path",
        ".tab_menu",
        ".tab_list",
        ".tabArea",
        ".btn_wrap",
        ".btnArea",
        ".sns_wrap",
        ".share",
    ]:
        for el in container.select(selector):
            el.decompose()

    text = clean_lines(container.get_text("\n"))
    return text


def extract_lh_notice_html(path: Path) -> str:
    """
    LH 웹 상세공고 HTML(DOC_LH_003)에서 센스리더 안내, GNB 메뉴, 자동로그아웃 모달,
    SNS 공유/점자/뷰어 버튼, 빈 테이블을 제거하고 순수 공고 본문만 정제한다.
    """
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    section = soup.select_one("#sub_container > section") or soup.find(id="sub_container")
    if not section:
        section = soup.find(id="container") or soup.find("body")
    if not section:
        return ""

    for tag in section.find_all(["script", "style", "nav", "button"]):
        tag.decompose()

    for selector in [
        ".lyPop",
        ".btns",
        ".sns_box",
        ".share_box",
        ".viewer_down",
        ".braille",
        ".location",
        "#header",
        "#footer",
    ]:
        for el in section.select(selector):
            el.decompose()

    # 빈 테이블('조회된 데이터가 없습니다') 제거
    for tr in section.find_all("tr"):
        if "조회된 데이터가 없습니다" in tr.get_text():
            tr.decompose()

    lines = clean_lines(section.get_text("\n"))
    # 파일 다운로드 및 LH 전용 UI 버튼 잡음 정리
    lh_ui_noise = {
        "당첨자조회",
        "예비자조회",
        "소득계산방법",
        "정정공고 바로가기",
        "취소공고 바로가기",
        "원본공고 바로가기",
        "바로보기",
        "점자뷰어",
        "점자음성",
        "문서뷰어다운로드",
        "한컴오피스 뷰어",
        "Acrobat Reader",
        "다운로드",
        "미리보기",
        "점자다운로드",
        "뷰어다운로드",
        "전체다운로드",
    }
    filtered: List[str] = []
    for line in lines.splitlines():
        s_line = line.strip()
        if s_line in lh_ui_noise:
            continue
        if s_line.startswith("공유하기") or s_line.startswith("글자크기") or s_line.endswith("바로가기"):
            continue
        filtered.append(s_line)
    return "\n".join(filtered)


def extract_work24_html(path: Path) -> str:
    """고용24(Work24) HTML에서 좌측/상단 메뉴를 제외한 본문 영역(.cont_wrap)만 추출한다."""
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    body = (
        soup.select_one("#content .cont_wrap")
        or soup.find(id="content")
        or soup.find(id="container")
    )
    if not body:
        return ""

    for tag in body.find_all(["script", "style", "nav", "button"]):
        tag.decompose()
    for selector in [".location", ".tit_util", ".btn_group", ".sns_area", ".lnb"]:
        for el in body.select(selector):
            el.decompose()

    return clean_lines(body.get_text("\n"))


def parse_bokjiro_detail_xml(xml_path: Path) -> Dict[str, Any]:
    """복지로 상세 XML(NationalWelfaredetailedV001)을 파싱하여 구조화된 사전으로 반환한다."""
    raw_xml = xml_path.read_text(encoding="utf-8")
    root = ET.fromstring(raw_xml)

    def get_text(tag: str) -> str:
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    # 구비서류 및 온라인신청 사이트 추출
    req_docs: List[str] = []
    apply_urls: List[str] = []
    inquiries: List[str] = []
    legal_basis: List[str] = []

    for node in root.findall("basfrmList"):
        doc_nm = (node.findtext("servSeDetailNm") or "").strip()
        if doc_nm and doc_nm not in req_docs:
            req_docs.append(doc_nm)

    for node in root.findall("applmetList"):
        nm = (node.findtext("servSeDetailNm") or "").strip()
        link = (node.findtext("servSeDetailLink") or "").strip()
        if link and link.startswith("http") and link not in apply_urls:
            apply_urls.append(link)
        elif nm and nm.startswith("http") and nm not in apply_urls:
            apply_urls.append(nm)

    for node in root.findall("inqplCtadrList"):
        nm = (node.findtext("servSeDetailNm") or "").strip()
        tel = (node.findtext("servSeDetailLink") or "").strip()
        if nm or tel:
            inquiries.append(f"{nm} ({tel})".strip(" ()"))

    for node in root.findall("baslawList"):
        law = (node.findtext("servSeDetailNm") or "").strip()
        if law and law not in legal_basis:
            legal_basis.append(law)

    serv_id = get_text("servId")
    serv_nm = get_text("servNm")
    jur_mnof = get_text("jurMnofNm")
    jur_org = get_text("jurOrgNm")
    agency = f"{jur_mnof} {jur_org}".strip() if jur_org else jur_mnof
    wlfare_info_outl = get_text("wlfareInfoOutlCn")
    tgtr_dtl = get_text("tgtrDtlCn")
    slct_crit = get_text("slctCritCn")
    alw_serv = get_text("alwServCn")
    life_array = get_text("lifeArray")
    trgter_indvdl = get_text("trgterIndvdlArray")
    intrs_thema = get_text("intrsThemaArray")
    srv_pvsn = get_text("srvPvsnNm")
    sprt_cyc = get_text("sprtCycNm")
    aply_mtd = get_text("aplyMtdNm")
    enfc_bgng = get_text("enfcBgngYmd")
    enfc_end = get_text("enfcEndYmd")

    def fmt_ymd(ymd: str) -> Optional[str]:
        if len(ymd) == 8 and ymd.isdigit():
            if ymd == "99991231":
                return None
            return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        return None

    source_url = f"https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId={serv_id}"
    apply_url = first_application_url(apply_urls, source_url)

    # 본문 텍스트 구성 (공식 XML의 모든 핵심 필드 통합)
    text_sections = [
        f"[서비스명] {serv_nm} ({serv_id})",
        f"[소관부처] {agency}",
        f"[생애주기 / 대상특성 / 관심주제] {life_array or '-'} / {trgter_indvdl or '-'} / {intrs_thema or '-'}",
        f"[제공유형 / 지원주기 / 신청방법] {srv_pvsn or '-'} / {sprt_cyc or '-'} / {aply_mtd or '-'}",
        f"[사업개요]\n{wlfare_info_outl}",
        f"[지원대상 상세]\n{tgtr_dtl}",
        f"[선정기준]\n{slct_crit}" if slct_crit else "",
        f"[지원내용(급여서비스)]\n{alw_serv}",
        f"[구비서류]\n" + "\n".join(f"- {d}" for d in req_docs) if req_docs else "",
        f"[근거법령]\n" + ", ".join(legal_basis) if legal_basis else "",
        f"[문의처]\n" + ", ".join(inquiries) if inquiries else "",
        f"[출처 URL] {source_url}",
    ]
    full_text = "\n\n".join(s for s in text_sections if s)

    return {
        "serv_id": serv_id,
        "policy_name": serv_nm,
        "agency": agency or "중앙부처",
        "life_array": life_array,
        "trgter_indvdl": trgter_indvdl,
        "intrs_thema": intrs_thema,
        "srv_pvsn": srv_pvsn,
        "sprt_cyc": sprt_cyc,
        "aply_mtd": aply_mtd,
        "summary": wlfare_info_outl,
        "target_detail": tgtr_dtl,
        "selection_criteria": slct_crit,
        "benefit": alw_serv,
        "required_documents": req_docs,
        "legal_basis": legal_basis,
        "inquiries": inquiries,
        "application_start": fmt_ymd(enfc_bgng),
        "application_end": fmt_ymd(enfc_end),
        "application_url": apply_url,
        "source_url": source_url,
        "full_text": full_text,
    }


def format_gov24_item_text(item: Dict[str, Any]) -> str:
    """정부24(Gov24) 통합 레코드(serviceList + serviceDetail + supportConditions)에서 공식 본문 텍스트를 생성한다."""
    svc_id = item.get("서비스ID", "")
    svc_nm = item.get("서비스명", "")
    org_nm = item.get("소관기관명", "") or item.get("부서명", "")
    field_nm = item.get("서비스분야", "")
    purpose = item.get("서비스목적요약", "") or item.get("서비스목적", "")
    target = item.get("지원대상", "")
    criteria = item.get("선정기준", "")
    benefit = item.get("지원내용", "")
    apply_method = item.get("신청방법", "")
    apply_period = item.get("신청기한", "")
    req_docs = item.get("구비서류", "")
    reception_org = item.get("접수기관", "") or item.get("접수기관명", "")
    contact = item.get("문의처", "") or item.get("전화문의", "")
    laws = item.get("법령", "") or item.get("자치법규", "")
    detail_url = item.get("상세조회URL", "") or f"https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{svc_id}"

    sections = [
        f"[서비스명] {svc_nm} (서비스ID: {svc_id})",
        f"[소관기관 / 서비스분야] {org_nm} / {field_nm}",
        f"[서비스목적]\n{purpose}" if purpose else "",
        f"[지원대상]\n{target}" if target else "",
        f"[선정기준]\n{criteria}" if criteria else "",
        f"[지원내용]\n{benefit}" if benefit else "",
        f"[신청기간 및 신청방법]\n- 신청기한: {apply_period or '상시/별도공고'}\n- 신청방법: {apply_method or '-'}\n- 접수기관: {reception_org or '-'}",
        f"[구비서류]\n{req_docs}" if req_docs else "",
        f"[근거법령]\n{laws}" if laws else "",
        f"[문의처] {contact}" if contact else "",
        f"[출처 URL] {detail_url}",
    ]
    return "\n\n".join(s for s in sections if s)


def format_youthcenter_item_text(item: Dict[str, Any]) -> str:
    """온통청년(YouthCenter) 정책 JSON에서 공식 본문 텍스트를 생성한다."""
    plcy_no = item.get("plcyNo", "")
    plcy_nm = item.get("plcyNm", "")
    sprvsn = item.get("sprvsnInstCdNm", "")
    oper = item.get("operInstCdNm", "")
    agency = f"{sprvsn} / {oper}".strip(" /") if oper and oper != sprvsn else (sprvsn or oper or "관계부처/지자체")
    lclsf = item.get("lclsfNm", "")
    mclsf = item.get("mclsfNm", "")
    expln = item.get("plcyExplnCn", "")
    sprt_cn = item.get("plcySprtCn", "")
    aply_mtd = item.get("plcyAplyMtdCn", "")
    srng_mtd = item.get("srngMthdCn", "")
    sbmsn_dcmnt = item.get("sbmsnDcmntCn", "")
    etc_mttr = item.get("etcMttrCn", "")
    min_age = item.get("sprtTrgtMinAge", "")
    max_age = item.get("sprtTrgtMaxAge", "")
    earn_etc = item.get("earnEtcCn", "")
    add_aply = item.get("addAplyQlfcCndCn", "")
    ptcp_prp = item.get("ptcpPrpTrgtCn", "")
    aply_ymd = item.get("aplyYmd", "")
    biz_bgng = item.get("bizPrdBgngYmd", "")
    biz_end = item.get("bizPrdEndYmd", "")
    source_url = f"https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/{plcy_no}"
    aply_url = application_url_or_none(item.get("aplyUrlAddr"), source_url)

    age_str = f"만 {min_age}세 ~ {max_age}세" if min_age or max_age else "제한없음/공고참조"

    sections = [
        f"[정책명] {plcy_nm} (정책번호: {plcy_no})",
        f"[주관/운영기관] {agency}",
        f"[정책분류] {lclsf} > {mclsf}",
        f"[정책개요]\n{expln}" if expln else "",
        f"[지원내용]\n{sprt_cn}" if sprt_cn else "",
        f"[지원대상 및 자격요건]\n- 연령: {age_str}\n- 소득요건: {earn_etc or '별도 제한 없음/공고 참조'}\n- 추가자격요건: {add_aply or '-'}\n- 참여제한대상: {ptcp_prp or '-'}",
        f"[신청기간 및 방법]\n- 신청기간: {aply_ymd or f'{biz_bgng} ~ {biz_end}'}\n- 신청절차: {aply_mtd or '-'}\n- 심사 및 발표: {srng_mtd or '-'}",
        f"[제출서류]\n{sbmsn_dcmnt}" if sbmsn_dcmnt else "",
        f"[기타 유의사항]\n{etc_mttr}" if etc_mttr else "",
        f"[신청 URL] {aply_url}" if aply_url else "",
        f"[출처 URL] {source_url}",
    ]
    return "\n\n".join(s for s in sections if s)

