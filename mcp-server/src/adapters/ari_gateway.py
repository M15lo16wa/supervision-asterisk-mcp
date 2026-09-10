# src/adapters/ari_gateway.py
"""Asterisk ARI implementation using Panoramisk."""
import os
import logging
from panoramisk import AmiClient
from src.domain.ports import AriGateway
from src.domain.entities import Channel, ChannelState, OriginateResult, HangupResult
from src.domain.exceptions import AsteriskConnectionError, ChannelNotFound

logger = logging.getLogger(__name__)


class PanoramiskAriGateway(AriGateway):
    """Concrete ARI implementation using Panoramisk AMI client."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5038,
        username: str = "admin",
        secret: str = "admin",
    ):
        """Initialize AMI client connection parameters.
        
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
        self._client: AmiClient | None = None

    async def _connect(self) -> AmiClient:
        """Get or create AMI client connection.
        
        Raises:
            AsteriskConnectionError: If connection fails.
        """
        if self._client is None:
            try:
                self._client = AmiClient(
                    loop=None,
                    host=self.host,
                    port=self.port,
                    username=self.username,
                    secret=self.secret,
                )
                await self._client.connect()
            except Exception as e:
                logger.error(f"Failed to connect to Asterisk AMI: {e}")
                raise AsteriskConnectionError(f"AMI connection failed: {str(e)}")
        return self._client

    async def list_channels(self) -> list[Channel]:
        """List all active channels from Asterisk.
        
        Returns:
            List of Channel entities
            
        Raises:
            AsteriskConnectionError: If Asterisk is unreachable.
        """
        try:
            client = await self._connect()
            response = await client.send_command("CoreShowChannels")

            channels = []
            if "Events" in response:
                for event in response["Events"]:
                    try:
                        channel = self._event_to_channel(event)
                        channels.append(channel)
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
            endpoint: Destination endpoint (e.g., 'SIP/2000')
            context: Dial context
            exten: Extension to dial
            
        Returns:
            OriginateResult with channel info and status
            
        Raises:
            AsteriskConnectionError: If operation fails.
        """
        try:
            client = await self._connect()
            response = await client.send_command(
                "Originate",
                Channel=endpoint,
                Context=context,
                Exten=exten,
                Priority="1",
                CallerID="<1000>",
                Async="true",
            )

            status = response.get("Response", "Error")
            channel_id = response.get("Channel", "unknown")

            return OriginateResult(
                channel_id=channel_id,
                channel_name=endpoint,
                status="success" if status == "Success" else status,
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
            channel_id: Channel ID to hangup
            
        Returns:
            HangupResult with status
            
        Raises:
            ChannelNotFound: If channel doesn't exist.
            AsteriskConnectionError: If operation fails.
        """
        try:
            client = await self._connect()
            response = await client.send_command(
                "Hangup",
                Channel=channel_id,
            )

            status = response.get("Response", "Error")
            return HangupResult(
                channel_id=channel_id,
                status="success" if status == "Success" else status,
            )
        except Exception as e:
            logger.error(f"Hangup failed for {channel_id}: {e}")
            return HangupResult(
                channel_id=channel_id,
                status=f"error: {str(e)}",
            )

    def _event_to_channel(self, event: dict) -> Channel:
        """Convert AMI event to Channel entity.
        
        Args:
            event: AMI CoreShowChannelsComplete event
            
        Returns:
            Channel entity
        """
        state_str = event.get("ChannelState", "0")
        state_map = {v.value.lower(): k for k, v in ChannelState.__members__.items()}
        state = state_map.get(state_str.lower(), ChannelState.DOWN)

        return Channel(
            id=event.get("Channel", "unknown"),
            name=event.get("Channel", "unknown"),
            state=state,
            caller_id_num=event.get("CallerIDNum", ""),
            caller_id_name=event.get("CallerIDName", ""),
            connected_line_num=event.get("ConnectedLineNum", None),
            connected_line_name=event.get("ConnectedLineName", None),
            language=event.get("Language", "en"),
            accountcode=event.get("AccountCode", ""),
        )
