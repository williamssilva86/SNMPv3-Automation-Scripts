# Aruba Instant On 1930 - Referência de Configuração SNMP via HTTP

## Modelo Detectado
- **Modelo:** Aruba Instant On 1930 48p Gigabit CL4 PoE 4p SFP+ 370W
- **Versão:** 3.1.0 (verificar com `show version`)
- **Serial:** TW39LB406J (exemplo)

## Acesso
- **Protocolo:** HTTP (não HTTPS, não SSH)
- **URL Base:** `http://10.64.46.37`
- **Login:** Página `/csabdb24af/hpe/config/login.htm`
- **Dashboard:** `/csabdb24af/hpe/home.htm`
- **SNMP Config:** Menu → Switching → SNMP

---

## Estrutura SNMP Pré-Existente

### Groups (Access Control Groups)
| Nome | SNMP Version | Security Level | Read | Write | Notify |
|------|--------------|----------------|------|-------|--------|
| DefaultRead | v3 | No Auth No Priv | Default | Default | - |
| DefaultSuper | v3 | No Auth No Priv | DefaultSuper | DefaultSuper | DefaultSuper |
| DefaultWrite | v3 | No Auth No Priv | Default | Default | - |

**⚠️ Importante:** Os grupos já existem com security level "No Auth No Priv". 
Ao criar usuários, é possível que o switch force "No Auth No Priv" mesmo se você tentar "Auth Priv".

### Views
| Nome | OID Tree | Tipo |
|------|----------|------|
| Default | 1.3.6.1 (incluindo) | Included |
| DefaultSuper | .1 (tudo) | Included |

Também há OIDs excluídos pré-configurados (1.3.6.1.6.3.13, 1.3.6.1.6.3.16, etc) - ver página web para lista completa.

---

## Configuração de SNMP via Formulário Web

### System Information (Dashboard)
```
GET /csabdb24af/hpe/home.htm
POST /csabdb24af/hpe/home.htm  (ou similar)

Campos:
- System Name: hostname (0-63 caracteres)
- System Location: localização (0-160 caracteres)
- System Contact: contato (0-160 caracteres)
```

### Adicionar Usuário SNMPv3
```
GET /csabdb24af/hpe/config/snmp.htm  → carrega página com formulário

POST para adicionar usuário (buscar action/endpoint):
- Username: nome do usuário (1-20 caracteres)
- Group Name: DefaultRead, DefaultSuper ou DefaultWrite
- Engine ID Type: Local (use isto) ou Remote
- Authentication Method: MD5, SHA, ou None
- Password: senha de autenticação (1-32 caracteres)
- Privacy: DES ou None
- Privacy Key: chave de privacidade (1-32 HEX caracteres ou 1-32 ASCII)
```

---

## Endpoints Testados

| Endpoint | Método | Status | Nota |
|----------|--------|--------|------|
| `/csabdb24af/hpe/home.htm` | GET | 200 | Dashboard |
| `/csabdb24af/hpe/api/v1/system` | GET | 200 | Retorna "OK" |
| `/rest/v1/system` | GET | 404 | Não disponível |
| `/rest/v1/system/snmp` | GET | 404 | Não disponível |
| `/rest/v1/system/snmp/usm` | GET | 404 | Não disponível |

**⚠️ API REST não funciona como documentada.** Use **formulário web** em vez disso.

---

## Estratégia de Configuração (Recomendada)

### Opção 1: Selenium/Playwright (Automação de Browser)
- Abre browser
- Clica em menus
- Preenche formulários
- Submete via botão "Apply"
- **Vantagem:** Segue exatamente o que o admin faria
- **Desvantagem:** Lento, dependente de browser

### Opção 2: Form Submission via Requests (Se endpoints conhecidos)
- POST direto aos endpoints de form
- Precisa descobrir `action`, `method`, nomes de campos
- **Vantagem:** Mais rápido
- **Desvantagem:** Endpoints podem mudar entre versões

### Opção 3: Pesquisar API Privada
- O browser provavelmente faz requisições XHR/Fetch
- Pode haver API JSON em `/csabdb24af/hpe/api/v2/` ou similar
- **Ação:** Abrir DevTools (F12) → Network → Capturar POST ao adicionar usuário

---

## Limitações Conhecidas

| Limitação | Impacto | Solução |
|-----------|--------|--------|
| Sem ACL específico de SNMP | Não pode restringir por IP como Cisco | Configure firewall/ACL global se necessário |
| Grupos pré-configurados com "No Auth No Priv" | Mesmo com Auth Priv no usuário, pode não funcionar | Testar com usuário criado |
| Sem API REST v1 padrão | Não pode usar requests simples | Usar Selenium ou descobrir POST endpoints |
| Sem SSH CLI | Não pode usar Netmiko | Usar web browser automation |

---

## Campos System Info (de referência)

```
System Name:        "SW-{ip.replace('.', '-')}" ex: SW-10-64-46-37
System Location:    valor de CSV coluna 2
System Contact:     noc@empresa.com
```

---

## Dados de Teste

```
Username: adminSNMPv3
Group: DefaultRead
Engine ID Type: Local
Auth: SHA
Auth Password: (pergunta ao user)
Privacy: DES
Privacy Key: (pergunta ao user)
```

---

## Como Usar Este Arquivo

1. **Descobrir endpoints POST:** Abra DevTools no switch, clique "Add SNMP User", veja Network tab
2. **Capturar payload:** Copy request como cURL, adapte para Python
3. **Atualizar script:** Insira endpoints POST reais
4. **Testar:** Execute em 1 switch primeiro, valide com `snmpwalk`

---

## Referência de Diálogos Web Encontrados

- "Add Community" (dialog)
- "Add Trap Receiver" (v1/v2 e v3)
- "Add View"
- "Add Filter"
- "Add Remote Engine ID"
- "Add SNMP User" ← **Usar isto**
- "Add Access Control Group"

---

## Próximos Passos

1. [ ] Abrir DevTools no switch
2. [ ] Adicionar um usuário manualmente
3. [ ] Capturar POST request
4. [ ] Extrair action URL, method, field names
5. [ ] Atualizar script com endpoints reais
6. [ ] Testar com ThreadPoolExecutor

