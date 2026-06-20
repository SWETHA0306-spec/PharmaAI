import inspect
from sarvamai import SarvamAI

client = SarvamAI(api_subscription_key="dummy_key")

def inspect_method(name, method):
    print(f"=== {name} ===")
    try:
        sig = inspect.signature(method)
        print(f"Signature: {sig}")
    except Exception as e:
        print(f"Could not get signature: {e}")

inspect_method("client.text.translate", client.text.translate)
inspect_method("client.text_to_speech.convert", client.text_to_speech.convert)
