import os
import io
import requests
import pdfplumber
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from playwright.sync_api import sync_playwright

URL_TCE = "https://www.tce.ce.gov.br/contexto/#/doe"
ORGAO_ALVO = "NÚCLEO DE TECNOLOGIA E QUALIDADE INDUSTRIAL DO CEARÁ"
EMAILS_DESTINO = ["pedro.brito@nutec.ce.gov.br", "pedropbrito@gmail.com"]

# Variáveis de ambiente (serão configuradas no GitHub)
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE")
SENHA_APP = os.getenv("SENHA_APP")

def obter_link_diario_hoje():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Adiciona um User-Agent para simular um navegador Google Chrome real do Windows
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            # Aumenta o tempo limite de acesso para 60 segundos
            page.goto(URL_TCE, timeout=60000)
            
            # Aguarda diretamente pelo link do PDF (até 60 segundos)
            page.wait_for_selector("a:has-text('Visualizar'), a[href*='pdf']", timeout=60000)
            
            elemento_pdf = page.locator("a:has-text('Visualizar'), a[href*='pdf']").first
            
            if elemento_pdf.count() > 0:
                link_pdf = elemento_pdf.get_attribute("href")
                if not link_pdf.startswith("http"):
                    link_pdf = "https://www.tce.ce.gov.br" + link_pdf
                browser.close()
                return link_pdf
                
        except Exception as e:
            print(f"Erro ao carregar a página ou encontrar o PDF: {e}")
            
        browser.close()
        return None

def ler_pdf_e_buscar(link_pdf):
    # O timeout longo evita falhas se o site do TCE estiver lento
    resposta = requests.get(link_pdf, timeout=60)
    recortes = []
    
    with pdfplumber.open(io.BytesIO(resposta.content)) as pdf:
        for numero_pagina, pagina in enumerate(pdf.pages, start=1):
            texto = pagina.extract_text()
            if texto and ORGAO_ALVO.upper() in texto.upper():
                # Encontra o índice da menção para pegar um contexto antes e depois
                idx = texto.upper().find(ORGAO_ALVO.upper())
                inicio = max(0, idx - 400)
                fim = min(len(texto), idx + 800)
                trecho = texto[inicio:fim].replace('\n', ' ')
                
                recortes.append(f"Página {numero_pagina}:\n...{trecho}...\n")
                
    return recortes

def enviar_email(recortes, link_pdf):
    msg = MIMEMultipart()
    msg['Subject'] = f"Alerta TCE-CE: Citação do NUTEC - {datetime.now().strftime('%d/%m/%Y')}"
    msg['From'] = EMAIL_REMETENTE
    msg['To'] = ", ".join(EMAILS_DESTINO)
    
    corpo = f"O órgão '{ORGAO_ALVO}' foi citado no Diário Oficial do TCE-CE de hoje.\n\n"
    corpo += f"Acesse o documento original aqui: {link_pdf}\n\n"
    corpo += "Trechos encontrados:\n\n" + "\n\n---\n\n".join(recortes)
    
    msg.attach(MIMEText(corpo, 'plain'))
    
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_REMETENTE, SENHA_APP)
        smtp.send_message(msg)

if __name__ == "__main__":
    link = obter_link_diario_hoje()
    if link:
        resultados = ler_pdf_e_buscar(link)
        if resultados:
            enviar_email(resultados, link)
            print(f"Alerta enviado com {len(resultados)} ocorrência(s).")
        else:
            print("Órgão não citado no diário de hoje.")
    else:
        print("Link do PDF não encontrado. Verifique o seletor CSS.")