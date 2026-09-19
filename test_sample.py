import json
import google.generativeai as genai
from dotenv import load_dotenv
import os
import re
import time

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

# JSON 로드
with open('data/data_20260211_085737.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 각 회사에서 5개씩 샘플링 (총 25개 정도)
test_samples = []
for company, urls in data.items():
    company_samples = list(urls.items())[:5]  # 회사당 5개
    for url, info in company_samples:
        test_samples.append({
            'company': company,
            'url': url,
            'title': info['title'],
            'content': info['content']
        })

print("=" * 80)
print(f"🧪 샘플 테스트 시작 (총 {len(test_samples)}건)")
print("=" * 80)

success_count = 0
error_count = 0
error_details = []

for idx, sample in enumerate(test_samples, 1):
    print(f"\n[{idx}/{len(test_samples)}] {sample['company']} - {sample['title'][:40]}")

    prompt = f"""
다음 통신사 이벤트 페이지의 혜택 정보를 분석해주세요.

제목: {sample['title']}
내용: {sample['content'][:2000]}

아래 JSON 형식으로만 답변해주세요. 다른 설명은 절대 포함하지 마세요:
{{
  "benefit_amt": 숫자,
  "benefit_type": "DISCOUNT|CASHBACK|GIFT|POINT|ETC",
  "cond_type": "NEW_SUBSCRIPTION|NUMBER_TRANSFER|PLAN_CHANGE|ETC",
  "cond_plan_price": 숫자,
  "ai_summary": "요약"
}}
"""

    try:
        response = model.generate_content(prompt)
        response_text = response.text.strip()

        # 코드 블록 제거
        if response_text.startswith("```"):
            response_text = re.sub(r"^```(json)?|```$", "", response_text, flags=re.MULTILINE).strip()

        # JSON 파싱
        result = json.loads(response_text)

        print(f"   ✅ 성공: {result.get('benefit_amt', 0)}원 - {result.get('benefit_type', 'N/A')}")
        success_count += 1

    except json.JSONDecodeError as e:
        print(f"   ❌ JSON 파싱 실패")
        print(f"      응답 첫 200자: {response_text[:200]}")
        error_count += 1
        error_details.append({
            'company': sample['company'],
            'title': sample['title'][:40],
            'error': 'JSON 파싱 실패',
            'response': response_text[:200]
        })

    except Exception as e:
        print(f"   ❌ 오류: {type(e).__name__} - {str(e)[:100]}")
        error_count += 1
        error_details.append({
            'company': sample['company'],
            'title': sample['title'][:40],
            'error': f"{type(e).__name__}: {str(e)[:100]}"
        })

    # API 과부하 방지
    time.sleep(0.5)

print("\n" + "=" * 80)
print("📊 테스트 결과")
print("=" * 80)
print(f"총 {len(test_samples)}건 테스트")
print(f"✅ 성공: {success_count}건 ({success_count/len(test_samples)*100:.1f}%)")
print(f"❌ 실패: {error_count}건 ({error_count/len(test_samples)*100:.1f}%)")

if error_details:
    print("\n" + "=" * 80)
    print("🚨 실패 상세 내역 (처음 5개)")
    print("=" * 80)
    for detail in error_details[:5]:
        print(f"\n회사: {detail['company']}")
        print(f"제목: {detail['title']}")
        print(f"오류: {detail['error']}")
        if 'response' in detail:
            print(f"응답: {detail['response']}")
