"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-01-29 (타임스탬프 추가 + 무료 한도 최적화)
"""

import os
import json
import time
import glob
from datetime import datetime
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

# [수정됨] 날짜뿐만 아니라 시간까지 정확하게 기록
NOW = datetime.now()
TODAY_STR = NOW.strftime("%Y-%m-%d")
TIME_STR = NOW.strftime("%H:%M:%S") # 예: 14:30:05

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
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

def crawl_site_logic(driver, site_name, base_url, pagination_param=None, target_selector=None):
    print(f"🚀 [{site_name}] 데이터 수집 시작...")
    collected_links = []
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
            time.sleep(3)
            remove_popups(driver)
            
            if target_selector:
                try:
                    container = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, target_selector))
                    )
                    links = container.find_elements(By.TAG_NAME, "a")
                except:
                    links = driver.find_elements(By.TAG_NAME, "a")
            else:
                links = driver.find_elements(By.TAG_NAME, "a")

            current_page_links = []
            
            for link in links:
                try:
                    href = link.get_attribute('href')
                    if href and ('event' in href or 'view' in href or 'detail' in href or 'notice' in href) and not href.startswith('#') and 'javascript' not in href:
                        if href.startswith('/'):
                            from urllib.parse import urljoin
                            href = urljoin(base_url, href)
                        
                        if href not in current_page_links:
                            current_page_links.append(href)
                except:
                    continue
            
            if not current_page_links: break
            if sorted(current_page_links) == sorted(last_page_links): break

            for lnk in current_page_links:
                if lnk not in collected_links:
                    collected_links.append(lnk)

            if not pagination_param: break
            
            last_page_links = current_page_links
            page += 1
            if page > 10: break

        except Exception as e:
            print(f"  ⚠️ 오류: {e}")
            break

    print(f"  🔎 상세 분석 중 ({len(collected_links)}건)...")
    site_data = {}
    
    for link in collected_links:
        try:
            driver.get(link)
            time.sleep(1)
            remove_popups(driver)
            site_data[link] = clean_html(driver.page_source)
        except:
            pass
            
    return site_data

def update_index_page():
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
        </style>
    </head>
    <body>
        <h1>📊 경쟁사 프로모션 모니터링 이력</h1>
        <p>최종 업데이트: {TODAY_STR} {TIME_STR}</p>
        <div class="list-container">
    """
    
    if not report_files:
        index_html += "<p>아직 생성된 리포트가 없습니다.</p>"
    
    for file_path in report_files:
        filename = os.path.basename(file_path)
        date_str = filename.replace("report_", "").replace(".html", "")
        
        badge = '<span class="badge">NEW</span>' if date_str == TODAY_STR else ''
        
        index_html += f"""
            <div class="card">
                <div>
                    <a href="reports/{filename}">📄 {date_str} 리포트</a>
                    {badge}
                </div>
                <span class="date">{date_str}</span>
            </div>
        """
        
    index_html += """
        </div>
    </body>
    </html>
    """
    
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    print("✅ [대시보드] index.html 업데이트 완료")

def main():
    driver = setup_driver()
    
    competitors = [
        {"name": "SKT 다이렉트", "url": "https://shop.tworld.co.kr/exhibition/submain", "param": None, "selector": "#wrap > div.container > div > div.event-list-wrap > div > ul"},
        {"name": "KTM 모바일", "url": "https://www.ktmmobile.com/event/eventBoardList.do", "param": None, "selector": "#listArea1"},
        {"name": "U+ 유모바일", "url": "https://www.uplusumobile.com/event-benefit/event/ongoing", "param": None, "selector": "#wrap > main > div > section"},
        {"name": "헬로모바일", "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli&category=USIM", "param": "pageIndex", "selector": "#contentWrap > div.event-list-wrap > section > div.list-wrap > ul"},
        {"name": "스카이라이프", "url": "https://www.skylife.co.kr/event?category=mobile", "param": "page", "selector": "body > div.pb-50.min-w-\[1248px\] > div.m-auto.max-w-\[1248px\].pt-20 > div > div > div.pt-14 > div > div.grid.grid-cols-3.gap-6.pt-4"}
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
            if url in pages and url not in old_pages:
                is_changed = True
                change_html = f"<h3 style='color:green'>[NEW] <a href='{url}' target='_blank'>새 이벤트</a></h3><br>"
            elif url not in pages and url in old_pages:
                is_changed = True
                change_html = f"<h3 style='color:red'>[DELETED] <a href='{url}' target='_blank'>종료됨</a></h3><br>"
            elif pages[url].replace(" ","") != old_pages[url].replace(" ",""):
                is_changed = True
                diff = difflib.HtmlDiff().make_table(old_pages[url].splitlines(), pages[url].splitlines(), context=True, numlines=3)
                change_html = f"<h3 style='color:orange'>[UPDATED] <a href='{url}' target='_blank'>내용 변경</a></h3>{diff}<br>"
            
            if is_changed:
                site_changes += change_html
                site_change_count += 1
        
        if site_changes:
            report_body += f"<h2>{name} ({site_change_count}건)</h2>{site_changes}<hr>"
            total_change_count += site_change_count
            company_summary.append(f"{name}({site_change_count})")

    summary_text = f"총 {total_change_count}건 업데이트 ({', '.join(company_summary)})" if total_change_count > 0 else "특이사항 없음"
    
    # [수정됨] 리포트 제목에 정확한 생성 시간(시:분:초) 포함
    report_header = f"""
    <h1>📅 {TODAY_STR} 리포트 <span style="font-size:0.6em; color:#888;">({TIME_STR} 기준)</span></h1>
    <div style='background-color:#f4f4f4; padding:15px; border-radius:10px; border:1px solid #ddd;'>
        <h3>📊 {summary_text}</h3>
        <p><a href="../index.html">🔙 전체 이력 목록으로 돌아가기</a></p>
    </div>
    <hr>
    """
    full_report = report_header + (report_body if total_change_count > 0 else "<p>✅ 금일 변동 사항이 없습니다.</p>")
    
    filename = f"report_{TODAY_STR}.html"
    with open(os.path.join(REPORT_DIR, filename), "w", encoding="utf-8") as f:
        f.write(full_report)
        
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(today_results, f, ensure_ascii=False)

    update_index_page()

    dashboard_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/"
    report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{filename}"
    
    if total_change_count > 0:
        payload = {
            "text": f"📢 *[{TODAY_STR} {TIME_STR}] 경쟁사 동향 보고* \n\n✅ *요약:* {summary_text}\n👉 *오늘 리포트:* {report_url}\n📂 *전체 이력:* {dashboard_url}"
        }
    else:
        payload = {
            "text": f"📋 *[{TODAY_STR} {TIME_STR}] 경쟁사 동향 보고* \n\n✅ 특이사항 없음\n📂 *전체 이력:* {dashboard_url}"
        }
        
    if SLACK_WEBHOOK_URL:
        requests.post(SLACK_WEBHOOK_URL, json=payload)
        print("✅ 슬랙 알림 완료")
    else:
        print("⚠️ 슬랙 URL 없음")

if __name__ == "__main__":
    main()
