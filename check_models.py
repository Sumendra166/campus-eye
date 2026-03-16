import google.generativeai as genai

genai.configure(api_key="AIzaSyCjQWKTBvE9hSVeFEF3P-Upa2yplOD-kso")

for m in genai.list_models():
    if "generateContent" in m.supported_generation_methods:
        print(m.name)