"""Exercise the actual extractor in a separate headless Chrome, without alerts."""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from selenium import webdriver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from content_extractor import extract_page_content
from notice_observation import collect_verified_notice
from check_notice_regions_live import CASES


def main():
    dest = Path(sys.argv[1]).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    os.environ['SE_CACHE_PATH'] = str(dest / 'selenium-cache')
    os.environ['SE_AVOID_STATS'] = 'true'
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--user-data-dir=' + str(dest / 'chrome-profile'))
    options.page_load_strategy = 'eager'
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(35)
    results = []
    try:
        for label, url in CASES:
            try:
                driver.get(url)
                result = collect_verified_notice(driver, url, extract_page_content)
                (dest / f'{label}.html').write_text(driver.page_source, encoding='utf-8')
                row = {'label': label, 'url': url, 'final_url': driver.current_url,
                       'observation': result.get('_notice_observation'), 'notice': result.get('notice'),
                       'regions': result.get('_notice_regions'), 'missing': result.get('_notice_missing_regions')}
                print(label, row['observation']['status'], 'panels', len(row['regions'] or []), 'missing', row['missing'], flush=True)
            except Exception as error:
                row = {'label': label, 'error': str(error)}
                print(label, 'ERROR', type(error).__name__, flush=True)
            results.append(row)
            (dest / 'results.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(), 'results':results}, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
