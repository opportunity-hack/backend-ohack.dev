"""Admin-managed email/message templates with version history.

Collection layout:
  email_templates/{template_id}
    - title, category, category_key, applicable_roles[], message, icon
    - status: "active" | "archived"
    - origin: "seed" | "admin"
    - version: int (current version number)
    - created_at, created_by, updated_at, updated_by
  email_templates/{template_id}/versions/{000N}
    - full content snapshot per version (append-only; includes the current one)
    - version, title, category, category_key, applicable_roles, message, icon
    - updated_at, updated_by, change_note

Revert never rewrites history: it copies an old version's content forward as a
brand-new version. The seed data (the original hardcoded frontend templates)
lives in email_templates_seed.py and is inserted on first list call so the
database always retains the originals as version 1.
"""

import re
from datetime import datetime

from common.log import get_logger
from db.db import get_db
from api.messages.message import Message
from services.email_templates_seed import DEFAULT_EMAIL_TEMPLATES

logger = get_logger("email_templates_service")

COLLECTION = "email_templates"

_CONTENT_KEYS = ("title", "category", "category_key", "applicable_roles", "message", "icon")
_ALLOWED_PATCH_KEYS = set(_CONTENT_KEYS) | {"status", "change_note"}

SEED_ACTOR = {"propel_user_id": None, "email": "system@ohack.org", "name": "System (seed)"}


def _now_iso():
    return datetime.now().isoformat()


def _version_doc_id(version):
    return f"{version:06d}"


def _snapshot_from(doc_dict):
    return {k: doc_dict.get(k) for k in _CONTENT_KEYS}


def _write_version(doc_ref, doc_dict, version, actor, change_note):
    snapshot = _snapshot_from(doc_dict)
    snapshot.update(
        {
            "version": version,
            "updated_at": _now_iso(),
            "updated_by": actor or {},
            "change_note": change_note or "",
        }
    )
    doc_ref.collection("versions").document(_version_doc_id(version)).set(snapshot)


def _slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "_", (title or "").lower()).strip("_")
    return slug[:60] or "template"


def _doc_with_id(doc):
    d = doc.to_dict()
    d["id"] = doc.id
    return d


def seed_default_templates(db=None):
    """Insert any DEFAULT_EMAIL_TEMPLATES missing from the collection.

    Existing docs are never touched, so admin edits and deletions of
    non-default templates survive. Returns the number of templates inserted.
    """
    db = db or get_db()
    inserted = 0
    for tpl in DEFAULT_EMAIL_TEMPLATES:
        doc_ref = db.collection(COLLECTION).document(tpl["id"])
        if doc_ref.get().exists:
            continue
        now = _now_iso()
        doc = {
            "title": tpl["title"],
            "category": tpl["category"],
            "category_key": tpl["category_key"],
            "applicable_roles": tpl["applicable_roles"],
            "message": tpl["message"],
            "icon": tpl.get("icon", ""),
            "status": "active",
            "origin": "seed",
            "version": 1,
            "created_at": now,
            "created_by": SEED_ACTOR,
            "updated_at": now,
            "updated_by": SEED_ACTOR,
        }
        doc_ref.set(doc)
        _write_version(doc_ref, doc, 1, SEED_ACTOR, "Imported from hardcoded messageTemplates.js")
        inserted += 1
    if inserted:
        logger.info(f"seed_default_templates: inserted {inserted} default templates")
    return inserted


def admin_list_templates():
    """List all templates (any status). Auto-seeds the originals when empty."""
    db = get_db()
    docs = list(db.collection(COLLECTION).stream())
    if not docs:
        seed_default_templates(db)
        docs = list(db.collection(COLLECTION).stream())
    results = sorted(
        (_doc_with_id(d) for d in docs),
        key=lambda t: (t.get("category_key") or "", t.get("title") or ""),
    )
    return Message(results)


def admin_seed_templates():
    """Explicit 'restore defaults' — re-inserts any missing seed templates."""
    inserted = seed_default_templates()
    msg = Message(f"Restored {inserted} default template(s)")
    msg.inserted = inserted
    return msg, 200


def admin_create_template(json_in, actor):
    json_in = json_in or {}
    title = (json_in.get("title") or "").strip()
    message_body = json_in.get("message") or ""
    if not title or not message_body.strip():
        return Message("Both title and message are required"), 400

    db = get_db()
    base_slug = _slugify(title)
    slug = base_slug
    suffix = 2
    while db.collection(COLLECTION).document(slug).get().exists:
        slug = f"{base_slug}_{suffix}"
        suffix += 1

    now = _now_iso()
    doc = {
        "title": title,
        "category": json_in.get("category") or "Custom",
        "category_key": json_in.get("category_key") or "CUSTOM",
        "applicable_roles": json_in.get("applicable_roles") or [],
        "message": message_body,
        "icon": json_in.get("icon") or "✉️",
        "status": "active",
        "origin": "admin",
        "version": 1,
        "created_at": now,
        "created_by": actor or {},
        "updated_at": now,
        "updated_by": actor or {},
    }
    doc_ref = db.collection(COLLECTION).document(slug)
    doc_ref.set(doc)
    _write_version(doc_ref, doc, 1, actor, json_in.get("change_note") or "Created")

    msg = Message("Created template")
    msg.id = slug
    return msg, 201


def admin_update_template(template_id, json_in, actor):
    patch = {k: v for k, v in (json_in or {}).items() if k in _ALLOWED_PATCH_KEYS}
    if not patch:
        return Message("No allowed fields in payload"), 400
    if "title" in patch and not (patch["title"] or "").strip():
        return Message("Title cannot be empty"), 400
    if "message" in patch and not (patch["message"] or "").strip():
        return Message("Message cannot be empty"), 400

    db = get_db()
    doc_ref = db.collection(COLLECTION).document(template_id)
    snap = doc_ref.get()
    if not snap.exists:
        return Message("Not found"), 404

    current = snap.to_dict()
    change_note = patch.pop("change_note", "")
    content_changed = any(k in patch and patch[k] != current.get(k) for k in _CONTENT_KEYS)

    new_version = current.get("version", 1)
    merged = {**current, **patch}
    if content_changed:
        new_version += 1
    merged["version"] = new_version
    merged["updated_at"] = _now_iso()
    merged["updated_by"] = actor or {}

    doc_ref.set(merged)
    if content_changed:
        _write_version(doc_ref, merged, new_version, actor, change_note)

    msg = Message("Updated template")
    msg.id = template_id
    msg.version = new_version
    return msg, 200


def admin_delete_template(template_id):
    """Hard delete a template doc and its version history."""
    db = get_db()
    doc_ref = db.collection(COLLECTION).document(template_id)
    if not doc_ref.get().exists:
        return Message("Not found"), 404
    for version_doc in doc_ref.collection("versions").stream():
        version_doc.reference.delete()
    doc_ref.delete()
    logger.info(f"admin_delete_template: removed {template_id}")
    return Message("Deleted template"), 200


def admin_get_template_versions(template_id):
    db = get_db()
    doc_ref = db.collection(COLLECTION).document(template_id)
    if not doc_ref.get().exists:
        return Message("Not found"), 404
    versions = [v.to_dict() for v in doc_ref.collection("versions").stream()]
    versions.sort(key=lambda v: v.get("version", 0), reverse=True)
    return Message(versions), 200


def admin_revert_template(template_id, json_in, actor):
    target_version = (json_in or {}).get("version")
    if not isinstance(target_version, int) or target_version < 1:
        return Message("A version (int) to revert to is required"), 400

    db = get_db()
    doc_ref = db.collection(COLLECTION).document(template_id)
    snap = doc_ref.get()
    if not snap.exists:
        return Message("Not found"), 404

    version_snap = doc_ref.collection("versions").document(_version_doc_id(target_version)).get()
    if not version_snap.exists:
        return Message(f"Version {target_version} not found"), 404

    current = snap.to_dict()
    current_version = current.get("version", 1)
    if target_version == current_version:
        return Message("Already at that version"), 400

    new_version = current_version + 1
    merged = {**current, **_snapshot_from(version_snap.to_dict())}
    merged["version"] = new_version
    merged["updated_at"] = _now_iso()
    merged["updated_by"] = actor or {}

    doc_ref.set(merged)
    _write_version(doc_ref, merged, new_version, actor, f"Reverted to version {target_version}")

    msg = Message("Reverted template")
    msg.id = template_id
    msg.version = new_version
    return msg, 200
