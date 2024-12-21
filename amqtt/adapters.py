from asyncio import StreamReader, StreamWriter
from contextlib import suppress
import io
import logging
from typing import Any

from websockets import ConnectionClosed
from websockets.legacy.protocol import WebSocketCommonProtocol


class ReaderAdapter:
    """Base class for all network protocol reader adapters.

    Reader adapters are used to adapt read operations on the network depending on the
    protocol used.
    """

    async def read(self, n: int = -1) -> bytes:
        """Read up to n bytes. If n is not provided, or set to -1, read until EOF and return all read bytes.

        If the EOF was received and the internal buffer is
        empty, return an empty bytes object. :return: packet read as bytes data.
        """
        raise NotImplementedError

    def feed_eof(self) -> None:
        """Acknowledge EOF."""
        raise NotImplementedError


class WriterAdapter:
    """Base class for all network protocol writer adapters.

    Writer adapters are used to adapt write operations on the network depending on
    the protocol used.
    """

    def write(self, data: bytes) -> None:
        """Write some data to the protocol layer."""
        raise NotImplementedError

    async def drain(self) -> None:
        """Let the write buffer of the underlying transport a chance to be flushed."""
        raise NotImplementedError

    def get_peer_info(self) -> Any | tuple[Any, Any]:
        """Return peer socket info (remote address and remote port as tuple)."""
        raise NotImplementedError

    async def close(self) -> None:
        """Close the protocol connection."""
        raise NotImplementedError


class WebSocketsReader(ReaderAdapter):
    """WebSockets API reader adapter.

    This adapter relies on WebSocketCommonProtocol to read from a WebSocket.
    """

    def __init__(self, protocol: WebSocketCommonProtocol) -> None:
        self._protocol = protocol
        self._stream = io.BytesIO(b"")

    async def read(self, n: int = -1) -> bytes:
        await self._feed_buffer(n)
        return self._stream.read(n)

    async def _feed_buffer(self, n: int = 1) -> None:
        """Feed the data buffer by reading a WebSocket message.

        :param n: if given, feed buffer until it contains at least n bytes.
        """
        buffer = bytearray(self._stream.read())
        while len(buffer) < n:
            try:
                message = await self._protocol.recv()
            except ConnectionClosed:
                message = None
            if message is None:
                break
            if not isinstance(message, bytes):
                msg = "message must be bytes"
                raise TypeError(msg)
            buffer.extend(message)
        self._stream = io.BytesIO(buffer)


class WebSocketsWriter(WriterAdapter):
    """WebSockets API writer adapter.

    This adapter relies on WebSocketCommonProtocol to write to a WebSocket.
    """

    def __init__(self, protocol: WebSocketCommonProtocol) -> None:
        self._protocol = protocol
        self._stream = io.BytesIO(b"")

    def write(self, data: bytes) -> None:
        """Write some data to the protocol layer."""
        self._stream.write(data)

    async def drain(self) -> None:
        """Let the write buffer of the underlying transport a chance to be flushed."""
        data = self._stream.getvalue()
        if data and len(data):
            await self._protocol.send(data)
        self._stream = io.BytesIO(b"")

    def get_peer_info(self) -> Any:
        # remote_address can be either a 4-tuple or 2-tuple depending on whether
        # it is an IPv6 or IPv4 address, so we take their shared (host, port)
        # prefix here to present a uniform return value.
        return self._protocol.remote_address[:2]

    async def close(self) -> None:
        await self._protocol.close()


class StreamReaderAdapter(ReaderAdapter):
    """Asyncio Streams API protocol adapter.

    This adapter relies on StreamReader to read from a TCP socket.
    Because API is very close, this class is trivial.
    """

    def __init__(self, reader: StreamReader) -> None:
        self._reader = reader

    async def read(self, n: int = -1) -> bytes:
        if n == -1:
            data = await self._reader.read(n)
        else:
            data = await self._reader.readexactly(n)
        return data

    def feed_eof(self) -> None:
        self._reader.feed_eof()


class StreamWriterAdapter(WriterAdapter):
    """Asyncio Streams API protocol adapter.

    This adapter relies on StreamWriter to write to a TCP socket.
    Because API is very close, this class is trivial.
    """

    def __init__(self, writer: StreamWriter) -> None:
        self.logger = logging.getLogger(__name__)
        self._writer = writer
        self.is_closed = False  # StreamWriter has no test for closed...we use our own

    def write(self, data: bytes) -> None:
        if not self.is_closed:
            self._writer.write(data)

    async def drain(self) -> None:
        if not self.is_closed:
            await self._writer.drain()

    def get_peer_info(self) -> tuple[str, int]:
        extra_info = self._writer.get_extra_info("peername")
        return extra_info[0], extra_info[1]

    async def close(self) -> None:
        if not self.is_closed:
            self.is_closed = True  # we first mark this closed so yields below don't cause races with waiting writes
            await self._writer.drain()
            if self._writer.can_write_eof():
                self._writer.write_eof()
            self._writer.close()
            with suppress(AttributeError):
                await self._writer.wait_closed()


class BufferReader(ReaderAdapter):
    """Byte Buffer reader adapter.

    This adapter simply adapts reading a byte buffer.
    """

    def __init__(self, buffer: bytes) -> None:
        self._stream = io.BytesIO(buffer)

    async def read(self, n: int = -1) -> bytes:
        return self._stream.read(n)


class BufferWriter(WriterAdapter):
    """ByteBuffer writer adapter.

    This adapter simply adapts writing to a byte buffer.
    """

    def __init__(self, buffer: bytes = b"") -> None:
        self._stream = io.BytesIO(buffer)

    def write(self, data: bytes) -> None:
        """Write some data to the protocol layer."""
        self._stream.write(data)

    async def drain(self) -> None:
        pass

    def get_buffer(self) -> bytes:
        return self._stream.getvalue()

    def get_peer_info(self) -> tuple[str, int]:
        return "BufferWriter", 0

    async def close(self) -> None:
        self._stream.close()
