"""
notices_history.csv를 최근 N일치로 잘라서 저장한다.
- 원본 전체 이력은 main 브랜치에 그대로 남아있음 (이 스크립트는 streamlit-deploy 브랜치에서만 실행)
- Home.py의 혜택 추이 차트는 최근 30일만 쓰므로 180일 버퍼를 유지
"""
import sys
import pandas as pd

HISTORY_PATH = "data/notices_history.csv"
RETENTION_DAYS = 180


def main():
    try:
        df = pd.read_csv(HISTORY_PATH, encoding="utf-8-sig")
    except FileNotFoundError:
        print(f"⚠️ {HISTORY_PATH} 없음 — 건너뜀")
        return

    before = len(df)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=RETENTION_DAYS)
    df = df[df["date"] >= cutoff]
    df.to_csv(HISTORY_PATH, index=False, encoding="utf-8-sig")
    print(f"✅ {HISTORY_PATH} 정리: {before}건 → {len(df)}건 (최근 {RETENTION_DAYS}일)")


if __name__ == "__main__":
    sys.exit(main())
