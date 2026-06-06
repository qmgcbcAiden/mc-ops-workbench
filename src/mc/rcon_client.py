from __future__ import annotations

import socket
import struct
import time


class RCONError(Exception):
    pass


class RCONClient:
    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        timeout: float = 5.0,
    ) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection(
                (self._host, self._port), timeout=self._timeout
            )
        except OSError as exc:
            self._sock = None
            raise RCONError(f"RCON 连接失败 {self._host}:{self._port}: {exc}") from exc
        try:
            self._authenticate()
        except RCONError:
            self.close()
            raise
        except OSError as exc:
            self.close()
            raise RCONError(f"RCON 认证失败: {exc}") from exc

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def command(self, cmd: str) -> str:
        if self._sock is None:
            raise RCONError("RCON 未连接")
        self._send(2, cmd)
        return self._recv_response()

    def __enter__(self) -> RCONClient:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _authenticate(self) -> None:
        self._send(3, self._password)
        _request_id, resp_type, payload = self._recv_packet(expect_type=2)
        if resp_type != 2:
            raise RCONError(f"RCON 登录失败: 意外的响应类型 {resp_type}")
        if payload:
            raise RCONError(f"RCON 登录被拒: {payload}")

    def _send(self, msg_type: int, payload: str) -> None:
        assert self._sock is not None
        data = payload.encode("utf-8") + b"\x00\x00"
        request_id = int(time.monotonic() * 1000) & 0x7FFFFFFF
        packet = struct.pack("<iii", 4 + 4 + len(data), request_id, msg_type) + data
        try:
            self._sock.sendall(packet)
        except OSError as exc:
            raise RCONError(f"RCON 发送失败: {exc}") from exc

    def _recv_packet(self, expect_type: int) -> tuple[int, int, str]:
        assert self._sock is not None
        header = self._recv_exact(4)
        if len(header) < 4:
            raise RCONError("RCON 响应头不完整")
        length = struct.unpack("<i", header)[0]
        body = self._recv_exact(length)
        if len(body) < 8:
            raise RCONError("RCON 响应体不完整")
        request_id = struct.unpack("<i", body[0:4])[0]
        resp_type = struct.unpack("<i", body[4:8])[0]
        payload_str = body[8:].rstrip(b"\x00").decode("utf-8", errors="replace")
        if resp_type != expect_type:
            raise RCONError(
                f"RCON 响应类型不匹配 (期望 {expect_type}, 收到 {resp_type}): {payload_str}"
            )
        return request_id, resp_type, payload_str

    def _recv_response(self) -> str:
        assert self._sock is not None
        parts: list[str] = []
        while True:
            _req_id, _resp_type, payload = self._recv_packet(expect_type=0)
            parts.append(payload)
            if len(payload) < 4096:
                break
        return "".join(parts)

    def _recv_exact(self, size: int) -> bytes:
        assert self._sock is not None
        buf = b""
        deadline = time.monotonic() + self._timeout
        while len(buf) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RCONError("RCON 读取超时")
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(size - len(buf))
            except socket.timeout:
                raise RCONError("RCON 读取超时")
            except OSError as exc:
                raise RCONError(f"RCON 连接错误: {exc}") from exc
            if not chunk:
                raise RCONError("RCON 连接已关闭")
            buf += chunk
        return buf


def send_rcon_command(
    host: str,
    port: int,
    password: str,
    command: str,
    timeout: float = 5.0,
) -> str:
    with RCONClient(host, port, password, timeout=timeout) as client:
        return client.command(command)
