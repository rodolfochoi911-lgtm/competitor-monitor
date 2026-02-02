"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템 (V39)
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-02-01 (U+, KTM, Skylife HTML 분석 기반 전용 로직 탑재 / 슬랙 전체목록 복구)
"""

import os
import json
import time
import glob
import re
import traceback
from datetime import datetime, timedelta, timezone
import requests
from urllib.parse import urljoin # 상대경로 처리를 위해 추가
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
    return body.prettify() if (body := soup.find('body')) else "No Content"

def load_previous_data():
    json_files = glob.glob(os.path.join(DATA_DIR, "data_*.json"))
    if not json_files: return {}
    json_files.sort()
    latest_file = json_files[-1]
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

def analyze_content_changes(old_html, new_html):
    soup_old = BeautifulSoup(old_html, 'html.parser')
    soup_new = BeautifulSoup(new_html, 'html.parser')
    if soup_old.get_text().strip() != soup_new.get_text().strip(): return "✏️ 텍스트 수정"
    imgs_old = set([i['src'] for i in soup_old.find_all('img') if i.get('src')])
    imgs_new = set([i['src'] for i in soup_new.find_all('img') if i.get('src')])
    if imgs_old != imgs_new: return "🖼️ 이미지 교체"
    return "🎨 레이아웃 변경"

# =========================================================
# [전용 추출기 1] U+ 유모바일 (HTML 분석 기반)
# =========================================================
def extract_uplus_mobile(driver):
    cards_data = {}
    try:
        # 컨테이너: .going-list-wrap
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".going-list-wrap"))
        )
        # 아이템: a.cardList-wrap (li 아님, 통짜 a태그)
        items = container.find_elements(By.CSS_SELECTOR, "a.cardList-wrap")
        print(f"    [U+ Mobile] Found {len(items)} items")
        
        for item in items:
            try:
                href = item.get_attribute('href')
                if not href or "javascript" in href: continue
                
                # 상대경로 처리
                final_url = urljoin("https://www.uplusumobile.com", href)

                # 제목: .main-title (없으면 .cardList-desc)
                try: title = item.find_element(By.CSS_SELECTOR, ".main-title").text.strip()
                except: title = "제목 없음"
                
                # 이미지: .cardList-img img
                img_src = ""
                try:
                    img = item.find_element(By.CSS_SELECTOR, ".cardList-img img")
                    img_src = img.get_attribute("src")
                except: pass
                
                cards_data[final_url] = {"title": title, "img": img_src}
            except: continue
    except Exception as e:
        print(f"    ⚠️ U+ 유모바일 추출 실패: {e}")
    return cards_data

# =========================================================
# [전용 추출기 2] KTM 모바일 (ntcartseq 속성 분석)
# =========================================================
def extract_ktm_mobile(driver):
    cards_data = {}
    try:
        # 컨테이너: .event-list
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".event-list"))
        )
        items = container.find_elements(By.TAG_NAME, "li")
        print(f"    [KTM Mobile] Found {len(items)} items")
        
        for item in items:
            try:
                # a 태그 찾기
                link_el = item.find_element(By.TAG_NAME, "a")
                
                # 핵심: href가 아니라 'ntcartseq' 속성 값을 가져와야 함
                seq = link_el.get_attribute("ntcartseq")
                
                if seq:
                    # URL 직접 조립
                    final_url = f"https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq={seq}"
                else:
                    # 혹시 href가 있는 경우 대비
                    href = link_el.get_attribute('href')
                    if href and "javascript" not in href: final_url = href
                    else: continue

                # 제목
                try: title = item.find_element(By.CSS_SELECTOR, ".event-list__title__sub").text.strip()
                except: title = "제목 없음"
                
                # 이미지
                img_src = ""
                try:
                    img = item.find_element(By.TAG_NAME, "img")
                    img_src = img.get_attribute("src")
                except: pass

                cards_data[final_url] = {"title": title, "img": img_src}
            except: continue
    except Exception as e:
        print(f"    ⚠️ KTM 모바일 추출 실패: {e}")
    return cards_data

# =========================================================
# [전용 추출기 3] 스카이라이프 (Grid 구조 분석)
# =========================================================
def extract_skylife(driver):
    cards_data = {}
    try:
        # 컨테이너: div.grid (Tailwind 클래스 활용)
        # body > div... 등 복잡한 경로 대신 핵심 클래스로 찾음
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.grid.grid-cols-3"))
        )
        items = container.find_elements(By.TAG_NAME, "a")
        print(f"    [Skylife] Found {len(items)} items")
        
        for item in items:
            try:
                href = item.get_attribute('href')
                if not href or "javascript" in href: continue
                
                final_url = urljoin("https://www.skylife.co.kr", href)

                # 제목 (p 태그 중 폰트 굵은 것)
                try: title = item.find_element(By.CSS_SELECTOR, "p.font-semibold").text.strip()
                except: title = "제목 없음"
                
                # 이미지
                img_src = ""
                try:
                    img = item.find_element(By.TAG_NAME, "img")
                    img_src = img.get_attribute("srcset").split(" ")[0] # srcset 처리
                    if not img_src: img_src = img.get_attribute("src")
                except: pass

                cards_data[final_url] = {"title": title, "img": img_src}
            except: continue
    except Exception as e:
        print(f"    ⚠️ 스카이라이프 추출 실패: {e}")
    return cards_data

# [기존] Legacy Simple (SKT 다이렉트용)
def extract_legacy_simple(driver, container_selector, site_name):
    cards_data = {} 
    try:
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, container_selector))
        )
        items = container.find_elements(By.TAG_NAME, "li")
        print(f"    [Legacy] Found {len(items)} items in {site_name}")

        for item in items:
            try:
                try: link_el = item.find_element(By.TAG_NAME, "a")
                except: 
                    if item.tag_name == 'a': link_el = item
                    else: continue

                href = link_el.get_attribute('href')
                if not href: continue

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

# [기존] JS 해독 로직 (헬로모바일, 7모바일용)
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
                    if m := re.search(r"(\d+)", onclick):
                        final_url = f"https://direct.lghellovision.net/event/viewEventDetail.do?idxOfEvent={m.group(1)}"
                
                elif "SK 7세븐모바일" in site_name and "fnSearchView" in onclick:
                    if m := re.search(r"['\"]([^'\"]+)['\"]", onclick):
                        final_url = f"https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId={m.group(1)}"
                
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
        
        # [핵심] 사이트별 로직 분기 (3대장 복구)
        if site_name == "U+ 유모바일":
            page_data = extract_uplus_mobile(driver)
        elif site_name == "KTM 모바일":
            page_data = extract_ktm_mobile(driver)
        elif site_name == "스카이라이프":
            page_data = extract_skylife(driver)
        elif site_name == "SKT 다이렉트":
            page_data = extract_legacy_simple(driver, target_selector, site_name)
        else: # 헬로, 7모바일
            page_data = extract_special_js(driver, target_selector, site_name)
        
        if not page_data: break
        
        new_cnt = 0
        for href, info in page_data.items():
            # 이미 절대경로로 변환된 href가 들어옴
            if href not in collected_items:
                collected_items[href] = info
                new_cnt += 1
        
        if new_cnt == 0: break
        if not pagination_param: break
        
        page += 1
        if page > 10: break

    print(f"  🔎 [{site_name}] 상세 분석 ({len(collected_items)}건)...")
