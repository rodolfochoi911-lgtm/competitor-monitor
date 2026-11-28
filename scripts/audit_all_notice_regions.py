"""Check every current event with isolated headless browsers. No notifications."""
import json
import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notice_regions import extract_regions, region_text


def worker(index, cases, dest):
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-extensions')
    options.add_argument('--blink-settings=imagesEnabled=false')
    options.add_argument('--user-data-dir=' + str(dest / ('profile-' + str(index))))
    options.page_load_strategy = 'eager'
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(25)
    results = []
    try:
        for case_index, case in cases:
            row = dict(case, index=case_index)
            try:
                try:
                    driver.get(case['url'])
                except TimeoutException:
                    pass
                extracted = None
                def ready(d):
                    nonlocal extracted
                    extracted = extract_regions(d.page_source, case['url'], region_text)
                    return extracted is not None and extracted['_notice_complete']
                try:
                    WebDriverWait(driver, 6, poll_frequency=0.5).until(ready)
                except TimeoutException:
                    pass
                row.update(result=extracted, final_url=driver.current_url)
                (dest / f'{case_index}.html').write_text(driver.page_source, encoding='utf-8')
                print(case_index, case['company'], 'OK' if extracted and extracted['_notice_complete'] else 'CHECK',
                      extracted.get('_notice_missing_regions') if extracted else '', flush=True)
            except Exception as error:
                row['error'] = str(error)
                print(case_index, case['company'], 'ERROR', type(error).__name__, flush=True)
            results.append(row)
            (dest / f'results-{index}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        driver.quit()
    return results


def main():
    cases = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    dest = Path(sys.argv[2]).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    os.environ['SE_CACHE_PATH'] = str(Path('../live-notice-browser-final/selenium-cache').resolve())
    os.environ['SE_AVOID_STATS'] = 'true'
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(worker, index, list(enumerate(cases))[index::2], dest) for index in range(2)]
        results = [row for job in as_completed(jobs) for row in job.result()]
    results.sort(key=lambda row: row['index'])
    (dest / 'results.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'results':results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('TOTAL', len(results), 'COMPLETE', sum(bool((r.get('result') or {}).get('_notice_complete')) for r in results), flush=True)


if __name__ == '__main__':
    main()
