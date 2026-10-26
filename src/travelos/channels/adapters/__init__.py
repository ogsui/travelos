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
from travelos.channels.adapters.whatsapp import (
    CloudAPIWhatsAppTransport,
    MockWhatsAppTransport,
    WhatsAppChannelAdapter,
    WhatsAppPayloadError,
    WhatsAppSendBlockedError,
    WhatsAppTransport,
    is_approved_template,
    is_within_24h_window,
    parse_whatsapp_webhook,
    verify_meta_signature,
    verify_webhook_challenge,
)

__all__ = [
    "ChannelAdapter",
    "CloudAPIWhatsAppTransport",
    "EmailChannelAdapter",
    "EmailSendBlockedError",
    "EmailTransport",
    "MailpitTransport",
    "MockChannelAdapter",
    "MockWhatsAppTransport",
    "NullTransport",
    "OutboundDisabledError",
    "SMTPTransport",
    "SMTPTransportConfig",
    "WhatsAppChannelAdapter",
    "WhatsAppPayloadError",
    "WhatsAppSendBlockedError",
    "WhatsAppTransport",
    "is_approved_template",
    "is_within_24h_window",
    "parse_raw_email",
    "parse_whatsapp_webhook",
    "verify_meta_signature",
    "verify_webhook_challenge",
]
