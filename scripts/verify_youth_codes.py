"""
LifePolicy AI — 온통청년 공식 코드정의서(API코드정보.xlsx)와 정규화 데이터 전수 비교 검증 스크립트 (v2)
raw_attributes 필드 전수 조사
"""
import openpyxl
import json
from pathlib import Path
from collections import defaultdict, Counter

BASE_DIR = Path(__file__).resolve().parent.parent
EXCEL_PATH = BASE_DIR / "docs" / "api" / "youthcenter" / "API코드정보.xlsx"
JSON_DEFS_PATH = BASE_DIR / "data" / "metadata" / "youthcenter_code_definitions.json"
RAW_YOUTH_PATH = BASE_DIR / "data" / "processed" / "youthcenter_policies.jsonl"
POLICIES_PATH = BASE_DIR / "data" / "processed" / "policies.jsonl"
CONDITIONS_PATH = BASE_DIR / "data" / "handoff" / "neo4j" / "nodes_eligibility_condition.jsonl"
EDGES_PATH = BASE_DIR / "data" / "handoff" / "neo4j" / "relationships.jsonl"

def run_verification():
    wb = openpyxl.load_workbook(EXCEL_PATH)
    print("=" * 70)
    print("1. 엑셀 4개 시트 확인 및 시트 목록")
    print("=" * 70)
    print("Sheet names:", wb.sheetnames)
    
    # Sheet 1: 코드정보
    ws1 = wb['코드정보']
    excel_codes = defaultdict(dict)
    current_group = None
    current_group_name = None

    for row in ws1.iter_rows(values_only=True):
        if not any(row): continue
        grp, grp_name, gcode, code, name = row[0], row[1], row[2], row[3], row[4]
        if grp:
            current_group = str(grp).strip()
            current_group_name = str(grp_name).strip() if grp_name else ''
        if code and name:
            code_str = str(code).strip()
            name_str = str(name).strip()
            if code_str != '코드':
                excel_codes[current_group][code_str] = name_str

    print("\n[코드정보 시트 분류별 코드 건수]")
    for grp, items in excel_codes.items():
        print(f"  - {grp}: {len(items)}개 코드")

    # Sheet 2: 정책대분류
    ws2 = wb['정책대분류']
    lclsf = [row[1] for row in ws2.iter_rows(values_only=True) if row[1] and row[1] != '정책대분류명(lclsfNm)']
    print(f"\n[정책대분류 시트] {len(lclsf)}개: {lclsf}")

    # Sheet 3: 정책중분류
    ws3 = wb['정책중분류']
    mclsf = [row[1] for row in ws3.iter_rows(values_only=True) if row[1] and row[1] != '정책중분류명(mclsfNm)']
    print(f"\n[정책중분류 시트] {len(mclsf)}개: {mclsf}")

    # Sheet 4: 정책키워드
    ws4 = wb['정책키워드']
    kywd = [row[1] for row in ws4.iter_rows(values_only=True) if row[1] and row[1] != '정책키워드명(plcyKywdNm)']
    print(f"\n[정책키워드 시트] {len(kywd)}개: {kywd}")

    print("\n" + "=" * 70)
    print("2. 코드정보 시트 vs youthcenter_code_definitions.json 1:1 비교")
    print("=" * 70)
    with open(JSON_DEFS_PATH, 'r', encoding='utf-8') as f:
        json_defs = json.load(f)

    match_count = 0
    mismatch_count = 0
    missing_count = 0

    for grp, items in excel_codes.items():
        if grp not in json_defs:
            print(f"  [MISSING GROUP] {grp} in JSON")
            missing_count += len(items)
            continue
        j_items = json_defs[grp].get('items', {})
        for c, n in items.items():
            jn = j_items.get(c)
            if jn == n:
                match_count += 1
            elif jn is None:
                print(f"  [MISSING CODE] {grp} {c} ({n})")
                missing_count += 1
            else:
                print(f"  [MISMATCH] {grp} {c}: Excel='{n}' vs JSON='{jn}'")
                mismatch_count += 1

    print(f"결과: 일치={match_count}건, 불일치={mismatch_count}건, 누락={missing_count}건")

    print("\n" + "=" * 70)
    print("3. 핵심 자격 요건 6대 코드 심층 검사")
    print("   (mrgSttsCd, earnCndSeCd, jobCd, schoolCd, plcyMajorCd, sbizCd)")
    print("=" * 70)
    target_fields = ['mrgSttsCd', 'earnCndSeCd', 'jobCd', 'schoolCd', 'plcyMajorCd', 'sbizCd']
    for tf in target_fields:
        items = excel_codes[tf]
        print(f"\n[{tf}] 공식 정의 ({len(items)}개):")
        for c, n in items.items():
            print(f"   {c} -> {n}")

    print("\n" + "=" * 70)
    print("4. 온통청년 3,861건 원천 응답(raw_attributes) 코드 사용 실측 전수 검사")
    print("=" * 70)
    
    raw_codes_used = defaultdict(Counter)
    total_raw_policies = 0
    with open(RAW_YOUTH_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            total_raw_policies += 1
            p = json.loads(line)
            raw_attr = p.get('raw_attributes', {})
            for tf in target_fields:
                val = raw_attr.get(tf)
                # sBizCd 별칭 체크
                if val is None and tf == 'sbizCd':
                    val = raw_attr.get('sBizCd')
                if val:
                    # 쉼표나 슬래시로 구분된 복수 코드 처리
                    for item in str(val).replace(';', ',').split(','):
                        item = item.strip()
                        if item:
                            raw_codes_used[tf][item] += 1

    print(f"온통청년 원천 데이터 총 {total_raw_policies}건 검사 완료.")
    for tf in target_fields:
        defined_set = set(excel_codes[tf].keys())
        used_set = set(raw_codes_used[tf].keys())
        unknown_used = used_set - defined_set
        unused = defined_set - used_set
        print(f"\n[{tf}]")
        print(f"  - 정의된 코드 수: {len(defined_set)}개")
        print(f"  - 실제 사용된 코드 수: {len(used_set)}개")
        print(f"  - 미정의(Unknown) 사용 코드: {len(unknown_used)}개 {unknown_used if unknown_used else '(없음: 100% 정상)'}")
        print(f"  - 실제 응답에서 미사용된 코드: {len(unused)}개 {[f'{c}({excel_codes[tf][c]})' for c in unused]}")
        print("  - 실제 사용 빈도 상위:")
        for c, cnt in raw_codes_used[tf].most_common(5):
            print(f"     * {c} ({excel_codes[tf].get(c, 'UNKNOWN')}): {cnt}건 ({cnt/total_raw_policies*100:.1f}%)")

    print("\n" + "=" * 70)
    print("5. 필드명 대소문자 별칭(Alias) 검토")
    print("   (sBizCd vs sbizCd, bizPrdSeCd vs bizPrdSecd)")
    print("=" * 70)
    sbiz_variants = Counter()
    bizprd_variants = Counter()
    with open(RAW_YOUTH_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            p = json.loads(line)
            raw_attr = p.get('raw_attributes', {})
            for k in raw_attr.keys():
                if k.lower() == 'sbizcd': sbiz_variants[k] += 1
                if k.lower() == 'bizprdsecd': bizprd_variants[k] += 1

    print(f"sbizCd variants found in raw_attributes: {dict(sbiz_variants)}")
    print(f"bizPrdSecd variants found in raw_attributes: {dict(bizprd_variants)}")

    print("\n" + "=" * 70)
    print("6. '제한없음', '무관', '기타' 처리 검토 및 조건 노드/관계 영향")
    print("=" * 70)
    with open(CONDITIONS_PATH, 'r', encoding='utf-8') as f:
        conditions = [json.loads(line) for line in f]
    print(f"현재 인계 패키지 내 EligibilityCondition 노드: {len(conditions)}개")
    youth_conds = [c for c in conditions if c['condition_id'].startswith(('COND_JOB_', 'COND_EDU_', 'COND_SP_'))]
    print(f"온통청년 관련 자격 조건 노드 수: {len(youth_conds)}개")
    for yc in youth_conds:
        print(f"  - {yc['condition_id']} : {yc['name']}")

    # '제한없음', '무관'이 조건 노드로 생성되었는지 확인
    open_ended = [c for c in youth_conds if any(w in c['name'] for w in ['제한없음', '무관'])]
    print(f"\n'제한없음'/'무관' 조건 노드 존재 여부: {len(open_ended)}개 {open_ended}")

    print("\n" + "=" * 70)
    print("7. 통합 데이터셋(237건) 내 온통청년 정책(POL_YOUTH_*) 자격조건 매핑 검증")
    print("=" * 70)
    with open(POLICIES_PATH, 'r', encoding='utf-8') as f:
        policies = [json.loads(line) for line in f]
    youth_integrated = [p for p in policies if p['policy_id'].startswith('POL_YOUTH_') or p['source_type'] == 'youthcenter_api']
    print(f"통합 데이터셋 내 온통청년 정책 수: {len(youth_integrated)}건")

    with open(EDGES_PATH, 'r', encoding='utf-8') as f:
        edges = [json.loads(line) for line in f]
    
    youth_pids = set(p['policy_id'] for p in youth_integrated)
    youth_cond_edges = [e for e in edges if e['source_id'] in youth_pids and e['relation'] == 'REQUIRES_CONDITION']
    print(f"온통청년 정책과 연결된 REQUIRES_CONDITION 엣지 수: {len(youth_cond_edges)}건")

    # 연결된 조건들의 유효성 확인
    target_cond_ids = set(e['target_id'] for e in youth_cond_edges)
    all_cond_ids = set(c['condition_id'] for c in conditions)
    broken_conds = target_cond_ids - all_cond_ids
    print(f"참조 불일치 조건 ID: {len(broken_conds)}건 {broken_conds if broken_conds else '(0건: 100% 무결성 유지)'}")

if __name__ == "__main__":
    run_verification()
