# 1. Começamos com Python (que já é baseado em Debian/Ubuntu)
FROM python:3.13-slim

# 2. Configurações de ambiente
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# 3. Instalamos TUDO o que é necessário do sistema em um único passo
# Incluindo as bibliotecas de vídeo e a libusb para o leitor biométrico
# Instalamos as dependências do sistema e do FFmpeg para o PyAV
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    pkg-config \
    curl \
    # Headers necessários para compilar a lib 'av' no python
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libswscale-dev \
    libswresample-dev \
    libavfilter-dev \
    # Bibliotecas para o leitor biométrico
    libusb-1.0-0-dev \
    usbutils \
    && rm -rf /var/lib/apt/lists/*

# 4. Instalação das dependências do Python (Streamlit, etc)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 5. Copia o resto do código
COPY . .

# 6. Porta do Streamlit
EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health

# 7. Inicialização
ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]