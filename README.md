# ELM EWM MCP

Servidor MCP local para consultar o IBM Engineering Lifecycle Management, com foco inicial no IBM Engineering Workflow Management em `/ccm`.

O projeto expõe ferramentas MCP para listar projetos, consultar work items e obter detalhes de work items usando endpoints OSLC do EWM.

> Status: projeto open source em desenvolvimento inicial.

## Objetivo

Este repositório foi criado para permitir que clientes compatíveis com MCP consultem dados do IBM ELM/EWM localmente, sem depender de serviços intermediários.

O foco atual é:

- listar áreas de projeto visíveis no EWM
- listar work items de um projeto
- obter detalhes de um work item por ID
- expor resources e prompts básicos para facilitar o uso em clientes MCP
- servir como base para automações locais com clientes como Cursor, Codex e outros clientes compatíveis com MCP

## Arquitetura

O fluxo atual é simples e local:

1. o cliente MCP inicia o servidor via `stdio`
2. o servidor lê a configuração em `elm_credentials.json`
3. o acesso ao IBM ELM é feito usando `curl.exe -k`
4. a autenticação usa `Authorization: Basic <token>`
5. as respostas XML/JSON do ELM/EWM são transformadas em tools, resources e prompts MCP

## Componentes principais

```text
.
├── elm_mcp_server.py
├── elm_credentials.template.json
├── .gitignore
├── README.md
└── LICENSE
````

### `elm_mcp_server.py`

Implementação principal do servidor MCP.

### `elm_credentials.template.json`

Template de configuração. Deve conter apenas placeholders.

### `elm_credentials.json`

Arquivo local com credenciais reais. Este arquivo não deve ser versionado.

### `.gitignore`

Ignora arquivos locais sensíveis e artefatos de ambiente, como:

* `.venv/`
* `elm_credentials.json`
* `__pycache__/`

## Por que o projeto usa `curl.exe -k`

Durante os testes com o ambiente ELM alvo, o acesso via cliente HTTP tradicional em Python poderia falhar no handshake TLS antes da autenticação HTTP.

O método validado foi:

```bash
curl.exe -k -H "Authorization: Basic <base64>" "<HOST>/ccm/rootservices"
```

Esse método funcionou para:

* `<HOST>/ccm/rootservices`
* `<HOST>/jts/rootservices`
* `<HOST>/ccm/process/project-areas`
* endpoints OSLC de work items

Por isso, a implementação atual usa `curl.exe -k` internamente.

Essa decisão pode mudar no futuro, caso seja implementada uma camada HTTP mais portável com tratamento adequado de TLS/certificados.

## Requisitos

* Python 3.10 ou superior
* Windows com `curl.exe` disponível no PATH
* acesso de rede ao servidor IBM ELM/EWM
* credenciais válidas para o ambiente ELM
* cliente compatível com MCP

## Instalação

### 1. Clonar o repositório

```bash
git clone https://github.com/hevener10/elm_alm_ibm_mcp.git
cd elm_alm_ibm_mcp
```

### 2. Criar ambiente virtual

No Windows:

```powershell
python -m venv .venv
```

### 3. Instalar dependências

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install elmclient "mcp[cli]"
```

> Observação: o projeto ainda instala `elmclient`, mas o caminho operacional principal usa `curl.exe` diretamente.

## Configuração

### 1. Criar arquivo local de credenciais

```powershell
Copy-Item .\elm_credentials.template.json .\elm_credentials.json
```

### 2. Gerar token Basic em Base64

O token deve estar no formato:

```text
usuario:senha
```

No PowerShell:

```powershell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("usuario_exemplo:senha_exemplo"))
```

No Linux/macOS:

```bash
echo -n "usuario_exemplo:senha_exemplo" | base64
```

### 3. Preencher `elm_credentials.json`

Exemplo:

```json
{
  "host": "https://exemplo-elm",
  "username": "USUARIO_EXEMPLO",
  "password": "",
  "app_password_ccm": "BASE64_EXEMPLO_OPCIONAL",
  "app_password_jts": "BASE64_EXEMPLO_OPCIONAL",
  "token": "BASE64_EXEMPLO_USUARIO_SENHA",
  "jts_token": "BASE64_EXEMPLO_OPCIONAL",
  "jts_context": "jts",
  "ccm_context": "ccm",
  "verify_ssl": false
}
```

No modo atual, os campos mais importantes são:

* `host`
* `username`
* `token`
* `jts_context`
* `ccm_context`

O campo `verify_ssl` é mantido por compatibilidade de configuração, mas o transporte atual usa `curl.exe -k`.

## Segurança

Nunca publique credenciais reais.

Antes de fazer commit, confirme que:

* `elm_credentials.json` não foi adicionado ao Git
* `elm_credentials.template.json` contém apenas placeholders
* nenhum token Base64 aparece em screenshots, issues, logs ou commits
* nenhum dado interno sensível do ambiente IBM ELM foi incluído no repositório

Para verificar arquivos rastreados:

```bash
git status
```

Para procurar tokens ou valores sensíveis antes do commit:

```bash
git diff
```

## Integração com cliente MCP

Exemplo de configuração:

```json
{
  "mcpServers": {
    "elm-ewm": {
      "command": "python.exe",
      "args": [
        "C:/caminho/para/elm_alm_ibm_mcp/elm_mcp_server.py"
      ]
    }
  }
}
```

Ajuste o caminho conforme a localização real do repositório no seu computador.

## Tools expostas

### `connection_info`

Retorna um resumo da conexão configurada.

Exemplo de retorno:

```json
{
  "host": "https://exemplo-elm",
  "jts_context": "jts",
  "ccm_context": "ccm",
  "verify_ssl": true,
  "uses_basic_token": true,
  "cm_catalog_url": "https://exemplo-elm/ccm/oslc/workitems/catalog"
}
```

### `list_projects`

Lista áreas de projeto visíveis no EWM.

Campos retornados:

* `name`
* `summary`
* `uri`
* `archived`

### `list_workitems(project_name, pagesize=30)`

Lista work items de um projeto via OSLC Query.

Retorna:

* `project_name`
* `totalCount`
* `nextPage`
* `items[]`

Cada item contém:

* `id`
* `title`
* `uri`

### `get_workitem(project_name, workitem_id)`

Consulta um work item específico pelo identificador informado.

Retorna o payload OSLC completo do item.

## Resources expostos

### `elm://connection-info`

Resumo da conexão ativa.

### `elm://projects`

Lista de projetos disponíveis no EWM.

### `elm://project/{project_name}/workitems`

Lista inicial de work items do projeto informado.

## Prompt exposto

### `consultar_ewm`

Prompt base para orientar o cliente MCP a:

1. ler a conexão
2. listar projetos
3. selecionar um projeto
4. listar work items
5. consultar um work item específico por ID

## Como testar localmente

Validar sintaxe do arquivo principal:

```powershell
.\.venv\Scripts\python.exe -m py_compile .\elm_mcp_server.py
```

Testar acesso manual ao ELM:

```powershell
curl.exe -k -H "Authorization: Basic <TOKEN_BASE64>" "https://exemplo-elm/ccm/rootservices"
```

## Validações feitas

O projeto foi validado com chamadas para:

* `/ccm/rootservices`
* `/jts/rootservices`
* `/ccm/process/project-areas`
* `/ccm/oslc/workitems/catalog`
* `services.xml` de projeto
* consultas OSLC de work items em JSON

Também foi validado localmente:

* carregamento do MCP
* leitura de resources
* leitura de tools
* retorno de work items de exemplo do projeto configurado

## Limitações conhecidas

### Dependência de Windows

A versão atual depende explicitamente de `curl.exe`, então o comportamento foi pensado inicialmente para Windows.

### TLS/certificados

O transporte atual usa `curl.exe -k`, o que ignora validação estrita de certificado.

Isso é útil para ambientes internos com certificados corporativos ou autoassinados, mas deve ser melhorado em versões futuras.

### Codificação de caracteres

Algumas respostas podem aparecer com acentuação parcialmente degradada no Windows por causa da forma como a saída textual do `curl.exe` retorna os bytes.

### Escopo funcional

O foco atual é EWM em `/ccm`.

Ainda não há implementação completa para:

* RM em `/rm`
* QM em `/qm`
* navegação avançada por paginação OSLC
* busca textual avançada
* filtros por status
* filtros por tipo
* filtros por campos customizados

## Roadmap

Próximas melhorias planejadas:

* corrigir completamente problemas de codificação no Windows
* substituir o uso fixo de `curl.exe` por uma camada HTTP portável
* adicionar paginação navegável por `nextPage`
* adicionar busca de work items por texto
* adicionar filtros por status, tipo e campos customizados
* adicionar testes automatizados
* adicionar GitHub Actions para validação básica
* melhorar documentação para Cursor, Codex e outros clientes MCP
* adicionar suporte futuro a RM, QM e JTS
* criar exemplos seguros sem credenciais reais

## Contribuindo

Contribuições são bem-vindas.

Antes de abrir um pull request:

1. crie uma branch descritiva
2. evite incluir credenciais, URLs internas ou dados sensíveis
3. teste localmente a sintaxe do Python
4. descreva claramente o problema resolvido
5. inclua exemplos de uso quando fizer sentido

Exemplo:

```bash
git checkout -b fix/windows-encoding
```

## Aviso sobre IBM

Este projeto não é afiliado, endossado ou mantido pela IBM.

IBM, Engineering Lifecycle Management, Engineering Workflow Management e nomes relacionados pertencem aos seus respectivos proprietários.

## Licença

Este projeto está licenciado sob a **MIT License**.

Consulte o arquivo [`LICENSE`](./LICENSE) para mais detalhes.

## Autor

Mantido por `hevener10`.

````

E crie também um arquivo novo chamado `LICENSE` na raiz do projeto com este conteúdo:

```text
MIT License

Copyright (c) 2026 hevener10

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the “Software”), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````
[2]: https://docs.github.com/articles/licensing-a-repository?utm_source=chatgpt.com "Licensing a repository"
[3]: https://choosealicense.com/licenses/mit/?utm_source=chatgpt.com "MIT License | Choose a License"
