"""온통청년 API 실수집기. 공식 문서 기준 최소 요청 예시. 성공 응답 구조는 키 발급 후 검증할 것."""
import argparse
import os
from pathlib import Path
import requests

URL = 'https://www.youthcenter.go.kr/opi/youthPlcyList.do'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--query', default='청년')
    parser.add_argument('--page', type=int, default=1)
    parser.add_argument('--display', type=int, default=10)
    parser.add_argument('--out', default='data/raw/youth_policy_page1.xml')
    args=parser.parse_args()
    key=os.getenv('YOUTH_CENTER_API_KEY')
    if not key:
        raise SystemExit('YOUTH_CENTER_API_KEY 환경변수에 발급받은 인증키를 설정하세요.')
    params={'openApiVlak':key,'pageIndex':args.page,'display':args.display,'query':args.query}
    r=requests.get(URL,params=params,timeout=30,headers={'User-Agent':'LifePolicyAI-academic-project/0.1'})
    r.raise_for_status()
    content_type=r.headers.get('content-type','')
    if '<html' in r.text[:500].lower() or 'text/html' in content_type:
        raise RuntimeError('API 대신 HTML이 반환되었습니다. 인증 또는 엔드포인트 확인 필요')
    from xml.etree import ElementTree as ET
    try: ET.fromstring(r.content)
    except ET.ParseError as e: raise RuntimeError('XML 응답 형식이 아닙니다. 현재 공식 API 문서/오류 응답 재확인') from e
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(r.content)
    print('원본 XML 저장:',path, 'bytes:',len(r.content),'content-type:',content_type)
    print('출력 필드 매핑은 공식 코드정의서/실제 응답에 맞춰 별도 구현하세요.')
if __name__=='__main__':main()
