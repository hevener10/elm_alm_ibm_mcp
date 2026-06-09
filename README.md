# ELM EWM MCP

Servidor MCP local para consultar o IBM Engineering Lifecycle Management, com foco no IBM Engineering Workflow Management em `/ccm`, usando autenticação Basic via token Base64.

O projeto foi construído para funcionar com um ambiente ELM compatível, onde o acesso por cliente HTTP tradicional via bibliotecas Python pode exigir ajuste de TLS/handshake. A implementação atual usa `curl.exe -k` e `Authorization: Basic <base64>`.

## Objetivo

Este repositório expõe, via MCP, capacidades de consulta ao EWM para:

- listar projetos visíveis no EWM
- listar work items por projeto
- obter os detalhes de um work item por ID
- buscar work items por usuario e papel
- listar work items assinados pelo usuario em `Minhas Assinaturas`
- ler eventos recentes do feed de assinaturas
- expor resources e prompt básicos para facilitar o uso em clientes MCP

O projeto foi pensado para uso local, dentro do workspace, sem depender de serviços intermediários.

## Arquitetura

O fluxo atual é intencionalmente simples:

1. o cliente MCP sobe em `stdio` via Python
2. o servidor lê a configuração em `elm_credentials.json`
3. o acesso ao servidor ELM é feito com `curl.exe -k`
4. a autenticação usa `Authorization: Basic <token>`
5. as respostas XML/JSON do ALM são transformadas em:
   - `tools`
   - `resources`
   - `prompt`

### Componentes principais

- `elm_mcp_server.py`
  Implementação principal do servidor MCP.

- `elm_credentials.template.json`
  Template de configuração para servir como base de preenchimento.

- `elm_credentials.json`
  Arquivo local com segredos reais. Está ignorado pelo Git.

- `.venv/`
  Ambiente virtual local do projeto.

## Por que este projeto não usa `elmclient` no transporte principal

Durante a integração com o ambiente ELM alvo, o acesso via `elmclient`/`requests` pode falhar no handshake TLS antes da autenticação HTTP. Já o método abaixo foi validado com sucesso:

```powershell
curl.exe -k -H "Authorization: Basic <base64>" "<HOST>/ccm/rootservices"
```

Como isso funcionou de forma consistente para:

- `<HOST>/ccm/rootservices`
- `<HOST>/jts/rootservices`
- `<HOST>/ccm/process/project-areas`
- endpoints OSLC de work items

o servidor MCP passou a usar esse mesmo mecanismo internamente.

Em outras palavras: a decisão arquitetural principal deste projeto é usar o método que foi comprovado no ambiente real.

## Autenticação

### Método suportado

O projeto usa autenticação Basic com token Base64 no formato:

```text
usuario:senha
```

Exemplo:

```text
usuario_exemplo:senha_exemplo
```

Depois disso, o valor é convertido para Base64.

### Gerando o token

No PowerShell:

```powershell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("usuario_exemplo:senha_exemplo"))
```

No Linux:

```bash
echo -n "usuario_exemplo:senha_exemplo" | base64
```

### Arquivo de credenciais

Copie o template:

```powershell
Copy-Item .\elm_credentials.template.json .\elm_credentials.json
```

Preencha com seus dados:

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

Se este repositório for compartilhado com outras pessoas, revise o conteúdo de `elm_credentials.template.json` antes de publicar para garantir que ele contenha apenas placeholders e nenhum valor sensível.

### Campos realmente usados pelo cliente atual

No modo atual do projeto, os campos efetivamente relevantes são:

- `host`
- `username`
- `token`
- `jts_context`
- `ccm_context`

O campo `password` pode existir como fallback documental, mas o modo recomendado é usar `token`.

O campo `verify_ssl` hoje é mantido por compatibilidade de configuração, mas o acesso HTTP usa `curl.exe -k`, portanto ignora problemas de certificado do servidor.

## Estrutura do servidor

O arquivo principal é `elm_mcp_server.py`.

### Responsabilidades internas

- `_load_config()`
  Lê o arquivo de credenciais.

- `_curl_get()`
  Faz chamadas autenticadas com `curl.exe -k`.

- `_curl_get_json()`
  Faz chamadas autenticadas com cabeçalhos OSLC e resposta JSON.

- `_get_rootservices_xml()`
  Lê o `rootservices` do `ccm`.

- `_get_project_areas_xml()`
  Lê os projetos do EWM em `/ccm/process/project-areas`.

- `_get_project_service_url()`
  Resolve o `services.xml` de um projeto a partir do catálogo OSLC.

- `_get_project_query_base()`
  Resolve o endpoint `simpleQuery` para work items de um projeto.

- `_user_ccm_oslc_uri_from_identifier()`
  Monta a URI OSLC do usuario no CCM (`/ccm/oslc/users/{userId}`), usada para consultas de assinaturas.

- `_parse_rdf_workitem_summary()`
  Lê o RDF de um work item e confirma se o usuario aparece em `rtc_cm:subscribers`.

- `_parse_subscription_feed_events()`
  Lê Atom XML do feed de assinaturas e extrai eventos recentes.

## Tools expostas

### `connection_info`

Retorna um resumo da conexão e dos endpoints principais.

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

Lista as áreas de projeto visíveis no EWM.

Campos retornados:

- `name`
- `summary`
- `uri`
- `archived`

### `list_workitems(project_name, pagesize=30)`

Lista work items de um projeto via OSLC Query.

Retorna:

- `project_name`
- `totalCount`
- `nextPage`
- `items[]`

Cada item contém:

- `id`
- `title`
- `uri`

### `get_workitem(project_name, workitem_id)`

Consulta um work item específico pelo identificador no contexto do projeto informado.

Retorna o payload OSLC completo do item.

### `search_workitems_by_user(project_name, user, start_date="", end_date="", roles="modifiedBy,creator,contributor,subscribers,resolvedBy", pagesize=100, max_items=500)`

Pesquisa work items por usuario e papeis no projeto informado.

Para o papel `subscribers`, a consulta agora usa a estrategia dedicada de assinaturas por URI OSLC do CCM quando nao ha filtro de data.

### `list_subscribed_workitems(user="", project_name="", include_archived_projects=false, pagesize=100, max_items=500, verify_rdf=false, fallback_workitem_ids="", use_feed_fallback=true, feed_max_results=100)`

Lista work items assinados pelo usuario, equivalente operacional ao conceito de `Minhas Assinaturas`.

Comportamento:

- se `user` estiver vazio, usa o `username` configurado em `elm_credentials.json`
- se `project_name` estiver vazio, percorre os projetos visiveis nao arquivados
- consulta por projeto com `rtc_cm:subscribers="https://host/ccm/oslc/users/{userId}"`
- também tenta URIs alternativas do usuario para compatibilidade
- com `verify_rdf=true`, lê o RDF do work item e confirma `rtc_cm:subscribers`
- com `use_feed_fallback=true`, lê IDs do feed de assinaturas e confirma cada item via RDF quando a consulta OSLC nao encontra itens
- com `fallback_workitem_ids`, valida IDs específicos via RDF mesmo quando a consulta OSLC nao encontra o item

Retorna `items[]` com identificador, titulo, status, datas, URI, projeto, URI de assinante casada e origem da consulta.

### `list_subscription_feed(user="", start_date="", end_date="", max_results=50, enrich_workitems=false, project_name="")`

Lê eventos recentes do feed de assinaturas do usuario.

A tool tenta acessar o endpoint interno do EWM:

```text
/ccm/service/com.ibm.team.repository.common.internal.IFeedService?itemType=WorkItem&user={userId}&maxResults={max_results}
```

Retorna `events[]` com titulo, data, autor, link, resumo e `workitem_id` quando detectado. Com `enrich_workitems=true`, cada evento tenta anexar um resumo RDF do work item.

## Resources expostos

### `elm://connection-info`

Resumo da conexão ativa.

### `elm://projects`

Lista de projetos disponíveis no EWM.

### `elm://project/{project_name}/workitems`

Lista inicial de work items do projeto informado.

Atualmente retorna os 10 primeiros itens visíveis pela consulta OSLC configurada.

## Prompt exposto

### `consultar_ewm`

Prompt base para orientar o cliente MCP a:

1. ler a conexão
2. ler os projetos
3. selecionar um projeto
4. listar os work items
5. aprofundar em um work item por ID
6. usar `list_subscribed_workitems` quando a pergunta envolver assinaturas, itens seguidos ou `Minhas Assinaturas`
7. usar `list_subscription_feed` quando a pergunta envolver feed, eventos recentes, comentarios ou alteracoes nas assinaturas

## Integração

Exemplo:

```json
{
  "mcpServers": {
    "elm-ewm": {
      "command": "python.exe",
      "args": [
        "elm-mcp/elm_mcp_server.py"
      ]
    }
  }
}
```

## Como executar

### 1. Criar ambiente virtual

```powershell
python -m venv .venv
```

### 2. Instalar dependências

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install elmclient "mcp[cli]"
```

Observação: o projeto ainda instala `elmclient`, mas o caminho operacional principal usa `curl.exe` diretamente.

### 3. Criar credenciais locais

```powershell
Copy-Item .\elm_credentials.template.json .\elm_credentials.json
```

### 4. Preencher o token Base64

Edite `elm_credentials.json` com seus dados reais.

### 5. Iniciar via cliente MCP

O uso normal é pelo cliente MCP em `stdio`.

Para testes locais de sintaxe:

```powershell
.\.venv\Scripts\python.exe -m py_compile .\elm_mcp_server.py
```

## Validações feitas

Este projeto foi validado com:

- `<HOST>/ccm/rootservices`
- `<HOST>/jts/rootservices`
- `<HOST>/ccm/process/project-areas`
- `oslc/workitems/catalog`
- `services.xml` de projeto
- consultas OSLC de work items em JSON
- leitura RDF de work items para validar `rtc_cm:subscribers`

Também foi validado localmente:

- carregamento do MCP
- leitura de resources
- leitura de tools
- retorno de work items de exemplo do projeto configurado

## Limitações conhecidas

### Codificação de caracteres

Algumas respostas podem aparecer com acentuação parcialmente degradada no Windows por causa da forma como o `curl.exe` e a saída textual retornam os bytes. A consulta continua funcional, mas a apresentação pode exigir refinamento adicional.

### Dependência de Windows

O cliente atual depende explicitamente de `curl.exe`, então o comportamento foi pensado para Windows.

### Escopo atual

O foco atual é EWM em `/ccm`.

Ainda não há implementação dedicada para:

- consultas em `rm`
- consultas em `qm`
- paginação orientada a cursor além do `nextPage` bruto
- busca textual avançada por status, tipo ou campos personalizados

### Feed de assinaturas

`list_subscription_feed` usa um endpoint interno do EWM (`IFeedService`). Se uma versão ou configuração do servidor não expuser esse serviço, a tool retorna o erro em JSON e a alternativa recomendada é usar `list_subscribed_workitems` com `verify_rdf=true` ou `fallback_workitem_ids`.

## Troubleshooting

### O MCP conecta mas parece “vazio”

Verifique:

- se `elm_credentials.json` existe
- se o `token` Base64 está correto
- se o cliente MCP está apontando para `elm_mcp_server.py`

### `Projeto não encontrado`

O nome do projeto precisa coincidir com o catálogo OSLC do EWM. Leia primeiro o resource `elm://projects` ou use a tool `list_projects`.

### `curl.exe` falha

Confirme:

- acesso de rede ao host configurado
- token Base64 válido
- disponibilidade do `curl.exe` no Windows

## Sugestões de evolução

Próximos passos naturais para este repositório:

- corrigir completamente a codificação dos acentos
- adicionar busca de work items por texto, status e tipo
- expor paginação navegável por `nextPage`
- adicionar suporte a `rm` e `jts` além do `ccm`
- documentar exemplos de prompts de uso em Cursor

## Segurança

- `elm_credentials.json` contém segredo e deve permanecer fora do Git
- `.venv/` não deve ser versionado
- nunca publique o token Base64 em screenshots, issues ou commits
- antes de distribuir o repositório, valide que `elm_credentials.template.json` não contém credenciais reais

O arquivo `.gitignore` já cobre:

- `.venv/`
- `elm_credentials.json`
- `__pycache__/`

## Licença e uso

Este repositório foi montado para uso operacional local no workspace do projeto e para integração via MCP. Se ele evoluir para um repositório compartilhado, vale adicionar explicitamente:

- licença
- changelog
- instruções de contribuição
- exemplos versionados sem segredo
