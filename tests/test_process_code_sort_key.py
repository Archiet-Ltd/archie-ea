"""iso06e: the natural sort key for APQC process codes."""

from app.models.apqc_process import process_code_sort_key as key


def test_numeric_segments_compare_as_numbers():
    assert key("10.0") > key("9.9")
    assert key("1.1") < key("1.2") < key("2.1") < key("10.0") < key("10.2a")


def test_letters_and_mixed_segments_sort_without_error():
    codes = ["10.2a", "0.117a.999", "1.1", "1.1.1", "1.1a", "x", "2.b.3"]
    ordered = sorted(codes, key=key)
    assert ordered[0] == "0.117a.999"
    assert ordered.index("1.1") < ordered.index("1.1.1")
    assert sorted(["a1", "a10", "a2"], key=key) == ["a1", "a2", "a10"]


def test_empty_and_none_never_raise_and_sort_first():
    assert key(None) == key("") == ()
    assert sorted(["1.1", None, "", "0.1"], key=key) == [None, "", "0.1", "1.1"]
    assert key(" 3.4 ") == key("3.4")
    assert key("..") is not None
