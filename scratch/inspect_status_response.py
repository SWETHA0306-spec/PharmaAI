from sarvamai.types.doc_digitization_job_status_response import DocDigitizationJobStatusResponse

print("Fields in DocDigitizationJobStatusResponse:")
try:
    # Let's inspect class attributes/annotations
    annotations = DocDigitizationJobStatusResponse.__annotations__
    for k, v in annotations.items():
        print(f"  {k}: {v}")
except Exception as e:
    print(f"Error: {e}")
