import requests
from bs4 import BeautifulSoup
import pandas as pd
import json
import os
import datetime
import pytz
import time

# --- [설정] ---
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
TZ_KST = pytz.timezone('Asia/Seoul')
YESTERDAY = (datetime.datetime.now(TZ_KST) - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
# YESTERDAY = "2024-05-20" # 테스트용 날짜 고정 시 사용

# 헤더 설정 (차단 방지)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# --- [1. 크롤러 함수] ---

def get_ppomppu_posts(target_date):
    """뽐뿌 휴대폰포럼/기타정보 크롤링"""
    posts = []
    page = 1
    
    # 뽐뿌 휴대폰포럼 URL (필요 시 기타정보 URL로 변경 가능)
    base_url = "https://www.ppomppu.co.kr/zboard/zboard.php?id=phone&page={}" 
    
    while True:
        res = requests.get(base_url.format(page), headers=HEADERS)
        soup = BeautifulSoup(res.text, 'html.parser')
        rows = soup.select('tr.common-list0, tr.common-list1') # 게시글 리스트 row

        if not rows: break
        
        stop_crawling = False
        for row in rows:
            try:
                # 날짜 파싱 (뽐뿌는 오늘:시간, 과거:YY.MM.DD)
                date_tag = row.select_one('.board_date')
                if not date_tag: continue
                date_text = date_tag.text.strip()
                
                # 날짜 변환 로직
                if ":" in date_text: # 오늘 날짜 (시간만 표시됨) -> 어제 데이터 아니므로 패스
                    continue
                
                # '24.05.20' -> '2024-05-20' 변환
                post_date = "20" + date_text.replace('.', '-')
                
                if post_date == target_date:
                    title_tag = row.select_one('font.list_title') or row.select_one('a')
                    title = title_tag.text.strip()
                    link = "https://www.ppomppu.co.kr/zboard/" + row.select_one('a')['href']
                    
                    # 조회수 / 댓글수
                    views = int(row.select_one('.board_hit').text.strip().replace(',', ''))
                    comment_span = row.select_one('.list_comment2')
                    comments = int(comment_span.text.strip()) if comment_span else 0
                    
                    posts.append({
                        'source': 'ppomppu', 'title': title, 'link': link,
                        'views': views, 'comments': comments, 'date': post_date
                    })
                elif post_date < target_date:
                    # 어제보다 이전 날짜가 나오면 크롤링 종료 (더 볼 필요 없음)
                    stop_crawling = True
                    break
            except Exception as e:
                continue
        
        if stop_crawling or page > 20: # 안전장치: 최대 20페이지까지만 탐색
            break
        page += 1
        time.sleep(0.5) # 서버 부하 방지
        
    return posts

def get_dc_posts(target_date):
    """디시 알뜰폰 갤러리 크롤링"""
    posts = []
    page = 1
    base_url = "https://gall.dcinside.com/mgallery/board/lists/?id=mvnogallery&page={}"
    # 디시 날짜 포맷: MM.DD (연도 없음 주의, 해 넘길 때 예외처리 필요하나 여기선 생략)
    target_md = target_date[5:].replace('-', '.') # '05-20' -> '05.20'
    
    while True:
        res = requests.get(base_url.format(page), headers=HEADERS)
        soup = BeautifulSoup(res.text, 'html.parser')
        rows = soup.select('tr.ub-content')
        
        if not rows: break
        
        stop_crawling = False
        for row in rows:
            try:
                if row.select_one('.gall_writer.ub-writer > .ip'): continue # 유동IP 글 제외? (선택사항)
                if 'ub-notice' in row.get('class', []): continue # 공지 제외
                
                date_text = row.select_one('.gall_date').text.strip()
                
                # 디시: 오늘(시간), 어제이전(MM.DD)
                if ":" in date_text: continue
                
                if date_text == target_md:
                    title = row.select_one('.gall_tit > a').text.strip()
                    link = "https://gall.dcinside.com" + row.select_one('.gall_tit > a')['href']
                    views = int(row.select_one('.gall_count').text.strip().replace(',', '') or 0)
                    
                    # 댓글수 (제목 옆에 [3] 이런식으로 있거나 별도 태그)
                    reply_tag = row.select_one('.reply_num')
                    comments = int(reply_tag.text.strip('[]')) if reply_tag else 0
                    
                    posts.append({
                        'source': 'dc', 'title': title, 'link': link,
                        'views': views, 'comments': comments, 'date': target_date
                    })
                # 디시는 날짜 정렬이 꼬일 때가 있어서, 날짜가 다르다고 바로 break하면 위험할 수 있으나 일반적으론 가능
                elif date_text < target_md: 
                    stop_crawling = True
                    break
            except:
                continue
        
        if stop_crawling or page > 30: # 디시는 리젠이 빠르니 좀 더 깊게 탐색
            break
        page += 1
        time.sleep(0.5)
        
    return posts

# --- [2. 데이터 분석 및 메시지 포맷팅] ---

def analyze_and_notify(p_posts, d_posts):
    total_posts = p_posts + d_posts
    df = pd.DataFrame(total_posts)
    
    if df.empty:
        print("수집된 데이터가 없습니다.")
        return

    # 1) 시장 활성도 (지진계) - 임의 기준값 설정 (나중엔 과거 평균으로 대체)
    p_count = len(p_posts)
    d_count = len(d_posts)
    p_status = "🔴 과열" if p_count > 100 else ("🟢 평온" if p_count < 50 else "🟡 활발")
    
    # 2) 브랜드 점유율 (SOV)
    brands = {
        '세븐모바일': ['세븐모바일', '7모', 'sk7', 'sk텔링크'],
        '모빙': ['모빙'],
        '리브엠': ['리브엠', '리브모바일'],
        '이야기': ['이야기', '이야기모바일']
    }
    sov_msg = ""
    for name, keywords in brands.items():
        count = df[df['title'].apply(lambda x: any(k in x for k in keywords))].shape[0]
        sov_msg += f"• {name}: {count}건\n"

    # 3) Top 5 선정 및 태깅 함수
    def format_top5(sub_df):
        msg = ""
        # 조회수 내림차순 정렬
        top5 = sub_df.sort_values(by='views', ascending=False).head(5)
        
        for idx, row in top5.iterrows():
            title = row['title']
            # 하이라이트 태깅
            icon = ""
            if any(k in title for k in ['0원', '무제한', '평생', '대란']): icon = " 💰"
            
            msg += f"{idx+1}. <{row['link']}|{title}>{icon} (👁️ {row['views']:,} / 💬 {row['comments']})\n"
        return msg

    # --- [슬랙 메시지 조합] ---
    slack_text = f"""
*[📊 {YESTERDAY} 알뜰폰 시장 모니터링]*

*🌡️ 시장 활성도 (어제 게시글 수)*
• 뽐뿌: {p_status} ({p_count}개)
• 디시: {d_count}개

*📈 브랜드 언급량 (SOV)*
{sov_msg}
*1️⃣ 뽐뿌 휴대폰포럼 (Top 5)*
{format_top5(pd.DataFrame(p_posts).reset_index(drop=True))}

*2️⃣ 디시 알뜰폰 갤러리 (Top 5)*
{format_top5(pd.DataFrame(d_posts).reset_index(drop=True))}

👉 <https://your-github-username.github.io/repo-name|웹 대시보드 확인하기>
    """
    
    # 슬랙 전송
    if SLACK_WEBHOOK_URL:
        requests.post(SLACK_WEBHOOK_URL, json={"text": slack_text})
    else:
        print("SLACK_WEBHOOK_URL이 설정되지 않았습니다.")
        print(slack_text)

    # 데이터 저장 (JSON)
    os.makedirs('data/monitoring', exist_ok=True)
    file_path = f'data/monitoring/data_{YESTERDAY}.json'
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(total_posts, f, ensure_ascii=False, indent=4)

# --- [메인 실행] ---
if __name__ == "__main__":
    print(f"[{YESTERDAY}] 데이터 수집 시작...")
    ppomppu_data = get_ppomppu_posts(YESTERDAY)
    dc_data = get_dc_posts(YESTERDAY)
    
    analyze_and_notify(ppomppu_data, dc_data)
    print("완료.")
