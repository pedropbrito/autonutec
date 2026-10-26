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
        page = browser.new_page()
        page.goto(URL_TCE)
        
        # Aguarda a tabela de diários carregar no site
        page.wait_for_selector("table") 
        
        # Como o site pode ter classes específicas, aqui mapeamos o primeiro link que aponta para um PDF
        # Você pode precisar inspecionar a página e ajustar o seletor ("a[href$='.pdf']") se necessário
        elemento_pdf = page.locator("a:has-text('Visualizar'), a[href*='pdf']").first
        
        if elemento_pdf.count() > 0:
            link_pdf = elemento_pdf.get_attribute("href")
            # Garante que o link é absoluto
            if not link_pdf.startswith("http"):
                link_pdf = "https://www.tce.ce.gov.br" + link_pdf
            browser.close()
            return link_pdf
        
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