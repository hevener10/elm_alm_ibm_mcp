import base64
import json
import logging
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from mcp.server.fastmcp import FastMCP


logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)


mcp = FastMCP(
    "elm-ewm",
    instructions=(
        "Servidor MCP local para IBM Engineering Lifecycle Management. "
        "Fornece acesso a projetos e work items do Engineering Workflow Management."
    ),
)

DEFAULT_CREDENTIALS = Path(__file__).with_name("elm_credentials.json")
NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/terms/",
    "jp06": "http://jazz.net/xmlns/prod/jazz/process/0.6/",
    "jp": "http://jazz.net/xmlns/prod/jazz/process/1.0/",
    "oslc_cm": "http://open-services.net/xmlns/cm/1.0/",
}


def _decode_basic_token(token: str | None, expected_username: str) -> str | None:
    if not token:
        return None
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception as exc:
        raise RuntimeError("Nao foi possivel decodificar o token Base64 informado.") from exc

    if ":" not in decoded:
        raise RuntimeError("O token Base64 informado nao esta no formato usuario:segredo.")

    token_username, token_secret = decoded.split(":", 1)
    if token_username != expected_username:
        raise RuntimeError("O usuario do token Base64 nao corresponde ao username configurado.")

    return token_secret


def _build_basic_token(username: str, password: str) -> str:
    return base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")


def _load_config() -> dict:
    if not DEFAULT_CREDENTIALS.exists():
        raise RuntimeError(
            "Credenciais ELM nao encontradas. "
            f"Preencha {DEFAULT_CREDENTIALS} com host, username e token."
        )

    with DEFAULT_CREDENTIALS.open(encoding="utf-8") as f:
        data = json.load(f)

    cfg = {
        "host": data["host"].rstrip("/"),
        "username": data["username"],
        "password": data.get("password", ""),
        "jts_context": data.get("jts_context", "jts"),
        "ccm_context": data.get("ccm_context", "ccm"),
        "verify_ssl": data.get("verify_ssl", True),
    }
    token = data.get("token")
    if not token and cfg["password"]:
        token = _build_basic_token(cfg["username"], cfg["password"])
    if not token:
        raise RuntimeError("Informe o campo 'token' Base64 ou username/password no arquivo de credenciais.")
    cfg["token"] = token
    return cfg


def _curl_get(url: str) -> str:
    cfg = _load_config()
    command = [
        "curl.exe",
        "-k",
        "-sS",
        "-H",
        f"Authorization: Basic {cfg['token']}",
        url,
    ]
    result = subprocess.run(command, capture_output=True, text=False, check=False)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"Falha ao acessar {url}")
    return result.stdout.decode("utf-8", errors="replace")


def _curl_get_json(url: str) -> dict:
    cfg = _load_config()
    command = [
        "curl.exe",
        "-k",
        "-sS",
        "-H",
        f"Authorization: Basic {cfg['token']}",
        "-H",
        "OSLC-Core-Version: 2.0",
        "-H",
        "Accept: application/json",
        url,
    ]
    result = subprocess.run(command, capture_output=True, text=False, check=False)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"Falha ao acessar {url}")
    payload = result.stdout.decode("utf-8", errors="replace")
    return json.loads(payload)


def _get_rootservices_xml() -> ET.Element:
    cfg = _load_config()
    xml_text = _curl_get(f"{cfg['host']}/{cfg['ccm_context']}/rootservices")
    return ET.fromstring(xml_text)


def _get_project_areas_xml() -> ET.Element:
    cfg = _load_config()
    xml_text = _curl_get(f"{cfg['host']}/{cfg['ccm_context']}/process/project-areas")
    return ET.fromstring(xml_text)


def _get_cm_catalog_url() -> str | None:
    root = _get_rootservices_xml()
    node = root.find("oslc_cm:cmServiceProviders", NS)
    if node is None:
        return None
    return node.attrib.get(f"{{{NS['rdf']}}}resource")


def _get_project_service_url(project_name: str) -> str:
    cfg = _load_config()
    catalog_xml = _curl_get(f"{cfg['host']}/{cfg['ccm_context']}/oslc/workitems/catalog")
    root = ET.fromstring(catalog_xml)
    title_xpath = ".//{http://purl.org/dc/terms/}title"
    services_xpath = ".//{http://open-services.net/xmlns/discovery/1.0/}services"

    for entry in root.findall(".//{http://open-services.net/xmlns/discovery/1.0/}ServiceProvider"):
        title_node = entry.find(title_xpath)
        if title_node is None:
            continue
        if (title_node.text or "").strip().lower() != project_name.strip().lower():
            continue
        services_node = entry.find(services_xpath)
        if services_node is None:
            break
        service_url = services_node.attrib.get(f"{{{NS['rdf']}}}resource")
        if service_url:
            return service_url

    raise RuntimeError(f"Projeto '{project_name}' nao encontrado no catalogo OSLC.")


def _get_project_query_base(project_name: str) -> str:
    services_xml = _curl_get(_get_project_service_url(project_name))
    root = ET.fromstring(services_xml)
    simple_query_node = root.find(".//{http://open-services.net/xmlns/cm/1.0/}simpleQuery")
    if simple_query_node is None:
        raise RuntimeError(f"Nao foi encontrado simpleQuery para o projeto '{project_name}'.")

    url_node = simple_query_node.find("{http://open-services.net/xmlns/cm/1.0/}url")
    if url_node is None or not (url_node.text or "").strip():
        raise RuntimeError(f"Nao foi encontrado endpoint de consulta para o projeto '{project_name}'.")
    return (url_node.text or "").strip()


def _project_exists(project_name: str) -> bool:
    root = _get_project_areas_xml()
    for project in root.findall("jp06:project-area", NS):
        name = project.attrib.get(f"{{{NS['jp06']}}}name", "")
        if name.strip().lower() == project_name.strip().lower():
            return True
    return False


def _extract_results(payload: dict) -> list:
    return payload.get("oslc:results") or []


@mcp.resource(
    "elm://connection-info",
    name="elm-connection-info",
    title="Conexao ELM",
    description="Resumo da configuracao e dos endpoints basicos do ELM/EWM.",
    mime_type="application/json",
)
def resource_connection_info() -> str:
    return connection_info()


@mcp.resource(
    "elm://projects",
    name="elm-projects",
    title="Projetos EWM",
    description="Lista das areas de projeto visiveis no EWM.",
    mime_type="application/json",
)
def resource_projects() -> str:
    return list_projects()


@mcp.resource(
    "elm://project/{project_name}/workitems",
    name="elm-project-workitems",
    title="Work items do projeto",
    description="Lista inicial de work items do projeto informado no EWM.",
    mime_type="application/json",
)
def resource_project_workitems(project_name: str) -> str:
    return list_workitems(project_name, 10)


@mcp.prompt(
    name="consultar_ewm",
    title="Consultar EWM",
    description="Prompt base para consultar projetos e work items do EWM via MCP.",
)
def prompt_consultar_ewm(project_name: str = "") -> list[dict]:
    if project_name:
        content = (
            f"Consulte o projeto '{project_name}' no EWM. "
            "Primeiro leia o recurso elm://projects para confirmar o nome exato do projeto, "
            "depois use a tool list_workitems para listar os itens iniciais e, se necessario, "
            "use get_workitem para aprofundar em um item especifico."
        )
    else:
        content = (
            "Consulte o EWM. Primeiro leia elm://connection-info e elm://projects, "
            "depois escolha um projeto e use list_workitems para listar os itens iniciais. "
            "Se precisar de detalhes, use get_workitem com o ID retornado."
        )
    return [{"role": "user", "content": content}]


@mcp.tool()
def connection_info() -> str:
    cfg = _load_config()
    return json.dumps(
        {
            "host": cfg["host"],
            "jts_context": cfg["jts_context"],
            "ccm_context": cfg["ccm_context"],
            "verify_ssl": cfg["verify_ssl"],
            "uses_basic_token": True,
            "cm_catalog_url": _get_cm_catalog_url(),
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def list_projects() -> str:
    root = _get_project_areas_xml()
    projects = []
    for project in root.findall("jp06:project-area", NS):
        projects.append(
            {
                "name": project.attrib.get(f"{{{NS['jp06']}}}name", "?"),
                "summary": (project.findtext("jp06:summary", default="", namespaces=NS) or "").strip(),
                "uri": (project.findtext("jp06:url", default="", namespaces=NS) or "").strip(),
                "archived": (project.findtext("jp06:archived", default="false", namespaces=NS) or "").strip() == "true",
            }
        )
    return json.dumps(projects, ensure_ascii=False, indent=2)


@mcp.tool()
def list_workitems(project_name: str, pagesize: int = 30) -> str:
    if not _project_exists(project_name):
        return json.dumps({"error": f"Projeto '{project_name}' nao encontrado"}, ensure_ascii=False, indent=2)

    query_base = _get_project_query_base(project_name)
    params = {
        "oslc.select": "dcterms:identifier,dcterms:title",
        "oslc.pageSize": str(pagesize),
    }
    url = f"{query_base}?{urllib.parse.urlencode(params)}"
    payload = _curl_get_json(url)

    items = []
    for item in _extract_results(payload):
        items.append(
            {
                "id": item.get("dcterms:identifier"),
                "title": item.get("dcterms:title"),
                "uri": item.get("rdf:about"),
            }
        )

    response_info = payload.get("oslc:responseInfo") or {}
    return json.dumps(
        {
            "project_name": project_name,
            "totalCount": response_info.get("oslc:totalCount"),
            "nextPage": response_info.get("oslc:nextPage"),
            "items": items,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def get_workitem(project_name: str, workitem_id: str) -> str:
    if not _project_exists(project_name):
        return json.dumps({"error": f"Projeto '{project_name}' nao encontrado"}, ensure_ascii=False, indent=2)

    query_base = _get_project_query_base(project_name)
    params = {
        "oslc.where": f'dcterms:identifier="{workitem_id}"',
        "oslc.select": "*",
        "oslc.pageSize": "1",
    }
    url = f"{query_base}?{urllib.parse.urlencode(params)}"
    payload = _curl_get_json(url)
    results = _extract_results(payload)
    if not results:
        return json.dumps(
            {"error": f"Work item {workitem_id} nao encontrado no projeto '{project_name}'"},
            ensure_ascii=False,
            indent=2,
        )

    item = results[0]
    return json.dumps(item, ensure_ascii=False, indent=2)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
