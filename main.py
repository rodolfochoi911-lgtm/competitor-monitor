"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템 (V25)
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-01-30 (유모바일 등 4대장 로직 원상복구 / 문제아 3인방 별도 격리)
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
        for _ in range(3): 
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

# =========================================================
# [그룹 A] 기존 성공 멤버 전용 (V16 로직 복원)
# 대상: SKT 다이렉트, 유모바일, 스카이라이프
# =========================================================
def extract_legacy_simple(driver, container_selector, site_name):
    cards_data = {} 
    try:
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, container_selector))
        )
        
        items = []
        # [유모바일 V16 오리지널 로직]
        if "유모바일" in site_name:
            items = container.find_elements(By.XPATH, ".//li | .//div[contains(@class, 'card')]")
            if not items: items = container.find_elements(By.TAG_NAME, "li")
        
        # [SKT 다이렉트 V16 오리지널 로직]
        elif "SKT 다이렉트" in site_name:
            items = container.find_elements(By.TAG_NAME, "li")
            
        # [스카이라이프 V16 오리지널 로직]
        elif "스카이라이프" in site_name:
            items = container.find_elements(By.XPATH, "./div")

        print(f"    [Legacy] Found {len(items)} items in {site_name}")

        for item in items:
            try:
                link_el = item.find_element(By.TAG_NAME, "a")
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

# =========================================================
# [그룹 B] 문제아 3인방 전용 (JS 해독 + 신규 로직)
# 대상: 헬로모바일, SK 7모바일, KTM
# =========================================================
def extract_special_js(driver, container_selector, site_name):
    cards_data = {} 
    try:
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, container_selector))
        )
        
        items = []
        if "헬
