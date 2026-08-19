import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(__file__).resolve().parent / "data" / "analyses"
UPLOAD_DIR = BASE_DIR / "uploads"
VIDEO_DIR = BASE_DIR / "data" / "videos"
VIDEO_WORK_DIR = VIDEO_DIR / "work"
ANALYSIS_ID_RE = re.compile(r"^[a-f0-9]{32}$")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)
SUPABASE_ANALYSES_TABLE = os.getenv("SUPABASE_ANALYSES_TABLE", "analyses")
SUPABASE_PROJECTS_TABLE = os.getenv("SUPABASE_PROJECTS_TABLE", "projects")
R2_BUCKET = os.getenv("R2_BUCKET", "")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "").rstrip("/")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_REGION = os.getenv("R2_REGION", "auto")


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def is_supabase_storage_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def storage_backend_name() -> str:
    return "supabase" if is_supabase_storage_enabled() else "local_json"


def is_r2_storage_enabled() -> bool:
    return bool(R2_BUCKET and R2_ENDPOINT_URL and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY)


def _r2_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name=R2_REGION,
    )


def _r2_analysis_prefix(user_id: str, analysis_id: str, project_id: str | None = None) -> str:
    if project_id:
        return f"users/{user_id}/projects/{project_id}/analyses/{analysis_id}"
    return f"users/{user_id}/analyses/{analysis_id}"


EXTENSION_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _r2_source_key(user_id: str, analysis_id: str, ext: str = ".pdf", project_id: str | None = None) -> str:
    return f"{_r2_analysis_prefix(user_id, analysis_id, project_id=project_id)}/original{ext}"


_r2_pdf_key = lambda user_id, analysis_id, project_id=None: _r2_source_key(user_id, analysis_id, ".pdf", project_id=project_id)


def upload_source_document_to_r2(path: str | Path, user_id: str, analysis_id: str, project_id: str | None = None) -> str:
    if not is_r2_storage_enabled():
        return ""

    source_path = Path(path)
    if not source_path.exists() or not source_path.is_file():
        return ""

    ext = source_path.suffix.lower() or ".pdf"
    content_type = EXTENSION_CONTENT_TYPES.get(ext, "application/octet-stream")
    object_key = _r2_source_key(user_id, analysis_id, ext, project_id=project_id)
    _r2_client().upload_file(
        str(source_path),
        R2_BUCKET,
        object_key,
        ExtraArgs={"ContentType": content_type},
    )
    return object_key


upload_source_pdf_to_r2 = upload_source_document_to_r2


def get_r2_object(object_key: str) -> dict[str, Any] | None:
    if not is_r2_storage_enabled() or not object_key:
        return None
    try:
        return _r2_client().get_object(Bucket=R2_BUCKET, Key=object_key)
    except Exception:
        return None


def head_r2_object(object_key: str) -> dict[str, Any] | None:
    if not is_r2_storage_enabled() or not object_key:
        return None
    try:
        return _r2_client().head_object(Bucket=R2_BUCKET, Key=object_key)
    except Exception:
        return None


def delete_r2_object(object_key: str) -> bool:
    if not is_r2_storage_enabled() or not object_key:
        return False
    try:
        _r2_client().delete_object(Bucket=R2_BUCKET, Key=object_key)
    except Exception:
        return False
    return True


def _supabase_headers(prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _supabase_table_url(table: str = SUPABASE_ANALYSES_TABLE, query: str = "") -> str:
    base = f"{SUPABASE_URL}/rest/v1/{urllib.parse.quote(table)}"
    return f"{base}?{query}" if query else base


def _supabase_request(
    method: str,
    query: str = "",
    payload: Any | None = None,
    prefer: str | None = None,
    table: str = SUPABASE_ANALYSES_TABLE,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _supabase_table_url(table=table, query=query),
        data=data,
        headers=_supabase_headers(prefer=prefer),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase storage request failed: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Supabase storage request failed: {error.reason}") from error

    return json.loads(body) if body else None


def _summary_metadata(record: dict) -> dict:
    result = record.get("result", {})
    summary = result.get("document_summary", {}) if isinstance(result, dict) else {}
    source = result.get("source_metadata", {}) if isinstance(result.get("source_metadata"), dict) else {}
    return {
        "paper_title": source.get("title") or result.get("paper_title") or summary.get("title"),
        "summary_mode": result.get("summary_mode"),
        "processing_seconds": result.get("processing_seconds"),
        "summary": summary.get("summary", ""),
    }


def _supabase_row_from_record(record: dict, user_id: str) -> dict:
    metadata = _summary_metadata(record)
    return {
        "analysis_id": record.get("analysis_id"),
        "user_id": user_id,
        "project_id": record.get("project_id"),
        "filename": record.get("filename"),
        "paper_title": metadata.get("paper_title"),
        "summary_mode": metadata.get("summary_mode"),
        "status": record.get("status") or "completed",
        "processing_seconds": metadata.get("processing_seconds"),
        "summary": metadata.get("summary"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at") or datetime.now(timezone.utc).isoformat(),
        "record": record,
    }


def _record_belongs_to_user(record: dict, user_id: str | None) -> bool:
    return not user_id or record.get("user_id") == user_id


def _project_summary_from_analysis(analysis: dict) -> dict:
    title = analysis.get("paper_title") or analysis.get("filename") or "Untitled Paper"
    created_at = analysis.get("created_at")
    updated_at = analysis.get("updated_at") or created_at
    status = analysis.get("status") or "completed"
    return {
        "project_id": f"legacy-{analysis.get('analysis_id')}",
        "name": title,
        "created_at": created_at,
        "updated_at": updated_at,
        "paper_count": 1,
        "completed_count": 1 if status == "completed" else 0,
        "failed_count": 1 if status == "failed" else 0,
        "active_analysis_id": analysis.get("analysis_id"),
        "legacy": True,
        "papers": [analysis],
    }


def _paper_summary_from_record(record: dict) -> dict:
    result = record.get("result", {})
    metadata = _summary_metadata(record)
    return {
        "analysis_id": record.get("analysis_id"),
        "project_id": record.get("project_id"),
        "filename": record.get("filename"),
        "paper_title": metadata.get("paper_title"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at") or record.get("created_at"),
        "status": record.get("status") or "completed",
        "summary_mode": metadata.get("summary_mode"),
        "processing_seconds": metadata.get("processing_seconds"),
        "summary": metadata.get("summary"),
        "error": record.get("error") or result.get("error") if isinstance(result, dict) else record.get("error"),
    }


def create_project(name: str, user_id: str, project_id: str | None = None) -> dict:
    project_id = project_id or uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    project = {
        "project_id": project_id,
        "user_id": user_id,
        "name": str(name or "Untitled Project").strip() or "Untitled Project",
        "created_at": now,
        "updated_at": now,
    }

    if is_supabase_storage_enabled():
        rows = _supabase_request(
            "POST",
            payload=project,
            prefer="return=representation",
            table=SUPABASE_PROJECTS_TABLE,
        )
        return rows[0] if isinstance(rows, list) and rows else project

    return project


def touch_project(project_id: str, user_id: str) -> None:
    if not project_id or project_id.startswith("legacy-"):
        return
    now = datetime.now(timezone.utc).isoformat()
    if is_supabase_storage_enabled():
        query = urllib.parse.urlencode({
            "project_id": f"eq.{project_id}",
            "user_id": f"eq.{user_id}",
        })
        _supabase_request(
            "PATCH",
            query=query,
            payload={"updated_at": now},
            table=SUPABASE_PROJECTS_TABLE,
        )


def rename_project(project_id: str, name: str, user_id: str) -> Optional[dict]:
    if not project_id or project_id.startswith("legacy-"):
        return None
    cleaned_name = str(name or "").strip()
    if not cleaned_name:
        return None

    if is_supabase_storage_enabled():
        query = urllib.parse.urlencode({
            "project_id": f"eq.{project_id}",
            "user_id": f"eq.{user_id}",
        })
        rows = _supabase_request(
            "PATCH",
            query=query,
            payload={
                "name": cleaned_name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            prefer="return=representation",
            table=SUPABASE_PROJECTS_TABLE,
        )
        return rows[0] if isinstance(rows, list) and rows else None

    return None


def list_projects(user_id: str | None = None) -> list[dict]:
    analyses = list_analyses(user_id=user_id)

    if is_supabase_storage_enabled():
        if not user_id:
            return []
        query = urllib.parse.urlencode({
            "user_id": f"eq.{user_id}",
            "select": "project_id,name,created_at,updated_at",
            "order": "updated_at.desc",
        })
        rows = _supabase_request("GET", query=query, table=SUPABASE_PROJECTS_TABLE)
        projects = rows if isinstance(rows, list) else []
    else:
        projects = []

    analyses_by_project: dict[str, list[dict]] = {}
    legacy_projects: list[dict] = []
    for analysis in analyses:
        project_id = analysis.get("project_id")
        if project_id:
            analyses_by_project.setdefault(project_id, []).append(analysis)
        else:
            legacy_projects.append(_project_summary_from_analysis(analysis))

    summaries: list[dict] = []
    if not is_supabase_storage_enabled():
        for project_id, papers in analyses_by_project.items():
            statuses = [paper.get("status") or "completed" for paper in papers]
            completed_count = statuses.count("completed")
            failed_count = statuses.count("failed")
            first_openable = next((paper for paper in papers if paper.get("status") == "completed"), papers[0] if papers else {})
            summaries.append({
                "project_id": project_id,
                "name": (
                    papers[0].get("paper_title")
                    or papers[0].get("filename")
                    if len(papers) == 1 and papers
                    else "Untitled Project"
                ),
                "created_at": min((paper.get("created_at") or "" for paper in papers), default=""),
                "updated_at": max((paper.get("updated_at") or paper.get("created_at") or "" for paper in papers), default=""),
                "paper_count": len(papers),
                "completed_count": completed_count,
                "failed_count": failed_count,
                "active_analysis_id": first_openable.get("analysis_id"),
                "legacy": False,
                "papers": papers,
            })

    for project in projects:
        project_id = project.get("project_id")
        papers = analyses_by_project.get(project_id, [])
        statuses = [paper.get("status") or "completed" for paper in papers]
        completed_count = statuses.count("completed")
        failed_count = statuses.count("failed")
        first_openable = next((paper for paper in papers if paper.get("status") == "completed"), papers[0] if papers else {})
        summaries.append({
            **project,
            "paper_count": len(papers),
            "completed_count": completed_count,
            "failed_count": failed_count,
            "active_analysis_id": first_openable.get("analysis_id"),
            "legacy": False,
            "papers": papers,
        })

    return sorted(
        [*summaries, *legacy_projects],
        key=lambda project: project.get("updated_at") or project.get("created_at") or "",
        reverse=True,
    )


def get_project(project_id: str, user_id: str) -> Optional[dict]:
    if not project_id:
        return None

    if project_id.startswith("legacy-"):
        analysis_id = project_id.replace("legacy-", "", 1)
        record = get_analysis(analysis_id, user_id=user_id)
        if not record:
            return None
        analysis = _paper_summary_from_record(record)
        return _project_summary_from_analysis(analysis) | {
            "papers": [{**analysis, "record": record}],
        }

    if is_supabase_storage_enabled():
        query = urllib.parse.urlencode({
            "project_id": f"eq.{project_id}",
            "user_id": f"eq.{user_id}",
            "select": "project_id,name,created_at,updated_at",
            "limit": "1",
        })
        rows = _supabase_request("GET", query=query, table=SUPABASE_PROJECTS_TABLE)
        if not isinstance(rows, list) or not rows:
            return None
        project = rows[0]
        analyses_query = urllib.parse.urlencode({
            "project_id": f"eq.{project_id}",
            "user_id": f"eq.{user_id}",
            "select": "analysis_id,project_id,filename,paper_title,created_at,updated_at,status,summary_mode,processing_seconds,summary,record",
            "order": "created_at.asc",
        })
        analysis_rows = _supabase_request("GET", query=analyses_query)
        papers = []
        for row in analysis_rows if isinstance(analysis_rows, list) else []:
            record = row.get("record") if isinstance(row.get("record"), dict) else {}
            papers.append({
                "analysis_id": row.get("analysis_id"),
                "project_id": row.get("project_id"),
                "filename": row.get("filename"),
                "paper_title": row.get("paper_title"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at") or row.get("created_at"),
                "status": row.get("status") or "completed",
                "summary_mode": row.get("summary_mode"),
                "processing_seconds": row.get("processing_seconds"),
                "summary": row.get("summary"),
                "record": record,
            })
        statuses = [paper.get("status") or "completed" for paper in papers]
        return {
            **project,
            "paper_count": len(papers),
            "completed_count": statuses.count("completed"),
            "failed_count": statuses.count("failed"),
            "active_analysis_id": next((paper.get("analysis_id") for paper in papers if paper.get("status") == "completed"), papers[0].get("analysis_id") if papers else None),
            "legacy": False,
            "papers": papers,
        }

    papers = []
    for record_path in DATA_DIR.glob("*.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not _record_belongs_to_user(record, user_id):
            continue
        if record.get("project_id") != project_id:
            continue
        papers.append({**_paper_summary_from_record(record), "record": record})

    if not papers:
        return None

    papers.sort(key=lambda paper: paper.get("created_at") or "")
    statuses = [paper.get("status") or "completed" for paper in papers]
    return {
        "project_id": project_id,
        "name": (
            papers[0].get("paper_title")
            or papers[0].get("filename")
            if len(papers) == 1 and papers
            else "Untitled Project"
        ),
        "created_at": min((paper.get("created_at") or "" for paper in papers), default=""),
        "updated_at": max((paper.get("updated_at") or paper.get("created_at") or "" for paper in papers), default=""),
        "paper_count": len(papers),
        "completed_count": statuses.count("completed"),
        "failed_count": statuses.count("failed"),
        "active_analysis_id": next((paper.get("analysis_id") for paper in papers if paper.get("status") == "completed"), papers[0].get("analysis_id")),
        "legacy": False,
        "papers": papers,
    }


def save_analysis(
    filename: str,
    result: dict,
    source_pdf_path: str | Path | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    status: str = "completed",
) -> dict:
    _ensure_data_dir()

    analysis_id = uuid4().hex
    result["analysis_id"] = analysis_id
    record = {
        "analysis_id": analysis_id,
        "project_id": project_id,
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "result": result,
    }
    if user_id:
        record["user_id"] = user_id
    if source_pdf_path:
        record["source_pdf_path"] = str(source_pdf_path)
        if user_id:
            object_key = upload_source_document_to_r2(source_pdf_path, user_id, analysis_id, project_id=project_id)
            if object_key:
                record["source_pdf_object_key"] = object_key

    if is_supabase_storage_enabled():
        if not user_id:
            raise RuntimeError("Supabase storage requires a user_id.")
        rows = _supabase_request(
            "POST",
            payload=_supabase_row_from_record(record, user_id),
            prefer="return=representation",
        )
        if project_id:
            touch_project(project_id, user_id=user_id)
        return rows[0].get("record", record) if isinstance(rows, list) and rows else record

    record_path = DATA_DIR / f"{analysis_id}.json"
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    return record


def save_failed_analysis(
    filename: str,
    error: str,
    summary_mode: str,
    user_id: str | None = None,
    project_id: str | None = None,
) -> dict:
    _ensure_data_dir()

    analysis_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "analysis_id": analysis_id,
        "project_id": project_id,
        "summary_mode": summary_mode,
        "error": error,
    }
    record = {
        "analysis_id": analysis_id,
        "project_id": project_id,
        "filename": filename,
        "created_at": now,
        "updated_at": now,
        "status": "failed",
        "error": error,
        "result": result,
    }
    if user_id:
        record["user_id"] = user_id

    if is_supabase_storage_enabled():
        if not user_id:
            raise RuntimeError("Supabase storage requires a user_id.")
        rows = _supabase_request(
            "POST",
            payload=_supabase_row_from_record(record, user_id),
            prefer="return=representation",
        )
        if project_id:
            touch_project(project_id, user_id=user_id)
        return rows[0].get("record", record) if isinstance(rows, list) and rows else record

    record_path = DATA_DIR / f"{analysis_id}.json"
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def list_analyses(user_id: str | None = None) -> list[dict]:
    _ensure_data_dir()
    analyses: list[dict] = []

    if is_supabase_storage_enabled():
        if not user_id:
            return []
        query = urllib.parse.urlencode({
            "user_id": f"eq.{user_id}",
            "select": "analysis_id,project_id,filename,paper_title,created_at,updated_at,status,summary_mode,processing_seconds,summary",
            "order": "updated_at.desc",
        })
        rows = _supabase_request("GET", query=query)
        return rows if isinstance(rows, list) else []

    for record_path in DATA_DIR.glob("*.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not _record_belongs_to_user(record, user_id):
            continue

        result = record.get("result", {})
        summary = result.get("document_summary", {})
        source = result.get("source_metadata", {}) if isinstance(result.get("source_metadata"), dict) else {}
        analyses.append({
            "analysis_id": record.get("analysis_id"),
            "project_id": record.get("project_id"),
            "filename": record.get("filename"),
            "paper_title": source.get("title") or result.get("paper_title") or summary.get("title"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at") or record.get("created_at"),
            "status": record.get("status") or "completed",
            "summary_mode": result.get("summary_mode"),
            "processing_seconds": result.get("processing_seconds"),
            "summary": summary.get("summary", ""),
        })

    return sorted(
        analyses,
        key=lambda analysis: analysis.get("created_at") or "",
        reverse=True,
    )


def get_analysis(analysis_id: str, user_id: str | None = None) -> Optional[dict]:
    _ensure_data_dir()
    if not ANALYSIS_ID_RE.match(analysis_id):
        return None

    if is_supabase_storage_enabled():
        if not user_id:
            return None
        query = urllib.parse.urlencode({
            "analysis_id": f"eq.{analysis_id}",
            "user_id": f"eq.{user_id}",
            "select": "record",
            "limit": "1",
        })
        rows = _supabase_request("GET", query=query)
        if not isinstance(rows, list) or not rows:
            return None
        record = rows[0].get("record")
        return record if isinstance(record, dict) else None

    record_path = DATA_DIR / f"{analysis_id}.json"
    if not record_path.exists():
        return None

    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return record if _record_belongs_to_user(record, user_id) else None


def save_analysis_record(analysis_id: str, record: dict, user_id: str | None = None) -> Optional[dict]:
    _ensure_data_dir()
    if not ANALYSIS_ID_RE.match(analysis_id):
        return None
    if user_id and record.get("user_id") not in {None, user_id}:
        return None

    record["analysis_id"] = analysis_id
    if user_id:
        record["user_id"] = user_id
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    record["status"] = record.get("status") or "completed"
    result = record.get("result")
    if isinstance(result, dict):
        result["analysis_id"] = result.get("analysis_id") or analysis_id

    if is_supabase_storage_enabled():
        if not user_id:
            return None
        query = urllib.parse.urlencode({
            "analysis_id": f"eq.{analysis_id}",
            "user_id": f"eq.{user_id}",
        })
        rows = _supabase_request(
            "PATCH",
            query=query,
            payload=_supabase_row_from_record(record, user_id),
            prefer="return=representation",
        )
        if not isinstance(rows, list) or not rows:
            return None
        saved_record = rows[0].get("record")
        project_id = str(record.get("project_id") or "")
        if project_id:
            touch_project(project_id, user_id=user_id)
        return saved_record if isinstance(saved_record, dict) else record

    record_path = DATA_DIR / f"{analysis_id}.json"
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def _path_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _safe_unlink(path_value: object, allowed_dirs: list[Path]) -> Optional[str]:
    if not path_value:
        return None

    path = Path(str(path_value))
    if not path.exists() or not path.is_file():
        return None
    if not any(_path_inside(path, allowed_dir) for allowed_dir in allowed_dirs):
        return None

    path.unlink()
    return str(path)


def _safe_rmtree(path: Path, allowed_parent: Path) -> Optional[str]:
    if not path.exists() or not path.is_dir():
        return None
    if not _path_inside(path, allowed_parent):
        return None

    shutil.rmtree(path)
    return str(path)


def _delete_associated_files(record: dict) -> list[str]:
    deleted_paths: list[str] = []
    source_pdf_object_key = str(record.get("source_pdf_object_key") or "").strip()
    if source_pdf_object_key and delete_r2_object(source_pdf_object_key):
        deleted_paths.append(f"r2://{R2_BUCKET}/{source_pdf_object_key}")

    source_pdf = _safe_unlink(record.get("source_pdf_path"), [UPLOAD_DIR])
    if source_pdf:
        deleted_paths.append(source_pdf)
    elif not record.get("source_pdf_path"):
        legacy_pdf = _legacy_pdf_path(record)
        if legacy_pdf and not _legacy_pdf_is_shared(record, legacy_pdf):
            deleted_legacy_pdf = _safe_unlink(legacy_pdf, [UPLOAD_DIR])
            if deleted_legacy_pdf:
                deleted_paths.append(deleted_legacy_pdf)

    result = record.get("result", {})
    video = result.get("video", {}) if isinstance(result, dict) else {}
    video_path = video.get("video_path") if isinstance(video, dict) else None
    deleted_video = _safe_unlink(video_path, [VIDEO_DIR])
    if deleted_video:
        deleted_paths.append(deleted_video)

    analysis_id = str(record.get("analysis_id") or result.get("analysis_id") or "")
    if ANALYSIS_ID_RE.match(analysis_id):
        work_dir = _safe_rmtree(VIDEO_WORK_DIR / analysis_id, VIDEO_WORK_DIR)
        if work_dir:
            deleted_paths.append(work_dir)

    return deleted_paths


def _legacy_pdf_path(record: dict) -> Optional[Path]:
    filename = Path(record.get("filename") or "").name
    if not filename:
        return None
    path = UPLOAD_DIR / filename
    return path if path.exists() else None


def _legacy_pdf_is_shared(record: dict, pdf_path: Path) -> bool:
    current_id = str(record.get("analysis_id") or "")
    current_filename = Path(record.get("filename") or "").name
    for record_path in DATA_DIR.glob("*.json"):
        try:
            other_record = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if str(other_record.get("analysis_id") or "") == current_id:
            continue
        other_source = other_record.get("source_pdf_path")
        if other_source and Path(str(other_source)).resolve(strict=False) == pdf_path.resolve(strict=False):
            return True
        if not other_source and Path(other_record.get("filename") or "").name == current_filename:
            return True
    return False


def delete_analysis(analysis_id: str, delete_files: bool = True, user_id: str | None = None) -> Optional[dict]:
    _ensure_data_dir()
    if not ANALYSIS_ID_RE.match(analysis_id):
        return None

    if is_supabase_storage_enabled():
        if not user_id:
            return None
        record = get_analysis(analysis_id, user_id=user_id)
        if not record:
            return None
        project_id = str(record.get("project_id") or "")
        deleted_files = _delete_associated_files(record) if delete_files else []
        query = urllib.parse.urlencode({
            "analysis_id": f"eq.{analysis_id}",
            "user_id": f"eq.{user_id}",
        })
        _supabase_request("DELETE", query=query)
        if project_id:
            delete_project_if_empty(project_id, user_id=user_id)
        return {
            "analysis_id": analysis_id,
            "project_id": project_id,
            "deleted": True,
            "deleted_files": deleted_files,
        }

    record_path = DATA_DIR / f"{analysis_id}.json"
    if not record_path.exists():
        return None

    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        record = {"analysis_id": analysis_id}
    if not _record_belongs_to_user(record, user_id):
        return None
    project_id = str(record.get("project_id") or "")

    deleted_files = _delete_associated_files(record) if delete_files else []

    record_path.unlink()
    return {
        "analysis_id": analysis_id,
        "project_id": project_id,
        "deleted": True,
        "deleted_files": deleted_files,
    }


def delete_project_if_empty(project_id: str, user_id: str) -> bool:
    if not project_id or project_id.startswith("legacy-"):
        return False
    if not is_supabase_storage_enabled():
        return False

    query = urllib.parse.urlencode({
        "project_id": f"eq.{project_id}",
        "user_id": f"eq.{user_id}",
        "select": "analysis_id",
        "limit": "1",
    })
    rows = _supabase_request("GET", query=query)
    if isinstance(rows, list) and rows:
        touch_project(project_id, user_id=user_id)
        return False

    delete_query = urllib.parse.urlencode({
        "project_id": f"eq.{project_id}",
        "user_id": f"eq.{user_id}",
    })
    _supabase_request("DELETE", query=delete_query, table=SUPABASE_PROJECTS_TABLE)
    return True


def delete_project(project_id: str, delete_files: bool = True, user_id: str | None = None) -> Optional[dict]:
    if not user_id or not project_id:
        return None

    if project_id.startswith("legacy-"):
        analysis_id = project_id.replace("legacy-", "", 1)
        return delete_analysis(analysis_id, delete_files=delete_files, user_id=user_id)

    project = get_project(project_id, user_id=user_id)
    if not project:
        return None

    deleted_files: list[str] = []
    for paper in project.get("papers", []):
        record = paper.get("record") if isinstance(paper, dict) else None
        if isinstance(record, dict) and delete_files:
            deleted_files.extend(_delete_associated_files(record))

    if is_supabase_storage_enabled():
        query = urllib.parse.urlencode({
            "project_id": f"eq.{project_id}",
            "user_id": f"eq.{user_id}",
        })
        _supabase_request("DELETE", query=query, table=SUPABASE_PROJECTS_TABLE)
        return {
            "project_id": project_id,
            "deleted": True,
            "deleted_files": deleted_files,
        }

    return None


def save_video_script(analysis_id: str, video_script: dict, user_id: str | None = None) -> Optional[dict]:
    record = get_analysis(analysis_id, user_id=user_id)
    if not record:
        return None

    result = record.setdefault("result", {})
    result["video_script"] = video_script
    result["video_script_generated_at"] = datetime.now(timezone.utc).isoformat()
    result.pop("video", None)

    return save_analysis_record(analysis_id, record, user_id=user_id)

def save_video_result(analysis_id: str, video_result: dict, user_id: str | None = None) -> Optional[dict]:
    record = get_analysis(analysis_id, user_id=user_id)
    if not record:
        return None

    result = record.setdefault("result", {})
    result["video"] = video_result

    return save_analysis_record(analysis_id, record, user_id=user_id)
