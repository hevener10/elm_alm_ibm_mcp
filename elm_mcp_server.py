import base64
import html
import json
import logging
import re
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
    "foaf": "http://xmlns.com/foaf/0.1/",
    "jfs": "http://jazz.net/xmlns/prod/jazz/jfs/1.0/",
    "jp06": "http://jazz.net/xmlns/prod/jazz/process/0.6/",
    "jp": "http://jazz.net/xmlns/prod/jazz/process/1.0/",
    "oslc_cm": "http://open-services.net/xmlns/cm/1.0/",
    "rtc_cm": "http://jazz.net/xmlns/prod/jazz/rtc/cm/1.0/",
    "atom": "http://www.w3.org/2005/Atom",
}
DEFAULT_USER_ROLES = "modifiedBy,creator,contributor,subscribers,resolvedBy"
ROLE_FIELDS = {
    "modifiedBy": "rtc_cm:modifiedBy",
    "creator": "dcterms:creator",
    "contributor": "dcterms:contributor",
    "subscribers": "rtc_cm:subscribers",
    "resolvedBy": "rtc_cm:resolvedBy",
    "ownedBy": "rtc_cm:ownedBy",
}
ROLE_ALIASES = {
    "modifiedby": "modifiedBy",
    "modified_by": "modifiedBy",
    "modificador": "modifiedBy",
    "creator": "creator",
    "createdby": "creator",
    "autor": "creator",
    "contributor": "contributor",
    "contribuidor": "contributor",
    "subscribers": "subscribers",
    "subscriber": "subscribers",
    "assinante": "subscribers",
    "assinantes": "subscribers",
    "resolvedby": "resolvedBy",
    "resolved_by": "resolvedBy",
    "resolvedor": "resolvedBy",
    "ownedby": "ownedBy",
    "owned_by": "ownedBy",
    "responsavel": "ownedBy",
    "responsável": "ownedBy",
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


def _curl_get(url: str, accept: str | None = None) -> str:
    cfg = _load_config()
    command = [
        "curl.exe",
        "-k",
        "-sS",
        "-H",
        f"Authorization: Basic {cfg['token']}",
    ]
    if accept:
        command.extend(["-H", f"Accept: {accept}"])
    command.append(url)
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


def _list_project_names(include_archived: bool = False) -> list[str]:
    root = _get_project_areas_xml()
    project_names = []
    for project in root.findall("jp06:project-area", NS):
        archived = (project.findtext("jp06:archived", default="false", namespaces=NS) or "").strip() == "true"
        if archived and not include_archived:
            continue
        name = project.attrib.get(f"{{{NS['jp06']}}}name", "").strip()
        if name:
            project_names.append(name)
    return project_names


def _extract_results(payload: dict) -> list:
    return payload.get("oslc:results") or []


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag.rsplit(":", 1)[-1]


def _first_text_by_local_names(root: ET.Element, names: set[str]) -> str | None:
    for node in root.iter():
        if _local_name(node.tag) in names and node.text and node.text.strip():
            return node.text.strip()
        if _local_name(node.tag) in names:
            resource = node.attrib.get(f"{{{NS['rdf']}}}resource")
            if resource:
                return resource.strip()
    return None


def _first_resource_by_suffix(root: ET.Element, suffix: str) -> str | None:
    for node in root.iter():
        for value in node.attrib.values():
            if suffix in value:
                return value.strip()
    return None


def _user_uri_from_identifier(user_id: str) -> str:
    cfg = _load_config()
    encoded_user = urllib.parse.quote(user_id.strip(), safe="")
    return f"{cfg['host']}/{cfg['jts_context']}/users/{encoded_user}"


def _user_ccm_oslc_uri_from_identifier(user_id: str) -> str:
    cfg = _load_config()
    encoded_user = urllib.parse.quote(user_id.strip(), safe="")
    return f"{cfg['host']}/{cfg['ccm_context']}/oslc/users/{encoded_user}"


def _subscription_user_uris(profile: dict) -> list[str]:
    uris = []
    user_id = (profile.get("id") or "").strip()
    profile_uri = (profile.get("uri") or "").strip()
    if user_id:
        uris.append(_user_ccm_oslc_uri_from_identifier(user_id))
        uris.append(_user_uri_from_identifier(user_id))
    if profile_uri:
        uris.append(profile_uri)

    deduped = []
    seen = set()
    for uri in uris:
        key = uri.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(uri.strip())
    return deduped


def _normalize_user_uri(user: str) -> str:
    raw_user = user.strip()
    if raw_user.startswith(("http://", "https://")):
        return raw_user
    cfg = _load_config()
    user_id = cfg["username"] if _matches_current_user_alias(raw_user) else raw_user
    return _user_uri_from_identifier(user_id)


def _get_user_profile(user: str) -> dict:
    raw_user = user.strip()
    user_uri = raw_user if raw_user.startswith(("http://", "https://")) else _user_uri_from_identifier(raw_user)
    xml_text = _curl_get(user_uri, "application/rdf+xml")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise RuntimeError(f"Nao foi possivel ler o perfil JTS do usuario '{user}'.") from exc

    profile_uri = root.attrib.get(f"{{{NS['rdf']}}}about") or _first_resource_by_suffix(root, "/users/") or user_uri
    user_id = _first_text_by_local_names(root, {"userId", "user-id", "jazzId"})
    name = _first_text_by_local_names(root, {"name", "title", "fullName"})
    email = _first_text_by_local_names(root, {"emailAddress", "email", "mbox", "mail"})
    nick = _first_text_by_local_names(root, {"nick", "nickname"})
    archived_text = _first_text_by_local_names(root, {"archived", "isArchived"})

    if email and email.startswith("mailto:"):
        email = email[7:]
    if not user_id and "/users/" in profile_uri:
        user_id = urllib.parse.unquote(profile_uri.rstrip("/").rsplit("/", 1)[-1])
    if not any([user_id, name, email, nick]) and "/users/" not in profile_uri:
        raise RuntimeError(f"Perfil JTS do usuario '{user}' nao retornou dados reconheciveis.")

    return {
        "input": user,
        "resolved": True,
        "id": user_id,
        "uri": profile_uri,
        "name": name,
        "email": email,
        "nick": nick,
        "archived": (archived_text or "false").strip().lower() == "true",
        "resolution_mode": "jts_profile",
    }


def _alias_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    normalized = value.strip().lower()
    tokens = {normalized}
    for separator in ("@", ".", "_", "-", " "):
        parts = [part for part in normalized.replace("@", " ").replace(".", " ").replace("_", " ").replace("-", " ").split() if part]
        tokens.update(parts)
        if separator in normalized:
            tokens.add(normalized.split(separator, 1)[0])
    return {token for token in tokens if token}


def _profile_aliases(profile: dict) -> set[str]:
    aliases = set()
    for key in ("id", "uri", "name", "email", "nick"):
        aliases.update(_alias_tokens(profile.get(key)))
    return aliases


def _matches_current_user_alias(user: str) -> bool:
    raw_user = user.strip().lower()
    if not raw_user:
        return False
    cfg = _load_config()
    if raw_user == cfg["username"].strip().lower():
        return True
    try:
        profile = _get_user_profile(cfg["username"])
    except RuntimeError:
        return False
    return raw_user in _profile_aliases(profile)


def _resolve_user_profile(user: str) -> dict:
    raw_user = user.strip()
    if not raw_user:
        raise RuntimeError("Informe um usuario, ID JTS ou URI de usuario.")
    if _matches_current_user_alias(raw_user):
        profile = _get_user_profile(_load_config()["username"])
        profile["input"] = user
        profile["resolution_mode"] = "current_user_alias"
        return profile
    try:
        profile = _get_user_profile(raw_user)
        profile["input"] = user
        return profile
    except RuntimeError as exc:
        user_uri = _normalize_user_uri(raw_user)
        return {
            "input": user,
            "resolved": False,
            "id": urllib.parse.unquote(user_uri.rstrip("/").rsplit("/", 1)[-1]) if "/users/" in user_uri else None,
            "uri": user_uri,
            "name": None,
            "email": None,
            "nick": None,
            "archived": None,
            "resolution_mode": "uri_fallback",
            "warning": str(exc),
        }


def _format_oslc_datetime(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return f"{text}T00:00:00.000Z"
    if text.endswith("Z"):
        return text
    if "T" in text:
        return f"{text}Z"
    return text


def _date_conditions(start_date: str, end_date: str, field_name: str = "dcterms:modified") -> list[str]:
    conditions = []
    start_value = _format_oslc_datetime(start_date) if start_date else None
    end_value = _format_oslc_datetime(end_date) if end_date else None
    if start_value:
        conditions.append(f'{field_name}>="{start_value}"')
    if end_value:
        conditions.append(f'{field_name}<"{end_value}"')
    return conditions


def _combine_where(conditions: list[str]) -> str:
    return " and ".join(condition for condition in conditions if condition)


def _query_workitems(
    project_name: str,
    where: str = "",
    select: str = "*",
    pagesize: int = 100,
    max_items: int = 500,
    query_base: str | None = None,
) -> dict:
    query_base = query_base or _get_project_query_base(project_name)
    items = []
    params = {
        "oslc.select": select,
        "oslc.pageSize": str(max(1, pagesize)),
        "oslc.orderBy": "-dcterms:modified",
    }
    if where:
        params["oslc.where"] = where
    url = f"{query_base}?{urllib.parse.urlencode(params)}"
    response_info = {}
    truncated = False

    while url:
        payload = _curl_get_json(url)
        page_items = _extract_results(payload)
        response_info = payload.get("oslc:responseInfo") or {}
        remaining = max_items - len(items)
        if remaining <= 0:
            truncated = True
            break
        if len(page_items) > remaining:
            items.extend(page_items[:remaining])
            truncated = True
            break
        items.extend(page_items)
        url = response_info.get("oslc:nextPage")
        if url and len(items) >= max_items:
            truncated = True
            break

    return {
        "items": items,
        "responseInfo": response_info,
        "truncated": truncated,
    }


def _parse_roles(roles: str) -> list[str]:
    selected_roles = []
    role_text = roles or DEFAULT_USER_ROLES
    for role in role_text.split(","):
        key = role.strip().lower()
        canonical = ROLE_ALIASES.get(key)
        if canonical and canonical not in selected_roles:
            selected_roles.append(canonical)
    return selected_roles or ["modifiedBy", "creator", "contributor", "subscribers", "resolvedBy"]


def _collect_resources(value) -> list[str]:
    resources = []
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if key in ("rdf:resource", "@rdf:resource") and isinstance(nested_value, str):
                resources.append(nested_value)
            else:
                resources.extend(_collect_resources(nested_value))
    elif isinstance(value, list):
        for nested_value in value:
            resources.extend(_collect_resources(nested_value))
    elif isinstance(value, str):
        resources.append(value)
    return resources


def _user_match_values(profile: dict) -> set[str]:
    values = set()
    for key in ("id", "uri", "email", "nick"):
        value = profile.get(key)
        if value:
            values.add(str(value).strip().lower())
    return values


def _resource_matches_user(resource: str, profile: dict) -> bool:
    normalized_resource = resource.strip().lower()
    if not normalized_resource:
        return False
    for value in _user_match_values(profile):
        if normalized_resource == value:
            return True
        if normalized_resource.endswith(f"/users/{value}"):
            return True
        if value and value in normalized_resource and ("/users/" in normalized_resource or "@" in value):
            return True
    return False


def _item_matches_user_role(item: dict, role: str, profile: dict) -> bool:
    field_name = ROLE_FIELDS[role]
    values = _collect_resources(item.get(field_name))
    return any(_resource_matches_user(value, profile) for value in values)


def _roles_matched(item: dict, roles: list[str], profile: dict) -> list[str]:
    return [role for role in roles if _item_matches_user_role(item, role, profile)]


def _item_in_date_range(item: dict, start_date: str, end_date: str) -> bool:
    timestamp = item.get("dcterms:modified") or item.get("dcterms:created")
    if not timestamp:
        return False if start_date or end_date else True
    start_value = _format_oslc_datetime(start_date) if start_date else None
    end_value = _format_oslc_datetime(end_date) if end_date else None
    if start_value and timestamp < start_value:
        return False
    if end_value and timestamp >= end_value:
        return False
    return True


def _normalize_workitem(item: dict, roles_matched: list[str]) -> dict:
    return {
        "id": item.get("dcterms:identifier"),
        "title": item.get("dcterms:title"),
        "uri": item.get("rdf:about"),
        "type": item.get("dcterms:type"),
        "status": item.get("oslc_cm:status"),
        "created": item.get("dcterms:created"),
        "modified": item.get("dcterms:modified"),
        "rolesMatched": roles_matched,
    }


def _merge_item(target: dict, item: dict, matched_roles: list[str]) -> None:
    item_id = item.get("dcterms:identifier") or item.get("rdf:about")
    if not item_id:
        return
    if item_id not in target:
        target[item_id] = _normalize_workitem(item, matched_roles)
        return
    current_roles = set(target[item_id].get("rolesMatched") or [])
    current_roles.update(matched_roles)
    target[item_id]["rolesMatched"] = sorted(current_roles)


def _rdf_resource_attr() -> str:
    return f"{{{NS['rdf']}}}resource"


def _rdf_about_attr() -> str:
    return f"{{{NS['rdf']}}}about"


def _workitem_resource_url(workitem_id: str) -> str:
    cfg = _load_config()
    encoded_id = urllib.parse.quote(str(workitem_id).strip(), safe="")
    return f"{cfg['host']}/{cfg['ccm_context']}/resource/itemName/com.ibm.team.workitem.WorkItem/{encoded_id}"


def _workitem_id_from_uri(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    patterns = [
        r"WorkItem/(\d+)",
        r"workitem[=/](\d+)",
        r"workItem[=/](\d+)",
        r"[?&]id=(\d+)",
        r"(?:^|\D)(\d{4,})(?:\D*$|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _node_value(node: ET.Element) -> str:
    resource = node.attrib.get(_rdf_resource_attr())
    if resource:
        return resource.strip()
    return _clean_text("".join(node.itertext()))


def _child_values_by_local_names(parent: ET.Element, names: set[str]) -> list[str]:
    values = []
    for child in list(parent):
        if _local_name(child.tag) in names:
            value = _node_value(child)
            if value:
                values.append(value)
    return values


def _first_child_value(parent: ET.Element, names: set[str]) -> str | None:
    values = _child_values_by_local_names(parent, names)
    return values[0] if values else None


def _find_workitem_description(root: ET.Element, workitem_id: str = "") -> ET.Element | None:
    descriptions = [node for node in root.iter() if _local_name(node.tag) == "Description"]
    if workitem_id:
        for description in descriptions:
            about = description.attrib.get(_rdf_about_attr(), "")
            if about.rstrip("/").endswith(f"/{workitem_id}") or f"WorkItem/{workitem_id}" in about:
                return description
    return descriptions[0] if descriptions else None


def _parse_rdf_workitem_summary(
    rdf_text: str,
    profile: dict,
    fallback_id: str = "",
    project_name: str = "",
) -> dict:
    root = ET.fromstring(rdf_text)
    description = _find_workitem_description(root, fallback_id)
    if description is None:
        raise RuntimeError("A resposta RDF nao contem descricao de work item reconhecivel.")

    uri = description.attrib.get(_rdf_about_attr(), "")
    item_id = (
        _first_child_value(description, {"identifier", "shortId", "shortIdentifier"})
        or _workitem_id_from_uri(uri)
        or fallback_id
    )
    subscribers = _child_values_by_local_names(description, {"subscribers"})
    subscribed = any(_resource_matches_user(value, profile) for value in subscribers)
    state = _first_child_value(description, {"state"})
    status = _first_child_value(description, {"status"}) or state

    return {
        "id": item_id,
        "title": _first_child_value(description, {"title"}) or "",
        "uri": uri,
        "url": uri or (_workitem_resource_url(item_id) if item_id else ""),
        "project_name": project_name,
        "type": _first_child_value(description, {"type"}) or "",
        "status": status or "",
        "state": state or "",
        "created": _first_child_value(description, {"created"}) or "",
        "modified": _first_child_value(description, {"modified"}) or "",
        "subscribersCount": len(subscribers),
        "subscribed": subscribed,
        "rolesMatched": ["subscribers"] if subscribed else [],
        "source": "rdf_resource",
    }


def _normalize_subscription_item(item: dict, project_name: str, user_uri: str, source: str) -> dict:
    normalized = _normalize_workitem(item, ["subscribers"])
    normalized["project_name"] = project_name
    normalized["url"] = normalized.get("uri") or ""
    normalized["subscribed"] = True
    normalized["subscriberUriMatched"] = user_uri
    normalized["source"] = source
    return normalized


def _merge_subscription_item(target: dict, item: dict) -> None:
    item_id = str(item.get("id") or item.get("uri") or item.get("url") or "").strip()
    if not item_id:
        return
    if item_id not in target:
        target[item_id] = item
        return
    current = target[item_id]
    for key, value in item.items():
        if value and not current.get(key):
            current[key] = value
    sources = set(str(current.get("source") or "").split("+"))
    sources.add(str(item.get("source") or ""))
    current["source"] = "+".join(sorted(source for source in sources if source))


def _query_project_subscriptions(
    project_name: str,
    profile: dict,
    pagesize: int,
    max_items: int,
) -> tuple[list[dict], list[dict], bool]:
    items_by_id = {}
    query_details = []
    truncated = False
    query_base = _get_project_query_base(project_name)
    for user_uri in _subscription_user_uris(profile):
        where = f'rtc_cm:subscribers="{user_uri}"'
        try:
            query_result = _query_workitems(
                project_name,
                where,
                "*",
                pagesize,
                max_items,
                query_base=query_base,
            )
            truncated = truncated or query_result["truncated"]
            query_details.append(
                {
                    "project_name": project_name,
                    "strategy": "subscriber_oslc_server_filter",
                    "where": where,
                    "count": len(query_result["items"]),
                    "truncated": query_result["truncated"],
                }
            )
            for item in query_result["items"]:
                _merge_subscription_item(
                    items_by_id,
                    _normalize_subscription_item(item, project_name, user_uri, "oslc_subscriber_filter"),
                )
        except Exception as exc:
            query_details.append(
                {
                    "project_name": project_name,
                    "strategy": "subscriber_oslc_server_filter",
                    "where": where,
                    "error": str(exc),
                }
            )
    return list(items_by_id.values()), query_details, truncated


def _fetch_rdf_workitem_summary(workitem_id: str, profile: dict, project_name: str = "") -> dict:
    rdf_text = _curl_get(_workitem_resource_url(workitem_id), accept="application/rdf+xml")
    return _parse_rdf_workitem_summary(rdf_text, profile, str(workitem_id), project_name)


def _timestamp_in_date_range(timestamp: str, start_date: str, end_date: str) -> bool:
    if not timestamp:
        return False if start_date or end_date else True
    start_value = _format_oslc_datetime(start_date) if start_date else None
    end_value = _format_oslc_datetime(end_date) if end_date else None
    if start_value and timestamp < start_value:
        return False
    if end_value and timestamp >= end_value:
        return False
    return True


def _entry_child_text(entry: ET.Element, names: set[str]) -> str:
    for child in list(entry):
        if _local_name(child.tag) in names:
            return _clean_text("".join(child.itertext()))
    return ""


def _entry_link(entry: ET.Element) -> str:
    for child in list(entry):
        if _local_name(child.tag) == "link":
            href = child.attrib.get("href") or child.attrib.get(_rdf_resource_attr())
            if href:
                return href.strip()
            text = _clean_text("".join(child.itertext()))
            if text:
                return text
    return ""


def _entry_author(entry: ET.Element) -> str:
    for child in list(entry):
        if _local_name(child.tag) == "author":
            name = _entry_child_text(child, {"name", "title"})
            if name:
                return name
            text = _clean_text("".join(child.itertext()))
            if text:
                return text
    return _entry_child_text(entry, {"creator", "author"})


def _parse_subscription_feed_events(feed_text: str, start_date: str = "", end_date: str = "", max_results: int = 50) -> list[dict]:
    root = ET.fromstring(feed_text)
    events = []
    for entry in root.iter():
        if _local_name(entry.tag) != "entry":
            continue
        title = _entry_child_text(entry, {"title"})
        updated = _entry_child_text(entry, {"updated", "published", "created"})
        if not _timestamp_in_date_range(updated, start_date, end_date):
            continue
        link = _entry_link(entry)
        summary = _entry_child_text(entry, {"summary", "content", "description"})
        workitem_id = _workitem_id_from_uri(link) or _workitem_id_from_uri(title) or _workitem_id_from_uri(summary)
        events.append(
            {
                "title": title,
                "updated": updated,
                "published": _entry_child_text(entry, {"published"}),
                "author": _entry_author(entry),
                "link": link,
                "summary": summary,
                "workitem_id": workitem_id,
            }
        )
        if len(events) >= max(1, max_results):
            break
    return events


def _subscription_feed_url(profile: dict, max_results: int) -> str:
    cfg = _load_config()
    user_id = (profile.get("id") or cfg["username"]).strip()
    params = {
        "itemType": "WorkItem",
        "user": user_id,
        "maxResults": str(max(1, max_results)),
    }
    return f"{cfg['host']}/{cfg['ccm_context']}/service/com.ibm.team.repository.common.internal.IFeedService?{urllib.parse.urlencode(params)}"


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
            "Primeiro leia o recurso elm://projects para confirmar o nome exato do projeto. "
            "Use list_workitems para uma listagem inicial, get_workitem para aprofundar um item, "
            "find_user para resolver usuarios e search_workitems_by_user para pesquisas por responsavel, "
            "autor, modificador, assinante, resolvedor ou atividade em periodo. "
            "Use list_subscribed_workitems para listar itens assinados e list_subscription_feed "
            "para ler eventos recentes do feed de assinaturas."
        )
    else:
        content = (
            "Consulte o EWM. Primeiro leia elm://connection-info e elm://projects, "
            "depois escolha um projeto. Use list_workitems para listar itens iniciais, "
            "get_workitem para detalhes, find_user para resolver usuarios e "
            "search_workitems_by_user quando a pergunta envolver usuario, responsavel, autor, "
            "assinante, modificador, resolvedor ou periodo. Use list_subscribed_workitems "
            "quando a pergunta envolver minhas assinaturas, seguindo, feed ou atividades assinadas; "
            "use list_subscription_feed para eventos recentes das assinaturas."
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
def find_user(user: str) -> str:
    try:
        profile = _resolve_user_profile(user)
    except RuntimeError as exc:
        return json.dumps(
            {
                "input": user,
                "resolved": False,
                "error": str(exc),
            },
            ensure_ascii=False,
            indent=2,
        )
    return json.dumps(profile, ensure_ascii=False, indent=2)


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
def search_workitems_by_user(
    project_name: str,
    user: str,
    start_date: str = "",
    end_date: str = "",
    roles: str = DEFAULT_USER_ROLES,
    pagesize: int = 100,
    max_items: int = 500,
) -> str:
    if not _project_exists(project_name):
        return json.dumps({"error": f"Projeto '{project_name}' nao encontrado"}, ensure_ascii=False, indent=2)

    profile = _resolve_user_profile(user)
    selected_roles = _parse_roles(roles)
    user_uri = profile.get("uri") or _normalize_user_uri(user)
    merged_items = {}
    query_details = []
    truncated = False
    date_conditions = _date_conditions(start_date, end_date)

    if date_conditions:
        date_where = _combine_where(date_conditions)
        query_result = _query_workitems(project_name, date_where, "*", pagesize, max_items)
        truncated = truncated or query_result["truncated"]
        query_details.append(
            {
                "roles": selected_roles,
                "strategy": "date_server_filter_user_local_filter",
                "where": date_where,
                "count": len(query_result["items"]),
                "truncated": query_result["truncated"],
            }
        )
        for item in query_result["items"]:
            matched_roles = _roles_matched(item, selected_roles, profile)
            if matched_roles:
                _merge_item(merged_items, item, matched_roles)
    else:
        for role in [role for role in selected_roles if role != "subscribers"]:
            role_where = f'{ROLE_FIELDS[role]}="{user_uri}"'
            query_result = _query_workitems(project_name, role_where, "*", pagesize, max_items)
            truncated = truncated or query_result["truncated"]
            query_details.append(
                {
                    "role": role,
                    "strategy": "role_server_filter",
                    "where": role_where,
                    "count": len(query_result["items"]),
                    "truncated": query_result["truncated"],
                }
            )
            for item in query_result["items"]:
                matched_roles = _roles_matched(item, selected_roles, profile) or [role]
                _merge_item(merged_items, item, matched_roles)

        if "subscribers" in selected_roles:
            subscription_items, subscription_queries, subscription_truncated = _query_project_subscriptions(
                project_name,
                profile,
                pagesize,
                max_items,
            )
            truncated = truncated or subscription_truncated
            query_details.extend(subscription_queries)
            for item in subscription_items:
                legacy_item = {
                    "dcterms:identifier": item.get("id"),
                    "dcterms:title": item.get("title"),
                    "rdf:about": item.get("uri"),
                    "dcterms:type": item.get("type"),
                    "oslc_cm:status": item.get("status"),
                    "dcterms:created": item.get("created"),
                    "dcterms:modified": item.get("modified"),
                }
                _merge_item(merged_items, legacy_item, ["subscribers"])

    items = sorted(
        merged_items.values(),
        key=lambda current_item: current_item.get("modified") or current_item.get("created") or "",
        reverse=True,
    )
    return json.dumps(
        {
            "project_name": project_name,
            "user": profile,
            "date_range": {
                "start_date": start_date,
                "end_date": end_date,
                "start_oslc": _format_oslc_datetime(start_date) if start_date else None,
                "end_oslc": _format_oslc_datetime(end_date) if end_date else None,
            },
            "roles": selected_roles,
            "totalCount": len(items),
            "truncated": truncated,
            "queries": query_details,
            "items": items,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def list_subscribed_workitems(
    user: str = "",
    project_name: str = "",
    include_archived_projects: bool = False,
    pagesize: int = 100,
    max_items: int = 500,
    verify_rdf: bool = False,
    fallback_workitem_ids: str = "",
    use_feed_fallback: bool = True,
    feed_max_results: int = 100,
) -> str:
    """Lista work items assinados por um usuario no EWM."""
    cfg = _load_config()
    profile = _resolve_user_profile(user or cfg["username"])
    target_projects = [project_name] if project_name.strip() else _list_project_names(include_archived_projects)
    items_by_id = {}
    query_details = []
    errors = []
    truncated = False

    for current_project in target_projects:
        if not _project_exists(current_project):
            errors.append({"project_name": current_project, "error": f"Projeto '{current_project}' nao encontrado"})
            continue
        try:
            project_items, project_queries, project_truncated = _query_project_subscriptions(
                current_project,
                profile,
                pagesize,
                max_items,
            )
            query_details.extend(project_queries)
            truncated = truncated or project_truncated
            for item in project_items:
                if verify_rdf and item.get("id"):
                    try:
                        rdf_item = _fetch_rdf_workitem_summary(str(item["id"]), profile, current_project)
                        if rdf_item.get("subscribed"):
                            _merge_subscription_item(items_by_id, rdf_item)
                            continue
                        item["rdfVerification"] = "not_subscribed"
                    except Exception as exc:
                        item["rdfVerificationError"] = str(exc)
                _merge_subscription_item(items_by_id, item)
        except Exception as exc:
            errors.append({"project_name": current_project, "error": str(exc)})

    if use_feed_fallback:
        try:
            feed_text = _curl_get(_subscription_feed_url(profile, feed_max_results), accept="application/atom+xml, application/xml, text/xml")
            feed_events = _parse_subscription_feed_events(feed_text, "", "", feed_max_results)
            feed_ids = []
            for event in feed_events:
                workitem_id = event.get("workitem_id")
                if workitem_id and workitem_id not in feed_ids:
                    feed_ids.append(workitem_id)
            query_details.append(
                {
                    "strategy": "feed_fallback_ids_to_rdf",
                    "feedEvents": len(feed_events),
                    "workitemIds": feed_ids[:max_items],
                }
            )
            for workitem_id in feed_ids[:max_items]:
                try:
                    item = _fetch_rdf_workitem_summary(str(workitem_id), profile, project_name)
                    if item.get("subscribed"):
                        item["source"] = "feed_fallback_rdf"
                        _merge_subscription_item(items_by_id, item)
                except Exception as exc:
                    errors.append({"workitem_id": workitem_id, "strategy": "feed_fallback_ids_to_rdf", "error": str(exc)})
        except Exception as exc:
            errors.append({"strategy": "feed_fallback_ids_to_rdf", "error": str(exc)})

    if fallback_workitem_ids.strip():
        for raw_id in re.split(r"[,;\s]+", fallback_workitem_ids.strip()):
            if not raw_id:
                continue
            try:
                item = _fetch_rdf_workitem_summary(raw_id, profile, project_name)
                if item.get("subscribed"):
                    _merge_subscription_item(items_by_id, item)
                else:
                    errors.append({"workitem_id": raw_id, "warning": "Item RDF lido, mas usuario nao aparece em rtc_cm:subscribers."})
            except Exception as exc:
                errors.append({"workitem_id": raw_id, "error": str(exc)})

    items = sorted(
        items_by_id.values(),
        key=lambda current_item: current_item.get("modified") or current_item.get("created") or "",
        reverse=True,
    )
    if len(items) > max_items:
        items = items[:max_items]
        truncated = True

    return json.dumps(
        {
            "user": profile,
            "project_name": project_name or None,
            "projectsSearched": target_projects,
            "subscriberUrisTried": _subscription_user_uris(profile),
            "totalCount": len(items),
            "truncated": truncated,
            "queries": query_details,
            "errors": errors,
            "items": items,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def list_subscription_feed(
    user: str = "",
    start_date: str = "",
    end_date: str = "",
    max_results: int = 50,
    enrich_workitems: bool = False,
    project_name: str = "",
) -> str:
    """Le eventos recentes do feed de assinaturas do usuario no EWM."""
    cfg = _load_config()
    profile = _resolve_user_profile(user or cfg["username"])
    feed_url = _subscription_feed_url(profile, max_results)
    errors = []
    try:
        feed_text = _curl_get(feed_url, accept="application/atom+xml, application/xml, text/xml")
        events = _parse_subscription_feed_events(feed_text, start_date, end_date, max_results)
    except Exception as exc:
        return json.dumps(
            {
                "user": profile,
                "feedUrl": feed_url,
                "date_range": {
                    "start_date": start_date,
                    "end_date": end_date,
                    "start_oslc": _format_oslc_datetime(start_date) if start_date else None,
                    "end_oslc": _format_oslc_datetime(end_date) if end_date else None,
                },
                "totalCount": 0,
                "events": [],
                "errors": [str(exc)],
            },
            ensure_ascii=False,
            indent=2,
        )

    if enrich_workitems:
        for event in events:
            workitem_id = event.get("workitem_id")
            if not workitem_id:
                continue
            try:
                event["workitem"] = _fetch_rdf_workitem_summary(str(workitem_id), profile, project_name)
            except Exception as exc:
                event["workitemError"] = str(exc)
                errors.append({"workitem_id": workitem_id, "error": str(exc)})

    return json.dumps(
        {
            "user": profile,
            "feedUrl": feed_url,
            "date_range": {
                "start_date": start_date,
                "end_date": end_date,
                "start_oslc": _format_oslc_datetime(start_date) if start_date else None,
                "end_oslc": _format_oslc_datetime(end_date) if end_date else None,
            },
            "totalCount": len(events),
            "errors": errors,
            "events": events,
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
