# list_models.py
import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

print("🔍 사용 가능한 Gemini 모델:\n")

for model in genai.list_models():
    if 'generateContent' in model.supported_generation_methods:
        print(f"✅ {model.name}")

print("\n\n🧪 각 모델 테스트 중...\n")

# 가능한 이름들 테스트
test_names = [
    'gemini-2.0-flash',  
    'gemini-2.0-flash-exp',
    'gemini-2.0-flash-thinking-exp',
    'gemini-1.5-flash',
    'gemini-1.5-flash-latest',
    'gemini-1.5-pro',
    'models/gemini-2.0-flash-exp',
]

for name in test_names:
    try:
        model = genai.GenerativeModel(name)
        response = model.generate_content("test")
        print(f"✅ {name}: 작동함!")
    except Exception as e:
        error = str(e)[:100]
        print(f"❌ {name}: {error}")
