from bot import parse_allowed_ids, split_message


def test_parse_allowed_ids():
    assert parse_allowed_ids("") == set()
    assert parse_allowed_ids("123, 456,") == {123, 456}


def test_split_message_short_text_unchanged():
    assert split_message("hello") == ["hello"]


def test_split_message_respects_limit_and_keeps_content():
    text = "\n\n".join(["para " + "x" * 30] * 20)
    chunks = split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_split_message_hard_cuts_unbroken_text():
    chunks = split_message("a" * 250, limit=100)
    assert [len(c) for c in chunks] == [100, 100, 50]
