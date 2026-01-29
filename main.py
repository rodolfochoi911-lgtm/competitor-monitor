"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템 (V14)
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-01-29 (SKT Air 단일페이지 처리 + 헬로모바일 해시 페이징 + SK 7모바일 추가)
"""

import os
import json
import time
import glob
from datetime import datetime, timedelta, timezone
import difflib
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

# 한국 시간 설정
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

# [핵심] 카드 추출기 (엄격한 Selector 적용)
def extract_cards_smartly(driver, container_selector, site_name):
    cards_data = {} 
    try:
        # 컨테이너 대기
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, container_selector))
        )
        
        # 사이트별 아이템 태그 전략
        items = []
        if "SKT 다이렉트" in site_name:
            items = container.find_elements(By.TAG_NAME, "li")
        elif "KTM" in site_name:
            items = container.find_elements(By.TAG_NAME, "li") # listArea1 직계 li
        elif "유모바일" in site_name:
            # 유모바일 section 아래 div나 li 등을 찾음
            items = container.find_elements(By.XPATH, ".//*[contains(@class, 'event') or contains(@class, 'card') or name()='li']")
        elif "헬로모바일" in site_name:
            items = container.find_elements(By.TAG_NAME, "li")
        elif "스카이라이프" in site_name:
            items = container.find_elements(By.XPATH, "./div")
        elif "세븐모바일" in site_name:
            # table 형태 or list 형태
            items = container.find_elements(By.XPATH, ".//tr | .//li | .//div[contains(@class, 'item')]")
        
        # 공통 Fallback: 못 찾으면 그냥 a 태그를 아이템으로 간주
        if not items:
            items = container.find_elements(By.TAG_NAME, "a")

        print(f"    found {len(items)} items in {container_selector}")

        for item in items:
            try:
                # 링크 찾기
                link_el = item if item.tag_name == 'a' else None
                if not link_el:
                    try: link_el = item.find_element(By.TAG_NAME, "a")
                    except: continue
                
                href = link_el.get_attribute('href')
                if not href or "javascript" in href: continue

                # 제목
                title = item.text.strip().split("\n")[0]
                if not title:
                    try: title = item.find_element(By.TAG_NAME, "img").get_attribute("alt")
                    except: title = "제목 없음"
                
                # 이미지
                img_src = ""
                try:
                    img = item.find_element(By.TAG_NAME, "img")
                    src = img.get_attribute("src")
                    if src and "icon" not in src: img_src = src
                except: pass

                cards_data[href] = {"title": title, "img": img_src}
            except: continue
            
        return cards_data
    except Exception as e:
        print(f"    ⚠️ 카드 추출 실패 ({e})")
        return {}

# [NEW] SKT Air 전용: 단일 페이지 스냅샷
def extract_single_page_content(driver, selector):
    print("    📸 단일 페이지 스냅샷 모드 (SKT Air)")
    try:
        container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
        )
        html_content = clean_html(container.get_attribute('outerHTML'))
        # URL은 현재 페이지, 제목은 고정
        return {driver.current_url: {"title": "SKT Air 메인 프로모션", "img": "", "content": html_content}}
    except Exception as e:
        print(f"    ❌ SKT Air 추출 실패: {e}")
        return {}

def crawl_site_logic(driver, site_name, base_url, pagination_param=None, target_selector=None):
    print(f"🚀 [{site_name}] 데이터 수집 시작...")
    collected_items = {} 
    last_page_urls = []
    page = 1
    
    # [특수 처리] SKT Air는 페이지네이션 없이 한 번만 실행하고 종료
    if site_name == "SKT Air":
        driver.get(base_url)
        time.sleep(5)
        remove_popups(driver)
        return extract_single_page_content(driver, target_selector)

    while True:
        # URL 생성 로직
        target_url = base_url
        if pagination_param:
            if pagination_param == "#": # 헬로모바일 해시 방식
                target_url = f"{base_url}#{page}"
            else: # 일반 쿼리 파라미터 방식
                connector = '&' if '?' in base_url else '?'
                target_url = f"{base_url}{connector}{pagination_param}={page}"
            
        try:
            driver.get(target_url)
            # 헬로모바일 해시 변경 시 새로고침 필요할 수 있음
            if pagination_param == "#":
                driver.refresh()
                
            time.sleep(4)
            remove_popups(driver)
            scroll_to_bottom(driver)
            
            # 1단계: 카드 추출
            page_data = extract_cards_smartly(driver, target_selector, site_name)
            
            # 절대 경로 보정
            clean_page_data = {}
            for href, info in page_data.items():
                if href.startswith('/'):
                    from urllib.parse import urljoin
                    href = urljoin(base_url, href)
                clean_page_data[href] = info

            if page == 1:
                print(f"  - Page {page}: {len(clean_page_data)}개 항목 발견")
            
            if not clean_page_data: break
            
            # 페이지네이션 종료 체크
            current_urls = sorted(list(clean_page_data.keys()))
            if current_urls == sorted(last_page_urls): break
            
            collected_items.update(clean_page_data)
            
            if not pagination_param: break
            last_page_urls = current_urls
            page += 1
            if page > 10: break # 최대 10페이지 제한

        except Exception as e:
            print(f"  ⚠️ 오류: {e}")
            break

    # 2단계: 상세 페이지 진입 (SKT Air 제외)
    print(f"  🔎 상세 분석 중 ({len(collected_items)}건)...")
    for url, info in collected_items.items():
        try:
            driver.get(url)
            time.sleep(1)
            remove_popups(driver)
            collected_items[url]['content'] = clean_html(driver.page_source)
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
    
    # [설정] 지원이의 피드백을 100% 반영한 최종 리스트
    competitors = [
        # 1. SKT 다이렉트 (고정 - 성공)
        {"name": "SKT 다이렉트", "url": "https://shop.tworld.co.kr/exhibition/submain", "param": None, "selector": "#wrap > div.container > div > div.event-list-wrap > div > ul"},
        
        # 2. SKT Air (전략 수정: 단일 페이지 통째로 긁기)
        {"name": "SKT Air", "url": "https://sktair-event.com/", "param": None, "selector": "#app > div > section.content"},
        
        # 3. KTM 모바일 (범위 축소)
        {"name": "KTM 모바일", "url": "https://www.ktmmobile.com/event/eventBoardList.do", "param": None, "selector": "#listArea1"},
        
        # 4. U+ 유모바일 (범위 최적화)
        {"name": "U+ 유모바일", "url": "https://www.uplusumobile.com/event-benefit/event/ongoing", "param": None, "selector": "#wrap > main > div > section"},
        
        # 5. 헬로모바일 (해시 페이지네이션 #1, #2 적용)
        {"name": "헬로모바일", "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli", "param": "#", "selector": "#contentWrap > div.event-list-wrap > section > div.list-wrap > ul"},
        
        # 6. 스카이라이프 (고정 - 성공)
        {"name": "스카이라이프", "url": "https://www.skylife.co.kr/event?category=mobile", "param": "p", "selector": "body > div.pb-50.min-w-\[1248px\] > div.m-auto.max-w-\[1248px\].pt-20 > div > div > div.pt-14 > div > div.grid.grid-cols-3.gap-6.pt-4"},
        
        # 7. SK 7모바일 (신규 추가)
        {"name": "SK 7세븐모바일", "url": "https://www.sk7mobile.com/bnef/event/eventIngList.do", "param": None, "selector": "#frm > div.tb-list.bbs-card"}
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
                    </h3>
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

    full_list_html = f"<h1>📂 {DISPLAY_DATE} 전체 목록 ({DISPLAY_TIME} KST)</h1><hr>"
    for name, pages in today_results.items():
        full_list_html += f"<h3>{name} ({len(pages)}개)</h3><div style='display:grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap:10px;'>"
        for url, data in pages.items():
            img_tag = f"<img src='{data['img']}' style='width:100%; height:100px; object-fit:cover; border-radius:5px;'>" if data['img'] else ""
            full_list_html += f"<div style='border:1px solid #ddd; padding:10px; border-radius:8px;'><a href='{url}' target='_blank'>{img_tag}<p style='font-size:0.9em; margin-top:5px;'>{data['title']}</p></a></div>"
        full_list_html += "</div><hr>"
    
    list_filename = f"list_{FILE_TIMESTAMP}.html"
    with open(os.path.join(REPORT_DIR, list_filename), "w", encoding="utf-8") as f:
        f.write(full_list_html)

    summary_text = f"총 {total_change_count}건 업데이트 ({', '.join(company_summary)})" if total_change_count > 0 else "특이사항 없음"
    report_header = f"""
    <h1>📅 {DISPLAY_DATE} 리포트 <span style="font-size:0.6em; color:#888;">({DISPLAY_TIME} KST)</span></h1>
    <div style='background-color:#f4f4f4; padding:15px; border-radius:10px; border:1px solid #ddd;'>
        <h3>📊 {summary_text}</h3>
        <p><a href="../index.html">🔙 대시보드</a> | <a href="{list_filename}" target="_blank">🗂️ 전체 수집 목록(이미지 포함) 보기</a></p>
    </div>
    <hr>
    """
    full_report = report_header + (report_body if total_change_count > 0 else "<p>✅ 금일 변동 사항이 없습니다.</p>")
    
    filename = f"report_{FILE_TIMESTAMP}.html"
    with open(os.path.join(REPORT_DIR, filename), "w", encoding="utf-8") as f:
        f.write(full_report)
    
    data_filename = f"data_{FILE_TIMESTAMP}.json"
    with open(os.path.join(DATA_DIR, data_filename), "w", encoding="utf-8") as f:
        json.dump(today_results, f, ensure_ascii=False)

    update_index_page()

    dashboard_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/"
    report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{filename}"
    list_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{list_filename}"
    
    if total_change_count > 0:
        payload = {"text": f"📢 *[KST {DISPLAY_TIME}] 경쟁사 동향 보고* \n\n✅ *요약:* {summary_text}\n\n👉 *변경 리포트:* {report_url}\n🗂️ *전체 목록:* {list_url}\n📂 *대시보드:* {dashboard_url}"}
    else:
        payload = {"text": f"📋 *[KST {DISPLAY_TIME}] 경쟁사 동향 보고* \n\n✅ 특이사항 없음\n📂 *대시보드:* {dashboard_url}"}
        
    if SLACK_WEBHOOK_URL:
        requests.post(SLACK_WEBHOOK_URL, json=payload)
        print("✅ 슬랙 알림 완료")

if __name__ == "__main__":
    main()
