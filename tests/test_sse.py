import random

import pytest

from toolcall_contract.sse import Frame, SSEDecoder


@pytest.mark.parametrize("seed", range(30))
def test_arbitrary_byte_boundaries(seed):
    wire = '\ufeff: comment\r\nevent: tool\r\ndata: {"city":\r\ndata: "北京🧪"}\r\n\r\ndata: [DONE]\n\n'.encode()
    randomizer = random.Random(seed)
    decoder, frames, pos = SSEDecoder(), [], 0
    while pos < len(wire):
        size = randomizer.randint(1, 9)
        frames.extend(decoder.feed(wire[pos : pos + size]))
        pos += size
    frames.extend(decoder.feed(b"", final=True))
    assert frames == [Frame("tool", '{"city":\n"北京🧪"}'), Frame("message", "[DONE]")]


@pytest.mark.parametrize("newline", [b"\r", b"\n", b"\r\n"])
def test_newline_variants(newline):
    decoder = SSEDecoder()
    assert decoder.feed(b"data: x" + newline * 2, final=True) == [Frame("message", "x")]


@pytest.mark.parametrize("raw", [b'data: {"x":1}', b"data: x\n", b"data: \xff\n\n", b"data: \xe5"])
def test_truncation_and_invalid_utf8(raw):
    with pytest.raises(ValueError):
        SSEDecoder().feed(raw, final=True)


def test_oversized_event():
    with pytest.raises(ValueError):
        SSEDecoder(10).feed(b"data: " + b"x" * 20)


def test_comments_do_not_create_events():
    assert SSEDecoder().feed(b": ping\n\nid: 1\nretry: 100\n\n", final=True) == []
