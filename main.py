"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템 (V64)
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-02-05 (V64: 본문 내 링크 감지 제거 + 순수 텍스트 형광펜 비교 + 목록 썸네일 최적화)
"""

import os
import json
import time
import glob
import random
import re
import traceback
import html
import difflib
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
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
SIMILARITY_THRESHOLD = 0.8

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
FILE_TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")
DISPLAY_DATE = NOW.strftime("%Y-%m-%d")
DISPLAY_TIME = NOW.strftime("%H:%M:%S")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

# =========================================================
# [유틸리티] 슬랙 알람 전송
# =========================================================
def send_slack_alert(webhook_url, payload):
    if not webhook_url: return
    try:
        requests.post(webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
    except: pass

# =========================================================
# [핵심] 노이즈 제거 (타이머/카운트다운 차단)
# =========================================================
def clean_noise(text):
    if not text: return ""
    # 1. 조회수 제거
    text = re.sub(r'(조회|view|읽음)(수)?[\s:.]*[\d,]+', '', text, flags=re.IGNORECASE)
    
    # 2. 타이머 패턴 제거 (D-Day, 시간, 남은 기간)
    # 13 : 15 / 13:15 / 12시 30분 등
    text = re.sub(r'\d{1,2}\s*[:시]\s*\d{1,2}(\s*[:분]\s*\d{1,2})?', '', text)
    text = re.sub(r'D-[\dDay]+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\d+(일|시간|분|초)\s*(남음|남았|전|후)', '', text)
    text = re.sub(r'(마감|종료|이벤트)\s*(까지)?', '', text)
    
    # 3. 공백 및 특수 노이즈 정리
    text = re.sub(r'Loading.*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def clean_html(html_source):
    if not html_source: return ""
    soup = BeautifulSoup(html_source, 'html.parser')
    for tag in soup(['script', 'style', 'meta', 'noscript', 'header', 'footer', 'iframe', 'button', 'input', 'nav', 'aside', 'link', 'form']):
        tag.decompose()
    return soup.body.prettify() if soup.body else soup.prettify()

def get_clean_text(html_content):
    """
    [V64 복구] 본문 내 링크(B) 추출 로직을 제거하고 순수 텍스트만 추출함.
    """
    if not html_content: return ""
    soup = BeautifulSoup(html_content, "html.parser")
    return soup.get_text(separator=" ", strip=True)

def calculate_similarity(text1, text2):
    if not text1 or not text2: return 0.0
    return difflib.SequenceMatcher(None, text1, text2).ratio()

# =========================================================
# [시각화] 변경사항 형광펜 하이라이팅 생성기
# =========================================================
def generate_diff_html(old_text, new_text):
    matcher = difflib.SequenceMatcher(None, old_text, new_text)
    result_html = []
    has_change = False
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            content = old_text[i1:i2]
            if len(content) > 60:
                result_html.append(content[:30] + " ... " + content[-30:])
            else:
                result_html.append(content)
        elif tag in ('replace', 'delete', 'insert'):
            has_change = True
            old_part = html.escape(old_text[i1:i2]) if i1 != i2 else ""
            new_part = html.escape(new_text[j1:j2]) if j1 != j2 else ""
            
            if tag == 'replace':
                result_html.append(f'<span style="background:#ffeef0; text-decoration:line-through; color:#999;">{old_part}</span> → <span style="background:#e6fffa; color:#006600; font-weight:bold; padding:0 4px;">{new_part}</span>')
            elif tag == 'delete':
                result_html.append(f'<span style="background:#ffeef0; text-decoration:line-through; color:#999;">{old_part}</span>')
            elif tag == 'insert':
                result_html.append(f'<span style="background:#e6fffa; color:#006600; font-weight:bold; padding:0 4px;">{new_part}</span>')

    if not has_change: return ""
    return f'<div style="font-size:13px; line-height:1.6; color:#444; background:#fafafa; padding:12px; border-radius:8px; border-left:4px solid #3498db; margin-top:10px;">{" ".join(result_html)}</div>'

def check_update_same_url(prev, curr):
    reasons = []
    diff_html = ""
    
    # 제목 비교
    if prev.get('title', '').strip() != curr.get('title', '').strip():
        reasons.append("제목 변경")
        diff_html += f"<div style='margin-bottom:8px;'><b>제목:</b> {prev.get('title')} <span style='color:blue;'>▶</span> <b>{curr.get('title')}</b></div>"
    
    # 본문 비교 (순수 텍스트만)
    prev_raw = get_clean_text(prev.get('content', ''))
    curr_raw = get_clean_text(curr.get('content', ''))
    prev_clean = clean_noise(prev_raw)
    curr_clean = clean_noise(curr_raw)
    
    if prev_clean and curr_clean:
        if calculate_similarity(prev_clean, curr_clean) < 1.0: 
            reasons.append("본문 수정")
            diff_html += generate_diff_html(prev_clean, curr_clean)

    # 썸네일 비교
    if prev.get('img', '').strip() != curr.get('img', '').strip():
        reasons.append("썸네일 변경")
            
    if reasons: 
        return {"msg": f"{', '.join(reasons)}", "html": diff_html}
    return None

# =========================================================
# [크롤러] 목록 기반 수집 로직 (썸네일 선점)
# =========================================================
def setup_driver():
    options = uc.ChromeOptions()
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    driver = uc.Chrome(options=options, version_main=144)
    return driver

def extract_list_with_thumbnails(driver, site_name, keyword_list, onclick_pattern=None, base_url="", target_selector=None):
    """
    목록 페이지에서 이벤트 URL과 썸네일을 세트로 수집함.
    """
    targets = {} # { url: thumbnail_url }
    try:
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        for link in soup.find_all('a'):
            href = link.get('href', '')
            onclick = link.get('onclick', '')
            final_url = ""

            if href and "javascript" not in href and "#" != href:
                for key in keyword_list:
                    if key in href:
                        final_url = urljoin(base_url, href)
                        break
            elif onclick and onclick_pattern:
                match = re.search(onclick_pattern, onclick)
                if match:
                    if site_name == "헬로모바일": final_url = f"https://direct.lghellovision.net/event/viewEventDetail.do?idxOfEvent={match.group(1)}"
                    elif site_name == "SK 7세븐모바일": final_url = f"https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId={match.group(1)}"
            
            if site_name == "KTM 모바일" and not final_url:
                seq = link.get('ntcartseq')
                if seq: final_url = f"https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq={seq}"

            if final_url:
                if any(x in final_url for x in ["login", "my", "faq", "logout"]): continue
                img_tag = link.find('img')
                # 목록 이미지 주소 추출
                thumb = urljoin(base_url, img_tag.get('src') or img_tag.get('data-src')) if img_tag else ""
                if final_url not in targets or (thumb and not targets[final_url]):
                    targets[final_url] = thumb
    except: pass

    final_data = {}
    for url, thumb in targets.items():
        try:
            driver.get(url)
            time.sleep(2)
            content = ""
            if target_selector:
                try: content = clean_html(driver.find_element(By.CSS_SELECTOR, target_selector).get_attribute('outerHTML'))
                except: content = clean_html(driver.page_source)
            else: content = clean_html(driver.page_source)
            
            title = ""
            try: title = driver.find_element(By.TAG_NAME, "h1").text.strip()
            except: pass
            if not title: title = driver.title.strip()

            final_data[url] = {"title": title, "img": thumb, "content": content[:15000]}
        except: continue
    return final_data

def crawl_site_logic(driver, site_name, base_url, pagination_param=None, target_selector=None):
    print(f"🚀 [{site_name}] 크롤링 시작...")
    if site_name == "SKT Air":
        driver.get(base_url); time.sleep(3)
        try:
            cont = driver.find_element(By.CSS_SELECTOR, target_selector)
            return {driver.current_url: {"title": "SKT Air 메인", "img": "", "content": clean_html(cont.get_attribute('outerHTML'))}}
        except: return {}
    
    keywords = []
    onclick = None
    base = ""
    if site_name == "U+ 유모바일": keywords = ["event", "benefit"]; base = "https://www.uplusumobile.com"
    elif site_name == "KTM 모바일": keywords = ["eventDetail"]; base = "https://www.ktmmobile.com"
    elif site_name == "스카이라이프": keywords = ["/event/"]; base = "https://www.skylife.co.kr"
    elif site_name == "헬로모바일": keywords = ["event"]; onclick = r"(\d+)"; base = "https://direct.lghellovision.net"
    elif site_name == "SK 7세븐모바일": keywords = ["event"]; onclick = r"['\"]([^'\"]+)['\"]"; base = "https://www.sk7mobile.com"
    elif site_name == "SKT 다이렉트": keywords = ["event", "plan"]; base = "https://shop.tworld.co.kr"
    
    collected = {}
    # 목록 페이지 순회
    for page in range(1, 4):
        t_url = f"{base_url}{('&' if '?' in base_url else '?')}{pagination_param}={page}" if pagination_param and pagination_param != "#" else base_url
        driver.get(t_url); time.sleep(2)
        data = extract_list_with_thumbnails(driver, site_name, keywords, onclick, base, target_selector)
        if not data: break
        collected.update(data)
        if not pagination_param: break
    return collected

# =========================================================
# [대시보드] 메인 인덱스 업데이트
# =========================================================
def update_index_page(change_stats):
    report_files = glob.glob(os.path.join(REPORT_DIR, "report_*.html"))
    report_files.sort(reverse=True)
    
    index_html = f"""
    <html><head><meta charset="utf-8"><title>Competitor Monitor Dashboard</title>
    <style>body{{font-family:sans-serif; padding:40px; background:#f5f7fa; color:#333;}} 
    .card{{background:white; padding:25px; border-radius:15px; box-shadow:0 5px 15px rgba(0,0,0,0.05); margin-bottom:30px;}}
    a{{color:#3498db; text-decoration:none;}} a:hover{{text-decoration:underline;}}
    </style></head><body>
    <div class="card"><h1>📊 경쟁사 프로모션 관제탑 ({DISPLAY_DATE})</h1>
    <p>Last Crawl: {DISPLAY_TIME}</p></div>
    <div class="card"><h3>📑 최신 리포트 히스토리</h3>
    {''.join([f"<div style='margin-bottom:10px;'>📅 <a href='reports/{os.path.basename(f)}'>{os.path.basename(f)}</a></div>" for f in report_files[:15]])}
    </div></body></html>
    """
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f: f.write(index_html)
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w") as f: f.write("")

# =========================================================
# [메인] 실행 엔진
# =========================================================
def main():
    try:
        driver = setup_driver()
        competitors = [
            {"name": "SKT 다이렉트", "url": "https://shop.tworld.co.kr/exhibition/submain", "param": None, "selector": "#contents"},
            {"name": "SKT Air", "url": "https://sktair-event.com/", "param": None, "selector": "#app > div > section.content"},
            {"name": "U+ 유모바일", "url": "https://www.uplusumobile.com/event-benefit/event/ongoing", "param": None, "selector": ""},
            {"name": "KTM 모바일", "url": "https://www.ktmmobile.com/event/eventBoardList.do", "param": None, "selector": ""},
            {"name": "스카이라이프", "url": "https://www.skylife.co.kr/event?category=mobile", "param": "p", "selector": ""},
            {"name": "헬로모바일", "url": "https://direct.lghellovision.net/event/viewEventList.do?returnTab=allli", "param": "#", "selector": ""},
            {"name": "SK 7세븐모바일", "url": "https://www.sk7mobile.com/bnef/event/eventIngList.do", "param": None, "selector": ""}
        ]
        
        yesterday_data = load_previous_data()
        today_data = {}
        change_stats = {c['name']: {'new': 0, 'updated': 0, 'deleted': 0} for c in competitors}
        
        for c in competitors:
            try:
                result = crawl_site_logic(driver, c['name'], c['url'], c['param'], c['selector'])
                today_data[c['name']] = result if result else yesterday_data.get(c['name'], {})
            except: today_data[c['name']] = yesterday_data.get(c['name'], {})
        
        driver.quit()
        
        # 오늘의 데이터 저장
        with open(os.path.join(DATA_DIR, f"data_{FILE_TIMESTAMP}.json"), "w", encoding="utf-8") as f:
            json.dump(today_data, f, ensure_ascii=False)
            
        report_body = ""
        total_changes = 0
        company_summary = []
        
        for name, pages in today_data.items():
            old = yesterday_data.get(name, {})
            list_new = [{"url": u, "data": pages[u]} for u in (set(pages.keys()) - set(old.keys()))]
            list_del = [{"url": u, "data": old[u]} for u in (set(old.keys()) - set(pages.keys()))]
            list_upd = []
            
            for url in (set(pages.keys()) & set(old.keys())):
                diff = check_update_same_url(old[url], pages[url])
                if diff: list_upd.append({"url": url, "reason": diff['msg'], "data": pages[url], "diff_html": diff['html']})
            
            change_stats[name].update({'new': len(list_new), 'updated': len(list_upd), 'deleted': len(list_del)})
            cnt = len(list_new) + len(list_upd) + len(list_del)
            
            if cnt > 0:
                s_html = f"<div style='margin-bottom:50px;'><h2>🏢 {name} ({cnt}건 변동)</h2>"
                for i in list_new:
                    img = f"<img src='{i['data']['img']}' style='height:80px; margin-right:15px; vertical-align:middle;'>" if i['data']['img'] else ""
                    s_html += f"<div style='background:#f9fff9; padding:15px; border:1px solid #cfc; border-radius:8px; margin-bottom:10px;'>{img}<b>[신규] {i['data']['title']}</b><br><a href='{i['url']}' target='_blank'>상세보기</a></div>"
                for i in list_upd:
                    s_html += f"<div style='background:#fffcf5; padding:15px; border:1px solid #fc9; border-radius:8px; margin-bottom:10px;'><b>[변경] {i['data']['title']}</b><br>{i['diff_html']}<br><a href='{i['url']}' target='_blank'>상세보기</a></div>"
                for i in list_del:
                    s_html += f"<div style='background:#fff5f5; padding:15px; border:1px solid #fcc; border-radius:8px; margin-bottom:10px; color:#999;'><strike>{i['data']['title']}</strike> (종료됨)</div>"
                report_body += s_html + "</div><hr>"
                total_changes += cnt
                company_summary.append(f"{name}({cnt})")

        # 리포트 파일 생성
        report_file = f"report_{FILE_TIMESTAMP}.html"
        with open(os.path.join(REPORT_DIR, report_file), "w", encoding="utf-8") as f:
            f.write(f"<html><head><meta charset='utf-8'><style>body{{font-family:sans-serif; line-height:1.6; padding:30px; color:#333;}} b{{color:#e67e22;}}</style></head><body><h1>📅 {DISPLAY_DATE} 경쟁사 동향 리포트</h1><p>총 {total_changes}건의 변동사항이 감지되었습니다.</p><hr>{report_body}</body></html>")
        
        update_index_page(change_stats)
        
        # 슬랙 알림 발송
        report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{report_file}"
        summary_txt = f"총 {total_changes}건 변동 ({', '.join(company_summary)})" if total_changes > 0 else "변동사항 없음"
        payload = {"text": f"📢 *[경쟁사 모니터링]* {summary_txt}\n👉 <{report_url}|상세 리포트 확인하기>"}
        send_slack_alert(SLACK_WEBHOOK_URL, payload)
        print("✅ 시스템 정상 종료")

    except Exception as e:
        print(f"🔥 Error: {traceback.format_exc()}")
        send_slack_alert(SLACK_WEBHOOK_URL, {"text": f"🚨 크롤링 엔진 에러: {str(e)}"})

if __name__ == "__main__": main()
