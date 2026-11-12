"""
Configuração de SNMPv3 (view + groups + 2 users + location + contact + ACL) em switches Cisco IOS.
Os nomes aplicados nos switches (ACL, view, groups, location, contact) vão em MINÚSCULO.
Os nomes dos usuários SNMP (adminSNMPv3, adminSNMPv3_write) mantêm a caixa original.
As senhas SNMP NÃO são alteradas (mantêm exatamente o que foi digitado).

A ACL do SNMP é SEMPRE recriada do zero (permits na ordem de NMS_IPS + deny any log no final),
para que o resultado seja idêntico em switches novos e em switches já configurados.

Arquivo de dados (NOME_ARQUIVO_DADOS), CSV com cabeçalho. Exemplo:

    ip,location
    10.150.117.251,SITE-CE117

- Coluna 1: IP do switch
- Colunas seguintes: variavel1, variavel2... (variavel1 = location)

As senhas SNMP de cada usuário são pedidas uma vez (digitação mostra *****) e usadas em todos os switches.
Usuários que já existem no switch NÃO são recriados (apenas registrados no log).
"""
import csv
import re
import sys
import threading
import os
from concurrent.futures import ThreadPoolExecutor
from netmiko import ConnectHandler
from getpass import getpass

# --- Parâmetros ---
NOME_ARQUIVO_DADOS = "Switches-SBF.txt"
TXT_OK_BASE = "SNMP_OK.txt"
TXT_FAIL_BASE = "SNMP_ERRO.txt"
MAX_WORKERS = 15

# --- Parâmetros SNMPv3 ---
SNMP_VIEW = "view_all"
AUTH_PROTOCOL = "sha"
PRIV_PROTOCOL = "des"          # troque para "aes 128" se o NMS suportar
SNMP_CONTACT = "ti_i_redescorp.centauro.com.br"
SNMP_ACL = "acl_snmp"
NMS_IPS = [                    # servidores de monitoramento autorizados a consultar SNMP
    "172.16.101.110",           # proxy do Zabbix (alien06)
    "172.16.154.136",           #maquina bot vadldcgnetq01
    "172.18.0.110",            # proxy do Zabbix (alien03)
]

# Groups: write=None => somente leitura
GRUPOS_SNMP = [
    {"nome": "defaultread",     "read": SNMP_VIEW, "write": None},
    {"nome": "defaultwritenew", "read": SNMP_VIEW, "write": SNMP_VIEW},
]

# Usuários (as senhas são pedidas na execução)
USUARIOS_SNMP = [
    {"nome": "adminSNMPv3",       "group": "defaultread"},
    {"nome": "adminSNMPv3_write", "group": "defaultwritenew"},
]

# --- CONFIGURAÇÃO DE PRIVACIDADE ---
# True para ocultar senhas nos arquivos de log, False para mostrar tudo
OCULTAR_DADOS = True
SENHAS_SENSIVEIS = []          # preenchida em tempo de execução

# --- Definição dos parâmetros de conexão ---
DEVICE_PARAMS = {
    "device_type": "cisco_ios",
    "username": "",
    "password": "",
    "secret": "",
    "conn_timeout": 20,
}

# Comandos de verificação (executados após a configuração)
COMMANDS_VERIFY = [
    "show snmp user",
    "show snmp group",
    f"show ip access-lists {SNMP_ACL.lower()}",
]

# Locks
txt_ok_lock = threading.Lock()
txt_fail_lock = threading.Lock()

# --- Funções de Utilitário ---

def input_senha(prompt=""):
    """Lê a senha mostrando * a cada caractere digitado (Windows e Linux/Mac)."""
    # Em terminais sem TTY (ex.: alguns IDEs) usa o getpass padrão
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
            if ch in ("\x00", "\xe0"):      # teclas especiais (setas, F1...)
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


def montar_comandos(location, usuarios_existentes):
    """
    Monta a lista de comandos de um switch (sem usar .format, para aceitar { } nas senhas).
    Tudo é convertido para minúsculo, EXCETO as senhas e os nomes dos usuários SNMP.
    """
    acl = SNMP_ACL.lower()
    view = SNMP_VIEW.lower()

    # ACL recriada do zero: garante permits na ordem de NMS_IPS e o deny sempre por último
    cmds = [
        f"no ip access-list standard {acl}",
        f"ip access-list standard {acl}",
    ]
    cmds += [f" permit host {ip}" for ip in NMS_IPS]
    cmds += [" deny any log", "exit"]

    cmds.append(f"snmp-server view {view} iso included")

    for g in GRUPOS_SNMP:
        linha = f"snmp-server group {g['nome'].lower()} v3 priv read {g['read'].lower()}"
        if g["write"]:
            linha += f" write {g['write'].lower()}"
        linha += f" access {acl}"
        cmds.append(linha)

    ignorados = []
    for u in USUARIOS_SNMP:
        nome = u["nome"]  # nomes de usuário mantêm a caixa original (ex.: adminSNMPv3)
        if nome in usuarios_existentes:
            ignorados.append(nome)
            continue
        cmds.append(
            f"snmp-server user {nome} {u['group'].lower()} v3 "
            f"auth {AUTH_PROTOCOL.lower()} {u['auth_pass']} "
            f"priv {PRIV_PROTOCOL.lower()} {u['priv_pass']}"
        )

    if location:
        cmds.append(f"snmp-server location {location.lower()}")
    cmds.append(f"snmp-server contact {SNMP_CONTACT.lower()}")
    return cmds, ignorados


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


def processar_switch(switch_info):
    ip = switch_info["ip"]
    print(f"[{ip}] Iniciando...")

    try:
        device_params_local = DEVICE_PARAMS.copy()
        device_params_local["host"] = ip

        with ConnectHandler(**device_params_local) as ssh:
            if device_params_local.get("secret"):
                ssh.enable()

            # Descobre quais usuários já existem (não ficam no running-config)
            saida_users = ssh.send_command("show snmp user")
            existentes = set(re.findall(r"User name:\s*(\S+)", saida_users))

            # Usuários com o mesmo nome em outra caixa (ex.: adminSNMPv3 x adminsnmpv3)
            nomes_exatos = {u["nome"] for u in USUARIOS_SNMP}
            nomes_lower = {n.lower() for n in nomes_exatos}
            variantes = sorted(e for e in existentes if e.lower() in nomes_lower and e not in nomes_exatos)

            comandos, ignorados = montar_comandos(switch_info.get("variavel1", ""), existentes)

            output = ssh.send_config_set(comandos)

            if "% Invalid" in output or "% Incomplete" in output or "% Bad" in output:
                raise Exception("IOS retornou erro de sintaxe:\n" + mascarar(output))

            ssh.save_config()

            output_verify = ""
            for cmd in COMMANDS_VERIFY:
                output_verify += f"\n# {cmd}\n" + ssh.send_command(cmd) + "\n"

            with txt_ok_lock:
                with open(TXT_OK, "a", encoding="utf-8") as f:
                    f.write(f"\n========== SWITCH {ip} ==========\n")
                    if ignorados:
                        f.write("USUÁRIOS JÁ EXISTENTES (não recriados): " + ", ".join(ignorados) + "\n")
                    if variantes:
                        f.write("ATENÇÃO - usuários existentes com a caixa diferente da configurada (são usuários diferentes): "
                                + ", ".join(variantes) + "\n")
                    f.write("SENHAS SNMP:\n")
                    for u in USUARIOS_SNMP:
                        status = "JÁ EXISTIA - senha NÃO aplicada" if u["nome"] in ignorados else "CRIADO"
                        f.write(f"- usuario: {u['nome']} | group: {u['group'].lower()} | "
                                f"auth ({AUTH_PROTOCOL}): {mascarar(u['auth_pass'])} | "
                                f"priv ({PRIV_PROTOCOL}): {mascarar(u['priv_pass'])} | {status}\n")
                    f.write("\n")
                    f.write("COMANDOS EXECUTADOS:\n")
                    for cmd in comandos:
                        f.write(f"- {mascarar(cmd)}\n")

                    f.write("\nOUTPUT:\n")
                    f.write(mascarar(output).strip())
                    f.write("\n\nVERIFICAÇÃO:\n")
                    f.write(output_verify.strip())
                    f.write("\n====================================\n")

        aviso = f" (já existiam: {', '.join(ignorados)})" if ignorados else ""
        aviso += f" [ATENÇÃO maiúsculas: {', '.join(variantes)}]" if variantes else ""
        print(f"[{ip}] OK{aviso}")

    except Exception as e:
        with txt_fail_lock:
            with open(TXT_FAIL, "a", encoding="utf-8") as f:
                f.write(f"[{ip}] ERRO: {mascarar(str(e))}\n")
        print(f"[{ip}] ERRO")


# --- Execução Principal ---

if __name__ == "__main__":
    print("=== Credenciais SSH do Switch ===")
    DEVICE_PARAMS["username"] = input("Usuário: ")
    DEVICE_PARAMS["password"] = input_senha("Senha: ")
    DEVICE_PARAMS["secret"] = input_senha("Enable (Enter se não usa): ")

    for u in USUARIOS_SNMP:
        print(f"\n=== Credenciais SNMPv3 do usuário {u['nome']} (group {u['group'].lower()}) ===")
        u["auth_pass"] = pedir_senha("Senha de autenticação (SHA)")
        u["priv_pass"] = pedir_senha("Senha de criptografia (DES)")
        SENHAS_SENSIVEIS.extend([u["auth_pass"], u["priv_pass"]])

    dados_switches = ler_dados_switches(NOME_ARQUIVO_DADOS)

    if dados_switches and inicializar_txt():
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            list(executor.map(processar_switch, dados_switches))

        print("\nProcesso concluído.")