"""Generic object storage — bytes in, reference out (DEMO-UX-1).

Extracted from `core/channels/whatsapp/media.py`, where a perfectly generic S3 client had ended up
living inside a channel adapter. Catalog images need the same primitive, and a catalog importing
from `channels.whatsapp` would say something false about the architecture: the catalog does not
depend on WhatsApp, and a later channel must not inherit a catalog dependency either.

Dependency direction is now:

    core.media  ->  core.channels.whatsapp.media   (inbound message attachments)
                ->  core.catalog.media             (merchant product photographs)

Deliberately small. This is a storage primitive, not a media platform: put bytes somewhere, read
them back, delete them. Scanning, image processing, authorization and lifecycle belong to the
callers, which have different rules — an inbound customer attachment is hostile input to be
quarantined, a merchant's own product photograph is not.
"""

from core.media.store import (
    MediaStore,
    S3Store,
    SimulatedStore,
    default_store,
    object_key,
)

__all__ = ["MediaStore", "S3Store", "SimulatedStore", "default_store", "object_key"]
