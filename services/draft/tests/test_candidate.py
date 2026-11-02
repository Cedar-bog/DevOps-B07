import pytest

from draft.candidate import check_candidate


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
