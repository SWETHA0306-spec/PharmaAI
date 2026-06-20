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

inspect_method("client.text.translate", client.text.translate)
inspect_method("client.text_to_speech.convert", client.text_to_speech.convert)
inspect_method("client.chat.completions", client.chat.completions)

# Let's inspect document_intelligence methods
inspect_method("client.document_intelligence.create_job", client.document_intelligence.create_job)
inspect_method("client.document_intelligence.get_upload_links", client.document_intelligence.get_upload_links)
inspect_method("client.document_intelligence.start", client.document_intelligence.start)
inspect_method("client.document_intelligence.get_status", client.document_intelligence.get_status)
inspect_method("client.document_intelligence.get_download_links", client.document_intelligence.get_download_links)
