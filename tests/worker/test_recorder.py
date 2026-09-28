"""Tests for the recording of a call's room."""

import stat
from pathlib import Path

from pinecall.worker._recorder import recording_path


def test_a_call_gets_a_directory_of_its_own_the_recorder_may_write_in(tmp_path: Path) -> None:
    audio = recording_path(tmp_path, "call_1")
    assert audio == tmp_path / "call_1" / "audio.ogg"
    mode = (tmp_path / "call_1").stat().st_mode
    assert mode & stat.S_ISGID
    assert mode & stat.S_IWGRP
