"""
Message handler with end-to-end encryption integration.

Outgoing messages require:
  - a CryptoContext (crypto_ctx is not None), AND
  - the recipient's public key pinned in the local DB.

Incoming messages are decrypted transparently when the payload contains
the 'ephemeral_pubkey' field that signals E2E encryption.
"""

import logging
from typing import Callable, Awaitable

from shared.protocol import MsgType, make_packet
from shared.utils import new_id, now
from client.db.messages import (
    save,
    get_history,
    mark_delivered,
    mark_read_by_id,
    edit_message,
    delete_message,
)
from client.db.contacts import add_contact, get_public_key
from client.crypto.encrypt import encrypt_message, decrypt_message

log = logging.getLogger("ct.client.msg")


class MessageHandler:
    """Handles sending, receiving, editing, and deleting direct messages.

    Args:
        ws_client:      Connected WSClient instance.
        self_user:      Local username.
        crypto_ctx:     CryptoContext with at minimum a ``private_key`` bytes
                        attribute.  Pass None to disable encryption (legacy).
        on_new_message: Optional async callback invoked with the raw packet
                        dict whenever a new incoming message is processed.
    """

    def __init__(
        self,
        ws_client,
        self_user: str,
        crypto_ctx=None,
        on_new_message: Callable[[dict], Awaitable[None]] | None = None,
    ):
        self._ws       = ws_client
        self.me        = self_user
        self._ctx      = crypto_ctx
        self._callback = on_new_message

    # -----------------------------------------------------------------------
    # Outbound
    # -----------------------------------------------------------------------

    async def send(self, to_user: str, text: str) -> str:
        """Send a direct message, encrypting when possible, plaintext otherwise.

        Args:
            to_user: Recipient's username.
            text:    Plaintext message body.

        Returns:
            The newly created message ID (UUID string).
        """
        mid = new_id()
        ts  = now()

        if self._ctx is not None:
            pubkey_b64 = await get_public_key(to_user)
            if pubkey_b64:
                import base64
                recipient_pubkey = base64.b64decode(pubkey_b64)
                payload = encrypt_message(text, recipient_pubkey)
                payload["encrypted"] = True
            else:
                payload = {"text": text}
        else:
            payload = {"text": text}

        await self._ws.send(
            make_packet(
                MsgType.MESSAGE,
                id=mid,
                **{"from": self.me},
                to=to_user,
                payload=payload,
                timestamp=ts,
            )
        )

        await save(mid, self.me, to_user, text, ts, delivered=0)
        await add_contact(to_user)
        return mid

    # -----------------------------------------------------------------------
    # Inbound
    # -----------------------------------------------------------------------

    async def handle_incoming(self, pkt: dict) -> None:
        """Process an incoming MESSAGE packet.

        Decrypts the payload when it contains an ephemeral public key and a
        CryptoContext with a private key is available.  Saves the message to
        the local DB and fires the new-message callback.

        Args:
            pkt: Parsed packet dict from the server.
        """
        sender  = pkt.get("from", "")
        payload = pkt.get("payload", {})
        mid     = pkt.get("id") or new_id()
        ts      = pkt.get("timestamp") or now()

        # Determine plaintext
        if payload.get("ephemeral_pubkey") and self._ctx is not None:
            try:
                text = decrypt_message(payload, self._ctx.private_key)
            except Exception as exc:
                log.warning("Failed to decrypt message %s from %s: %s", mid, sender, exc)
                text = "[encrypted — decryption failed]"
        elif payload.get("ephemeral_pubkey"):
            text = "[encrypted — locked]"
        else:
            text = payload.get("text", "")

        if sender:
            await save(mid, sender, self.me, text, ts, delivered=1)
            await add_contact(sender)
            if self._callback:
                # Inject the resolved plaintext so callers don't need to decrypt again
                enriched = dict(pkt)
                enriched["_plaintext"] = text
                await self._callback(enriched)

    async def handle_delivery(self, pkt: dict) -> None:
        msg_id = pkt.get("msg_id")
        if msg_id and pkt.get("status") in {"delivered", "queued"}:
            await mark_delivered(msg_id)

    async def handle_read(self, pkt: dict) -> None:
        msg_id = pkt.get("msg_id")
        if msg_id:
            await mark_read_by_id(msg_id)

    async def handle_edit(self, pkt: dict) -> None:
        msg_id = pkt.get("msg_id")
        payload = pkt.get("payload", {})
        if msg_id:
            if payload.get("ephemeral_pubkey") and self._ctx is not None:
                text = decrypt_message(payload, self._ctx.private_key)
            elif payload.get("ephemeral_pubkey"):
                text = "[encrypted — locked]"
            else:
                text = payload.get("text", "")
            await edit_message(msg_id, text)

    async def handle_delete(self, pkt: dict) -> None:
        msg_id = pkt.get("msg_id")
        if msg_id:
            await delete_message(msg_id)

    # -----------------------------------------------------------------------
    # Edit / delete
    # -----------------------------------------------------------------------

    async def edit(self, msg_id: str, to_user: str, new_text: str) -> None:
        """Send a MESSAGE_EDIT request, encrypting when possible.

        Args:
            msg_id:   ID of the message to edit.
            to_user:  Original recipient (needed for server routing).
            new_text: Replacement message text.
        """
        if self._ctx is not None:
            import base64
            pubkey_b64 = await get_public_key(to_user)
            if pubkey_b64:
                payload = encrypt_message(new_text, base64.b64decode(pubkey_b64))
                payload["encrypted"] = True
            else:
                payload = {"text": new_text}
        else:
            payload = {"text": new_text}

        await self._ws.send(
            make_packet(
                MsgType.MESSAGE_EDIT,
                msg_id=msg_id,
                **{"from": self.me},
                to=to_user,
                payload=payload,
                timestamp=now(),
            )
        )
        await edit_message(msg_id, new_text)

    async def delete(self, msg_id: str, to_user: str) -> None:
        """Send a MESSAGE_DELETE request to retract a message.

        Args:
            msg_id:  ID of the message to delete.
            to_user: Original recipient (needed for server routing).
        """
        await self._ws.send(
            make_packet(
                MsgType.MESSAGE_DELETE,
                msg_id=msg_id,
                **{"from": self.me},
                to=to_user,
                timestamp=now(),
            )
        )
        await delete_message(msg_id)

    # -----------------------------------------------------------------------
    # History
    # -----------------------------------------------------------------------

    async def history(self, peer: str) -> list[dict]:
        """Return the local message history with a peer.

        Args:
            peer: The other user's username.

        Returns:
            List of message row dicts ordered chronologically (oldest first).
        """
        return await get_history(peer, self.me)
