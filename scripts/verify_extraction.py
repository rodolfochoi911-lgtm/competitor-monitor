"""Live regression check of the reported empty-body page; no notifications or commits."""
import glob
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.pop('SLACK_WEBHOOK_URL', None)
from main import setup_driver
from content_extractor import extract_page_content
from monitor_core import preserve_unavailable_fields, collection_warnings
import parser
parser.slack_webhook_url = None


def main():
    company = 'SKT 다이렉트'
    url = 'https://shop.tworld.co.kr/nf/index_nf_yp_plan.html?exhibitionId=P00000326'
    previous_path = sorted(glob.glob('data/data_*.json'))[-1]
    with open(previous_path, encoding='utf-8') as f:
        old = json.load(f)[company][url]
    driver = setup_driver()
    try:
        driver.get(url)
        extracted = extract_page_content(driver, url)
    finally:
        driver.quit()
    record = {key: old.get(key, '') for key in ('title', 'img')}
    record.update({key: extracted[key] for key in ('main_content', 'notice', 'full_text')})
    current, previous = {company: {url: record}}, {company: {url: old}}
    preserve_unavailable_fields(current, previous, os.path.basename(previous_path))
    assert not old.get('main_content') or record['main_content']
    assert not old.get('notice') or record['notice']
    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as temp:
        try:
            os.chdir(temp)
            Path('data').mkdir()
            for filename, value in [('data_20260908_000000.json', previous), ('data_20260908_000001.json', current)]:
                Path('data', filename).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            parser.run_parser()
            assert Path('data/dashboard_latest.csv').is_file()
        finally:
            os.chdir(original_cwd)
    message = (f"Reported page live fetch + parser passed. "
               f"Body: {len(extracted['main_content'])} chars; "
               f"notice: {len(extracted['notice'])} chars; "
               f"retained-field warnings: {len(collection_warnings(current))}. "
               "No notifications sent and no repository data changed.")
    print(message)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(message + '\n')


if __name__ == '__main__':
    main()
