from pathlib import Path


def test_mcp_dependency_is_pinned_to_the_supported_major_version() -> None:
    requirements = (Path(__file__).parents[1] / "requirements.txt").read_text()
    assert "mcp[cli]>=1." in requirements
    assert "<2" in requirements
