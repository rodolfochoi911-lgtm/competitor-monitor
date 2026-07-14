"""
pages/1_💰_조건별 혜택 시뮬레이터.py
[변경]
- notices_latest.csv 기반으로 변경 (유의사항 분석 결과)
- 이벤트 매칭 없음 — 순수 혜택 조건표로만 시뮬레이션
"""

import streamlit as st
import pandas as pd
import os

st.set_page_config(page_title="조건별 혜택 시뮬레이터", page_icon="💰", layout="wide")


@st.cache_data(ttl=300)
def load_notices(mtime: float) -> pd.DataFrame:
    path = "data/notices_latest.csv"
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df['benefit_amt']    = pd.to_numeric(df['benefit_amt'],    errors='coerce').fillna(0)
    df['cond_plan_price']= pd.to_numeric(df['cond_plan_price'],errors='coerce').fillna(0)
    df['benefit_type']   = df['benefit_type'].fillna('NO_BENEFIT')
    df['cond_type']      = df['cond_type'].fillna('ETC')
    return df


def get_mtime(path: str) -> float:
    return os.path.getmtime(path) if os.path.exists(path) else 0


df = load_notices(get_mtime("data/notices_latest.csv"))

if df.empty:
    st.error("⚠️ 유의사항 분석 데이터가 없습니다. 크롤링 및 파싱을 먼저 실행해주세요.")
    st.stop()

# ── 사이드바 ─────────────────────────────────────────────
st.sidebar.header("🎛️ 시뮬레이션 조건 설정")

prices = sorted(df['cond_plan_price'].unique().tolist())
if 0 not in prices:
    prices.insert(0, 0)

st.sidebar.subheader("💰 내 요금제 선택")
my_plan = st.sidebar.select_slider("월 요금제 금액", options=prices, value=prices[-1]) \
    if len(prices) > 1 else st.sidebar.selectbox("월 요금제 금액", prices)
st.sidebar.caption(f"💡 **{my_plan:,}원** 이상 요금제 조건 통과")

st.sidebar.subheader("🚫 포함/제외 조건")
use_friend = st.sidebar.checkbox("친구/지인 결합 포함", value=True)
use_card   = st.sidebar.checkbox("제휴카드 발급 포함", value=False)
use_coupon = st.sidebar.checkbox("쿠폰/상품권 포함",   value=True)

# ── 필터링 ───────────────────────────────────────────────
filtered = df[
    (df['cond_plan_price'] <= my_plan) &
    (df['benefit_amt'] > 0) &
    (df['benefit_type'] != 'NO_BENEFIT')
].copy()

if not use_friend:
    filtered = filtered[filtered['cond_type'] != 'FRIEND']
if not use_card:
    filtered = filtered[filtered['cond_type'] != 'CARD']
if not use_coupon:
    filtered = filtered[filtered['benefit_type'] != 'VOUCHER']

# ── 집계 ─────────────────────────────────────────────────
results = []
if not filtered.empty:
    basic_max = (
        filtered[filtered['cond_type'] == 'BASIC']
        .groupby('company')['benefit_amt'].max()
    )
    addon_sum = (
        filtered[filtered['cond_type'] != 'BASIC']
        .groupby('company')['benefit_amt'].sum()
    )
    for company in set(basic_max.index) | set(addon_sum.index):
        b = int(basic_max.get(company, 0))
        a = int(addon_sum.get(company, 0))
        results.append({
            "company":   company,
            "total":     b + a,
            "breakdown": f"기본 {b:,}원 + 추가 {a:,}원"
        })

# ── 출력 ─────────────────────────────────────────────────
st.title("💰 조건별 혜택 시뮬레이터")
st.markdown(
    f"**조건:** 요금제 {my_plan:,}원 이하 | "
    f"친구결합 {'✅' if use_friend else '❌'} | "
    f"제휴카드 {'✅' if use_card else '❌'} | "
    f"쿠폰 {'✅' if use_coupon else '❌'}"
)
st.caption("유의사항 분석 기반 — 이벤트 개별 매칭 없이 조건별 혜택을 직접 비교합니다")

if results:
    df_result = pd.DataFrame(results).sort_values('total', ascending=False)
    winner = df_result.iloc[0]

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#667eea,#764ba2);
        padding:30px;border-radius:15px;text-align:center;color:white;
        box-shadow:0 4px 6px rgba(0,0,0,.1);margin-bottom:20px;">
        <h1 style="margin:0;font-size:2.5em;">🏆</h1>
        <h2 style="margin:10px 0;font-size:2em;">{winner['company']}</h2>
        <p style="font-size:1.8em;margin:10px 0;font-weight:bold;">
            총 혜택: {int(winner['total']):,}원</p>
        <p style="font-size:1em;opacity:.9;margin:0;">{winner['breakdown']}</p>
    </div>""", unsafe_allow_html=True)

    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("📊 통신사별 순위")
        st.dataframe(
            df_result.rename(columns={"company": "통신사", "total": "총 혜택", "breakdown": "구성"}),
            column_config={"총 혜택": st.column_config.NumberColumn(format="%d원")},
            hide_index=True, use_container_width=True
        )

    with col2:
        st.subheader(f"📋 {winner['company']} 혜택 조건 상세")
        detail = filtered[filtered['company'] == winner['company']].sort_values('benefit_amt', ascending=False)
        st.dataframe(
            detail[['notice_text', 'benefit_amt', 'benefit_type', 'cond_type', 'cond_plan_price', 'summary']],
            column_config={
                "notice_text":     st.column_config.TextColumn("유의사항 원문", width="large"),
                "benefit_amt":     st.column_config.NumberColumn("혜택 금액", format="%d원"),
                "benefit_type":    st.column_config.TextColumn("유형", width="small"),
                "cond_type":       st.column_config.TextColumn("조건", width="small"),
                "cond_plan_price": st.column_config.NumberColumn("최소 요금제", format="%d원"),
                "summary":         st.column_config.TextColumn("요약", width="medium"),
            },
            hide_index=True, use_container_width=True
        )
else:
    st.warning("⚠️ 조건에 맞는 혜택이 없습니다.")
    col1, col2 = st.columns(2)
    with col1:
        st.info(f"""
**필터링 단계:**
1. 전체 유의사항 항목: **{len(df)}건**
2. 요금제 조건 통과: **{len(df[df['cond_plan_price'] <= my_plan])}건**
3. 혜택 0원 제외 후: **{len(filtered)}건**
        """)
    with col2:
        st.success("""
**해결 방법:**
- 요금제 금액을 높여보세요
- 제외 조건을 해제해보세요
- 쿠폰/상품권을 포함해보세요
        """)

# ── 전체 유의사항 혜택 테이블 ─────────────────────────────
st.divider()
st.subheader("🔍 조건에 맞는 전체 혜택 항목")
st.caption(f"총 {len(filtered)}개 항목")

if not filtered.empty:
    st.dataframe(
        filtered.sort_values('benefit_amt', ascending=False)[
            ['company', 'notice_text', 'benefit_amt', 'benefit_type', 'cond_type', 'cond_plan_price', 'summary']
        ],
        column_config={
            "company":         st.column_config.TextColumn("통신사", width="small"),
            "notice_text":     st.column_config.TextColumn("유의사항 원문", width="large"),
            "benefit_amt":     st.column_config.NumberColumn("혜택 금액", format="%d원"),
            "benefit_type":    st.column_config.TextColumn("유형", width="small"),
            "cond_type":       st.column_config.TextColumn("조건", width="small"),
            "cond_plan_price": st.column_config.NumberColumn("최소 요금제", format="%d원"),
            "summary":         st.column_config.TextColumn("요약", width="medium"),
        },
        hide_index=True, use_container_width=True
    )
