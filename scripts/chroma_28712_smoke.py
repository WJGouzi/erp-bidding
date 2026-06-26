import json
import os
import time
import uuid

from app.config import Config
from app.infrastructure.integrations import ChromaAdapter


def _pick_first(grouped):
    if isinstance(grouped, list):
        first_group = grouped[0] if grouped else None
        if isinstance(first_group, list):
            return first_group[0] if first_group else None
        return first_group
    return grouped


def main():
    host = Config.CHROMA_HOST
    port = Config.CHROMA_PORT
    tenant = Config.CHROMA_TENANT
    database = Config.CHROMA_DATABASE
    collection = os.getenv("CHROMA_SMOKE_COLLECTION") or Config.CHROMA_COLLECTION
    filename = f"smoke_{int(time.time())}_{uuid.uuid4().hex[:8]}.txt"
    payload = (
        "smoke test for 28712 service\n"
        "keywords: ai_platform business_license authorization_letter delivery_plan\n"
        "verify async upload task status file get query and delete\n"
    ).encode("utf-8")
    metadata_json = json.dumps(
        {
            "biz_type": "BIDDING_SMOKE_TEST",
            "file_name": filename,
            "purpose": "28712 integration acceptance",
        }
    )

    print(
        "CONFIG="
        + json.dumps(
            {
                "host": host,
                "port": port,
                "tenant": tenant,
                "database": database,
                "collection": collection,
                "filename": filename,
            }
        )
    )

    adapter = ChromaAdapter(host=host, port=port, tenant=tenant, database=database)
    start = time.time()
    result = adapter.upload_file_async(
        collection_name=collection,
        file_content=payload,
        filename=filename,
        content_type="text/plain",
        metadata_json=metadata_json,
    )
    print("UPLOAD_RESULT=" + json.dumps(result))

    task_id = result.get("task_id")
    document_id = result.get("document_id")
    if not task_id or not document_id:
        raise RuntimeError("missing task_id or document_id")

    status_payload = None
    for _ in range(60):
        status_payload = adapter.get_async_task(task_id)
        status = str((status_payload or {}).get("status") or "").upper()
        stage = (status_payload or {}).get("stage")
        print("TASK_STATUS=" + json.dumps({"status": status, "stage": stage}))
        if status in {"SUCCESS", "COMPLETED", "DONE", "FAILED", "ERROR"}:
            break
        time.sleep(1)
    else:
        raise RuntimeError("async task polling timeout")

    final_status = str((status_payload or {}).get("status") or "").upper()
    if final_status not in {"SUCCESS", "COMPLETED", "DONE"}:
        raise RuntimeError("async task failed: " + json.dumps(status_payload))

    file_docs = adapter.get_file_documents(collection_name=collection, document_id=document_id)
    doc_groups = (file_docs or {}).get("documents") or []
    meta_groups = (file_docs or {}).get("metadatas") or []
    first_doc = _pick_first(doc_groups) or ""
    first_meta = _pick_first(meta_groups) or {}
    print(
        "FILE_DOCS="
        + json.dumps(
            {
                "documents_type": type(doc_groups).__name__,
                "first_doc_preview": first_doc[:120],
                "first_metadata": first_meta,
            }
        )
    )

    query_result = adapter.query_documents(
        collection_name=collection,
        query_texts=["ai_platform authorization_letter delivery_plan"],
        top_k=3,
    )
    result_groups = (query_result or {}).get("documents") or []
    top_match = _pick_first(result_groups) or ""
    print(
        "QUERY_RESULT="
        + json.dumps(
            {
                "result_type": type(result_groups).__name__,
                "top_match_preview": top_match[:120],
            }
        )
    )

    delete_result = adapter.delete_file_documents(collection_name=collection, document_id=document_id)
    print("DELETE_RESULT=" + json.dumps(delete_result))

    try:
        after_delete = adapter.get_file_documents(collection_name=collection, document_id=document_id)
        print("AFTER_DELETE=" + json.dumps(after_delete))
    except Exception as exc:
        print("AFTER_DELETE_ERROR=" + str(exc))

    print("ELAPSED_SECONDS=%.2f" % (time.time() - start))


if __name__ == "__main__":
    main()
