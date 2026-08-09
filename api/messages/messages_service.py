from common.utils import safe_get_env_var
from common.utils.slack import send_slack_audit, send_slack, invite_user_to_channel
from common.utils.firebase import get_github_contributions_for_user
from common.utils.oauth_providers import is_slack_user_id, extract_slack_user_id
from api.messages.message import Message
from google.cloud.exceptions import NotFound

from services.users_service import get_propel_user_details_by_id, get_slack_user_from_propel_user_id, get_user_from_slack_id, save_user
from services.email_service import send_project_help_slack_invite_email
import json
import uuid
from datetime import datetime

from common.log import get_logger, debug, error
import firebase_admin
import threading
from firebase_admin import credentials, firestore

from cachetools import cached, TTLCache

from ratelimit import limits
import os

from db.db import fetch_user_by_user_id, get_db


logger = get_logger("messages_service")

ONE_MINUTE = 1*60
def get_public_message():
    logger.debug("~ Public ~")
    return Message(
        "aaThis is a public message."
    )


def get_protected_message():
    logger.debug("~ Protected ~")

    return Message(
        "This is a protected message."
    )


def get_admin_message():
    logger.debug("~ Admin ~")

    return Message(
        "This is an admin message."
    )

from common.utils.firestore_helpers import (
    hash_key,
    log_execution_time,
    doc_to_json,
    doc_to_json_recursive,
    clear_all_caches as _clear_all_caches,
)





def clear_cache():
    from services.hackathons_service import clear_cache as _hackathon_clear_cache
    _hackathon_clear_cache()

# Ref: https://stackoverflow.com/questions/59138326/how-to-set-google-firebase-credentials-not-with-json-file-but-with-python-dict
# Instead of giving the code a json file, we use environment variables so we don't have to source control a secrets file
cert_env = json.loads(safe_get_env_var("FIREBASE_CERT_CONFIG"))


#We don't want this to be a file, we want to use env variables for security (we would have to check in this file)
#cred = credentials.Certificate("./api/messages/ohack-dev-firebase-adminsdk-hrr2l-933367ee29.json")
cred = credentials.Certificate(cert_env)
# Check if firebase is already initialized
if not firebase_admin._apps:
    firebase_admin.initialize_app(credential=cred)

# --------------------------- Problem Statement functions to be deleted -----------------  #
@limits(calls=100, period=ONE_MINUTE)
def save_helping_status_old(propel_user_id, json):
    """Thin delegate to the canonical helping service (profile-stack
    retirement). Deleted once the frontend calls /api/users/profile/helping."""
    logger.info("legacy-profile-route hit: POST /api/messages/profile/helping")
    from services.problem_statements_service import save_helping_status
    result = save_helping_status(propel_user_id, json)
    if result is None:
        return None
    return Message("Updated helping status")


@limits(calls=50, period=ONE_MINUTE)
def save_problem_statement_old(json):
    db = get_db()  # this connects to our Firestore database
    logger.debug("Problem Statement Save")

    logger.debug("Clearing cache")    
    clear_cache()
    logger.debug("Done Clearing cache")


    send_slack_audit(action="save_problem_statement",
                     message="Saving", payload=json)
    # TODO: In this current form, you will overwrite any information that matches the same NPO name

    doc_id = uuid.uuid1().hex
    title = json["title"]
    description = json["description"]
    first_thought_of = json["first_thought_of"]
    github = json["github"]
    references = json["references"]
    status = json["status"]
    rank = json.get("rank")
        

    collection = db.collection('problem_statements')

    insert_res = collection.document(doc_id).set({
        "title": title,
        "description": description,
        "first_thought_of": first_thought_of,
        "github": github,
        "references": references,
        "status": status,
        "rank": rank
    })

    logger.debug(f"Insert Result: {insert_res}")

    return Message(
        "Saved Problem Statement"
    )

def get_problem_statement_from_id_old(problem_id):
    db = get_db()    
    doc = db.collection('problem_statements').document(problem_id)
    return doc

@cached(cache=TTLCache(maxsize=100, ttl=600), lock=threading.Lock())
def get_single_problem_statement_old(project_id):
    logger.debug(f"get_single_problem_statement start project_id={project_id}")    
    db = get_db()      
    doc = db.collection('problem_statements').document(project_id)
    
    if doc is None:
        logger.warning("get_single_problem_statement end (no results)")
        return {}
    else:
        result = doc_to_json(docid=doc.id, doc=doc)
        if result is None:
            logger.warning(f"get_single_problem_statement doc_to_json returned None for project_id={project_id}")
            return {}
        result["id"] = doc.id

        logger.info(f"get_single_problem_statement end (with result):{result}")
        return result
    return {}

@limits(calls=100, period=ONE_MINUTE)
def get_problem_statement_list_old():
    logger.debug("Problem Statements List")
    db = get_db()
    docs = db.collection('problem_statements').stream()  # steam() gets all records
    if docs is None:
        return {[]}
    else:
        results = []
        for doc in docs:
            results.append(doc_to_json(docid=doc.id, doc=doc))

    # log result
    logger.debug(results)        
    return { "problem_statements": results }

# Contribution data comes from a batch scraper — 1h cache, matching the
# redis cache on get_github_contributions_for_user (was ttl=10, absurdly short)
@cached(cache=TTLCache(maxsize=100, ttl=3600), lock=threading.Lock())
@limits(calls=100, period=ONE_MINUTE)
def get_github_profile(github_username):
    logger.debug(f"Getting Github Profile for {github_username}")

    return {
        "github_history": get_github_contributions_for_user(github_username)
    }


# -------------------- User functions to be deleted ---------------------------------------- #

# 10 minute cache for 100 objects LRU
@cached(cache=TTLCache(maxsize=100, ttl=600), lock=threading.Lock())
@limits(calls=100, period=ONE_MINUTE)
def get_profile_metadata_old(propel_id):
    """Thin delegate to the canonical users-service profile read, preserving
    the legacy {"text": ...} envelope and auth_failed error shape.
    (Profile-stack retirement — deleted once the frontend is migrated.)"""
    logger.info("legacy-profile-route hit: GET /api/messages/profile")
    from services.users_service import get_profile_metadata
    response = get_profile_metadata(propel_id)
    if response is None:
        return {"error": "Unable to resolve user profile", "status": "auth_failed"}
    return {"text": response}


_ADMIN_PROFILE_LEAN_FIELDS = (
    "name",
    "nickname",
    "email_address",
    "user_id",
    "profile_image",
    "last_login",
    "github",
    "linkedin_url",
    "instagram_url",
    "company",
    "education",
    "role",
    "shirt_size",
    "expertise",
    "why",
)


def _lean_admin_profile(doc):
    """Project a Firestore user doc into the lean shape the admin search uses.

    Resolves DocumentReference lists (badges/teams/hackathons) to id strings
    inline, since the frontend only reads `.length` on these arrays. Avoids
    `doc_to_json`'s broader behavior and the heavy `history` field entirely.
    """
    d = doc.to_dict() or {}
    out = {"id": doc.id}
    for key in _ADMIN_PROFILE_LEAN_FIELDS:
        v = d.get(key)
        if v is not None:
            out[key] = v

    for ref_key in ("badges", "teams", "hackathons"):
        value = d.get(ref_key)
        if isinstance(value, list):
            out[ref_key] = [
                v.id if isinstance(v, firestore.DocumentReference) else v
                for v in value
            ]

    vol = d.get("volunteering")
    if isinstance(vol, list):
        out["volunteering"] = [
            {"hours": v.get("hours", 0)}
            for v in vol if isinstance(v, dict)
        ]

    return out


# 5-minute TTL is enough to absorb tab refreshes / multiple admins loading the
# page in close succession while still picking up new signups within minutes.
@cached(cache=TTLCache(maxsize=1, ttl=300), lock=threading.Lock(), key=lambda: "all")
def get_all_profiles():
    db = get_db()
    docs = db.collection('users').stream()
    results = [_lean_admin_profile(doc) for doc in docs]
    logger.info(f"get_all_profiles returned {len(results)} profiles")
    return {"profiles": results}


# Caching is not needed because the parent method already is caching


@limits(calls=100, period=ONE_MINUTE)
def get_history_old(db_id):
    logger.debug("Get History Start")

    # Check if db_id is None first
    if db_id is None:
        logger.error("get_history_old called with None db_id")
        return None

    db = get_db()  # this connects to our Firestore database
    collection = db.collection('users')
    doc = collection.document(db_id)
    doc_get = doc.get()

    # Check if document exists before calling to_dict()
    if not doc_get.exists:
        logger.warning(f"User document not found for db_id: {db_id}")
        return None

    res = doc_get.to_dict()

    # Additional safety check in case to_dict() returns None
    if res is None:
        logger.warning(f"User document exists but to_dict() returned None for db_id: {db_id}")
        return None

    # Hackathon history is now derived from the unified `volunteers` collection
    # so attendance reflects who actually showed up (hackers always count;
    # mentors/judges/volunteers must be selected AND checked in). The legacy
    # `users.hackathons` ref array is no longer the source of truth.
    try:
        from services.volunteers_service import get_user_hackathon_attendance
        _hackathons = get_user_hackathon_attendance(
            user_id=res.get('user_id'),
            email=res.get('email_address'),
        )
    except Exception as e:
        logger.exception(f"Failed to load hackathon attendance for {db_id}: {e}")
        _hackathons = []

    _badges=[]
    if "badges" in res:
        for h in res["badges"]:
            _badges.append(h.get().to_dict())

    result = {
        "id": doc.id,
        "user_id": res["user_id"],
        "profile_image": res["profile_image"],
        "email_address" : res["email_address"],
        "history": res["history"] if "history" in res else "",
        "badges" : _badges,
        "hackathons" : _hackathons,
        "hackathon_history": _hackathons,
        "expertise": res["expertise"] if "expertise" in res else "",
        "education": res["education"] if "education" in res else "",
        "shirt_size": res["shirt_size"] if "shirt_size" in res else "",
        "linkedin_url": res["linkedin_url"] if "linkedin_url" in res else "",
        "instagram_url": res["instagram_url"] if "instagram_url" in res else "",        
        "github": res["github"] if "github" in res else "",
        "why": res["why"] if "why" in res else "",
        "role": res["role"] if "role" in res else "",
        "company": res["company"] if "company" in res else "",
        "propel_id": res["propel_id"] if "propel_id" in res else "",
        "street_address": res["street_address"] if "street_address" in res else "",
        "street_address_2": res["street_address_2"] if "street_address_2" in res else "",
        "city": res["city"] if "city" in res else "",
        "state": res["state"] if "state" in res else "",
        "postal_code": res["postal_code"] if "postal_code" in res else "",
        "country": res["country"] if "country" in res else "",
        "want_stickers": res["want_stickers"] if "want_stickers" in res else "",
        # Portfolio fields (Profile.js PortfolioTab)
        "bio": res.get("bio", ""),
        "headline": res.get("headline", ""),
        "bio_video_url": res.get("bio_video_url", ""),
        "portfolio_links": res.get("portfolio_links") or [],
        "profile_slug": res.get("profile_slug"),
        "profile_visibility": res.get("profile_visibility", "private"),
    }

    # Clear cache    

    logger.debug(f"RESULT\n{result}")
    return result


@limits(calls=50, period=ONE_MINUTE)
def save_user_old(
        user_id=None,
        email=None,
        last_login=None,
        profile_image=None,
        name=None,
        nickname=None,
        propel_id=None
        ):
    logger.info(f"User Save for {user_id} {email} {last_login} {profile_image} {name} {nickname}")
    # https://towardsdatascience.com/nosql-on-the-cloud-with-python-55a1383752fc


    if user_id is None or email is None or last_login is None or profile_image is None:
        logger.error(
            f"Empty values provided for user_id: {user_id},\
                email: {email}, or last_login: {last_login}\
                    or profile_image: {profile_image}")
        return

    db = get_db()  # this connects to our Firestore database

    # Even though there is 1 record, we always will need to iterate on it
    docs = db.collection('users').where("user_id","==",user_id).stream()

    for doc in docs:
        res = doc.to_dict()
        logger.debug(res)
        if res:
            # Found result already in DB, update
            logger.debug(f"Found user (_id={doc.id}), updating last_login")
            update_res = db.collection("users").document(doc.id).update(
                {
                    "last_login": last_login,
                    "profile_image": profile_image,
                    "name": name,
                    "nickname": nickname,
                    "propel_id": propel_id,
            })
            logger.debug(f"Update Result: {update_res}")

        logger.debug("User Save End")
        return doc.id # Should only have 1 record, but break just for safety 

    default_badge = db.collection('badges').document("fU7c3ne90Rd1TB5P7NTV")

    doc_id = uuid.uuid1().hex
    insert_res = db.collection('users').document(doc_id).set({
        "email_address": email,
        "last_login": last_login,
        "user_id": user_id,
        "profile_image": profile_image,
        "name": name,
        "nickname": nickname,
        "badges": [
            default_badge
        ],
        "teams": [],
        "propel_id": propel_id,
    })
    logger.debug(f"Insert Result: {insert_res}")
    return doc_id

def save_profile_metadata_old(propel_id, json):
    """Thin delegate to the canonical users-service profile write.
    Returns None when identity can't be resolved (route 404s, as before)."""
    logger.info("legacy-profile-route hit: POST /api/messages/profile")
    from services.users_service import save_profile_metadata
    result = save_profile_metadata(propel_id, json)
    if result is None:
        return None
    return Message("Saved Profile Metadata")


@cached(cache=TTLCache(maxsize=100, ttl=600), lock=threading.Lock(), key=lambda id: id)
def get_user_by_id_old(id):
    """Thin delegate to the privacy-safe public profile getter. Deliberate
    deltas vs the old body: no longer leaks `github` (privacy-gated field),
    now includes `profile_slug`."""
    logger.info("legacy-profile-route hit: GET /api/messages/profile/<id>")
    from services.users_service import get_profile_by_db_id
    return get_profile_by_db_id(id) or {}


def upload_image_to_cdn(request):
    """
    Upload an image to CDN. Accepts binary data, base64, or standard image formats.
    Returns the CDN URL of the uploaded image.
    """
    import base64
    import tempfile
    import mimetypes
    from werkzeug.utils import secure_filename
    from common.utils.cdn import upload_to_cdn
    
    logger.info("Starting image upload to CDN")
    
    try:
        # Check if file is in request.files (multipart/form-data)
        if 'file' in request.files:
            _directory = request.form.get("directory", "images")
            _filename = request.form.get("filename", None)
            logger.debug("Processing multipart file upload")
            file = request.files['file']
            if file.filename == '':
                logger.warning("Upload failed: No file selected")
                return {"success": False, "error": "No file selected"}, 400
            
            filename = secure_filename(_filename or file.filename)
            if not filename:
                logger.warning(f"Upload failed: Invalid filename provided: {file.filename}")
                return {"success": False, "error": "Invalid filename"}, 400
            
            logger.debug(f"Processing file upload: {filename}")
            
            # Check if it's an image
            if not _is_image_file(filename):
                logger.warning(f"Upload failed: File is not an image: {filename}")
                return {"success": False,"error": "File must be an image"}, 400
            
            # Create a properly named temporary file
            import tempfile
            temp_dir = tempfile.gettempdir()
            temp_filepath = os.path.join(temp_dir, filename)
            
            logger.debug(f"Saving file to temporary location: {temp_filepath}")
            file.save(temp_filepath)
            _optimize_image_for_web(temp_filepath)

            # Get just the file name without the path as the destination
            destination_filename = os.path.basename(temp_filepath)
            logger.debug(f"Destination filename for CDN upload: {destination_filename}")
            
            try:
                # Upload to CDN using the properly named temp file
                logger.info(f"Uploading {filename} to CDN from {temp_filepath}")
                cdn_url = upload_to_cdn(_directory, temp_filepath, destination_filename)
                
                logger.info(f"Successfully uploaded image to CDN: {cdn_url}")
                return {"success": True, "url": cdn_url, "message": "Image uploaded successfully"}
            finally:
                # Clean up temp file
                if os.path.exists(temp_filepath):
                    os.unlink(temp_filepath)
                    logger.debug(f"Cleaned up temporary file: {temp_filepath}")
        
        # Check if data is in JSON body (base64 or binary)
        elif request.is_json:
            logger.debug("Processing JSON request")
            data = request.get_json()
            
            if 'base64' in data:
                logger.debug("Processing base64 encoded image")
                # Handle base64 encoded image
                base64_data = data['base64']
                filename = data.get('filename', 'uploaded_image.png')
                
                logger.debug(f"Processing base64 image with filename: {filename}")
                
                # Remove data URL prefix if present
                if base64_data.startswith('data:image'):
                    logger.debug("Removing data URL prefix from base64 string")
                    base64_data = base64_data.split(',')[1]
                
                # Decode base64
                try:
                    image_data = base64.b64decode(base64_data)
                    logger.debug(f"Successfully decoded base64 data, size: {len(image_data)} bytes")
                except Exception as e:
                    logger.error(f"Failed to decode base64 data: {str(e)}")
                    return {"success": False, "error": "Invalid base64 data"}, 400
                
                filename = secure_filename(filename)
                if not _is_image_file(filename):
                    logger.warning(f"Upload failed: File is not an image: {filename}")
                    return {"success": False, "error": "File must be an image"}, 400
                
                # Create a properly named temporary file
                temp_dir = tempfile.gettempdir()
                temp_filepath = os.path.join(temp_dir, filename)
                
                logger.debug(f"Saving base64 image to temporary file: {temp_filepath}")
                with open(temp_filepath, 'wb') as temp_file:
                    temp_file.write(image_data)
                _optimize_image_for_web(temp_filepath)
                
                # Get just the file name without the path as the destination
                destination_filename = os.path.basename(temp_filepath)
                logger.debug(f"Destination filename for CDN upload: {destination_filename}")

                try:
                    # Upload to CDN using the properly named temp file
                    logger.info(f"Uploading base64 image {filename} to CDN from {temp_filepath}")
                    cdn_url = upload_to_cdn("images", temp_filepath, destination_filename)
                    
                    logger.info(f"Successfully uploaded base64 image to CDN: {cdn_url}")
                    return {"success": True, "url": cdn_url, "message": "Image uploaded successfully"}
                finally:
                    # Clean up temp file
                    if os.path.exists(temp_filepath):
                        os.unlink(temp_filepath)
                        logger.debug(f"Cleaned up temporary file: {temp_filepath}")
            
            elif 'binary' in data:
                logger.debug("Processing binary image data")
                # Handle binary data
                binary_data = data['binary']
                filename = data.get('filename', 'uploaded_image.png')
                
                logger.debug(f"Processing binary image with filename: {filename}")
                
                filename = secure_filename(filename)
                if not _is_image_file(filename):
                    logger.warning(f"Upload failed: File is not an image: {filename}")
                    return {"success": False, "error": "File must be an image"}, 400
                
                # Convert binary data to bytes if it's a string
                if isinstance(binary_data, str):
                    logger.debug("Converting string binary data to bytes")
                    binary_data = binary_data.encode('latin1')
                
                logger.debug(f"Binary data size: {len(binary_data)} bytes")
                
                # Create a properly named temporary file
                temp_dir = tempfile.gettempdir()
                temp_filepath = os.path.join(temp_dir, filename)
                
                logger.debug(f"Saving binary image to temporary file: {temp_filepath}")
                with open(temp_filepath, 'wb') as temp_file:
                    temp_file.write(binary_data)
                _optimize_image_for_web(temp_filepath)

                # Get just the file name without the path as the destination
                destination_filename = os.path.basename(temp_filepath)
                logger.debug(f"Destination filename for CDN upload: {destination_filename}")
                
                try:
                    # Upload to CDN using the properly named temp file
                    logger.info(f"Uploading binary image {filename} to CDN from {temp_filepath}")
                    cdn_url = upload_to_cdn("images", temp_filepath, destination_filename)
                    
                    logger.info(f"Successfully uploaded binary image to CDN: {cdn_url}")
                    return {"success": True, "url": cdn_url, "message": "Image uploaded successfully"}
                finally:
                    # Clean up temp file
                    if os.path.exists(temp_filepath):
                        os.unlink(temp_filepath)
                        logger.debug(f"Cleaned up temporary file: {temp_filepath}")
            
            else:
                logger.warning("Upload failed: Missing 'base64' or 'binary' field in JSON data")
                return {"success": False, "error": "Missing 'base64' or 'binary' field in JSON data"}, 400
        
        # Check if raw binary data is sent
        elif request.content_type and request.content_type.startswith('image/'):
            logger.debug(f"Processing raw binary image data with content-type: {request.content_type}")
            # Handle raw binary image data
            filename = f"uploaded_image_{uuid.uuid4().hex}.png"
            
            image_data = request.get_data()
            logger.debug(f"Received raw image data, size: {len(image_data)} bytes")
            
            # Create a properly named temporary file
            temp_dir = tempfile.gettempdir()
            temp_filepath = os.path.join(temp_dir, filename)
            
            logger.debug(f"Saving raw image to temporary file: {temp_filepath}")
            with open(temp_filepath, 'wb') as temp_file:
                temp_file.write(image_data)
            _optimize_image_for_web(temp_filepath)

            # Get just the file name without the path as the destination
            destination_filename = os.path.basename(temp_filepath)
            logger.debug(f"Destination filename for CDN upload: {destination_filename}")
            
            try:
                # Upload to CDN using the properly named temp file
                logger.info(f"Uploading raw image {filename} to CDN from {temp_filepath}")
                cdn_url = upload_to_cdn("images", temp_filepath, destination_filename)
                
                logger.info(f"Successfully uploaded raw image to CDN: {cdn_url}")
                return {
                    "success": True,
                    "url": cdn_url, 
                    "message": "Image uploaded successfully"
                    }
            finally:
                # Clean up temp file
                if os.path.exists(temp_filepath):
                    os.unlink(temp_filepath)
                    logger.debug(f"Cleaned up temporary file: {temp_filepath}")
        
        else:
            logger.warning(f"Upload failed: No valid image data found in request. Content-type: {request.content_type}")
            return {"success": False, "error": "No valid image data found in request"}, 400
            
    except Exception as e:
        logger.error(f"Unexpected error during image upload: {str(e)}", exc_info=True)
        return {"success": False, "error": "Failed to upload image"}, 500


def _is_image_file(filename):
    """Check if the filename has an image extension"""
    allowed_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
    is_image = any(filename.lower().endswith(ext) for ext in allowed_extensions)
    logger.debug(f"File extension check for {filename}: {'valid' if is_image else 'invalid'} image file")
    return is_image


def _optimize_image_for_web(filepath: str, max_dimension: int = 2048, jpeg_quality: int = 85) -> None:
    """Resize and compress an image in-place when it exceeds web-friendly limits.

    Uses Pillow (already in requirements.txt). Skips files that are already
    small (< 500 KB) and within max_dimension on both axes. On failure the
    original file is left untouched so the upload can still proceed.
    """
    from PIL import Image, ImageOps
    try:
        original_size = os.path.getsize(filepath)
        with Image.open(filepath) as _raw:
            # Apply EXIF orientation before anything else so portrait photos
            # from phones (which store pixels sideways + an EXIF rotation tag)
            # are not saved rotated after the tag is stripped on re-save.
            img = ImageOps.exif_transpose(_raw)
            needs_resize = img.width > max_dimension or img.height > max_dimension
            if not needs_resize and original_size < 500_000:
                return  # already web-friendly

            if needs_resize:
                img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

            ext = os.path.splitext(filepath)[1].lower()
            if ext in ('.jpg', '.jpeg'):
                if img.mode in ('RGBA', 'P', 'LA'):
                    img = img.convert('RGB')
                img.save(filepath, format='JPEG', quality=jpeg_quality, optimize=True)
            else:
                img.save(filepath, optimize=True)

        new_size = os.path.getsize(filepath)
        logger.info(
            f"_optimize_image_for_web: {original_size // 1024}KB → {new_size // 1024}KB"
        )
    except Exception as exc:
        logger.warning(f"_optimize_image_for_web failed for {filepath}, uploading original: {exc}")

