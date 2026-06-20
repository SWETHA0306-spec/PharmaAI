import inspect
from sarvamai import SarvamAI
from sarvamai.document_intelligence.client import DocumentIntelligenceJob

for attr in dir(DocumentIntelligenceJob):
    if not attr.startswith("_"):
        method = getattr(DocumentIntelligenceJob, attr)
        print(f"Job method: {attr}")
        try:
            print(f"  Sig: {inspect.signature(method)}")
        except:
            pass
        try:
            print(f"  Doc: {method.__doc__}")
        except:
            pass
