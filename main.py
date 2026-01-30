"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템 (V33)
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-01-30 (크롤링 V31 유지 + 슬랙 전송 로직 V16 원상복구)
"""

import os
import json
import time
import glob
import re
import traceback
from datetime import datetime, timedelta, timezone
import requests
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =========================================================
# [설정] 환경 변수
# =========================================================
GITHUB_USER = "rodolfochoi911-lgtm" 
REPO_NAME = "competitor-monitor" 
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL") 

DATA_DIR = "data"
DOCS_DIR = "docs"
REPORT_DIR = "docs/reports"

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
FILE_TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")
DISPLAY_DATE = NOW.strftime("%Y-%m-%d")
DISPLAY_TIME = NOW.strftime("%H:%M:%S")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

def setup_driver():
    print("🚗 브라우저 드라이버 설정 중...")
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def remove_popups(driver):
    try:
        driver.execute_script("""
            var popups = document.querySelectorAll('.popup, .modal, .layer, .dimmed, .overlay, .toast, .banner, #popup, .close');
            popups.forEach(function(element) { element.remove(); });
        """)
    except: pass

def scroll_to_bottom(driver):
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(5): 
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height: break
            last_height = new_height
    except: pass

def clean_html(html_source):
    if not html_source: return ""
    soup = BeautifulSoup(html_source, 'html.parser')
    for tag in soup(['script', 'style', 'meta', 'noscript', 'header', 'footer', 'iframe', 'button', 'input', 'nav', 'aside']):
        tag.decompose()
    for hidden in soup.find_all(attrs={"style": True}):
        if "display:none" in hidden["style"].replace(" ", "").lower():
            hidden.decompose()
    return body.prettify() if (body := soup.find('body')) else "No Content"

def load_previous_data():
    json_files = glob.glob(os.path.join(DATA_DIR, "data_*.json"))
    if not json_files: return {}
    json_files.sort()
    latest_file = json_files[-1]
    print(f"📂 이전 데이터 로드: {os.path.basename(latest_file)}")
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

def analyze_content_changes(old_html, new_html):
    soup_old = BeautifulSoup(old_html, 'html.parser')
    soup_new = BeautifulSoup(new_html, 'html.parser')
    summary = []
    if soup_old.get_text().strip() != soup_new.get_text().strip():
        summary.append("✏️ 상세내용(텍스트) 수정")
    imgs_old = set([i['src'] for i in soup_old.find_all('img') if i.get('src')])
    imgs_new = set([i['src'] for i in soup_new.find_all('img') if i.get('src')])
    if imgs_old != imgs_new:
        summary.append("🖼️ 상세이미지 교체")
    return " / ".join(summary) if summary else "🎨 디자인/레이아웃 변경"

# [그룹 A] V16 오리지널 로직 (SKT, 유모바일, 스카이라이프)
def extract_legacy_simple(driver, container_selector, site_name):
    cards_data = {} 
    try:
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, container_selector))
        )
        
        items = []
        if "유모바일" in site_name:
            items = container.find_elements(By.XPATH, ".//li | .//div[contains(@class, 'card')]")
            if not items: items = container.find_elements(By.TAG_NAME, "li")
        elif "SKT 다이렉트" in site_name:
            items = container.find_elements(By.TAG_NAME, "li")
        elif "스카이라이프" in site_name:
            items = container.find_elements(By.XPATH, "./div")

        print(f"    [Legacy] Found {len(items)} items in {site_name}")

        for item in items:
            try:
                try: link_el = item.find_element(By.TAG_NAME, "a")
                except: 
                    if item.tag_name == 'a': link_el = item
                    else: continue

                href = link_el.get_attribute('href')
                if not href or "javascript" in href: continue

                title = item.text.strip().split("\n")[0]
                if not title:
                    try: title = item.find_element(By.TAG_NAME, "img").get_attribute("alt")
                    except: title = "제목 없음"
                
                img_src = ""
                try:
                    img = item.find_element(By.TAG_NAME, "img")
                    img_src = img.get_attribute("src")
                except: pass

                cards_data[href] = {"title": title, "img": img_src}
            except: continue
            
        return cards_data
    except Exception as e:
        print(f"    ⚠️ [Legacy] 추출 실패 ({site_name}): {e}")
        return {}

# [그룹 B] JS 해독 로직 (헬로모바일, 7모바일, KTM)
def extract_special_js(driver, container_selector, site_name):
    cards_data = {} 
    try:
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, container_selector))
        )
        
        items = []
        if "헬로모바일" in site_name:
            try: items = container.find_element(By.CSS_SELECTOR, ".event-list").find_elements(By.TAG_NAME, "li")
            except: items = container.find_elements(By.TAG_NAME, "li")
        elif "SK 7세븐모바일" in site_name:
            try: 
                groups = container.find_elements(By.CSS_SELECTOR, ".event-group")
                for g in groups: items.extend(g.find_elements(By.TAG_NAME, "li"))
            except: items = container.find_elements(By.TAG_NAME, "li")
        else: # KTM
            items = container.find_elements(By.TAG_NAME, "li")

        print(f"    [Special] Found {len(items)} items in {site_name}")

        for item in items:
            try:
                link_el = item if item.tag_name == 'a' else None
                if not link_el:
                    try: link_el = item.find_element(By.TAG_NAME, "a")
                    except: continue
                
                href = link_el.get_attribute('href')
                onclick = str(link_el.get_attribute('onclick'))
                
                final_url = ""
                
                if "헬로모바일" in site_name and "fncEventDetail" in onclick:
                    match = re.search(r"(\d+)", onclick)
                    if match:
                        final_url = f"https://direct.lghellovision.net/event/viewEventDetail.do?idxOfEvent={match.group(1)}"
                
                elif "SK 7세븐모바일" in site_name and "fnSearchView" in onclick:
                    match = re.search(r"['\"]([^'\"]+)['\"]", onclick)
                    if match:
                        final_url = f"https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId={match.group(1)}"
                
                elif "KTM 모바일" in site_name:
                    if href and "javascript" not in href: final_url = href
                    else:
                        match = re.search(r"(\d+)", onclick)
                        if match:
                            final_url = f"https://www.ktmmobile.com/event/eventBoardView.do?seq={match.group(1)}"
                
                if not final_url:
                    if href and "javascript" not in href: final_url = href
                    elif href: final_url = href

                if not final_url: continue

                title = item.text.strip().split("\n")[0]
                if not title:
                    try: title = item.find_element(By.TAG_NAME, "img").get_attribute("alt")
                    except: title = "제목 없음"
                
                img_src = ""
                try:
                    img = item.find_element(By.TAG_NAME, "img")
                    img_src = img.get_attribute("src")
                except: pass

                cards_data[final_url] = {"title": title, "img": img_src}
            except: continue
        return cards_data
    except Exception as e:
        print(f"    ⚠️ [Special] 추출 실패 ({site_name}): {e}")
        return {}

def extract_single_page_content(driver, selector):
    try:
        container = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
        return {driver.current_url: {"title": "SKT Air 메인", "img": "", "content": clean_html(container.get_attribute('outerHTML'))}}
    except: return {}

def crawl_site_logic(driver, site_name, base_url, pagination_param=None, target_selector=None):
    print(f"🚀 [{site_name}] 시작...")
    collected_items = {} 
    
    if site_name == "SKT Air":
        driver.get(base_url); time.sleep(3)
        return extract_single_page_content(driver, target_selector)

    page = 1
    while True:
        target_url = base_url
        if pagination_param == "#": target_url = f"{base_url}#{page}"
        elif pagination_param == "p": target_url = f"{base_url}?{pagination_param}={page}"
            
        driver.get(target_url)
        if pagination_param == "#": driver.refresh(); time.sleep(2)
        time.sleep(3)
        remove_popups(driver)
        scroll_to_bottom(driver)
        
        if site_name in ["SKT 다이렉트", "U+ 유모바일", "스카이라이프"]:
            page_data = extract_legacy_simple(driver, target_selector, site_name)
        else:
            page_data = extract_special_js(driver, target_selector, site_name)
        
        if not page_data: break
        
        new_cnt = 0
        for href, info in page_data.items():
            if href.startswith('/'):
                from urllib.parse import urljoin
                href = urljoin(base_url, href)
            if href not in collected_items:
                collected_items[href] = info
                new_cnt += 1
        
        if new_cnt == 0: break
        if not pagination_param: break
        
        page += 1
        if page > 10: break

    print(f"  🔎 [{site_name}] 상세 분석 ({len(collected_items)}건)...")
    for url, info in collected_items.items():
        try:
            if "javascript" not in url:
                driver.get(url)
                time.sleep(0.5)
                collected_items[url]['content'] = clean_html(driver.page_source)
            else:
                collected_items[url]['content'] = "JS Link"
        except: pass
            
    return collected_items

def main():
    try:
        driver = setup_driver()
        
        competitors = [
            {"name": "SKT 다이렉트", "url": "https://shop.tworld.co.kr/exhibition/submain", "param": None, "selector": "#wrap > div.container > div > div.event-list-wrap > div > ul"},
            {"name": "SKT Air", "url": "https://sktair-event.com/", "param": None, "selector": "#app > div > section.content"},
            {"name": "U+ 유모바일", "url": "https://www.uplusumobile.com/event-benefit/event/ongoing", "param": None, "selector": "#wrap > main > div > section"},
            {"name": "스카이라이프", "url": "https://www.skylife.co.kr/event?category=mobile", "param": "p", "selector": "body > div.pb-50.min-w-\[1248px\] > div.m-auto.max-w-\[1248px\].pt-20 > div > div > div.pt-14 > div > div.grid.grid-cols-3.gap-6.pt-4"},
            {"name": "헬로모바일", "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli", "param": "#", "selector": ".event-list-wrap"},
            {"name": "SK 7세븐모바일", "url": "https://www.sk7mobile.com/bnef/event/eventIngList.do", "param": None, "selector": ".tb-list.bbs-card"},
            {"name": "KTM 모바일", "url": "https://www.ktmmobile.com/event/eventBoardList.do", "param": None, "selector": "#listArea1"}
        ]
        
        today_results = {}
        for comp in competitors:
            try:
                today_results[comp['name']] = crawl_site_logic(driver, comp['name'], comp['url'], comp['param'], comp['selector'])
            except Exception as e:
                print(f"❌ {comp['name']} Error: {e}")
        
        driver.quit()
        
        # 파일 저장
        data_filename = f"data_{FILE_TIMESTAMP}.json"
        with open(os.path.join(DATA_DIR, data_filename), "w", encoding="utf-8") as f:
            json.dump(today_results, f, ensure_ascii=False)
            
        print("✅ 데이터 수집 완료. 리포트 생성 시작...")

        # =========================================================
        # [슬랙 전송 로직: V16 시절 코드 원상복구]
        # =========================================================
        yesterday_results = load_previous_data()
        report_body = ""
        total_change_count = 0
        company_summary = []
        
        for name, pages in today_results.items():
            site_changes = ""
            site_change_count = 0 
            old_pages = yesterday_results.get(name, {})
            all_urls = set(pages.keys()) | set(old_pages.keys())
            
            for url in all_urls:
                is_changed = False; change_type = ""; reason = ""
                curr = pages.get(url, {"title": "?", "img": "", "content": ""})
                prev = old_pages.get(url, {"title": "?", "img": "", "content": ""})
                
                if url in pages and url not in old_pages:
                    is_changed = True; change_type = "NEW"; reason = "신규"
                elif url not in pages and url in old_pages:
                    is_changed = True; change_type = "DELETED"; reason = "종료"
                elif curr['content'].replace(" ","") != prev['content'].replace(" ",""):
                    is_changed = True; change_type = "UPDATED"; reason = analyze_content_changes(prev['content'], curr['content'])

                if is_changed:
                    color = "green" if change_type == "NEW" else "red" if change_type == "DELETED" else "orange"
                    img_html = f"<img src='{curr['img']}' style='height:50px; vertical-align:middle; margin-right:10px;'>" if curr['img'] else ""
                    
                    site_changes += f"""
                    <div style="border-left: 5px solid {color}; padding: 10px; margin-bottom: 10px; background: #fff;">
                        <h3 style="margin: 0 0 5px 0;"><span style="color:{color};">[{change_type}]</span> {curr['title']}</h3>
                        <div style="display:flex; align-items:center;">
                            {img_html}
                            <div style="font-size: 0.9em; color: #555;">
                                <b>변경 사유:</b> {reason}<br>
                                <a href="{url}" target="_blank">🔗 바로가기</a>
                            </div>
                        </div>
                    </div>
                    """
                    site_change_count += 1
            
            if site_changes:
                report_body += f"<h2>{name} ({site_change_count}건)</h2>{site_changes}<hr>"
                total_change_count += site_change_count
                company_summary.append(f"{name}({site_change_count})")

        summary_text = f"총 {total_change_count}건 업데이트 ({', '.join(company_summary)})" if total_change_count > 0 else "특이사항 없음"
        
        report_header = f"""
        <h1>📅 {DISPLAY_DATE} 리포트 <span style='font-size:0.6em; color:#888;'>({DISPLAY_TIME} KST)</span></h1>
        <div style='background-color:#f4f4f4; padding:15px;'>
            <h3>📊 {summary_text}</h3>
        </div>
        <hr>
        """
        
        full_report = report_header + (report_body if total_change_count > 0 else "<p>✅ 금일 변동 사항이 없습니다.</p>")
        
        filename = f"report_{FILE_TIMESTAMP}.html"
        with open(os.path.join(REPORT_DIR, filename), "w", encoding="utf-8") as f: f.write(full_report)
        update_index_page()
        
        dashboard_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/"
        report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{filename}"
        
        # [슬랙 전송: V16 오리지널 방식 복구]
        if total_change_count > 0:
            payload = {"text": f"📢 *[KST {DISPLAY_TIME}] 경쟁사 동향 보고* \n\n✅ *요약:* {summary_text}\n\n👉 *변경 리포트:* {report_url}\n📂 *대시보드:* {dashboard_url}"}
        else:
            payload = {"text": f"📋 *[KST {DISPLAY_TIME}] 경쟁사 동향 보고* \n\n✅ 특이사항 없음\n📂 *대시보드:* {dashboard_url}"}
            
        if SLACK_WEBHOOK_URL:
            requests.post(SLACK_WEBHOOK_URL, json=payload)
            print("✅ 슬랙 전송 요청 완료")

    except Exception as e:
        print(f"🔥 Critical Error: {traceback.format_exc()}")

if __name__ == "__main__":
    main()
