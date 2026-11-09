import os
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin

import pdfplumber
import requests
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

URL_TCE = "https://www.tce.ce.gov.br/contexto/#/doe"
URL_DOE_ESTADUAL_BASE = (
    "http://pesquisa.doe.seplag.ce.gov.br/doepesquisa/sead.do"
    "?page=ultimasDetalhe&cmd=10&action=Cadernos&data={data}"
)
ORGAO_ALVO = "NÚCLEO DE TECNOLOGIA E QUALIDADE INDUSTRIAL DO CEARÁ"
EMAILS_DESTINO = ["pedro.brito@nutec.ce.gov.br", "pedropbrito@gmail.com"]

# Variáveis de ambiente configuradas no GitHub Secrets
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE")
SENHA_APP = os.getenv("SENHA_APP")

FUSO_BRT = timezone(timedelta(hours=-3))


def obter_data_hoje_brt():
    return datetime.now(FUSO_BRT)


def obter_dia_util_anterior(data_ref=None):
    """Retorna o dia útil imediatamente anterior à data de referência (seg a sex)."""
    if data_ref is None:
        data_ref = obter_data_hoje_brt()
    dia_semana = data_ref.weekday()  # 0=Seg, 1=Ter, 2=Qua, 3=Qui, 4=Sex, 5=Sáb, 6=Dom
    if dia_semana == 0:  # Segunda-feira -> Sexta-feira (-3 dias)
        delta = 3
    elif dia_semana == 6:  # Domingo -> Sexta-feira (-2 dias)
        delta = 2
    else:  # Terça a Sábado -> Dia anterior (-1 dia)
        delta = 1
    return data_ref - timedelta(days=delta)


def analisar_pdf_e_extrair_paginas(caminho_pdf, nome_exibicao, caminho_pdf_saida):
    """
    Varre todas as páginas de `caminho_pdf` procurando `ORGAO_ALVO`.
    Retorna `(recortes_texto, caminho_pdf_extraido_ou_None)`.
    Se encontrar menções, salva apenas as páginas citadas em `caminho_pdf_saida`.
    """
    recortes = []
    paginas_citadas = []

    with pdfplumber.open(caminho_pdf) as pdf:
        for idx, pagina in enumerate(pdf.pages):
            numero_pagina = idx + 1
            texto = pagina.extract_text()
            if texto and ORGAO_ALVO.upper() in texto.upper():
                identificador = f"{nome_exibicao} - PÁGINA {numero_pagina}"
                recortes.append(
                    f"--- INÍCIO ({identificador}) ---\n\n{texto}\n\n--- FIM ({identificador}) ---"
                )
                paginas_citadas.append(idx)

    if paginas_citadas:
        leitor = PdfReader(caminho_pdf)
        escritor = PdfWriter()
        for idx in paginas_citadas:
            escritor.add_page(leitor.pages[idx])
        with open(caminho_pdf_saida, "wb") as f_out:
            escritor.write(f_out)
        return recortes, caminho_pdf_saida

    return [], None


def consultar_doe_tce():
    """Realiza a consulta no Diário Oficial do TCE-CE e extrai páginas se houver citação."""
    arquivo_temp = "temp_diario_tce.pdf"
    arquivo_recorte = "Paginas_Citadas_DOE_TCE.pdf"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            accept_downloads=True,
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
                    href = elemento.evaluate(
                        "el => el.closest('a') ? el.closest('a').href : null"
                    )
            except Exception:
                pass

            baixou = False
            if href and ".pdf" in href.lower():
                if not href.startswith("http"):
                    href = urljoin("https://www.tce.ce.gov.br", href)
                resposta = requests.get(href, timeout=60)
                if resposta.ok and resposta.content.startswith(b"%PDF"):
                    with open(arquivo_temp, "wb") as f:
                        f.write(resposta.content)
                    baixou = True

            if not baixou:
                with page.expect_download(timeout=60000) as download_info:
                    elemento.click()
                download = download_info.value
                download.save_as(arquivo_temp)

            browser.close()
        except Exception as e:
            print(f"Erro na automação web (TCE-CE): {e}")
            browser.close()
            return {"status": "erro", "recortes": [], "anexos": []}

    try:
        recortes, pdf_extraido = analisar_pdf_e_extrair_paginas(
            arquivo_temp, "DOE TCE-CE", arquivo_recorte
        )
        anexos = [pdf_extraido] if pdf_extraido else []
        status = "encontrado" if recortes else "nao_encontrado"
        return {"status": status, "recortes": recortes, "anexos": anexos}
    finally:
        if os.path.exists(arquivo_temp):
            os.remove(arquivo_temp)


def _listar_urls_cadernos_doe_estadual(data_param, headers):
    """
    Obtém a lista de tuplas (nome_arquivo, url_pdf) para a data `data_param` (YYYYMMDD).
    1º consulta diretamente o servidor de arquivos `http://imagens.seplag.ce.gov.br/PDF/{data_param}/do{data_param}pXX.pdf`
       (evitando o bloqueio de IPs estrangeiros existente em `pesquisa.doe.seplag.ce.gov.br`).
    2º usa `pesquisa.doe.seplag.ce.gov.br` apenas como fallback caso o servidor de imagens não responda.
    """
    cadernos = []

    # Tentativa 1: Busca sequencial direta no servidor de PDFs (imagens.seplag.ce.gov.br)
    algum_servidor_respondeu = False
    for indice in range(1, 15):
        nome_arq = f"do{data_param}p{indice:02d}.pdf"
        url_direta = f"http://imagens.seplag.ce.gov.br/PDF/{data_param}/{nome_arq}"
        try:
            resp_head = requests.head(
                url_direta, headers=headers, timeout=15, allow_redirects=True
            )
            algum_servidor_respondeu = True
            if resp_head.status_code == 200:
                cadernos.append((nome_arq, url_direta))
            elif resp_head.status_code == 404:
                # Quando p01 retorna 404, não há publicação na data; quando p02+ retorna 404, acabaram os cadernos
                break
            else:
                break
        except Exception as e:
            print(f"Aviso: falha ao verificar {url_direta}: {e}")
            break

    if algum_servidor_respondeu:
        return "ok", cadernos

    # Tentativa 2 (Fallback): Página de listagem (pesquisa.doe.seplag.ce.gov.br)
    url_listagem = URL_DOE_ESTADUAL_BASE.format(data=data_param)
    try:
        resp = requests.get(url_listagem, headers=headers, timeout=15)
        if resp.ok:
            html = resp.text
            matches = re.findall(
                r"href=['\"]([^'\"]*?(do\d+p\d+\.pdf))['\"]",
                html,
                flags=re.IGNORECASE,
            )
            for url_pdf, nome_arq in matches:
                url_completa = urljoin(url_listagem, url_pdf).replace(
                    "imagens.seplag.ce.gov.br/pdf/", "imagens.seplag.ce.gov.br/PDF/"
                )
                if (nome_arq, url_completa) not in cadernos:
                    cadernos.append((nome_arq, url_completa))
            return "ok", cadernos
    except Exception as e:
        print(f"Aviso: falha ao acessar fallback em pesquisa.doe.seplag.ce.gov.br ({e}).")

    return "erro_conexao", []


def consultar_doe_estadual(data_consulta):
    """
    Acessa o DOE Executivo Estadual (SEPLAG/CE) para `data_consulta`,
    verifica se há cadernos, baixa e varre cada PDF extraindo apenas as páginas com citação.
    """
    data_param = data_consulta.strftime("%Y%m%d")
    data_formatada = data_consulta.strftime("%d/%m/%Y")
    url = URL_DOE_ESTADUAL_BASE.format(data=data_param)

    print(f"Consultando DOE Estadual para {data_formatada} (data={data_param}): {url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    status_listagem, cadernos = _listar_urls_cadernos_doe_estadual(data_param, headers)

    if status_listagem == "erro_conexao":
        print(
            "Erro: Timeout ao conectar nos servidores da SEPLAG-CE "
            "(possível bloqueio de IPs estrangeiros pela SEPLAG-CE)."
        )
        return {
            "status": "erro",
            "data_formatada": data_formatada,
            "data_param": data_param,
            "recortes": [],
            "anexos": [],
        }

    if not cadernos:
        print(f"Nenhum caderno encontrado no DOE Estadual para {data_formatada}.")
        return {
            "status": "sem_publicacao",
            "data_formatada": data_formatada,
            "data_param": data_param,
            "recortes": [],
            "anexos": [],
        }

    total_arquivos = len(cadernos)
    print(f"{total_arquivos} arquivo(s) encontrado(s) no DOE Estadual: {[c[0] for c in cadernos]}")

    recortes_totais = []
    anexos_totais = []
    arquivos_baixados = 0

    for i, (nome_arquivo, url_pdf) in enumerate(cadernos, start=1):
        print(f"Baixando e processando arquivo {i}/{total_arquivos}: {nome_arquivo} ({url_pdf})...")
        caminho_pdf_temp = f"temp_{nome_arquivo}"

        try:
            resp_pdf = requests.get(url_pdf, headers=headers, timeout=90)
            if not resp_pdf.ok or not resp_pdf.content.startswith(b"%PDF"):
                print(f"Não foi possível obter um PDF válido em {url_pdf} (status={resp_pdf.status_code}).")
                continue

            with open(caminho_pdf_temp, "wb") as f:
                f.write(resp_pdf.content)
            arquivos_baixados += 1

            nome_base = os.path.splitext(nome_arquivo)[0]
            caminho_recorte = f"Paginas_Citadas_{nome_base}.pdf"
            recortes, pdf_extraido = analisar_pdf_e_extrair_paginas(
                caminho_pdf_temp,
                f"DOE Estadual ({nome_arquivo})",
                caminho_recorte,
            )
            if recortes:
                recortes_totais.extend(recortes)
            if pdf_extraido:
                anexos_totais.append(pdf_extraido)
        except Exception as e:
            print(f"Erro ao processar {nome_arquivo}: {e}")
        finally:
            if os.path.exists(caminho_pdf_temp):
                os.remove(caminho_pdf_temp)

    if arquivos_baixados == 0:
        return {
            "status": "erro",
            "data_formatada": data_formatada,
            "data_param": data_param,
            "recortes": [],
            "anexos": [],
        }

    status = "encontrado" if recortes_totais else "nao_encontrado"
    return {
        "status": status,
        "data_formatada": data_formatada,
        "data_param": data_param,
        "recortes": recortes_totais,
        "anexos": anexos_totais,
    }


def enviar_email_consolidado(resultado_tce, resultado_estadual):
    msg = MIMEMultipart()
    msg["Subject"] = "Reporte de Publicações DOE Estadual e TCE"
    msg["From"] = EMAIL_REMETENTE
    msg["To"] = ", ".join(EMAILS_DESTINO)

    data_hoje_str = obter_data_hoje_brt().strftime("%d/%m/%Y")
    data_est_str = resultado_estadual["data_formatada"]
    data_est_param = resultado_estadual["data_param"]

    partes_corpo = []

    # 1. Seção DOE Executivo Estadual
    partes_corpo.append("============================================================")
    partes_corpo.append(
        f"1. DIÁRIO OFICIAL DO ESTADO (DOE EXECUTIVO ESTADUAL) - Publicação: {data_est_str} (data={data_est_param})"
    )
    partes_corpo.append("============================================================\n")

    status_est = resultado_estadual["status"]
    if status_est == "sem_publicacao":
        partes_corpo.append(
            f"Diário Oficial do Estado sem publicação na data {data_est_str}.\n"
        )
    elif status_est == "encontrado":
        partes_corpo.append(
            f"O órgão '{ORGAO_ALVO}' FOI ENCONTRADO no Diário Oficial do Estado "
            f"para a publicação da data {data_est_str} (data={data_est_param}).\n\n"
            "Segue(m) em anexo apenas a(s) página(s) da publicação onde houve citação. "
            "Abaixo está a transcrição integral da(s) página(s):\n\n"
            + "\n\n".join(resultado_estadual["recortes"])
            + "\n"
        )
    elif status_est == "nao_encontrado":
        partes_corpo.append(
            f"Não houve citação ao órgão '{ORGAO_ALVO}' no Diário Oficial do Estado "
            f"para publicação da data {data_est_str} (data={data_est_param}).\n"
        )
    else:
        partes_corpo.append(
            f"Erro ao consultar o Diário Oficial do Estado para a data {data_est_str} (data={data_est_param}).\n"
        )

    partes_corpo.append("\n")

    # 2. Seção DOE TCE-CE
    partes_corpo.append("============================================================")
    partes_corpo.append(
        f"2. DIÁRIO OFICIAL DO TCE-CE (DOE TRIBUNAL) - Consulta em: {data_hoje_str}"
    )
    partes_corpo.append("============================================================\n")

    status_tce = resultado_tce["status"]
    if status_tce == "encontrado":
        partes_corpo.append(
            f"O órgão '{ORGAO_ALVO}' FOI ENCONTRADO no Diário Oficial do TCE-CE de hoje ({data_hoje_str}).\n\n"
            "Segue(m) em anexo apenas a(s) página(s) onde houve citação. "
            "Abaixo está a transcrição integral da(s) página(s):\n\n"
            + "\n\n".join(resultado_tce["recortes"])
            + "\n"
        )
    elif status_tce == "nao_encontrado":
        partes_corpo.append(
            f"O órgão '{ORGAO_ALVO}' NÃO FOI CITADO no Diário Oficial do TCE-CE da data de hoje ({data_hoje_str}).\n"
        )
    else:
        partes_corpo.append(
            f"Não foi possível acessar o PDF do Diário Oficial do TCE-CE na data de hoje ({data_hoje_str}).\n"
        )

    corpo = "\n".join(partes_corpo)
    msg.attach(MIMEText(corpo, "plain", "utf-8"))

    # Anexa apenas os PDFs recortados (quando houver citação em qualquer um dos diários)
    todos_anexos = resultado_estadual.get("anexos", []) + resultado_tce.get("anexos", [])
    for caminho_anexo in todos_anexos:
        if caminho_anexo and os.path.exists(caminho_anexo):
            with open(caminho_anexo, "rb") as f:
                anexo = MIMEApplication(f.read(), _subtype="pdf")
                anexo.add_header(
                    "Content-Disposition",
                    "attachment",
                    filename=os.path.basename(caminho_anexo),
                )
                msg.attach(anexo)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_REMETENTE, SENHA_APP)
        smtp.send_message(msg)


if __name__ == "__main__":
    print("1/2 - Iniciando consulta ao Diário Oficial do TCE-CE...")
    resultado_tce = consultar_doe_tce()
    print(
        f"Consulta TCE-CE concluída: status={resultado_tce['status']}, "
        f"{len(resultado_tce['recortes'])} página(s) citada(s)."
    )

    print("2/2 - Iniciando consulta ao Diário Oficial do Estado (DOE Executivo Estadual)...")
    data_util_anterior = obter_dia_util_anterior()
    resultado_estadual = consultar_doe_estadual(data_util_anterior)
    print(
        f"Consulta DOE Estadual concluída: status={resultado_estadual['status']}, "
        f"{len(resultado_estadual['recortes'])} página(s) citada(s)."
    )

    try:
        print("Enviando e-mail consolidado...")
        enviar_email_consolidado(resultado_tce, resultado_estadual)
        print("Processo finalizado com sucesso.")
    finally:
        # Remove os arquivos temporários de recorte após o envio
        for caminho in resultado_tce.get("anexos", []) + resultado_estadual.get("anexos", []):
            if caminho and os.path.exists(caminho):
                os.remove(caminho)
