"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-01-29 (초 단위 아카이빙 + KST + SKT Air 전체검색)
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
# [설정]
# =========================================================
GITHUB_USER = "rodolfochoi911-lgtm" 
REPO_NAME = "competitor-monitor" 
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL") 

DATA_DIR = "data"
DOCS_DIR = "docs"
REPORT_DIR = "docs/reports"

# [중요] 한국 시간(KST) 설정
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)

# 파일명에 쓸 타임스탬프 (예: 20260129_143005) -> 덮어쓰기 방지
FILE_TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

# 화면 표시용 문자열 (예: 2026-01-29)
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
            var popups = document.querySelectorAll('.popup, .modal, .layer, .dimmed, .overlay, .toast, .banner, #popup');
            popups.forEach(function(element) { element.remove(); });
        """)
    except:
        pass

def scroll_to_bottom(driver):
    """스크롤을 10번 내려서 모든 데이터 로딩 유도"""
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(10): 
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
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

def analyze_changes(old_html, new_html):
    soup_old = BeautifulSoup(old_html, 'html.parser')
    soup_new = BeautifulSoup(new_html, 'html.parser')
    
    summary_tags = []
    
    imgs_old = set([img.get('src') for img in soup_old.find_all('img') if img.get('src')])
    imgs_new = set([img.get('src') for img in soup_new.find_all('img') if img.get('src')])
    if imgs_old != imgs_new:
        summary_tags.append("🖼️ <b>이미지/배너 변경</b>")
        
    if soup_old.get_text().strip() != soup_new.get_text().strip():
        summary_tags.append("✏️ <b>텍스트(내용) 수정</b>")
        
    links_old = set([a.get('href') for a in soup_old.find_all('a') if a.get('href')])
    links_new = set([a.get('href') for a in soup_new.find_all('a') if a.get('href')])
    if links_old != links_new:
        summary_tags.append("🔗 <b>연결 링크 변경</b>")

    if not summary_tags and old_html != new_html:
        summary_tags.append("🎨 <b>디자인/스타일 변경</b>")
        
    if not summary_tags:
        return "🔍 미세한 코드 변경"
    
    return " / ".join(summary_tags)

def extract_links_safely(driver, base_url, target_selector):
    links = []
    method = "Selector"
    
    if target_selector:
        try:
            container = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, target_selector))
            )
            found_links = container.find_elements(By.TAG_NAME, "a")
            if len(found_links) == 0:
                print(f"    ⚠️ 선택자({target_selector}) 실패/0건 -> 전체 검색 전환")
                links = driver.find_elements(By.TAG_NAME, "a")
                method = "Fallback (All)"
            else:
                links = found_links
        except:
            print(f"    ⚠️ 선택자 에러 -> 전체 검색 전환")
            links = driver.find_elements(By.TAG_NAME, "a")
            method = "Fallback (All)"
    else:
        links = driver.find_elements(By.TAG_NAME, "a")
        method = "Full Page"

    current_page_urls = []
    extracted_data = {} 

    for link in links:
        try:
            href = link.get_attribute('href')
            title = link.text.strip()
            if not title:
                img = link.find_element(By.TAG_NAME, "img")
                title = img.get_attribute("alt") if img else "제목 없음"
            
            if href and ('event' in href or 'view' in href or 'detail' in href or 'notice' in href) and not href.startswith('#') and 'javascript' not in href:
                if href.startswith('/'):
                    from urllib.parse import urljoin
                    href = urljoin(base_url, href)
                
                if href not in current_page_urls:
                    current_page_urls.append(href)
                    if href not in extracted_data:
                        extracted_data[href] = title
        except:
            continue
            
    return extracted_data, method

def crawl_site_logic(driver, site_name, base_url, pagination_param=None, target_selector=None):
    print(f"🚀 [{site_name}] 데이터 수집 시작...")
    collected_links = {} 
    last_page_links = []
    page = 1
    
    while True:
        if pagination_param:
            connector = '&' if '?' in base_url else '?'
            target_url = f"{base_url}{connector}{pagination_param}={page}"
        else:
            target_url = base_url
            
        try:
            driver.get(target_url)
            time.sleep(5) 
            remove_popups(driver)
            scroll_to_bottom(driver)
            
            page_data, method = extract_links_safely(driver, base_url, target_selector)
            
            if page == 1:
                print(f"  - [{method}] Page {page}: {len(page_data)}개 발견")
            
            if not page_data: break
            
            current_urls = sorted(list(page_data.keys()))
            if current_urls == sorted(last_page_links): 
                break
            
            collected_links.update(page_data)
            
            if not pagination_param: break
            
            last_page_links = current_urls
            page += 1
            if page > 10: break

        except Exception as e:
            print(f"  ⚠️ 오류: {e}")
            break

    print(f"  🔎 상세 분석 중 ({len(collected_links)}건)...")
    site_data = {}
    
    for link, title in collected_links.items():
        try:
            driver.get(link)
            time.sleep(1)
            remove_popups(driver)
            site_data[link] = {
                "title": title if title else "제목 없음",
                "content": clean_html(driver.page_source)
            }
        except:
            pass
            
    return site_data

def update_index_page():
    # 파일명에 시간이 들어가므로, 이름 역순으로 정렬하면 최신 파일이 맨 위로 옴
    report_files = glob.glob(os.path.join(REPORT_DIR, "report_*.html"))
    report_files.sort(reverse=True)
    
    index_html = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>경쟁사 모니터링 대시보드</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background-color: #f9f9f9; }}
            h1 {{ color: #333; border-bottom: 2px solid #0056b3; padding-bottom: 10px; }}
            .card {{ background: white; padding: 15px; margin-bottom: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; }}
            .card a {{ text-decoration: none; color: #0056b3; font-weight: bold; font-size: 1.1em; }}
            .card a:hover {{ text-decoration: underline; }}
            .date {{ color: #666; font-size: 0.9em; }}
            .badge {{ background-color: #28a745; color: white; padding: 3px 8px; border-radius: 12px; font-size: 0.8em; }}
            .sub-link {{ font-size: 0.8em; color: #888; margin-left: 10px; }}
        </style>
    </head>
    <body>
        <h1>📊 경쟁사 모니터링 대시보드 (아카이브)</h1>
        <p>현재 시각: {DISPLAY_DATE} {DISPLAY_TIME} (KST)</p>
        <div class="list-container">
    """
    
    if not report_files:
        index_html += "<p>아직 생성된 리포트가 없습니다.</p>"
    
    for file_path in report_files:
        filename = os.path.basename(file_path)
        # 파일명: report_20260129_143000.html
        # 표시용: 2026-01-29 14:30:00
        timestamp_part = filename.replace("report_", "").replace(".html", "")
        try:
            dt_obj = datetime.strptime(timestamp_part, "%Y%m%d_%H%M%S")
            display_str = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
        except:
            display_str = timestamp_part # 포맷 안 맞으면 그냥 출력
        
        list_filename = f"list_{timestamp_part}.html"
        
        # 오늘 날짜와 같으면 NEW 뱃지
        is_today = display_str.startswith(DISPLAY_DATE)
        badge = '<span class="badge">NEW</span>' if is_today else ''
        
        index_html += f"""
            <div class="card">
                <div>
                    <a href="reports/{filename}">📄 {display_str} 리포트</a>
                    <a href="reports/{list_filename}" class="sub-link" target="_blank">🗂️ 전체 수집 목록</a>
                    {badge}
                </div>
            </div>
        """
        
    index_html += "</div></body></html>"
    
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    print("✅ [대시보드] index.html 업데이트 완료")

def main():
    driver = setup_driver()
    
    competitors = [
        {"name": "SKT 다이렉트", "url": "https://shop.tworld.co.kr/exhibition/submain", "param": None, "selector": None},
        {"name": "SKT Air", "url": "https://sktair-event.com/", "param": None, "selector": None},
        {"name": "KTM 모바일", "url": "https://www.ktmmobile.com/event/eventBoardList.do", "param": None, "selector": None},
        {"name": "U+ 유모바일", "url": "https://www.uplusumobile.com/event-benefit/event/ongoing", "param": None, "selector": "#wrap > main > div > section"},
        {"name": "헬로모바일", "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli&category=USIM", "param": "pageIndex", "selector": None},
        {"name": "스카이라이프", "url": "https://www.skylife.co.kr/event?category=mobile", "param": "p", "selector": None}
    ]
    
    today_results = {}
    for comp in competitors:
        try:
            today_results[comp['name']] = crawl_site_logic(driver, comp['name'], comp['url'], comp['param'], comp['selector'])
        except Exception as e:
            print(f"❌ {comp['name']} 실패: {e}")
    
    driver.quit()
    
    latest_file = os.path.join(DATA_DIR, "latest_data.json")
    yesterday_results = {}
    if os.path.exists(latest_file):
        with open(latest_file, "r", encoding="utf-8") as f:
            yesterday_results = json.load(f)
            
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
            change_html = ""
            
            curr_data = pages.get(url, {"title": "Unknown", "content": ""})
            prev_data = old_pages.get(url, {"title": "Unknown", "content": ""})
            
            if isinstance(prev_data, str): prev_data = {"title": "Old Data", "content": prev_data}
            if isinstance(curr_data, str): curr_data = {"title": "New Data", "content": curr_data}

            title_display = curr_data['title'] if url in pages else prev_data['title']

            if url in pages and url not in old_pages:
                is_changed = True
                change_html = f"<h3 style='color:green'>[NEW] {title_display} <a href='{url}' target='_blank' style='font-size:0.7em'>🔗링크</a></h3>"
            
            elif url not in pages and url in old_pages:
                is_changed = True
                change_html = f"<h3 style='color:red'>[DELETED] {title_display} <a href='{url}' target='_blank' style='font-size:0.7em'>🔗링크</a></h3>"
            
            elif curr_data['content'].replace(" ","") != prev_data['content'].replace(" ",""):
                is_changed = True
                change_summary = analyze_changes(prev_data['content'], curr_data['content'])
                diff = difflib.HtmlDiff().make_table(prev_data['content'].splitlines(), curr_data['content'].splitlines(), context=True, numlines=3)
                
                change_html = f"""
                <div style="border:1px solid #ddd; padding:10px; border-radius:8px; margin-bottom:10px; background-color:#fff;">
                    <h3 style='margin:0 0 5px 0; color:orange;'>[UPDATED] {title_display}</h3>
                    <div style="font-size:0.9em; color:#666; margin-bottom:10px;">
                        {change_summary} <a href='{url}' target='_blank' style='text-decoration:none;'>🔗 바로가기</a>
                    </div>
                    <details>
                        <summary style="cursor:pointer; color:#0056b3; font-weight:bold; padding:8px; background:#f8f9fa; border-radius:5px;">👉 변경된 코드 상세 보기 (클릭)</summary>
                        <div style="margin-top:10px; overflow-x:auto; font-size:0.85em;">{diff}</div>
                    </details>
                </div>
                """
            
            if is_changed:
                site_changes += change_html
                site_change_count += 1
        
        if site_changes:
            report_body += f"<h2>{name} ({site_change_count}건)</h2>{site_changes}<hr>"
            total_change_count += site_change_count
            company_summary.append(f"{name}({site_change_count})")

    # [수정] 파일명에 타임스탬프 적용 (덮어쓰기 방지)
    full_list_html = f"<h1>📂 {DISPLAY_DATE} 전체 수집 목록 ({DISPLAY_TIME} KST)</h1><hr>"
    for name, pages in today_results.items():
        full_list_html += f"<h3>{name} (총 {len(pages)}개)</h3><ul>"
        for url, data in pages.items():
            full_list_html += f"<li><a href='{url}' target='_blank'>{data['title']}</a></li>"
        full_list_html += "</ul><hr>"
    
    list_filename = f"list_{FILE_TIMESTAMP}.html"
    with open(os.path.join(REPORT_DIR, list_filename), "w", encoding="utf-8") as f:
        f.write(full_list_html)

    summary_text = f"총 {total_change_count}건 업데이트 ({', '.join(company_summary)})" if total_change_count > 0 else "특이사항 없음"
    
    report_header = f"""
    <h1>📅 {DISPLAY_DATE} 리포트 <span style="font-size:0.6em; color:#888;">({DISPLAY_TIME} KST)</span></h1>
    <div style='background-color:#f4f4f4; padding:15px; border-radius:10px; border:1px solid #ddd;'>
        <h3>📊 {summary_text}</h3>
        <p>
            <a href="../index.html">🔙 대시보드</a> | 
            <a href="{list_filename}" target="_blank">🗂️ 전체 수집 목록 보기</a>
        </p>
    </div>
    <hr>
    """
    full_report = report_header + (report_body if total_change_count > 0 else "<p>✅ 금일 변동 사항이 없습니다.</p>")
    
    # [수정] 파일명 타임스탬프 적용
    filename = f"report_{FILE_TIMESTAMP}.html"
    with open(os.path.join(REPORT_DIR, filename), "w", encoding="utf-8") as f:
        f.write(full_report)
        
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(today_results, f, ensure_ascii=False)

    update_index_page()

    dashboard_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/"
    report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{filename}"
    list_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{list_filename}"
    
    # [수정] 슬랙 알림에 KST 시간 강조
    if total_change_count > 0:
        payload = {
            "text": f"📢 *[KST {DISPLAY_TIME}] 경쟁사 동향 보고* \n\n✅ *요약:* {summary_text}\n\n👉 *변경 리포트:* {report_url}\n🗂️ *전체 목록:* {list_url}\n📂 *대시보드 (아카이브):* {dashboard_url}"
        }
    else:
        payload = {
            "text": f"📋 *[KST {DISPLAY_TIME}] 경쟁사 동향 보고* \n\n✅ 특이사항 없음\n📂 *대시보드 (아카이브):* {dashboard_url}"
        }
        
    if SLACK_WEBHOOK_URL:
        requests.post(SLACK_WEBHOOK_URL, json=payload)
        print("✅ 슬랙 알림 완료")
    else:
        print("⚠️ 슬랙 URL 없음")

if __name__ == "__main__":
    main()
