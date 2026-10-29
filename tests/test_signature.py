import pytest

from app.signature import SignatureError, build_header, verify

SECRET = "whsec_abc"
BODY = b'{"id":"evt_1"}'


def test_valid_signature_passes():
    verify(BODY, build_header(BODY, SECRET, timestamp=1000), SECRET, tolerance=300, now=1100)


def test_tampered_body_fails():
    header = build_header(BODY, SECRET, timestamp=1000)
    with pytest.raises(SignatureError, match="mismatch"):
        verify(b'{"id":"evt_2"}', header, SECRET, tolerance=300, now=1000)


def test_wrong_secret_fails():
    header = build_header(BODY, "whsec_other", timestamp=1000)
    with pytest.raises(SignatureError, match="mismatch"):
        verify(BODY, header, SECRET, tolerance=300, now=1000)


def test_old_timestamp_is_rejected_as_replay():
    header = build_header(BODY, SECRET, timestamp=1000)
    with pytest.raises(SignatureError, match="tolerance"):
        verify(BODY, header, SECRET, tolerance=300, now=1000 + 301)


def test_any_matching_v1_is_accepted_during_secret_rotation():
    good = build_header(BODY, SECRET, timestamp=1000).split(",")[1]
    verify(BODY, f"t=1000,v1=deadbeef,{good}", SECRET, tolerance=300, now=1000)


@pytest.mark.parametrize("header", [None, "", "garbage", "t=abc,v1=123", "t=1000"])
def test_malformed_headers_fail(header):
    with pytest.raises(SignatureError):
        verify(BODY, header, SECRET, tolerance=300, now=1000)
