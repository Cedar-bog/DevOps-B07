from dataclasses import replace

import pytest

from draft.candidate import (
    CandidateError, check_candidate, extract_candidates, normalize_continuations, select_candidate,
)
from fake_llm import fenced_dockerfile


def candidates(*texts):
    return extract_candidates("\n".join(fenced_dockerfile(text) for text in texts))


def test_extract_three_fenced_candidates_preserves_text_and_duplicates():
    text = "FROM base\nRUN echo  two spaces\nCMD [\"hello\"]"
    result = candidates(text, text, "FROM other")
    assert [item.index for item in result] == [1, 2, 3]
    assert result[0].text == text and result[0].fenced
    assert result[0].sha256 == result[1].sha256


def test_fallback_handles_continuations_and_reply_order():
    reply = "Candidate 1:\nFROM base\nRUN apt-get update && \\\n    apt-get install make\nCMD hello\n\nCandidate 2:\n" + fenced_dockerfile("FROM other\nCMD hello")
    result = extract_candidates(reply)
    assert len(result) == 2
    assert result[0].text.endswith("CMD hello") and not result[0].fenced
    assert result[1].text == "FROM other\nCMD hello" and result[1].fenced


def test_unlabelled_fences_and_crlf():
    result = extract_candidates("```\r\nFROM base\r\nCMD hello\r\n```\r\n")
    assert result[0].text == "FROM base\nCMD hello"


def test_no_candidate_raises():
    with pytest.raises(CandidateError):
        extract_candidates("I cannot help with that build.")


def test_normalizes_continuations_comments_and_blank_lines():
    assert normalize_continuations("# comment\nFROM base\n\nRUN install \\\n   make\n") == "FROM base\nRUN install make"


def test_r1_removes_used_candidates():
    items = candidates("FROM a", "FROM b", "FROM c")
    selection = select_candidate(items, used_sha256={items[0].sha256, items[2].sha256}, error_terms=[])
    assert selection.rule == "R1" and selection.candidate.index == 2
    assert {index for index, _ in selection.rejected} == {1, 3}
    with pytest.raises(CandidateError):
        select_candidate(items, used_sha256={item.sha256 for item in items}, error_terms=[])


def test_r2_majority_after_normalization():
    items = candidates("FROM base\nRUN install \\\n make", "FROM base\nRUN install gcc", "FROM base\nRUN install make")
    selection = select_candidate(items, used_sha256=set(), error_terms=["gcc"])
    assert selection.rule == "R2" and selection.candidate.index == 1


def test_r3_unique_error_term():
    items = candidates("FROM base\nRUN install gcc", "FROM base\nRUN install MAKE", "FROM base\nRUN install cc")
    selection = select_candidate(items, used_sha256=set(), error_terms=["make"])
    assert selection.rule == "R3" and selection.candidate.index == 2


def test_r4_receives_only_remaining_candidates():
    items = candidates("FROM a", "FROM b", "FROM c")
    seen = []

    def chooser(remaining):
        seen.extend(item.index for item in remaining)
        return 3

    selection = select_candidate(items, used_sha256={items[0].sha256}, error_terms=[], chooser=chooser)
    assert seen == [2, 3] and selection.rule == "R4" and selection.candidate.index == 3
    assert select_candidate(items, used_sha256=set(), error_terms=[]).candidate.index == 1
    with pytest.raises(CandidateError):
        select_candidate(items, used_sha256=set(), error_terms=[], chooser=lambda _: 99)


def test_r2_tied_majorities_fall_through_to_r4():
    items = candidates("FROM a", "FROM a", "FROM b")
    items.append(replace(items[2], index=4))
    assert select_candidate(items, used_sha256=set(), error_terms=[]).rule == "R4"


@pytest.mark.parametrize("change", [
    lambda text: text.replace('CMD ["./hello"]', 'CMD ["echo", "hello E3"]'),
    lambda text: text.replace("RUN make\n", ""),
    lambda text: text.replace("COPY main.c Makefile ./\n", ""),
    lambda text: text.replace("python:3.13-slim", "ubuntu:latest"),
    lambda text: text + "\nFROM other",
    lambda text: text + '\nCMD ["./hello"]',
    lambda text: text + '\nENTRYPOINT ["echo"]',
    lambda text: text.replace("WORKDIR /work\nCOPY main.c Makefile ./", "COPY main.c Makefile ./\nWORKDIR /work"),
    lambda text: text.replace("FROM python:3.13-slim", "RUN echo first\nFROM python:3.13-slim"),
    lambda text: text + "\nRUN echo bypass",
])
def test_rejects_invalid_candidates(fixture_dir, change):
    baseline = (fixture_dir / "Dockerfile.broken").read_text(encoding="utf-8")
    assert check_candidate(change(baseline), baseline)


def test_reference_candidate_is_valid(fixture_dir):
    baseline = (fixture_dir / "Dockerfile.broken").read_text(encoding="utf-8")
    reference = (fixture_dir / "Dockerfile.reference").read_text(encoding="utf-8")
    assert check_candidate(reference, baseline) == []
