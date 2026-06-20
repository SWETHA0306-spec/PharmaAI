from sarvamai.types.doc_digitization_job_detail import DocDigitizationJobDetail

print("Fields in DocDigitizationJobDetail:")
try:
    annotations = DocDigitizationJobDetail.__annotations__
    for k, v in annotations.items():
        print(f"  {k}: {v}")
except Exception as e:
    print(f"Error: {e}")
