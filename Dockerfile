# KuroBBS_Goods_Gui 运行镜像
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV IN_DOCKER=1

EXPOSE 5001

CMD ["python", "app.py"]
