# src/adapters/ari_gateway.py
"""Asterisk AMI implementation using Panoramisk."""
import logging

from panoramisk import Manager

from src.domain.ports import AriGateway
from src.domain.entities import Channel, ChannelState, OriginateResult, HangupResult
from src.domain.exceptions import AsteriskConnectionError

logger = logging.getLogger(__name__)


class PanoramiskAriGateway(AriGateway):
    """Concrete gateway to Asterisk over AMI using the Panoramisk client."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5038,
        username: str = "admin",
        secret: str = "admin",
    ):
        """Store AMI connection parameters.

        Args:
            host: Asterisk AMI host
            port: Asterisk AMI port
            username: AMI username
            secret: AMI secret/password
        """
        self.host = host
        self.port = port
        self.username = username
        self.secret = secret
        self._manager: Manager | None = None

    async def _connect(self) -> Manager:
        """Get or create the AMI connection.

        Raises:
            AsteriskConnectionError: If connection or login fails.
        """
        if self._manager is None:
            try:
                manager = Manager(
                    host=self.host,
                    port=int(self.port),
                    username=self.username,
                    secret=self.secret,
                )
                await manager.connect()
                # connect() schedules the Login action; wait for it to resolve
                # so callers don't race ahead of authentication.
                auth_future = getattr(manager, "authenticated_future", None)
                if auth_future is not None:
                    await auth_future
                self._manager = manager
            except Exception as e:
                logger.error(f"Failed to connect to Asterisk AMI: {e}")
                raise AsteriskConnectionError(f"AMI connection failed: {str(e)}")
        return self._manager

    async def list_channels(self) -> list[Channel]:
        """List all active channels from Asterisk.

        Returns:
            List of Channel entities

        Raises:
            AsteriskConnectionError: If Asterisk is unreachable.
        """
        try:
            manager = await self._connect()
            messages = await manager.send_action(
                {"Action": "CoreShowChannels"}, as_list=True
            )

            channels: list[Channel] = []
            for message in messages or []:
                if message.get("Event") != "CoreShowChannel":
                    continue
                try:
                    channels.append(self._event_to_channel(message))
                except Exception as e:
                    logger.warning(f"Failed to parse channel event: {e}")
                    continue
            return channels
        except AsteriskConnectionError:
            raise
        except Exception as e:
            logger.error(f"Error listing channels: {e}")
            raise AsteriskConnectionError(f"Failed to list channels: {str(e)}")

    async def originate(
        self, endpoint: str, context: str, exten: str
    ) -> OriginateResult:
        """Originate a new call.

        Args:
            endpoint: Destination channel (e.g., 'SIP/2000')
            context: Dial context
            exten: Extension to dial

        Returns:
            OriginateResult with channel info and status
        """
        try:
            manager = await self._connect()
            response = await manager.send_action(
                {
                    "Action": "Originate",
                    "Channel": endpoint,
                    "Context": context,
                    "Exten": exten,
                    "Priority": "1",
                    "CallerID": exten or endpoint,
                    "Async": "true",
                }
            )

            return OriginateResult(
                channel_id=response.get("Channel", "unknown"),
                channel_name=endpoint,
                status="success" if response.success else response.get("Message", "error"),
            )
        except Exception as e:
            logger.error(f"Originate failed: {e}")
            return OriginateResult(
                channel_id="unknown",
                channel_name=endpoint,
                status=f"error: {str(e)}",
            )

    async def hangup(self, channel_id: str) -> HangupResult:
        """Hangup a channel.

        Args:
            channel_id: Channel name/ID to hangup

        Returns:
            HangupResult with status
        """
        try:
            manager = await self._connect()
            response = await manager.send_action(
                {"Action": "Hangup", "Channel": channel_id}
            )
            return HangupResult(
                channel_id=channel_id,
                status="success" if response.success else response.get("Message", "error"),
            )
        except Exception as e:
            logger.error(f"Hangup failed for {channel_id}: {e}")
            return HangupResult(
                channel_id=channel_id,
                status=f"error: {str(e)}",
            )

    def _event_to_channel(self, event) -> Channel:
        """Convert an AMI ``CoreShowChannel`` event to a Channel entity."""
        state_desc = event.get("ChannelStateDesc", "") or ""
        try:
            state = ChannelState(state_desc)
        except ValueError:
            state = ChannelState.DOWN

        return Channel(
            id=event.get("Uniqueid") or event.get("Channel", "unknown"),
            name=event.get("Channel", "unknown"),
            state=state,
            caller_id_num=event.get("CallerIDNum", ""),
            caller_id_name=event.get("CallerIDName", ""),
            connected_line_num=event.get("ConnectedLineNum") or None,
            connected_line_name=event.get("ConnectedLineName") or None,
            language=event.get("Language", "en") or "en",
            accountcode=event.get("AccountCode", ""),
        )
