"""Incremental SSE parser: byte boundaries are unrelated to UTF-8 or event boundaries."""

import codecs
from dataclasses import dataclass


@dataclass(frozen=True)
class Frame:
    event: str
    data: str


class SSEDecoder:
    def __init__(self, max_event_bytes: int = 1_048_576):
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        self.buffer = ""
        self.data: list[str] = []
        self.event = "message"
        self.size = 0
        self.limit = max_event_bytes

    def feed(self, chunk: bytes, final: bool = False) -> list[Frame]:
        self.buffer += self.decoder.decode(chunk, final=final)
        frames = []
        while self.buffer:
            positions = [p for p in (self.buffer.find("\n"), self.buffer.find("\r")) if p >= 0]
            if not positions:
                break
            pos = min(positions)
            if self.buffer[pos] == "\r" and pos == len(self.buffer) - 1 and not final:
                break
            width = 2 if self.buffer[pos : pos + 2] == "\r\n" else 1
            line, self.buffer = self.buffer[:pos], self.buffer[pos + width :]
            self.size += len(line.encode("utf-8")) + width
            if self.size > self.limit:
                raise ValueError("SSE event exceeds configured limit")
            if line == "":
                if self.data:
                    frames.append(Frame(self.event, "\n".join(self.data)))
                self.data, self.event, self.size = [], "message", 0
            elif not line.startswith(":"):
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "data":
                    self.data.append(value)
                elif field == "event":
                    self.event = value
        if self.size + len(self.buffer.encode("utf-8")) > self.limit:
            raise ValueError("SSE event exceeds configured limit")
        if final and (self.buffer or self.data):
            raise ValueError("SSE ended inside an unfinished event")
        return frames
