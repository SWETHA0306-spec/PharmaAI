from sarvamai.types.translation_response import TranslationResponse
from sarvamai.types.text_to_speech_response import TextToSpeechResponse

print("TranslationResponse fields:")
for k, v in TranslationResponse.__annotations__.items():
    print(f"  {k}: {v}")

print("\nTextToSpeechResponse fields:")
for k, v in TextToSpeechResponse.__annotations__.items():
    print(f"  {k}: {v}")
