# resend_slack.py
import os
import requests
import pandas as pd
from datetime import datetime

# 깃허브 설정에서 불러옴
slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL")

def send_slack_report():
    if not slack_webhook_url:
        print("❌ Webhook URL이 없습니다.")
        return

    # 1. 저장된 CSV 읽기 (이미 만들어진 거 재활용)
    try:
        df = pd.read_csv("data/dashboard_latest.csv")
    except:
        print("❌ 데이터 파일이 없습니다.")
        return

    # 2. 메시지 만들기
    now_str = datetime.now().strftime("%y.%m.%d %H:%M:%S")
    # ▼▼▼ [수정된 진짜 주소] ▼▼▼
    dashboard_url = "https://share.streamlit.io/rodolfochoi911-lgtm/competitor-monitor/main/Home.py"
    
    # 요약 (간단하게)
    total_change = len(df)
    summary_text = f"데이터 {total_change}건 로드됨"

    message = f"""
[{now_str}] 🚨 긴급 재전송 테스트

요약: {summary_text}
대시보드: {dashboard_url}
    """
    
    try:
        requests.post(slack_webhook_url, json={"text": message})
        print("✅ 슬랙 재전송 성공!")
    except Exception as e:
        print(f"❌ 실패: {e}")

if __name__ == "__main__":
    send_slack_report()
