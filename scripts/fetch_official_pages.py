"""공개 HTML 원문 확보 보조 스크립트. 접근 허용 여부 및 각 기관 약관 확인 후 실행."""
from pathlib import Path
from urllib.parse import urlparse
import csv, time, requests, hashlib, json
root=Path(__file__).resolve().parents[1]
registry=root/'data/metadata/source_registry.csv'
out=root/'data/raw/html';out.mkdir(parents=True,exist_ok=True)
manifest=[]
with registry.open(encoding='utf-8-sig',newline='') as fp:
    rows=list(csv.DictReader(fp))
session=requests.Session();session.headers['User-Agent']='LifePolicyAI-StudentResearch/0.1 (respect robots and terms)'
for i,row in enumerate(rows,1):
    if row['kind'] not in ('official_html','notice_pdf_attachment','pdf_attachment'):
        continue
    u=row['source_url']
    entry={'name':row['source_name'],'url':u,'status':'failed'}
    try:
        r=session.get(u,timeout=18)
        entry['http_status']=r.status_code
        entry['content_type']=r.headers.get('content-type','')
        r.raise_for_status()
        if 'text/html' not in entry['content_type'] and not r.text.lstrip().lower().startswith('<!doctype html'):
            entry['status']='non_html_skip'
        else:
            path=out/f'source_{i:03}.html';path.write_bytes(r.content)
            entry.update({'status':'saved_html','path':str(path.relative_to(root)),'sha256':hashlib.sha256(r.content).hexdigest()})
    except Exception as exc:entry['error']=str(exc)[:250]
    manifest.append(entry)
    time.sleep(2)
(root/'data/metadata/fetch_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
print('checked',len(manifest),'saved',sum(m['status']=='saved_html' for m in manifest))
print('주의: HTML만 저장하며 PDF 첨부 자동 다운로드는 하지 않습니다. 공식 다운로드 링크를 직접 확인하세요.')
