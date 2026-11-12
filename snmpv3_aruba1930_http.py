"""
Configuração de SNMPv3 em massa - Aruba Instant On 1930 (via HTTP)
Mesma metodologia do script Cisco: CSV input, ThreadPoolExecutor,
password masking, múltiplos usuários, relatórios OK/ERRO.

Usa Playwright para automação de browser (não precisa SSH).

Arquivo de dados (NOME_ARQUIVO_DADOS), CSV com cabeçalho. Exemplo:

    ip,location
    10.64.46.37,Site Centro

- Coluna 1: IP do switch
- Coluna 2: location (opcional, usada para System Info)

As senhas SNMP de cada usuário são pedidas uma vez (digitação mostra *****)
e usadas em todos os switches.
"""
import csv
import re
import sys
import os
import threading
import time
import asyncio
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from getpass import getpass

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERRO: A biblioteca 'playwright' nao esta instalada.")
    print("Execute no terminal: pip install playwright")
    print("E depois: playwright install")
    sys.exit(1)

# --- Parâmetros ---
NOME_ARQUIVO_DADOS = "Switches-SBF.txt"
TXT_OK_BASE = "SNMP_OK.txt"
TXT_FAIL_BASE = "SNMP_ERRO.txt"
MAX_WORKERS = 5  # Reduzido para não sobrecarregar (browser é mais pesado)
TIMEOUT = 30

# --- Parâmetros SNMPv3 ---
SNMP_CONTACT = "noc@empresa.com"

# Usuários (as senhas são pedidas na execução)
USUARIOS_SNMP = [
    {"nome": "adminSNMPv3",       "group": "DefaultRead"},
    {"nome": "adminSNMPv3_write", "group": "DefaultWrite"},
]

# --- CONFIGURAÇÃO DE PRIVACIDADE ---
OCULTAR_DADOS = True
SENHAS_SENSIVEIS = []

# Locks
txt_ok_lock = threading.Lock()
txt_fail_lock = threading.Lock()

# --- Funções de Utilitário ---

def input_senha(prompt=""):
    """Lê a senha mostrando * a cada caractere digitado (Windows e Linux/Mac)."""
    if not sys.stdin.isatty():
        return getpass(prompt)

    sys.stdout.write(prompt)
    sys.stdout.flush()
    senha = ""

    if os.name == "nt":
        import msvcrt
        while True:
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                break
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch in ("\x00", "\xe0"):
                msvcrt.getwch()
                continue
            if ch == "\b":
                if senha:
                    senha = senha[:-1]
                    sys.stdout.write("\b \b")
            else:
                senha += ch
                sys.stdout.write("*")
            sys.stdout.flush()
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        antigo = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\r", "\n"):
                    break
                if ch in ("\x7f", "\b"):
                    if senha:
                        senha = senha[:-1]
                        sys.stdout.write("\b \b")
                else:
                    senha += ch
                    sys.stdout.write("*")
                sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, antigo)

    sys.stdout.write("\n")
    sys.stdout.flush()
    return senha


def gerar_nome_unico(nome_base):
    if not os.path.exists(nome_base):
        return nome_base
    nome, ext = os.path.splitext(nome_base)
    contador = 1
    while True:
        novo_nome = f"{nome}_{contador}{ext}"
        if not os.path.exists(novo_nome):
            return novo_nome
        contador += 1


def mascarar(texto):
    if not OCULTAR_DADOS:
        return texto
    for senha in SENHAS_SENSIVEIS:
        if senha and senha in texto:
            texto = texto.replace(senha, "********")
    return texto


def pedir_senha(rotulo):
    while True:
        s1 = input_senha(f"{rotulo} (mín. 8 caracteres): ")
        if len(s1) < 8:
            print("Senha muito curta, tente novamente.")
            continue
        s2 = input_senha("Confirme a senha: ")
        if s1 != s2:
            print("As senhas não conferem, tente novamente.")
            continue
        return s1


# --- Classe Configurator (Async) ---

class SwitchConfiguratorAruba1930:
    """Configura Aruba 1930 via HTTP + Playwright (web automation)."""

    def __init__(self, ip):
        self.ip = ip.strip()
        self.base_url = f"http://{self.ip}"
        self.browser = None
        self.page = None

    async def connect(self, username, password):
        """Abre browser, navega e faz login."""
        try:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(headless=True)
            self.page = await self.browser.new_page()

            # Navegar para o switch
            await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=TIMEOUT*1000)

            # Preencher login
            await self.page.fill('input[name="username"]', username)
            await self.page.fill('input[name="password"]', password)
            await self.page.click('button:has-text("Login")')

            # Aguardar dashboard
            await self.page.wait_for_url("**/home.htm", timeout=TIMEOUT*1000)

            return True
        except Exception as e:
            raise Exception(f"Erro ao conectar: {e}")

    async def configurar_system_info(self, hostname, location, contact):
        """Preenche System Name, Location, Contact no dashboard."""
        try:
            # Aguardar campos aparecerem
            await self.page.wait_for_selector('input[placeholder*="System"]', timeout=5000)

            # Preencher campos
            textboxes = await self.page.query_selector_all('input[type="text"]')

            if len(textboxes) >= 3:
                # Assumindo ordem: Name, Location, Contact
                await textboxes[0].fill(hostname if hostname else "")
                await textboxes[1].fill(location.lower() if location else "")
                await textboxes[2].fill(contact.lower() if contact else "")

                # Clicar Apply
                apply_btn = await self.page.query_selector('button:has-text("Apply")')
                if apply_btn:
                    await apply_btn.click()
                    await self.page.wait_for_timeout(1000)

            return True
        except Exception as e:
            raise Exception(f"Erro ao configurar System Info: {e}")

    async def adicionar_usuario_snmpv3(self, usuario_config):
        """Navega para SNMP e adiciona usuário."""
        try:
            # Navegar para SNMP
            await self.page.click('a:has-text("SNMP")')
            await self.page.wait_for_selector('text=SNMP User Configuration', timeout=10000)

            # Clicar botão "Add" em SNMP User Configuration
            buttons = await self.page.query_selector_all('button:has-text("Add")')
            if len(buttons) > 0:
                # Encontrar o botão "Add" correto (último, para SNMP User)
                await buttons[-1].click()  # Último Add

            # Aguardar diálogo "Add SNMP User"
            await self.page.wait_for_selector('text=Add SNMP User', timeout=5000)

            # Preencher formulário
            # Username
            await self.page.fill('input[placeholder*="Username"]', usuario_config["nome"])

            # Group Name
            await self.page.fill('input[placeholder*="Group"]', usuario_config["group"])

            # Engine ID Type: Local (radio button)
            await self.page.click('label:has-text("Local")')

            # Authentication: SHA
            await self.page.click('label:has-text("SHA")')

            # Password (auth)
            password_inputs = await self.page.query_selector_all('input[type="text"]')
            if len(password_inputs) > 0:
                # Encontrar campo "Password"
                for inp in password_inputs:
                    placeholder = await inp.get_attribute("placeholder")
                    if placeholder and "Password" in placeholder:
                        await inp.fill(usuario_config["auth_pass"])
                        break

            # Privacy: DES
            await self.page.click('label:has-text("DES")')

            # Privacy Key
            for inp in password_inputs:
                placeholder = await inp.get_attribute("placeholder")
                if placeholder and "Privacy" in placeholder:
                    await inp.fill(usuario_config["priv_pass"])
                    break

            # Clicar Apply no diálogo
            apply_buttons = await self.page.query_selector_all('button:has-text("Apply")')
            if len(apply_buttons) > 0:
                await apply_buttons[-1].click()  # Último Apply (do diálogo)

            await self.page.wait_for_timeout(1000)

            return True
        except Exception as e:
            raise Exception(f"Erro ao adicionar usuário: {e}")

    async def close(self):
        """Fecha browser."""
        try:
            if self.page:
                await self.page.close()
            if self.browser:
                await self.browser.close()
        except Exception:
            pass


# --- Funções Principais ---

def ler_dados_switches(nome_arquivo):
    switches = []
    try:
        with open(nome_arquivo, "r", encoding="utf-8") as arquivo:
            leitor_csv = csv.reader(arquivo)
            next(leitor_csv)  # cabeçalho
            for linha in leitor_csv:
                if len(linha) >= 1 and linha[0].strip():
                    sw = {"ip": linha[0].strip()}
                    for idx, valor in enumerate(linha[1:], start=1):
                        sw[f"variavel{idx}"] = valor.strip()
                    switches.append(sw)
        return switches
    except Exception as e:
        print(f"Erro ao ler arquivo: {e}")
        return []


def inicializar_txt():
    global TXT_OK, TXT_FAIL
    TXT_OK = gerar_nome_unico(TXT_OK_BASE)
    TXT_FAIL = gerar_nome_unico(TXT_FAIL_BASE)
    open(TXT_OK, "w", encoding="utf-8").close()
    open(TXT_FAIL, "w", encoding="utf-8").close()
    print(f"Arquivo OK: {TXT_OK}")
    print(f"Arquivo ERRO: {TXT_FAIL}")
    return True


def processar_switch(switch_info, username, password):
    """Processa um switch de forma síncrona (wraps async)."""
    ip = switch_info["ip"]
    location = switch_info.get("variavel1", "")
    print(f"[{ip}] Iniciando...")

    try:
        # Executar async code em event loop
        asyncio.run(processar_switch_async(ip, location, username, password))
        print(f"[{ip}] OK")

    except Exception as e:
        with txt_fail_lock:
            with open(TXT_FAIL, "a", encoding="utf-8") as f:
                f.write(f"[{ip}] ERRO: {mascarar(str(e))}\n")
        print(f"[{ip}] ERRO: {str(e)}")


async def processar_switch_async(ip, location, username, password):
    """Versão async do processamento."""
    hostname = f"SW-{ip.replace('.', '-')}"
    contact = SNMP_CONTACT.lower()

    sw = SwitchConfiguratorAruba1930(ip)

    try:
        # Connect e login
        await sw.connect(username, password)

        # System Info
        await sw.configurar_system_info(hostname, location, contact)

        # Adicionar usuários
        usuarios_criados = []
        for u in USUARIOS_SNMP:
            try:
                config = {
                    "nome": u["nome"],
                    "group": u["group"],
                    "auth_pass": u["auth_pass"],
                    "priv_pass": u["priv_pass"]
                }
                await sw.adicionar_usuario_snmpv3(config)
                usuarios_criados.append(f"CRIADO: {u['nome']}")
            except Exception as e:
                usuarios_criados.append(f"ERRO: {u['nome']} - {str(e)}")

        # Escrever sucesso
        with txt_ok_lock:
            with open(TXT_OK, "a", encoding="utf-8") as f:
                f.write(f"\n========== SWITCH {ip} ==========\n")
                f.write("SENHAS SNMP:\n")
                for u in USUARIOS_SNMP:
                    f.write(f"- usuario: {u['nome']} | group: {u['group'].lower()} | "
                            f"auth (SHA): {mascarar(u['auth_pass'])} | "
                            f"priv (DES): {mascarar(u['priv_pass'])}\n")
                f.write("\nRESULTADO:\n")
                for res in usuarios_criados:
                    f.write(f"- {res}\n")
                f.write("====================================\n")

    finally:
        await sw.close()


# --- Execução Principal ---

if __name__ == "__main__":
    print("=== Credenciais do Switch (HTTP) ===")
    username = input("Usuário: ")
    password = input_senha("Senha: ")

    for u in USUARIOS_SNMP:
        print(f"\n=== Credenciais SNMPv3 do usuário {u['nome']} (group {u['group'].lower()}) ===")
        u["auth_pass"] = pedir_senha("Senha de autenticação (SHA)")
        u["priv_pass"] = pedir_senha("Senha de criptografia (DES)")
        SENHAS_SENSIVEIS.extend([u["auth_pass"], u["priv_pass"]])

    dados_switches = ler_dados_switches(NOME_ARQUIVO_DADOS)

    if dados_switches and inicializar_txt():
        # Com Playwright, usar ThreadPoolExecutor com cuidado (browsers são síncronos)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(processar_switch, sw, username, password)
                for sw in dados_switches
            ]
            for future in futures:
                future.result()

        print("\nProcesso concluído.")
