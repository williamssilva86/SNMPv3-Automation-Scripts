"""
Configuração de SNMPv3 em massa - Aruba Instant On 1930 (via API HTTP da interface web)

Mesmo CSV, mesmas perguntas e mesmos relatórios do snmpv3_aruba1930_http.py,
mas sem navegador: fala direto com a API que a própria interface web usa
(login RSA + XML no endpoint /wcd), como o configurar_aruba_1930.py.

Arquivo de dados (NOME_ARQUIVO_DADOS), CSV com cabeçalho. Exemplo:

    ip,hostname,location
    10.64.46.37,SW-2108-01,Site Centro

- ip:       IP do switch
- hostname: System Name (opcional; vazio = mantém o nome atual do switch)
- location: System Location (opcional)

Fluxo por switch:
 1. Login (credenciais criptografadas com a chave RSA do switch).
 2. System Info (hostname, location, contact) + habilita SNMP.
 3. Cria os grupos SNMPv3 que ainda não existem.
 4. Cria os usuários SNMPv3 (SHA + DES) que ainda não existem.
 5. Salva running-config -> startup-config.
"""
import base64
import csv
import os
import re
import sys
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from getpass import getpass
from urllib.parse import quote
from xml.sax.saxutils import escape

try:
    import requests
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
except ImportError:
    print("ERRO: Execute no terminal: pip install requests cryptography")
    sys.exit(1)

requests.packages.urllib3.disable_warnings()

# --- Parâmetros ---
NOME_ARQUIVO_DADOS = "Switches-SBF.txt"
TXT_OK_BASE = "SNMP_OK.txt"
TXT_FAIL_BASE = "SNMP_ERRO.txt"
MAX_WORKERS = 10
TIMEOUT = 20
SALVAR_CONFIG = True  # False = aplica sem salvar na startup-config

# --- Parâmetros SNMPv3 ---
SNMP_CONTACT = "noc@empresa.com"
# Valores do formulário Add SNMP User (js/out/pages2.js):
#   authenticationMethod: 0 = None, 1 = MD5, 2 = SHA  | senha em texto, 1-32 caracteres
#   privacyMethod:        1 = None, 2 = DES           | Privacy Key em HEX, 1-32 dígitos
# As senhas vão exatamente como digitadas (igual à interface): digite as mesmas do Zabbix.
SNMP_AUTH_METH = "2"  # SHA
SNMP_PRIV_METH = "2"  # DES
MAX_AUTH_PASS = 32
MAX_PRIV_PASS = 32

# Usuários (as senhas são pedidas na execução)
# escrita=True cria o grupo com writeView, para permitir SET
USUARIOS_SNMP = [
    {"nome": "adminSNMPv3",       "group": "DefaultRead",  "escrita": False},
    {"nome": "adminSNMPv3_write", "group": "DefaultWrite", "escrita": True},
]

# --- CONFIGURAÇÃO DE PRIVACIDADE ---
OCULTAR_DADOS = True
SENHAS_SENSIVEIS = []

# Locks
txt_ok_lock = threading.Lock()
txt_fail_lock = threading.Lock()
print_lock = threading.Lock()

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


def pedir_senha(rotulo, maximo, so_hex=False):
    while True:
        s1 = input_senha(f"{rotulo} (8 a {maximo} caracteres{', só HEX 0-9 A-F' if so_hex else ''}): ")
        if so_hex and not re.fullmatch(r"[0-9A-Fa-f]+", s1):
            print("O switch aceita só HEX neste campo (0-9 e A-F).")
            continue
        if len(s1) < 8:
            print("Senha muito curta, tente novamente.")
            continue
        if len(s1) > maximo:
            print(f"Senha muito longa: o switch aceita no máximo {maximo} caracteres.")
            continue
        if not s1.isascii() or not s1.isprintable():
            print("Use só caracteres ASCII (sem acentos).")
            continue
        s2 = input_senha("Confirme a senha: ")
        if s1 != s2:
            print("As senhas não conferem, tente novamente.")
            continue
        return s1


def log(ip, msg):
    with print_lock:
        print(f"[{ip}] {msg}")


def parse_pkcs1_pem(pem):
    """Extrai (n, e) de uma chave pública RSA PKCS#1 em PEM."""
    b64 = "".join(l for l in pem.strip().splitlines() if "---" not in l)
    der = base64.b64decode(b64)

    def tlv(data, pos):
        pos += 1
        tam = data[pos]
        pos += 1
        if tam & 0x80:
            nb = tam & 0x7F
            tam = int.from_bytes(data[pos:pos + nb], "big")
            pos += nb
        return data[pos:pos + tam], pos + tam

    seq, _ = tlv(der, 0)
    nb, nxt = tlv(seq, 0)
    if nb[0] == 0:
        nb = nb[1:]
    eb, _ = tlv(seq, nxt)
    return int.from_bytes(nb, "big"), int.from_bytes(eb, "big")


def xml_status(r):
    try:
        root = ET.fromstring(r.content)
        return root.findtext(".//statusCode", "-1"), root.findtext(".//statusString", "")
    except ET.ParseError:
        return "-1", r.text[:100]


# --- Classe do Switch ---

class Aruba1930:
    """Configura Aruba 1930 pela API XML (/wcd) da interface web."""

    def __init__(self, ip):
        self.ip = ip.strip()
        self.s = requests.Session()
        self.s.verify = False
        self.base = f"http://{self.ip}"
        self.sid = None
        self.csrf = None
        self.hostname_atual = ""

    def _headers(self, xml=False):
        h = {"X-Requested-With": "XMLHttpRequest",
             "Referer": f"{self.base}/{self.sid}/hpe/home.htm",
             "Origin": self.base}
        if xml:
            h["Content-Type"] = "text/xml;charset=UTF-8"
        if self.csrf:
            h["csrftoken"] = self.csrf
        return h

    def login(self, usuario, senha):
        # 1. Session ID: "/" redireciona para /cs<token>/hpe/config/login.htm
        ultimo_erro = None
        for proto in ("http", "https"):
            try:
                self.base = f"{proto}://{self.ip}"
                r = self.s.get(self.base, timeout=TIMEOUT, allow_redirects=True)
                m = re.search(r"/(cs[0-9a-f]+)/", r.url) or re.search(r"/(cs[0-9a-f]+)/", r.text)
                if m:
                    self.sid = m.group(1)
                    break
            except requests.RequestException as e:
                ultimo_erro = e
        if not self.sid:
            raise Exception(f"Switch inacessível ou session ID não encontrado ({ultimo_erro or 'sem /cs.../ na URL'})")
        # A página de login traz o System Name atual: <INPUT ... name="sysName" ... value="SW-2108-01">
        m = re.search(r"""name=["']sysName["'][^>]*value=["']([^"']*)""", r.text)
        self.hostname_atual = m.group(1) if m else ""

        # 2. Chave RSA + token de login
        r = self.s.get(f"{self.base}/device/wcd", params="{EncryptionSetting}",
                       headers={"X-Requested-With": "XMLHttpRequest"}, timeout=TIMEOUT)
        enc = ET.fromstring(r.content).find(".//EncryptionSetting")
        if enc is None:
            raise Exception("Switch não retornou a chave RSA (EncryptionSetting)")
        pem = (enc.findtext("rsaPublicKey") or "").strip()
        token = (enc.findtext("loginToken") or "").strip()

        # 3. Login com credenciais criptografadas
        n, e = parse_pkcs1_pem(pem)
        pub = RSAPublicNumbers(e, n).public_key()
        plain = f"user={quote(usuario)}&password={quote(senha)}&ssd=true&token={token}&".encode()
        cred = pub.encrypt(plain, padding.PKCS1v15()).hex()
        r = self.s.get(f"{self.base}/{self.sid}/hpe/config/system.xml",
                       params={"action": "login", "cred": cred}, timeout=TIMEOUT)

        for src in (r.headers.get("csrftoken", ""), self.s.cookies.get("csrftoken", "")):
            if src and src.lower() not in ("null", "(null)"):
                self.csrf = src
                break

        sc, ss = xml_status(r)
        if sc != "0":
            raise Exception(f"Login recusado (statusCode={sc}: {ss}) - verifique usuário/senha")

    def _get(self, consulta):
        r = self.s.get(f"{self.base}/{self.sid}/hpe/wcd", params=consulta,
                       headers=self._headers(), timeout=TIMEOUT)
        return ET.fromstring(r.content)

    def _post(self, consulta, xml_body, descricao):
        url = f"{self.base}/{self.sid}/hpe/wcd" + (f"?{consulta}" if consulta else "")
        r = self.s.post(url, data=xml_body.encode("utf-8"), headers=self._headers(xml=True), timeout=TIMEOUT)
        sc, ss = xml_status(r)
        if sc != "0":
            raise Exception(f"{descricao}: switch recusou (statusCode={sc}: {ss})")

    def configurar_system_info(self, hostname, location, contact):
        xml = ("<?xml version='1.0' encoding='utf-8'?><DeviceConfiguration>"
               "<SystemGlobalSetting action=\"set\">"
               f"<systemName>{escape(hostname)}</systemName>"
               f"<systemLocation>{escape(location)}</systemLocation>"
               f"<systemContact>{escape(contact)}</systemContact>"
               "</SystemGlobalSetting>"
               "<SNMPGlobalSetting action=\"set\"><SNMPEnbl>1</SNMPEnbl></SNMPGlobalSetting>"
               "</DeviceConfiguration>")
        self._post("", xml, "System Info + SNMP Enable")

    def grupos_existentes(self):
        try:
            root = self._get("{SNMPGroupList}")
            return {(g.text or "").strip().lower() for g in root.iter("SNMPGroupName")}
        except Exception:
            return set()

    def criar_grupo(self, nome, escrita):
        write_view = "Default" if escrita else ""
        xml = ("<?xml version='1.0' encoding='utf-8'?><DeviceConfiguration>"
               "<SNMPGroupList action=\"set\"><SNMPGroup>"
               f"<SNMPGroupName>{escape(nome)}</SNMPGroupName>"
               "<securityModel>3</securityModel><securityType>3</securityType>"
               f"<readView>Default</readView><writeView>{write_view}</writeView><notifyView>Default</notifyView>"
               "</SNMPGroup></SNMPGroupList></DeviceConfiguration>")
        self._post("{SNMPGlobalSetting}{ViewOIDList}{SNMPGroupList}{SNMPRemoteEngineIDList}{NotificationFilterList}",
                   xml, f"Grupo '{nome}'")

    def usuarios_existentes(self):
        try:
            root = self._get("{SNMPUserISList}")
            return {(u.text or "").strip().lower() for u in root.iter("userName")}
        except Exception:
            return set()

    def engine_id(self):
        try:
            return self._get("{SNMPGlobalSetting}").findtext(".//localEngineID", "")
        except Exception:
            return ""

    def criar_usuario(self, u, engine_id):
        xml = ("<?xml version='1.0' encoding='utf-8'?><DeviceConfiguration>"
               "<SNMPUserISList action=\"add\"><Entry>"
               f"<userName>{escape(u['nome'])}</userName>"
               f"<engineIDType>1</engineIDType><engineID>{engine_id}</engineID>"
               f"<groupName>{escape(u['group'])}</groupName>"
               f"<authenticationMethod>{SNMP_AUTH_METH}</authenticationMethod>"
               f"<authenticationPassword>{escape(u['auth_pass'])}</authenticationPassword>"
               f"<privacyMethod>{SNMP_PRIV_METH}</privacyMethod>"
               f"<privacyPassword>{escape(u['priv_pass'])}</privacyPassword>"
               "</Entry></SNMPUserISList></DeviceConfiguration>")
        self._post("{SNMPGlobalSetting}{ViewOIDList}{SNMPGroupList}{SNMPRemoteEngineIDList}{NotificationFilterList}",
                   xml, f"Usuário '{u['nome']}'")

    def salvar_configuracao(self):
        """Copia running-config para startup-config (mesmo comando do botão Save da interface)."""
        url = f"{self.base}/{self.sid}/hpe/wcd?{{file=/hpe/home.htm}}{{FileCopy}}"
        xml = ("<?xml version='1.0' encoding='utf-8'?><DeviceConfiguration>"
               "<FileCopy action=\"set\"><srcLocation>1</srcLocation><srcFileType>2</srcFileType>"
               "<destLocation>1</destLocation><destFileType>3</destFileType></FileCopy>"
               "</DeviceConfiguration>")
        r = self.s.post(url, data=xml.encode("utf-8"), headers=self._headers(xml=True), timeout=TIMEOUT)
        if r.status_code == 200 and not r.content:
            return
        sc, ss = xml_status(r)
        if r.status_code != 200 or sc != "0":
            raise Exception(f"Salvar configuração: HTTP {r.status_code}, statusCode={sc}: {ss}")


# --- Funções Principais ---

def ler_dados_switches(nome_arquivo):
    switches = []
    try:
        with open(nome_arquivo, "r", encoding="utf-8-sig") as arquivo:
            for linha in csv.DictReader(arquivo):
                linha = {(k or "").strip().lower(): (v or "").strip() for k, v in linha.items()}
                if linha.get("ip"):
                    switches.append(linha)
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
    ip = switch_info["ip"]
    location = switch_info.get("location", "")
    contact = SNMP_CONTACT.lower()
    log(ip, "Iniciando...")

    try:
        sw = Aruba1930(ip)
        sw.login(username, password)
        # Hostname do CSV; se vazio, mantém o atual do switch
        hostname = switch_info.get("hostname") or sw.hostname_atual
        if not hostname:
            raise Exception("Hostname vazio no CSV e não foi possível ler o atual do switch")
        if sw.hostname_atual and hostname != sw.hostname_atual:
            log(ip, f"Login OK - hostname: {sw.hostname_atual} -> {hostname}")
        else:
            log(ip, f"Login OK - hostname: {hostname}")

        sw.configurar_system_info(hostname, location.lower(), contact)
        log(ip, "System Info + SNMP habilitado")

        # Grupos que faltam
        grupos = sw.grupos_existentes()
        for u in USUARIOS_SNMP:
            if u["group"].lower() not in grupos:
                sw.criar_grupo(u["group"], u["escrita"])
                grupos.add(u["group"].lower())
                log(ip, f"Grupo '{u['group']}' criado")

        # Usuários
        existentes = sw.usuarios_existentes()
        eid = sw.engine_id()
        resultado = []
        for u in USUARIOS_SNMP:
            if u["nome"].lower() in existentes:
                resultado.append(f"JÁ EXISTIA: {u['nome']} (não alterado)")
                continue
            try:
                sw.criar_usuario(u, eid)
                resultado.append(f"CRIADO: {u['nome']}")
            except Exception as e:
                resultado.append(f"ERRO: {u['nome']} - {e}")

        if SALVAR_CONFIG:
            sw.salvar_configuracao()
            resultado.append("CONFIGURAÇÃO SALVA (startup-config)")
        else:
            resultado.append("AVISO: configuração NÃO salva (SALVAR_CONFIG = False)")

        with txt_ok_lock:
            with open(TXT_OK, "a", encoding="utf-8") as f:
                f.write(f"\n========== SWITCH {ip} ==========\n")
                f.write("SENHAS SNMP:\n")
                for u in USUARIOS_SNMP:
                    f.write(f"- usuario: {u['nome']} | group: {u['group'].lower()} | "
                            f"auth (SHA): {mascarar(u['auth_pass'])} | "
                            f"priv (DES): {mascarar(u['priv_pass'])}\n")
                f.write("\nRESULTADO:\n")
                for res in resultado:
                    f.write(f"- {mascarar(res)}\n")
                f.write("====================================\n")

        if any(r.startswith("ERRO") for r in resultado):
            log(ip, "CONCLUÍDO COM ERROS: " + mascarar(" | ".join(resultado)))
        else:
            log(ip, "OK - " + " | ".join(resultado))

    except Exception as e:
        with txt_fail_lock:
            with open(TXT_FAIL, "a", encoding="utf-8") as f:
                f.write(f"[{ip}] ERRO: {mascarar(str(e))}\n")
        log(ip, f"ERRO: {mascarar(str(e))}")


# --- Execução Principal ---

if __name__ == "__main__":
    print("=== Credenciais do Switch (HTTP) ===")
    username = input("Usuário: ")
    password = input_senha("Senha: ")
    SENHAS_SENSIVEIS.append(password)

    for u in USUARIOS_SNMP:
        print(f"\n=== Credenciais SNMPv3 do usuário {u['nome']} (group {u['group'].lower()}) ===")
        u["auth_pass"] = pedir_senha("Senha de autenticação (SHA) - igual ao Zabbix", MAX_AUTH_PASS)
        u["priv_pass"] = pedir_senha("Senha de criptografia (DES) - igual ao Zabbix", MAX_PRIV_PASS, so_hex=True)
        SENHAS_SENSIVEIS.extend([u["auth_pass"], u["priv_pass"]])

    dados_switches = ler_dados_switches(NOME_ARQUIVO_DADOS)

    if dados_switches and inicializar_txt():
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(processar_switch, sw, username, password)
                for sw in dados_switches
            ]
            for future in futures:
                future.result()

        print("\nProcesso concluído.")
