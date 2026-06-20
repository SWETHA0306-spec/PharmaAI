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
    try:
        print(f"Docstring: {method.__doc__}")
    except Exception as e:
        print(f"Could not get docstring: {e}")

inspect_method("client.text.identify_language", client.text.identify_language)
