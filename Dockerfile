FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY cheapest_dub_turkey.py cheapest_dub_cai_egyptair.py cheapest_dub_ams.py ./
COPY results-2026-05-27.json dub_turkey_all_results.json dub_turkey_saw_ayt.json ./

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
