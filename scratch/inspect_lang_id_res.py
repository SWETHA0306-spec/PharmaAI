from sarvamai.types.language_identification_response import LanguageIdentificationResponse

print("Fields in LanguageIdentificationResponse:")
try:
    annotations = LanguageIdentificationResponse.__annotations__
    for k, v in annotations.items():
        print(f"  {k}: {v}")
except Exception as e:
    print(f"Error: {e}")
