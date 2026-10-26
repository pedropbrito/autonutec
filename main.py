import os
import requests
import pdfplumber
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime
from playwright.sync_api import sync_playwright

URL_TCE = "https://www.tce.ce.gov.br/contexto/#/doe"
ORGAO_ALVO = "NÚCLEO DE TECNOLOGIA E QUALIDADE INDUSTRIAL DO CEARÁ"
EMAILS_DESTINO = ["pedro.brito@nutec.ce.gov.br", "pedropbrito@gmail.com"]

# Variáveis de ambiente configuradas no GitHub Secrets
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE")
SENHA_APP = os.getenv("SENHA_APP")
ARQUIVO_PDF = "diario_oficial.pdf"

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

            # Baixa e salva o PDF localmente para facilitar o envio em anexo
            if href and ".pdf" in href.lower():
                if not href.startswith("http"):
                    href = "https://www.tce.ce.gov.br" + href
                resposta = requests.get(href, timeout=60)
                with open(ARQUIVO_PDF, "wb") as f:
                    f.write(resposta.content)
                browser.close()
                return ARQUIVO_PDF
            
            with page.expect_download(timeout=60000) as download_info:
                elemento.click()
            
            download = download_info.value
            download.save_as(ARQUIVO_PDF)
            browser.close()
            return ARQUIVO_PDF
            
        except Exception as e:
            print(f"Erro na automação web: {e}")
            browser.close()
            return None

def ler_pdf(caminho):
    recortes = []
    with pdfplumber.open(caminho) as pdf:
        for numero_pagina, pagina in enumerate(pdf.pages, start=1):
            texto = pagina.extract_text()
            if texto and ORGAO_ALVO.upper() in texto.upper():
                # Extrai o texto completo da página para não ficar suprimido
                recortes.append(f"--- INÍCIO DA PÁGINA {numero_pagina} ---\n\n{texto}\n\n--- FIM DA PÁGINA {numero_pagina} ---")
    return recortes

def enviar_email(recortes, caminho_pdf):
    msg = MIMEMultipart()
    msg['Subject'] = f"Alerta Diário TCE-CE - {datetime.now().strftime('%d/%m/%Y')}"
    msg['From'] = EMAIL_REMETENTE
    msg['To'] = ", ".join(EMAILS_DESTINO)
    
    if recortes:
        corpo = f"O órgão '{ORGAO_ALVO}' FOI ENCONTRADO no Diário Oficial de hoje.\n\n"
        corpo += "Segue em anexo o arquivo PDF original. Abaixo está a transcrição integral da(s) página(s) onde o órgão foi citado:\n\n"
        corpo += "\n\n".join(recortes)
    else:
        corpo = f"O órgão '{ORGAO_ALVO}' NÃO FOI CITADO no Diário Oficial do TCE-CE da data de hoje.\n"
    
    msg.attach(MIMEText(corpo, 'plain'))
    
    # Anexa o arquivo PDF ao e-mail
    if caminho_pdf and os.path.exists(caminho_pdf):
        with open(caminho_pdf, "rb") as f:
            anexo = MIMEApplication(f.read(), _subtype="pdf")
            anexo.add_header('Content-Disposition', 'attachment', filename="Diario_Oficial_TCE.pdf")
            msg.attach(anexo)
    
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_REMETENTE, SENHA_APP)
        smtp.send_message(msg)

if __name__ == "__main__":
    print("Iniciando busca do diário...")
    caminho_pdf = acessar_e_obter_pdf()
    
    if caminho_pdf:
        print("PDF localizado. Iniciando leitura...")
        trechos = ler_pdf(caminho_pdf)
        print(f"Leitura concluída. {len(trechos)} menção(ões) encontrada(s). Enviando e-mail...")
        enviar_email(trechos, caminho_pdf)
        
        # Exclui o arquivo temporário após o envio
        if os.path.exists(caminho_pdf):
            os.remove(caminho_pdf)
            
        print("Processo finalizado com sucesso.")
    else:
        print("Erro: Não foi possível acessar o PDF de hoje.")