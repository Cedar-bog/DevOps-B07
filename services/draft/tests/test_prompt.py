from draft.prompt import PROMPT_VERSION, build_context_prompt, build_error_prompt, locate_error


def test_error_lines_keep_original_numbers_and_tail_is_limited():
    lines = [f"line {i}" for i in range(100)]
    lines[2] = "ERROR failed"
    lines[70] = "Unknown dependency"
    excerpt = locate_error("\n".join(lines))
    assert excerpt.matched == [(3, "ERROR failed"), (71, "Unknown dependency")]
    assert excerpt.tail == lines[-50:]
    assert "3: ERROR failed" in excerpt.render()
    assert locate_error("line", tail_limit=0).tail == []


def test_context_prompt_extends_error_prompt():
    basic = build_error_prompt("FROM base", "error: missing make")
    context = build_context_prompt("FROM base", "error: missing make", "readme contents")
    assert context.startswith(basic)
    assert "readme contents" in context[len(basic):]
    assert PROMPT_VERSION in basic and "3 个候选" in basic and "```Dockerfile" in basic
    assert "不得改动运行命令" in basic
