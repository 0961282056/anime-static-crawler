"""Typed errors used to keep failures out of the public data contract."""


class CrawlerError(RuntimeError):
    """Base class for expected crawler failures."""


class ConfigurationError(CrawlerError):
    """Required configuration is missing or invalid."""


class SourceFetchError(CrawlerError):
    """The source page could not be fetched safely."""


class SourceNotFoundError(SourceFetchError):
    """The requested future season does not exist yet."""


class ItemParseError(CrawlerError):
    """One source card could not be parsed."""


class SelectorCanaryError(CrawlerError):
    """The live source page no longer matches the parser contract."""


class ImageStoreError(CrawlerError):
    """An image could not be downloaded or stored safely."""


class QuotaExceededError(ImageStoreError):
    """Cloudinary quota is over the configured safe threshold."""


class DataContractError(CrawlerError):
    """A dataset failed schema or quality validation."""


class NotificationError(CrawlerError):
    """A configured operational notification could not be delivered."""


class RetentionError(CrawlerError):
    """A manual retention operation could not be completed safely."""
