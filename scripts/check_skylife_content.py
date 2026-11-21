"""Read-only live verification of Skylife events; never writes production data."""
import argparse
import json
import os
import sys
from pathlib import Path
from selenium import webdriver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skylife_content import extract_skylife_content
from notice_observation import collect_verified_notice


def main():
    args = argparse.ArgumentParser()
    args.add_argument('destination')
    args.add_argument('urls', nargs='+')
    options = args.parse_args()
    dest = Path(options.destination).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('SE_CACHE_PATH', str(dest / 'selenium-cache'))
    os.environ['SE_AVOID_STATS'] = 'true'
    chrome = webdriver.ChromeOptions()
    chrome.add_argument('--headless=new')
    chrome.add_argument('--window-size=1920,1080')
    chrome.add_argument('--blink-settings=imagesEnabled=false')
    chrome.page_load_strategy = 'eager'
    driver = webdriver.Chrome(options=chrome)
    driver.set_page_load_timeout(35)
    results = []
    try:
        for index, url in enumerate(options.urls):
            try:
                driver.get(url)
                result = collect_verified_notice(driver, url, extract_skylife_content)
                row = {'url': url, 'result': result}
                print(url, result['_notice_observation']['status'], flush=True)
            except Exception as error:
                row = {'url': url, 'error': str(error)}
                print(url, str(error), flush=True)
            (dest / f'{index}.html').write_text(driver.page_source, encoding='utf-8')
            results.append(row)
            (dest / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
