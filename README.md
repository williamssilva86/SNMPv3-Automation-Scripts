# SNMPv3-Automation-Scripts

Automação de configuração de **SNMPv3 em massa** para switches **Cisco IOS**, **Aruba Instant On 1930** e **HPE 1920S**, com suporte a múltiplos usuários, grupos, ACLs e relatórios detalhados.

---

## 📋 Conteúdo

- [Visão Geral](#visão-geral)
- [Scripts Disponíveis](#scripts-disponíveis)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Como Usar](#como-usar)
- [Estrutura do Arquivo CSV](#estrutura-do-arquivo-csv)
- [Saídas Geradas](#saídas-geradas)
- [Diferenças Entre Scripts](#diferenças-entre-scripts)
- [Troubleshooting](#troubleshooting)

---

## 🎯 Visão Geral

Este repositório contém **scripts Python** para automatizar a configuração de **SNMPv3** em switches de rede, seguindo a mesma metodologia em todos eles:

✅ **CSV como entrada** → lista de switches e localizações  
✅ **Senhas pedidas uma vez** → aplicadas em todos os switches  
✅ **Paralelo com ThreadPoolExecutor** → processamento rápido  
✅ **Relatórios detalhados** → logs OK/ERRO com timestamps únicos  
✅ **Mascaramento de senhas** → segurança nos logs  

---

## 📁 Scripts Disponíveis

### 1. **snmpv3_switches.py** (Cisco IOS)
**Protocolo:** SSH via Netmiko  
**Modelos suportados:** Cisco Catalyst, C3650, etc  
**Configurações:**
- ACL SNMP (restringir IPs do NMS)
- View SNMP (filtros de OID)
- 2 Grupos (DefaultRead, DefaultWriteNew)
- 2 Usuários (adminSNMPv3, adminSNMPv3_write)
- SHA + DES (autenticação + privacidade)
- System Info (hostname, location, contact)

**Uso:**
```bash
python3 snmpv3_switches.py
```

---

### 2. **snmpv3_aruba1930_http.py** (Aruba Instant On 1930)
**Protocolo:** HTTP via `requests`, sem navegador. Usa a mesma API XML (`/wcd`) que a interface web do switch usa por baixo.  
**Login:** credenciais criptografadas com a chave RSA pública do switch (igual à tela de login)  
**Modelos suportados:** Aruba Instant On 1930 48p/24p  
**Configurações:**
- System Info: hostname (do CSV, ou mantém o atual se a coluna estiver vazia), location e contact
- Habilita o SNMP no switch
- 2 Usuários (adminSNMPv3 → DefaultRead, adminSNMPv3_write → DefaultWrite)
- Grupos pré-existentes (DefaultRead, DefaultWrite), criados só se não existirem
- SHA + DES
- Usuários que já existem não são recriados nem alterados
- Salva a configuração (running-config → startup-config) ao final de cada switch
- Sem ACL específico (use firewall se necessário)

**Uso:**
```bash
python3 snmpv3_aruba1930_http.py
```

**Parâmetros no início do script:**

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| `NOME_ARQUIVO_DADOS` | `Switches-SBF.txt` | Arquivo CSV de entrada |
| `MAX_WORKERS` | `10` | Switches processados em paralelo |
| `SALVAR_CONFIG` | `True` | `False` aplica sem salvar na startup-config (útil para teste) |
| `SNMP_CONTACT` | `noc@empresa.com` | System Contact |
| `USUARIOS_SNMP` | adminSNMPv3 / adminSNMPv3_write | Usuários e grupos |

---

### 3. **configurar_switches.py** (HPE 1920S / Aruba 1930)
**Protocolo:** HTTP via requests  
**Modelos suportados:** HPE 1920S, Aruba Instant On 1930  
**Status:** Referência / Validação (REST API pode não funcionar em todas as versões)

---

## 📚 Documentação

- **ARUBA_1930_REFERENCE.md** → Guia técnico de endpoints, grupos, views e limitações do Aruba 1930

---

## 💻 Requisitos

### Para Cisco (SSH):
```bash
pip install netmiko
```

### Para Aruba 1930 (HTTP):
```bash
pip install requests cryptography
```

### Geral:
- Python 3.7+
- Acesso SSH (Cisco) ou HTTP (Aruba)
- Credenciais de admin no switch
- Conectividade de rede

---

## 🚀 Instalação

### 1. Clone o repositório
```bash
git clone https://github.com/williamssilva86/SNMPv3-Automation-Scripts.git
cd SNMPv3-Automation-Scripts
```

### 2. Crie um ambiente virtual (recomendado)
```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate     # Windows
```

### 3. Instale dependências
```bash
pip install -r requirements.txt
```

---

## 📖 Como Usar

### Passo 1: Prepare o arquivo CSV

**Cisco (snmpv3_switches.py):**
```csv
ip,location
10.64.1.37,Loja Centro SP
10.64.2.37,Loja Zona Leste
10.64.3.37,Loja Zona Oeste
```

**Aruba 1930 (snmpv3_aruba1930_http.py):**
```csv
ip,hostname,location
10.64.46.37,SW-NS2108-01,Site Centro
10.64.47.37,,Site Zona Leste
```
(na segunda linha o hostname está vazio, então o switch mantém o nome atual)

### Passo 2: Execute o script

```bash
python3 snmpv3_switches.py  # Cisco
# ou
python3 snmpv3_aruba1930_http.py  # Aruba 1930
```

### Passo 3: Forneça credenciais

O script pedirá:
1. **Usuário SSH/HTTP** do switch
2. **Senha SSH/HTTP** do switch
3. **Senha de autenticação SNMPv3** (SHA) - pedida uma vez
4. **Senha de privacidade SNMPv3** (DES) - pedida uma vez

**Importante:** As senhas são mascaradas com `*` e ocultas nos logs.

**Aruba 1930:** as senhas são enviadas ao switch **exatamente como digitadas**, como faz a interface web. Digite as mesmas senhas configuradas no NMS (ex.: Zabbix).

| Senha | Regra do switch |
|-------|-----------------|
| Autenticação (SHA) | 8 a 32 caracteres ASCII |
| Privacidade (DES) | 8 a 32 caracteres, **somente HEX** (0-9, A-F) |

O script recusa na hora uma senha fora dessas regras e pede outra.

### Passo 4: Verifique os resultados

Logs gerados:
- `SNMP_OK_1.txt` → switches configurados com sucesso
- `SNMP_ERRO_1.txt` → erros de conexão/configuração

---

## 📊 Estrutura do Arquivo CSV

**Formato:** CSV com delimitador `,` e encoding UTF-8

**Colunas obrigatórias:**
1. `ip` → IP do switch (obrigatório)

**Colunas opcionais:**
2. `location` → Localização (0-160 caracteres, será lowercase)

**Aruba 1930:** as colunas são lidas pelo nome do cabeçalho (`ip`, `hostname`, `location`), e a coluna opcional `hostname` define o System Name. Se estiver vazia, o hostname atual do switch é mantido.

**Exemplo com acentos (será normalizado):**
```csv
ip,location
10.64.1.37,Praça da República - São Paulo
10.64.2.37,Avenida Paulista - São Paulo
```

**Após normalisação (Cisco):**
```
snmp-server location praca da republica - sao paulo
snmp-server location avenida paulista - sao paulo
```

---

## 📄 Saídas Geradas

### SNMP_OK_1.txt (exemplo)
```
========== SWITCH 10.64.1.37 ==========
USUÁRIOS JÁ EXISTENTES (não recriados): adminSNMPv3
SENHAS SNMP:
- usuario: adminSNMPv3 | group: defaultread | auth (SHA): ******** | priv (DES): ********
- usuario: adminSNMPv3_write | group: defaultwritenew | auth (SHA): ******** | priv (DES): ********

COMANDOS EXECUTADOS:
- no ip access-list standard acl_snmp
- ip access-list standard acl_snmp
-  permit host 172.16.101.110
-  permit host 172.16.154.136
-  permit host 172.18.0.110
-  deny any log
- exit
...

VERIFICAÇÃO:
# show snmp user
User name: adminSNMPv3
...
====================================
```

### SNMP_OK_1.txt (exemplo Aruba 1930)
```
========== SWITCH 10.64.46.37 ==========
SENHAS SNMP:
- usuario: adminSNMPv3 | group: defaultread | auth (SHA): ******** | priv (DES): ********
- usuario: adminSNMPv3_write | group: defaultwrite | auth (SHA): ******** | priv (DES): ********

RESULTADO:
- CRIADO: adminSNMPv3
- JÁ EXISTIA: adminSNMPv3_write (não alterado)
- CONFIGURAÇÃO SALVA (startup-config)
====================================
```

### SNMP_ERRO_1.txt (exemplo)
```
[10.64.3.37] ERRO: % Invalid command encountered - 'snmp-server location prááça'
[10.64.5.37] ERRO: Nao foi possivel conectar. Verifique IP e conectividade.
[10.64.46.37] ERRO: Login recusado (statusCode=...) - verifique usuário/senha
```

---

## 🔄 Diferenças Entre Scripts

| Aspecto | Cisco | Aruba 1930 |
|---------|-------|-----------|
| **Protocolo** | SSH (Netmiko) | HTTP (requests) |
| **Conexão** | Terminal CLI | API XML da interface web (`/wcd`) |
| **Grupos SNMP** | Customizáveis | Pré-existentes (DefaultRead, DefaultWrite, DefaultSuper) |
| **Views SNMP** | Customizáveis | Pré-existentes (Default, DefaultSuper) |
| **ACL SNMP** | ✅ Sim (IP standard) | ❌ Não (use firewall) |
| **Auth Protocol** | SHA / MD5 | SHA / MD5 |
| **Priv Protocol** | DES / AES 128 | DES |
| **Usuarios** | 2 (customizáveis) | 2 (nomes fixos) |
| **Traps SNMP** | ✅ Opcional | ❌ Não configurável |
| **Salvar config** | — | ✅ Automático (running → startup) |
| **Velocidade** | Rápido | Rápido |
| **Workers** | 15 paralelos | 10 paralelos |

---

## 🐛 Troubleshooting

### Cisco

**Erro: `Pattern not detected`**
- **Causa:** Caracteres acentuados na location
- **Solução:** Script normaliza automaticamente (NFKD + ASCII)

**Erro: `Authentication failure` no snmpwalk**
- **Causa:** Usuário já existia com senha diferente
- **Solução:** Execute `no snmp-server user adminSNMPv3 defaultread v3` no switch e rerun

**Erro: `% Invalid command`**
- **Causa:** Versão IOS não suporta o comando
- **Solução:** Verifique `show version`, atualize IOS se necessário

### Aruba 1930

**Erro: `Switch inacessível ou session ID não encontrado`**
- **Causa:** Switch fora da rede, IP errado ou interface web desabilitada
- **Solução:** Verifique IP, conectividade e se a página `http://<ip>` abre no navegador

**Erro: `Login recusado (statusCode=...)`**
- **Causa:** Usuário ou senha do switch incorretos
- **Solução:** Confira as credenciais entrando manualmente na interface web

**Erro: `<etapa>: switch recusou (statusCode=...: mensagem)`**
- **Causa:** O switch rejeitou a configuração. A mensagem vem do próprio switch.
- **Solução:** Leia a mensagem. Causas comuns: senha fora das regras ou nome de usuário com mais de 20 caracteres.

**Mensagem: `O switch aceita só HEX neste campo`**
- **Causa:** O campo Privacy Key (DES) do Aruba 1930 só aceita dígitos hexadecimais
- **Solução:** Use uma senha DES só com 0-9 e A-F (8 a 32 caracteres)

**Resultado: `JÁ EXISTIA: adminSNMPv3 (não alterado)`**
- **Causa:** O usuário já estava configurado. O script não altera usuários existentes.
- **Solução:** Para trocar a senha, remova o usuário pela interface web (Switching → SNMP) e rode novamente

**`Authentication failure` / `Decryption error` no NMS (Zabbix)**
- **Causa:** Senhas no NMS diferentes das digitadas no script
- **Solução:** Remova o usuário no switch e rode o script de novo com as senhas exatas do NMS

---

## 📝 Exemplo Completo (Cisco)

```bash
# 1. Prepare Switches-SBF.txt
cat Switches-SBF.txt
# ip,location
# 10.64.1.37,Loja Centro
# 10.64.2.37,Loja Zona Leste

# 2. Execute
python3 snmpv3_switches.py

# 3. Forneça:
# Usuário: admin
# Senha: ****
# Senha Auth (SHA): ****
# Senha Priv (DES): ****

# 4. Verifique
cat SNMP_OK_1.txt
cat SNMP_ERRO_1.txt

# 5. Teste
snmpwalk -v 3 -l authPriv -u adminSNMPv3 -a SHA -A senha_auth -x DES -X senha_priv 10.64.1.37
```

---

## 🤝 Contribuindo

Melhorias bem-vindas:
- Suporte a outros modelos (Juniper, Arista, etc)
- Validação de senhas mais forte
- Suporte a IPv6

---

## 📄 Licença

MIT License - Use livremente, credite se possível.

---

## 👤 Autor

Desenvolvido por: **William Silva** (williamssilva86)  
Última atualização: Novembro 2026

---

## 📞 Suporte

Para dúvidas ou problemas:
1. Verifique a seção **Troubleshooting**
2. Revise **ARUBA_1930_REFERENCE.md** para Aruba
3. Abra uma issue no repositório

---

**Happy automating! 🚀**
