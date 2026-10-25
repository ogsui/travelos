"""Channel adapter implementations."""

from travelos.channels.adapters.email import (
    EmailChannelAdapter,
    EmailSendBlockedError,
    EmailTransport,
    MailpitTransport,
    NullTransport,
    SMTPTransport,
    SMTPTransportConfig,
    parse_raw_email,
)
from travelos.channels.adapters.mock import (
    ChannelAdapter,
    MockChannelAdapter,
    OutboundDisabledError,
)

__all__ = [
    "ChannelAdapter",
    "EmailChannelAdapter",
    "EmailSendBlockedError",
    "EmailTransport",
    "MailpitTransport",
    "MockChannelAdapter",
    "NullTransport",
    "OutboundDisabledError",
    "SMTPTransport",
    "SMTPTransportConfig",
    "parse_raw_email",
]
