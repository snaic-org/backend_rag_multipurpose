"""Re-index the Contentful content once after a deploy changes how it is indexed.

CMS entries are turned into text when they are ingested, so a change to that
text (contentful.CMS_INDEX_VERSION) only reaches the chatbot after a re-ingest.
This runs it on startup instead of relying on someone calling POST /ingest/cms.
"""

from uuid import UUID

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.schemas import AuthenticatedUser, CmsIngestRequest
from app.services import contentful
from app.services.ingest_service import IngestService

logger = get_logger(__name__)

_VERSION_KEY = "cms_index_version"
_LOCK_KEY = "cms_index_sync_lock"
# Long enough for a full CMS ingest; the lock expires if a task dies mid-sync.
_LOCK_SECONDS = 15 * 60
_SYSTEM_USER = AuthenticatedUser(id=UUID(int=0), username="system", is_admin=True, auth_type="bearer")


async def sync_cms_if_outdated(settings: Settings, redis_client: Redis, ingest_service: IngestService) -> bool:
    """Re-ingest all CMS content if it was indexed by an older format.

    Returns True when a re-ingest ran and fully succeeded. Safe to run on every
    task at once: a Redis lock lets only one of them do the work.
    """
    if not ((settings.contentful_space_id or "").strip() and (settings.contentful_delivery_token or "").strip()):
        return False
    if await redis_client.get(_VERSION_KEY) == contentful.CMS_INDEX_VERSION:
        return False
    if not await redis_client.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_SECONDS):
        return False

    try:
        logger.info("cms_sync_started version=%s", contentful.CMS_INDEX_VERSION)
        result = await ingest_service.ingest_cms(
            CmsIngestRequest(content_types=[], force_reingest=True),
            current_user=_SYSTEM_USER,
        )
        if result.failed:
            # Leave the version unset so the next startup tries again.
            logger.warning("cms_sync_incomplete failed=%d succeeded=%d", result.failed, result.succeeded)
            return False
        await redis_client.set(_VERSION_KEY, contentful.CMS_INDEX_VERSION)
        logger.info(
            "cms_sync_complete version=%s documents=%d chunks=%d",
            contentful.CMS_INDEX_VERSION,
            result.succeeded,
            result.total_chunks_inserted,
        )
        return True
    except Exception as exc:
        logger.warning("cms_sync_failed error=%r", exc)
        return False
    finally:
        await redis_client.delete(_LOCK_KEY)
