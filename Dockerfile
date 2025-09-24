#Use a imagem base oficial do Python com a versão especificada
FROM python:3.12.10

#Defina o diretório de trabalho dentro do container
WORKDIR /app

#Copie o arquivo de dependências primeiro para aproveitar o cache do Docker
COPY requirements.txt .

#Instale as dependências
RUN pip install --no-cache-dir -r requirements.txt

#Copie o restante dos arquivos da aplicação
COPY . .

#Exponha a porta que o Streamlit usa por padrão
EXPOSE 8501

#Comando para executar a aplicação
#O healthcheck monitora se a aplicação está rodando corretamente
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]