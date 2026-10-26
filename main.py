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

# Variáveis de ambiente configuradas no GitHub Secrets
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE")
SENHA_APP = os.getenv("SENHA_APP")

def acessar_e_obter_pdf():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            accept_downloads=True
        )
        page = context.new_page()
        
        try:
            page.goto(URL_TCE, timeout=60000)
            
            # Aguarda o botão aparecer de forma visível, ignorando processos em 2º plano do site
            seletor_botao = "text='Visualizar completo'"
            page.wait_for_selector(seletor_botao, state="visible", timeout=60000)
            elemento = page.locator(seletor_botao).first
            
            href = None
            try:
                href = elemento.get_attribute("href")
                if not href:
                    href = elemento.evaluate("el => el.closest('a') ? el.closest('a').href : null")
            except:
                pass

            if href and ".pdf" in href.lower():
                if not href.startswith("http"):
                    href = "https://www.tce.ce.gov.br" + href
                browser.close()
                return {"tipo": "link", "dados": href}
            
            with page.expect_download(timeout=60000) as download_info:
                elemento.click()
            
            download = download_info.value
            caminho = f"./{download.suggested_filename}"
            download.save_as(caminho)
            browser.close()
            
            return {"tipo": "arquivo", "dados": caminho}
            
        except Exception as e:
            print(f"Erro na automação web: {e}")
            browser.close()
            return None

def ler_pdf(origem):
    recortes = []
    
    # Prepara o PDF dependendo se veio por link (requests) ou arquivo local baixado
    if origem["tipo"] == "link":
        resposta = requests.get(origem["dados"], timeout=60)
        pdf_obj = io.BytesIO(resposta.content)
    else:
        pdf_obj = open(origem["dados"], "rb")
        
    with pdfplumber.open(pdf_obj) as pdf:
        for numero_pagina, pagina in enumerate(pdf.pages, start=1):
            texto = pagina.extract_text()
            if texto and ORGAO_ALVO.upper() in texto.upper():
                idx = texto.upper().find(ORGAO_ALVO.upper())
                inicio = max(0, idx - 400)
                fim = min(len(texto), idx + 800)
                trecho = texto[inicio:fim].replace('\n', ' ')
                recortes.append(f"Página {numero_pagina}:\n...{trecho}...\n")
                
    # Limpa o arquivo local se houver
    if origem["tipo"] == "arquivo":
        pdf_obj.close()
        if os.path.exists(origem["dados"]):
            os.remove(origem["dados"])
        
    return recortes

def enviar_email(recortes):
    msg = MIMEMultipart()
    msg['Subject'] = f"Alerta Diário TCE-CE - {datetime.now().strftime('%d/%m/%Y')}"
    msg['From'] = EMAIL_REMETENTE
    msg['To'] = ", ".join(EMAILS_DESTINO)
    
    # Condicional que garante o envio do e-mail em ambos os cenários (encontrado ou não)
    if recortes:
        corpo = f"O órgão '{ORGAO_ALVO}' FOI ENCONTRADO no Diário Oficial de hoje.\n\n"
        corpo += "Trechos localizados:\n\n" + "\n\n---\n\n".join(recortes)
    else:
        corpo = f"O órgão '{ORGAO_ALVO}' NÃO FOI CITADO no Diário Oficial do TCE-CE da data de hoje.\n"
    
    msg.attach(MIMEText(corpo, 'plain'))
    
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_REMETENTE, SENHA_APP)
        smtp.send_message(msg)

if __name__ == "__main__":
    print("Iniciando busca do diário...")
    resultado_pdf = acessar_e_obter_pdf()
    
    if resultado_pdf:
        print("PDF localizado. Iniciando leitura...")
        trechos = ler_pdf(resultado_pdf)
        print(f"Leitura concluída. {len(trechos)} menção(ões) encontrada(s). Enviando e-mail...")
        enviar_email(trechos)
        print("Processo finalizado com sucesso.")
    else:
        print("Erro: Não foi possível acessar o PDF de hoje.")