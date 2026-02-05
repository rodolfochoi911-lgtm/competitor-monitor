"""
[프로젝트] 경쟁사 프로모션 모니터링 자동화 시스템 (V65_Restored)
[작성자] 최지원 (GTM Strategy)
[업데이트] 2026-02-05 (V65: 슬랙 알람 포맷 원복 + 전체 목록 파일 생성 로직 복구)
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
# [유틸리티] 도구함
# =========================================================
def send_slack_alert(webhook_url, payload):
    if not webhook_url: return
    try:
        requests.post(webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
    except: pass

def load_previous_data():
    json_files = glob.glob(os.path.join(DATA_DIR, "data_*.json"))
    if not json_files: return {}
    json_files.sort()
    latest_file = json_files[-1]
    try:
        with open(latest_file, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

def calculate_similarity(text1, text2):
    if not text1 or not text2: return 0.0
    return difflib.SequenceMatcher(None, text1, text2).ratio()

# =========================================================
# [핵심] 노이즈 제거 (타이머/카운트다운 차단)
# =========================================================
def clean_noise(text):
    if not text: return ""
    text = re.sub(r'(조회|view|읽음)(수)?[\s:.]*[\d,]+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\d{1,2}\s*[:시]\s*\d{1,2}(\s*[:분]\s*\d{1,2})?', '', text)
    text = re.sub(r'D-[\dDay]+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\d+(일|시간|분|초)\s*(남음|남았|전|후)', '', text)
    text = re.sub(r'(마감|종료|이벤트)\s*(까지)?', '', text)
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
    if not html_content: return ""
    soup = BeautifulSoup(html_content, "html.parser")
    return soup.get_text(separator=" ", strip=True)

# =========================================================
# [시각화] 변경사항 형광펜 하이라이팅
# =========================================================
def generate_diff_html(old_text, new_text):
    matcher = difflib.SequenceMatcher(None, old_text, new_text)
    result_html = []
    has_change = False
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            content = old_text[i1:i2]
            result_html.append(content[:30] + " ... " + content[-30:] if len(content) > 60 else content)
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
    return f'<div style="font-size:13px; line-height:1.6; color:#444; background:#fafafa; padding:12px; border-radius:8px; border-left:4px solid #3498db; margin-top:10px;">{" ".join(result_html)}</div>' if has_change else ""

def check_update_same_url(prev, curr):
    reasons = []
    diff_html = ""
    if prev.get('title', '').strip() != curr.get('title', '').strip():
        reasons.append("제목 변경")
        diff_html += f"<div style='margin-bottom:8px;'><b>제목:</b> {prev.get('title')} <span style='color:blue;'>▶</span> <b>{curr.get('title')}</b></div>"
    
    p_clean, c_clean = clean_noise(get_clean_text(prev.get('content', ''))), clean_noise(get_clean_text(curr.get('content', '')))
    if p_clean and c_clean and calculate_similarity(p_clean, c_clean) < 1.0:
        reasons.append("본문 수정")
        diff_html += generate_diff_html(p_clean, c_clean)
        
    if prev.get('img', '').strip() != curr.get('img', '').strip():
        reasons.append("썸네일 변경")
    return {"msg": f"{', '.join(reasons)}", "html": diff_html} if reasons else None

# =========================================================
# [크롤러] 목록 기반 수집 로직
# =========================================================
def setup_driver():
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    return uc.Chrome(options=options, version_main=144)

def extract_list_with_thumbnails(driver, site_name, keyword_list, onclick_pattern=None, base_url="", target_selector=None):
    targets = {}
    try:
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        for link in soup.find_all('a'):
            href, onclick = link.get('href', ''), link.get('onclick', '')
            final_url = ""
            if href and "javascript" not in href and "#" != href:
                for key in keyword_list:
                    if key in href:
                        final_url = urljoin(base_url, href); break
            elif onclick and onclick_pattern:
                m = re.search(onclick_pattern, onclick)
                if m:
                    if site_name == "헬로모바일": final_url = f"https://direct.lghellovision.net/event/viewEventDetail.do?idxOfEvent={m.group(1)}"
                    elif site_name == "SK 7세븐모바일": final_url = f"https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId={m.group(1)}"
            if site_name == "KTM 모바일" and not final_url:
                seq = link.get('ntcartseq')
                if seq: final_url = f"https://www.ktmmobile.com/event/eventDetail.do?ntcartSeq={seq}"
            if final_url and "login" not in final_url:
                img = link.find('img')
                thumb = urljoin(base_url, img.get('src') or img.get('data-src')) if img else ""
                if final_url not in targets or (thumb and not targets[final_url]): targets[final_url] = thumb
    except: pass
    final_data = {}
    for url, thumb in targets.items():
        try:
            driver.get(url); time.sleep(2)
            try: cont = clean_html(driver.find_element(By.CSS_SELECTOR, target_selector).get_attribute('outerHTML')) if target_selector else clean_html(driver.page_source)
            except: cont = clean_html(driver.page_source)
            try: title = driver.find_element(By.TAG_NAME, "h1").text.strip()
            except: title = driver.title.strip()
            final_data[url] = {"title": title, "img": thumb, "content": cont[:15000]}
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
    if site_name == "U+ 유모바일": keywords, base = ["event", "benefit"], "https://www.uplusumobile.com"
    elif site_name == "KTM 모바일": keywords, base = ["eventDetail"], "https://www.ktmmobile.com"
    elif site_name == "스카이라이프": keywords, base = ["/event/"], "https://www.skylife.co.kr"
    elif site_name == "헬로모바일": keywords, onclick, base = ["event"], r"(\d+)", "https://direct.lghellovision.net"
    elif site_name == "SK 7세븐모바일": keywords, onclick, base = ["event"], r"['\"]([^'\"]+)['\"]", "https://www.sk7mobile.com"
    elif site_name == "SKT 다이렉트": keywords, base = ["event", "plan"], "https://shop.tworld.co.kr"
    collected = {}
    for page in range(1, 4):
        t_url = f"{base_url}{('&' if '?' in base_url else '?')}{pagination_param}={page}" if pagination_param and pagination_param != "#" else base_url
        driver.get(t_url); time.sleep(2)
        data = extract_list_with_thumbnails(driver, site_name, keywords, onclick, base, target_selector)
        if not data: break
        collected.update(data)
        if not pagination_param: break
    return collected

def update_index_page():
    report_files = sorted(glob.glob(os.path.join(REPORT_DIR, "report_*.html")), reverse=True)
    index_html = f"""
    <html><head><meta charset='utf-8'><title>Dashboard</title>
    <style>body{{font-family:sans-serif; padding:40px; background:#f5f7fa;}} .card{{background:white; padding:25px; border-radius:15px; box-shadow:0 5px 15px rgba(0,0,0,0.05);}}</style>
    </head><body><div class='card'><h1>📊 모니터링 관제탑</h1>
    {''.join([f"<div style='margin-bottom:10px;'>📅 <a href='reports/{os.path.basename(f)}'>{os.path.basename(f)}</a></div>" for f in report_files[:15]])}
    </div></body></html>"""
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f: f.write(index_html)

# =========================================================
# [메인] 실행 로직
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
        yesterday = load_previous_data()
        today = {}
        for c in competitors:
            try:
                res = crawl_site_logic(driver, c['name'], c['url'], c['param'], c['selector'])
                today[c['name']] = res if res else yesterday.get(c['name'], {})
            except: today[c['name']] = yesterday.get(c['name'], {})
        driver.quit()
        with open(os.path.join(DATA_DIR, f"data_{FILE_TIMESTAMP}.json"), "w", encoding="utf-8") as f: json.dump(today, f, ensure_ascii=False)
        
        # 리포트 생성
        report_body, total_chg, summary = "", 0, []
        for name, pages in today.items():
            old = yesterday.get(name, {})
            list_new, list_del, list_upd = [{"url": u, "data": pages[u]} for u in (set(pages.keys()) - set(old.keys()))], [{"url": u, "data": old[u]} for u in (set(old.keys()) - set(pages.keys()))], []
            for url in (set(pages.keys()) & set(old.keys())):
                diff = check_update_same_url(old[url], pages[url])
                if diff: list_upd.append({"url": url, "reason": diff['msg'], "data": pages[url], "diff_html": diff['html']})
            cnt = len(list_new) + len(list_upd) + len(list_del)
            if cnt > 0:
                s_html = f"<h2>🏢 {name} ({cnt}건)</h2>"
                for i in list_new: s_html += f"<div style='background:#f9fff9; padding:10px; border:1px solid #cfc; margin-bottom:10px;'><img src='{i['data']['img']}' style='height:60px; margin-right:10px;'><b>[신규] {i['data']['title']}</b><br><a href='{i['url']}'>이동</a></div>"
                for i in list_upd: s_html += f"<div style='background:#fffcf5; padding:10px; border:1px solid #fc9; margin-bottom:10px;'><b>[변경] {i['data']['title']}</b><br>{i['diff_html']}<br><a href='{i['url']}'>이동</a></div>"
                for i in list_del: s_html += f"<div style='background:#fff5f5; padding:10px; border:1px solid #fcc; margin-bottom:10px; color:#999;'><strike>{i['data']['title']}</strike> (종료)</div>"
                report_body += s_html + "<hr>"; total_chg += cnt; summary.append(f"{name}({cnt})")
        
        rep_file = f"report_{FILE_TIMESTAMP}.html"
        with open(os.path.join(REPORT_DIR, rep_file), "w", encoding="utf-8") as f: f.write(f"<html><head><meta charset='utf-8'></head><body><h1>📅 {DISPLAY_DATE} 리포트</h1>{report_body}</body></html>")
        
        # [복구됨] 전체 목록 파일 생성 (슬랙 링크용)
        list_html = f"<h1>📂 {DISPLAY_DATE} 전체 목록</h1><hr>"
        for name, pages in today.items():
            list_html += f"<h3>{name} ({len(pages)}개)</h3><div style='display:grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap:15px;'>"
            for url, data in pages.items():
                img = f"<img src='{data.get('img','')}' style='width:100%; height:120px; object-fit:cover; border-radius:5px; border:1px solid #eee;'>" if data.get('img') else "<div style='width:100%; height:120px; background:#f0f0f0; display:flex; align-items:center; justify-content:center;'>No Image</div>"
                list_html += f"<div style='border:1px solid #ddd; padding:10px;'><a href='{url}' target='_blank' style='text-decoration:none; color:#333;'>{img}<p style='margin:10px 0 0 0; font-weight:bold; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;'>{data.get('title')}</p></a></div>"
            list_html += "</div><hr>"
        list_file = f"list_{FILE_TIMESTAMP}.html"
        with open(os.path.join(REPORT_DIR, list_file), "w", encoding="utf-8") as f: f.write(list_html)

        update_index_page()
        
        # [복구됨] 슬랙 알람 포맷 (풍성한 버전)
        dashboard_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/"
        report_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{rep_file}"
        list_url = f"https://{GITHUB_USER}.github.io/{REPO_NAME}/reports/{list_file}"
        summary_text = f"총 {total_chg}건 변동 ({', '.join(summary)})" if total_chg > 0 else "특이사항 없음"
        
        payload = {
            "text": f"📢 *[KST {DISPLAY_TIME}] 경쟁사 동향 보고* \n\n✅ *요약:* {summary_text}\n\n👉 *변경 리포트:* {report_url}\n🗂️ *전체 목록:* {list_url}\n📂 *대시보드:* {dashboard_url}"
        }
        send_slack_alert(SLACK_WEBHOOK_URL, payload)
        print("✅ 모든 작업 완료")

    except Exception as e:
        print(f"🔥 Error: {traceback.format_exc()}")
        send_slack_alert(SLACK_WEBHOOK_URL, {"text": f"🚨 에러: {str(e)}"})

if __name__ == "__main__": main()
