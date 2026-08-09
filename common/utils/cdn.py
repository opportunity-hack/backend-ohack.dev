import logging
from google.cloud import storage
from google.oauth2 import service_account
from dotenv import load_dotenv
import os
import sys
import logging
import json

# add logger
logger = logging.getLogger(__name__)
# set logger to standard out
logger.addHandler(logging.StreamHandler())
# set log level
logger.setLevel(logging.INFO)

from datetime import datetime


sys.path.append("../")
load_dotenv()


CDN_SERVER = os.getenv("CDN_SERVER")
GCLOUD_CDN_BUCKET = os.getenv("GCLOUD_CDN_BUCKET")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

def _get_bucket():
    """Storage bucket handle using the service-account credentials."""
    gcp_json_credentials_dict = json.loads(GOOGLE_APPLICATION_CREDENTIALS)
    creds = service_account.Credentials.from_service_account_info(gcp_json_credentials_dict)
    project_name = GCLOUD_CDN_BUCKET.split("_")[0]
    storage_client = storage.Client(project=project_name, credentials=creds)
    return storage_client.bucket(GCLOUD_CDN_BUCKET)


def generate_signed_upload_url(directory, filename, content_type, max_bytes, expiration_minutes=15):
    """V4 signed PUT URL so large files (videos) upload straight to GCS.

    Never proxy video bytes through Flask — a multi-MB body pins a worker
    thread + RAM for the whole transfer on our small Fly box.

    The x-goog-content-length-range header makes GCS itself enforce the size
    cap; the client MUST send the same header on the PUT or the signature
    check fails.
    """
    from datetime import timedelta

    bucket = _get_bucket()
    blob = bucket.blob(f"{directory}/{filename}")
    signed_url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(minutes=expiration_minutes),
        method="PUT",
        content_type=content_type,
        headers={"x-goog-content-length-range": f"0,{max_bytes}"},
    )
    return {
        "upload_url": signed_url,
        "blob_path": f"{directory}/{filename}",
        "final_url": f"{CDN_SERVER}/{directory}/{filename}",
        "required_headers": {
            "Content-Type": content_type,
            "x-goog-content-length-range": f"0,{max_bytes}",
        },
    }


def get_blob_metadata(path):
    """{exists, size, content_type} for a blob path like 'users/<id>/video.mp4'."""
    bucket = _get_bucket()
    blob = bucket.get_blob(path)
    if blob is None:
        return {"exists": False, "size": None, "content_type": None}
    return {"exists": True, "size": blob.size, "content_type": blob.content_type}


def delete_from_cdn(path):
    """Best-effort delete of a blob path. Returns True when deleted."""
    try:
        bucket = _get_bucket()
        blob = bucket.blob(path)
        if blob.exists():
            blob.delete()
            logger.info(f"Deleted {path} from CDN bucket")
            return True
    except Exception as e:
        logger.warning(f"Failed to delete {path} from CDN bucket: {e}")
    return False


def upload_to_cdn(directory, source_file_name, destination_file_name=None):
    """Uploads a file to the bucket."""
    bucket = _get_bucket()

    # Use destination_file_name if provided, otherwise use source_file_name
    blob_filename = destination_file_name if destination_file_name else source_file_name
    blob = bucket.blob(f"{directory}/{blob_filename}")

    # Optional: set a generation-match precondition to avoid potential race conditions
    # and data corruptions. The request to upload is aborted if the object's
    # generation number does not match your precondition. For a destination
    # object that does not yet exist, set the if_generation_match precondition to 0.
    # If the destination object already exists in your bucket, set instead a
    # generation-match precondition using its generation number.
    # generation_match_precondition = 0 # Don't use this because we want to overwrite files

    # See if file already exists
    if blob.exists():
        logger.info(f"File {source_file_name} already exists in {directory}/{blob_filename}. Overwriting...")
    else:
        logger.info(f"File {source_file_name} does not exist in {directory}/{blob_filename}. Uploading...")
    
    # Upload the file
    blob.upload_from_filename(source_file_name)

    logger.info(
        f"File {source_file_name} uploaded to {directory}/{blob_filename}."
    )

    return f"{CDN_SERVER}/{directory}/{blob_filename}"