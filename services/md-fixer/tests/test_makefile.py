import pytest

from mdfixer.guard import RepairError
from mdfixer.makefile import parse_makefile, reference_usage, references
from mdfixer.style import Style, identify_style

@pytest.mark.parametrize("style,expected", [("target", Style.DIRECT), ("macro", Style.MACRO), ("hybrid", Style.HYBRID), ("implicit", Style.IMPLICIT)])
def test_original_styles_and_declared_graph(style, expected, fixture_root):
    document = parse_makefile((fixture_root / "styles" / style / "Makefile.before").read_text())
    rule = next(r for r in document.rules if r.stem("main.o") is not None)
    assert identify_style(rule) == expected
    assert document.distance("target:hello", "file:main.o") == 1
    if style == "macro":
        assert document.distance("target:main.o", "variable:DEPS") == 1
        assert document.affected_targets("DEPS") == ("main.o",)


def test_nested_variables_continuations_comments_and_order_only():
    document = parse_makefile("HEADER = config.h\nDEPS := main.c $(HEADER)\nDEPS += unused.h\nmain.o: $(DEPS) \\\n other.h | generated # keep this comment\n\tcc -c $< -o $@\n")
    rule = document.rules[0]
    assert rule.prerequisites == ("main.c", "config.h", "unused.h", "other.h")
    assert rule.order_only == ("generated",)
    assert document.distance("target:main.o", "variable:HEADER") == 2
    assert rule.end_line == 5


def test_recursive_simple_conditional_and_append_semantics():
    document = parse_makefile("A = first.h\nB := $(A)\nC = $(A)\nA = last.h\nA ?= ignored.h\nB += extra.h\nmain.o: $(B) $(C)\n")
    assert document.rules[0].prerequisites == ("first.h", "extra.h", "last.h")


@pytest.mark.parametrize("text", [
    "A = $(B)\nB = $(A)\nmain.o: $(A)\n",
    "A = $(shell touch sentinel)\nmain.o: main.c\n",
    "include other.mk\nmain.o: main.c\n",
    "ifeq (a,b)\nmain.o: main.c\nendif\n",
    "main.o: CFLAGS=-g\n",
    "main.o:: main.c\n",
    "main.o: %.o: %.c\n",
    "main.o: $(DEPS)\nDEPS = main.c\n",
    "main.o: $(UNDEFINED)\n",
])
def test_unsupported_declarations_fail_explicitly(text, tmp_path):
    with pytest.raises(RepairError) as caught:
        parse_makefile(text)
    assert caught.value.code == "UNSUPPORTED_TARGET"
    assert not (tmp_path / "sentinel").exists()


@pytest.mark.parametrize("text", [
    "DEPS := main.c | unused.h\nmain.o: $(DEPS)\n",
    "ORDER = | unused.h\nDEPS = main.c $(ORDER)\nmain.o: $(DEPS)\n",
    "DEPS = main.c|unused.h\nmain.o: $(DEPS)\n",
])
def test_order_only_separator_is_recognized_after_expansion(text):
    document = parse_makefile(text)
    assert document.rules[0].prerequisites == ("main.c",)
    assert document.rules[0].order_only == ("unused.h",)
    assert "file:|" not in set().union(*document.graph.values())


def test_multiple_expanded_order_only_separators_are_rejected():
    with pytest.raises(RepairError):
        parse_makefile("DEPS = main.c | unused.h\nmain.o: $(DEPS) | config.h\n")


def test_include_is_rejected_by_default_but_allowed_for_repaired_candidates():
    text = "OBJS := main.o\nmain.o: main.c\n-include $(DEPS)\n"
    with pytest.raises(RepairError) as caught:
        parse_makefile(text)
    assert caught.value.code == "UNSUPPORTED_TARGET"
    document = parse_makefile(text, allow_includes=True)
    assert document.rules[0].targets == ("main.o",)
    assert document.rules[0].prerequisites == ("main.c",)


@pytest.mark.parametrize("text,expected,opaque", [
    ("cc -c $S -o $@", ("S",), False),
    ("$(CC) -c ${S} $(S:.c=.o) $< $^", ("CC", "S"), False),
    ("echo $$S $$(S) $${S}", (), False),
    ("echo $$$S", ("S",), False),
    ("echo $($(NAME))", ("NAME",), True),
    ("echo $(addprefix x,$S)", ("S",), True),
])
def test_recipe_reference_scanning_handles_make_dollar_rules(text, expected, opaque):
    assert references(text) == expected
    assert reference_usage(text) == (expected, opaque)
