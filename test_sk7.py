import time
import re
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup

# [핵심] V99 main.py와 동일한 설정
BAD_TITLES = [
    "진행 이벤트", "지난 이벤트", "종료된 이벤트", "당첨자 발표", "이벤트", 
    "SK 7mobile", "KT M모바일", "LG HelloVision", "스카이라이프", "SKT Tworld",
    "친구 추천", "이번달 이벤트", "월 이벤트", "이달의 이벤트",
    "주메뉴", "바로가기", "본문 바로가기", "TOP", "전체 메뉴", "로그인", "회원가입", "사이트맵"
]

def setup_driver():
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    driver = uc.Chrome(options=options, version_main=144)
    return driver

def test_sk7_real_logic():
    print("🚀 [TEST] SK 7세븐모바일 V99 로직 동기화 테스트...\n")
    driver = setup_driver()
    
    # 1. 목록 진입
    list_url = "https://www.sk7mobile.com/bnef/event/eventIngList.do"
    print(f"▶ 목록 페이지 이동: {list_url}")
    driver.get(list_url)
    time.sleep(5) # 로딩 대기 (넉넉하게)
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    # [V99 로직] 영역 제한 (있으면 쓰고 없으면 전체)
    area = soup.select_one("#ct > section")
    if area: soup = area

    targets = []
    
    # [핵심 수정] main.py에 있는 정규식 그대로 복구 (엄격한 필터 제거)
    # 기존 main.py: onclick, base = ["event", "main.do"], r"['\"]([^'\"]+)['\"]", "https://www.sk7mobile.com"
    onclick_pattern = r"['\"]([^'\"]+)['\"]"

    links = soup.find_all('a')
    print(f"   - 발견된 전체 a 태그: {len(links)}개")

    # 2. URL 추출 (V99 로직 복붙)
    for link in links:
        onclick = link.get('onclick', '')
        href = link.get('href', '')
        
        final_url = ""
        
        # 1순위: onclick 파싱 (V99 방식)
        if onclick:
            m = re.search(onclick_pattern, onclick)
            if m:
                # 추출된 ID가 숫자거나 식별자일 때만 URL 조립
                extracted_id = m.group(1)
                if len(extracted_id) > 0 and "http" not in extracted_id:
                     final_url = f"https://www.sk7mobile.com/bnef/event/eventIngView.do?cntId={extracted_id}"
        
        # 2순위: href 파싱 (보조)
        if not final_url and href and "eventIngView" in href:
             if "javascript" not in href:
                 final_url = f"https://www.sk7mobile.com{href}" if href.startswith("/") else href

        # 중복 제거 및 저장
        if final_url and final_url not in targets:
            targets.append(final_url)
            if len(targets) >= 3: break # 테스트니까 3개만
    
    print(f"✅ 추출된 URL: {len(targets)}개")
    if len(targets) == 0:
        print("❌ 실패: URL을 하나도 못 찾았습니다. HTML 구조가 변경되었거나 차단되었을 수 있습니다.")
        driver.quit()
        return

    # 3. 상세 페이지 제목 검증
    for i, url in enumerate(targets):
        print(f"\n[{i+1}] 상세 진입: {url}")
        driver.get(url)
        time.sleep(2)
        
        print("   👇 [제목 후보군 확인]")
        
        # (A) 우리가 찾아야 할 정답 (strong.title)
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, "strong.title")
            for el in elems:
                print(f"      🔵 [strong.title] : \"{el.text.strip()}\" (★정답★)")
        except: pass

        # (B) 오탐지되는 범인들
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, ".event_view_top h3")
            for el in elems:
                txt = el.text.strip()
                status = "🚫 BAD" if any(b in txt for b in BAD_TITLES) else "❓"
                print(f"      🔴 [h3 (범인)] : \"{txt}\" -> {status}")
        except: pass

        try:
            elems = driver.find_elements(By.TAG_NAME, "h2")
            for el in elems:
                txt = el.text.strip()
                if txt and len(txt) < 20:
                    status = "🚫 BAD" if any(b in txt for b in BAD_TITLES) else "❓"
                    print(f"      🟠 [h2 (범인)] : \"{txt}\" -> {status}")
        except: pass

    driver.quit()
    print("\n🏁 테스트 종료")

if __name__ == "__main__":
    test_sk7_real_logic()
