"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템 (V22)
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-01-30 (전수조사 모드 / 헬로모바일 & 7모바일 JS 해독 / 안전장치 해제)
"""

import os
import json
import time
import glob
import re
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
# [설정] 환경 변수 및 시간 (KST)
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
    except:
        pass

def scroll_to_bottom(driver):
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
        # 전수조사를 위해 스크롤 충분히 (5회)
        for _ in range(5): 
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
    except:
        pass

def clean_html(html_source):
    soup = BeautifulSoup(html_source, 'html.parser')
    for tag in soup(['script', 'style', 'meta', 'noscript', 'header', 'footer', 'iframe', 'button', 'input', 'nav', 'aside']):
        tag.decompose()
    for hidden in soup.find_all(attrs={"style": True}):
        if "display:none" in hidden["style"].replace(" ", "").lower():
            hidden.decompose()
    trash_ids = ['across_adn_container', 'criteo-tags-div', 'kakao-pixel-id', 'facebook-pixel-id']
    for t_id in trash_ids:
        tag = soup.find(id=t_id)
        if tag: tag.decompose()
    body = soup.find('body')
    return body.prettify() if body else "No Content"

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

# [핵심] V22 스마트 카드 추출기 (JS 해독 + 전수조사)
def extract_cards_smartly(driver, container_selector, site_name):
    cards_data = {} 
    try:
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, container_selector))
        )
        
        items = []
        
        # 1. SKT 다이렉트 (고정)
        if "SKT 다이렉트" in site_name:
            items = container.find_elements(By.TAG_NAME, "li")

        # 2. 유모바일 (고정)
        elif "유모바일" in site_name:
            items = container.find_elements(By.XPATH, ".//li | .//div[contains(@class, 'card')]")
            if not items: items = container.find_elements(By.TAG_NAME, "li")

        # 3. 스카이라이프 (고정)
        elif "스카이라이프" in site_name:
            items = container.find_elements(By.XPATH, "./div")

        # 4. [HELLOMOBILE] JS ID 추출
        elif "헬로모바일" in site_name:
            print("    ⚡ 헬로모바일: JS ID 해독 모드")
            # HTML 구조상 .event-list 안에 li들이 있음
            try:
                list_ul = container.find_element(By.CSS_SELECTOR, ".event-list")
                items = list_ul.find_elements(By.TAG_NAME, "li")
            except:
                items = container.find_elements(By.TAG_NAME, "li")

        # 5. [SK 7MOBILE] JS ID 추출
        elif "SK 7세븐모바일" in site_name:
            print("    ⚡ SK 7모바일: JS ID 해독 모드")
            # HTML 구조상 .event-group 안에 li들이 있음
            try:
                # event-group이 여러 개일 수 있으므로 모두 찾음
                groups = container.find_elements(By.CSS_SELECTOR, ".event-group")
                for g in groups:
                    items.extend(g.find_elements(By.TAG_NAME, "li"))
            except:
                items = container.find_elements(By.TAG_NAME, "li")

        # 6. KTM / 그 외: 전수조사
        else:
            print(f"    ⚡ {site_name}: 링크 전수조사")
            items = container.find_elements(By.TAG_NAME, "li")
            if not items: items = container.find_elements(By.TAG_NAME, "a")

        if not items:
            print("    ⚠️ 아이템 없음 -> a 태그 강제 수집")
            items = container.find_elements(By.TAG_NAME, "a")

        print(f"    found {len(items)} items in {site_name}")

        for item in items:
            try:
                link_el = item if item.tag_name == 'a' else None
                if not link_el:
                    try: link_el = item.find_element(By.TAG_NAME, "a")
                    except: continue
                
                href = link_el.get_attribute('href')
                onclick = link_el.get_attribute('onclick')
                
                final_url = ""

                # [해독 1] 헬로모바일: fncEventDetail(753, ...)
                if "헬로모바일" in site_name and onclick:
                    match = re.search(r"fncEventDetail\((\d+)", onclick)
                    if match:
                        event_id = match.group(1)
                        final_url = f"https://direct.lghellovision.net/event/viewEventDetail.do?idxOfEvent={event_id}"
                
                # [해독 2] SK 7모바일: fnSearchView('code', ...)
                elif "SK 7세븐모바일" in site_name and onclick:
                    match = re.search(r"fnSearchView\('([^']+)'", onclick)
                    if match:
                        content_id = match.group(1)
                        final_url = f"https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId={content_id}"
                
                # [일반] href 사용
                elif href and "javascript" not in href:
                    final_url = href
                
                # [Fallback] JS 링크지만 일단 가져옴 (유니크 키 용도)
                elif href:
                    final_url = href

                if not final_url: continue

                # 제목 추출
                title = item.text.strip().split("\n")[0]
                if not title:
                    try: title = item.find_element(By.TAG_NAME, "img").get_attribute("alt")
                    except: title = "제목 없음"
                
                # 이미지 추출
                img_src = ""
                try:
                    img = item.find_element(By.TAG_NAME, "img")
                    src = img.get_attribute("src")
                    if src and "icon" not in src and "logo" not in src: img_src = src
                except: pass

                cards_data[final_url] = {"title": title, "img": img_src}
            except: continue
            
        return cards_data
    except Exception as e:
        print(f"    ⚠️ 카드 추출 오류: {e}")
        return {}

def extract_single_page_content(driver, selector):
    print("    📸 단일 페이지 스냅샷 (SKT Air)")
    try:
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
        )
        html_content = clean_html(container.get_attribute('outerHTML'))
        return {driver.current_url: {"title": "SKT Air 메인 프로모션", "img": "", "content": html_content}}
    except: return {}

def crawl_site_logic(driver, site_name, base_url, pagination_param=None, target_selector=None):
    print(f"🚀 [{site_name}] 데이터 수집 시작...")
    collected_items = {} 
    
    if site_name == "SKT Air":
        driver.get(base_url)
        time.sleep(3)
        remove_popups(driver)
        return extract_single_page_content(driver, target_selector)

    # 페이지네이션 없이 1페이지만 전수조사 (일반적 상황)
    # 필요하면 while 루프 살릴 수 있지만, 현재 이슈 해결이 우선이라 1페이지 집중
    target_url = base_url
    if pagination_param == "#": target_url = f"{base_url}#1"
        
    try:
        driver.get(target_url)
        time.sleep(3)
        remove_popups(driver)
        scroll_to_bottom(driver)
        
        # 카드 수집
        page_data = extract_cards_smartly(driver, target_selector, site_name)
        
        # 절대경로 변환
        for href, info in page_data.items():
            if href.startswith('/'):
                from urllib.parse import urljoin
                href = urljoin(base_url, href)
            collected_items[href] = info

    except Exception as e:
        print(f"  ⚠️ 오류: {e}")

    # [전수조사] 모든 상세 페이지 접속 (시간 걸림)
    print(f"  🔎 상세 분석 중 ({len(collected_items)}건) - 잠시만 기다려주세요...")
    
    for url, info in collected_items.items():
        try:
            # 해독된 URL은 접속 가능
            if "javascript" not in url:
                driver.get(url)
                time.sleep(1) # 안정성을 위해 1초 대기
                remove_popups(driver)
                collected_items[url]['content'] = clean_html(driver.page_source)
            else:
                collected_items[url]['content'] = "JS Link (No Content)"
        except:
            collected_items[url]['content'] = "" 
            
    return collected_items

def update_index_page():
    report_files = glob.glob(os.path.join(REPORT_DIR, "report_*.html"))
    report_files.sort(reverse=True)
    
    index_html = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>경쟁사 모니터링</title>
        <style>
            body {{ font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f9f9f9; }}
            h1 {{ border-bottom: 2px solid #0056b3; padding-bottom: 10px; }}
            .card {{ background: white; padding: 15px; margin-bottom: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
            a {{ text-decoration: none; color: #0056b3; font-weight: bold; }}
            .badge {{ background: #28a745; color: white; padding: 3px 8px; border-radius: 10px; font-size: 0.8em; }}
        </style>
    </head>
    <body>
        <h1>📊 모니터링 아카이브</h1>
        <p>현재 시각: {DISPLAY_DATE} {DISPLAY_TIME} (KST)</p>
    """
    if not report_files: index_html += "<p>데이터 없음</p>"
    for f in report_files:
        name = os.path.basename(f)
        ts = name.replace("report_", "").replace(".html", "")
        try:
            dt = datetime.strptime(ts, "%Y%m%d_%H%M%S")
            disp = dt.strftime("%Y-%m-%d %H:%M:%S")
        except: disp = ts
        badge = '<span class="badge">NEW</span>' if disp.startswith(DISPLAY_DATE) else ''
        index_html += f"<div class='card'><a href='reports/{name}'>📄 {disp} 리포트</a> {badge}</div>"
    index_html += "</body></html>"
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)

def main():
    driver = setup_driver()
    
    competitors = [
        # [고정] 성공한 4개
        {"name": "SKT 다이렉트", "url": "https://shop.tworld.co.kr/exhibition/submain", "param": None, "selector": "#wrap > div.container > div > div.event-list-wrap > div > ul"},
        {"name": "SKT Air", "url": "https://sktair-event.com/", "param": None, "selector": "#app > div > section.content"},
        {"name": "U+ 유모바일", "url": "https://www.uplusumobile.com/event-benefit/event/ongoing", "param": None, "selector": "#wrap > main > div > section"},
        {"name": "스카이라이프", "url": "https://www.skylife.co.kr/event?category=mobile", "param": "p", "selector": "body > div.pb-50.min-w-\[1248px\] > div.m-auto.max-w-\[1248px\].pt-20 > div > div > div.pt-14 > div > div.grid.grid-cols-3.gap-6.pt-4"},
        
        # [JS 해독 + 울타리 타겟팅]
        {"name": "헬로모바일", "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli", "param": "#", "selector": ".event-list-wrap"},
        {"name": "SK 7세븐모바일", "url": "https://www.sk7mobile.com/bnef/event/eventIngList.do", "param": None, "selector": ".tb-list.bbs-card"},
        
        # [KTM: 리스트 영역 고정]
        {"name": "KTM 모바일", "url": "https://www.ktmmobile.com/event/eventBoardList.do", "param": None, "selector": "#listArea1"}
    ]
    
    today_results = {}
    for comp in competitors:
        try:
            today_results[comp['name']] = crawl_site_logic(driver, comp['name'], comp['url'], comp['param'], comp['selector'])
        except Exception as e:
            print(f"❌ {comp['name']} 실패: {e}")
    
    driver.quit()
    
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
            is_changed = False
            change_type = ""
            reason = ""
            
            curr = pages.get(url, {"title": "?", "img": "", "content": ""})
            prev = old_pages.get(url, {"title": "?", "img": "", "content": ""})
            if isinstance(prev, str): prev = {"title": "Old", "img": "", "content": prev}

            if url in pages and url not in old_pages:
                is_changed = True
                change_type = "NEW"
                reason = "신규 이벤트 등록"
            elif url not in pages and url in old_pages:
                is_changed = True
                change_type = "DELETED"
                reason = "이벤트 종료/삭제"
            else:
                if curr['title'] != prev['title']:
                    is_changed = True
                    change_type = "UPDATED"
                    reason = f"제목 변경: {prev['title']} -> {curr['title']}"
                elif curr['img'] != prev['img']:
                    is_changed = True
                    change_type = "UPDATED"
                    reason = "썸네일/배너 이미지 변경"
                elif curr['content'].replace(" ","") != prev['content'].replace(" ",""):
                    is_changed = True
                    change_type = "UPDATED"
                    reason = analyze_content_changes(prev['content'], curr['content'])

            if is_changed:
                color = "green" if change_type == "NEW" else "red" if change_type == "DELETED" else "orange"
                img_html = f"<img src='{curr['img']}' style='height:50px; vertical-align:middle; margin-right:10px;'>" if curr['img'] else ""
                site_changes += f"""
                <div style="border-left: 5px solid {color}; padding: 10px; margin-bottom: 10px; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.1);">
                    <h3 style="margin: 0 0 5px 0;">
                        <span style="color:{color}; font-weight:bold;">[{change_type}]</span> {curr['title']}
