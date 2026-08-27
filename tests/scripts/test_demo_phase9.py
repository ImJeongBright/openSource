from __future__ import annotations

from scripts.demo_phase9 import uniquify_demo_content


def test_uniquify_demo_content_changes_checksum_without_searchable_marker(tmp_path) -> None:
    source = tmp_path / "policy.md"
    source.write_text("# 보안 정책\n\n비밀번호는 12자 이상입니다.\n", encoding="utf-8")

    first = uniquify_demo_content(source, "same-run-v2")
    update = uniquify_demo_content(source, "same-run-v2-update")

    assert first != update
    assert first.rstrip() == source.read_bytes().rstrip()
    assert update.rstrip() == source.read_bytes().rstrip()
    assert b"same-run" not in first
    assert b"same-run" not in update
