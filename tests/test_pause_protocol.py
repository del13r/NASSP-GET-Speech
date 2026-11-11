import pytest

from apollo_speaker.pause_protocol import PauseProtocolError, PauseRecordParser


def test_split_combined_and_heartbeat():
    parser = PauseRecordParser()
    assert list(parser.feed(b"1 5 ")) == []
    assert list(parser.feed(b"0\n1 5 0\n1 6 1\n1 7 0\n")) == [False, True, False]


@pytest.mark.parametrize("record", [
    b"1 2 0\n", b"1 0 1\n", b"1 1 0\n", b"2 1 1\n",
    b"1 18446744073709551616 1\n", b"1 0 2\n", b"x" * 25,
])
def test_invalid_stream_requires_new_snapshot(record):
    parser = PauseRecordParser()
    assert list(parser.feed(b"1 0 0\n")) == [False]
    with pytest.raises(PauseProtocolError):
        list(parser.feed(record))
    assert list(PauseRecordParser().feed(b"1 100 1\n")) == [True]
