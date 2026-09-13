"""Prose that states a rule is under contract, mechanically (review round 18).

**The problem this closes.** Eight consecutive review rounds found the same
defect class: a rule with one correct implementation and *many* prose
restatements, each restatement an independent opportunity to drift. Rounds
10-14, 17 and 18 all landed on it. Two rounds' fixes were certified as
complete audits of a surface and were each wrong at two entries, because the
instrument was a human re-read. Fixing the ninth instance does not stop the
tenth; the count of independent restatements is the defect.

**The rule this file enforces.**

    A rule has exactly ONE canonical statement — a row of plan §4.5's
    contract table. Prose elsewhere REFERENCES it rather than restating it.
    Where prose genuinely must restate a rule for a reader who will not
    follow a link into a plan — published page copy, README's
    consumer-facing predicate — the restatement is REGISTERED below and
    pinned, the way ``templates/`` already is.

So a future drift is either impossible (there is only one statement) or
caught (the restatement is registered, and rewording it fails here).

**Two guards, both mechanical and re-runnable.**

``test_no_contract_prose_uses_a_retired_spelling`` is the denylist:
spellings a numbered round ruled out BY EXECUTION cannot come back. Each
entry records the round, why the spelling is false, and what to write
instead. This is the net that would have caught round 18's I-1 and I-2 and
round 17's I-2 mechanically, on the commit that introduced them, rather than
on the eighth read.

``test_every_prose_restatement_of_a_canonical_rule_is_registered`` is the
allowlist: each recurring rule has a *trigger* — a token whose presence in
prose means the rule is being stated rather than referenced — and every
prose span in the contract's file set that matches a trigger must appear in
:data:`REGISTER`, with the phrase that carries it. Adding a tenth
restatement of the run-total rule anywhere in the contract set fails the
build until it is registered, and registering it forces an author to name
the phrase and the pin.

**What these guards do NOT certify** (stated here because round 18's finding
was precisely that two commits claimed audits they had not done). They are
lexical. They cannot prove any prose correct; they cannot catch a brand-new
paraphrase that avoids every trigger token and every retired spelling. They
check the two lexical properties above over the declared file set, and
nothing else. A green run means no retired spelling is present and no
unregistered restatement exists — it is not a certificate that the
registered restatements say true things. That property is established by
mutation, per review round, recorded in the commit messages.

**One guard considered and rejected, with the measurement.** A structural
proximity check — "any prose sentence containing a delta word and ``leg``
but not ``attempt`` states the granularity rule at the wrong level" — was
built and run against the contract set before this file was written. It
flagged 33 sentences, of which the overwhelming majority are legitimately
leg-scoped (the round-9 SOURCE rule is deliberately leg-level and
table-pinned at plan §4.5). An allowlist of 33 exceptions is a worse rot
risk than the rule it guards, so the delta rule is covered by its trigger
token and its retired spellings instead. Recorded so the next round does not
re-derive it.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from tests.test_codex_cli import _DOCS_CITING_TESTS

ROOT = Path(__file__).resolve().parent.parent

# This module necessarily quotes every spelling it retires, so scanning it
# would fire on its own denylist. It is the register; it is not prose that
# states the rules.
_SELF = "tests/test_prose_contract.py"

# The contract's declared file set (single-sourced from
# ``test_codex_cli._DOCS_CITING_TESTS`` rather than restated here — the same
# principle this file exists to enforce), plus the test suite: round 18 found
# a retired spelling preserved as a quotation in ``tests/test_build_site.py``,
# so test rationale is prose under the same contract.
SCANNED = tuple(_DOCS_CITING_TESTS) + tuple(
    sorted(
        f"tests/{p.name}" for p in (ROOT / "tests").glob("test_*.py") if p.name != Path(_SELF).name
    )
)


def _prose_spans(rel: str) -> list[tuple[int, str]]:
    """``[(line, text)]`` of the human-language spans of ``rel``.

    For Python that is comments and docstrings only — an identifier in a
    ``frozenset`` literal or a parameter name is code, not a statement of a
    rule, and must not trip a prose guard. For Markdown, HTML/Jinja and CSS
    the whole file is prose for these purposes.
    """
    path = ROOT / rel
    text = path.read_text()
    if path.suffix != ".py":
        return [(i, ln) for i, ln in enumerate(text.splitlines(), 1)]
    spans: list[tuple[int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            spans.append((tok.start[0], tok.string))
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                spans.append((node.body[0].lineno if node.body else 1, doc))
    return sorted(spans)


def _sentences(rel: str) -> list[tuple[int, str]]:
    """``[(line, sentence)]`` — whitespace-normalised, so a claim split over
    several comment lines is matched as the one sentence a reader sees."""
    out: list[tuple[int, str]] = []
    for line, text in _prose_spans(rel):
        for sentence in re.split(r"(?<=[.;:!?])\s+", " ".join(text.split())):
            if sentence.strip():
                out.append((line, sentence.strip()))
    return out


# --------------------------------------------------------------------------
# Guard 1 — spellings a review round ruled out by execution.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Retired:
    pattern: str  # matched case-insensitively against a normalised sentence
    round: int
    why_false: str
    instead: str
    #: ``(rel, marker)`` pairs where the spelling is quoted IN ORDER TO record
    #: that it was retired — a §4.5 change-history row, a test docstring
    #: naming the inversion it executed. The exemption is keyed on a verbatim
    #: marker from the quoting sentence, not on the file, so a NEW assertion
    #: of the retired spelling in the same file still fails.
    quoted_as_history: tuple[tuple[str, str], ...] = ()


RETIRED_SPELLINGS: tuple[Retired, ...] = (
    Retired(
        pattern=r"only the caveats in\b.{0,60}\bRUN_TOTAL_LOWER_BOUND_CAVEATS",
        round=18,
        why_false=(
            "The run-level filter reads RUN_TOTAL_NEUTRAL_CAVEATS, not the "
            "lower-bound set. Executed: a record whose attempt carries "
            "`compaction_unmeasured` — in neither set — yields "
            "totals_are_lower_bound True with reasons ['compaction_unmeasured']."
        ),
        instead=(
            "A run total is short unless every caveat on it is run-total-neutral; "
            "the lower-bound set is the classification of today's values, not the predicate."
        ),
    ),
    Retired(
        pattern=r"the others move spend between attempts",
        round=18,
        why_false=(
            "The second half of the same false comment. A caveat outside both "
            "sets does NOT leave the total alone — it shortens it. Only "
            "`absorbed_missing_leg` moves spend between attempt rows."
        ),
        instead=(
            "Name `absorbed_missing_leg` specifically, or say 'every caveat that is "
            "run-total-neutral'. Never 'the others'."
        ),
    ),
    Retired(
        pattern=r"\bthis leg = total\b",
        round=18,
        why_false=(
            "The delta is the ATTEMPT's share, not one leg's. Executed with the "
            "repo's fakes: start leg (thread total 15,359/5), a dead resume leg, "
            "then a returning resume leg (31,478/10) publishes usage_delta "
            "16,119/5 — measured from the START leg's total, spanning both "
            "resume legs — and the identical sequence WITHOUT the dead leg "
            "publishes exactly the same 16,119/5."
        ),
        instead=(
            "'the returning leg records total − previous_thread_total — the ATTEMPT's "
            "share, not one leg's'. See _Usage, which states the rule canonically."
        ),
    ),
    Retired(
        pattern=r"covers every leg of the attempt",
        round=17,
        why_false=(
            "True of a dead RESUME leg only. A dead START leg abandons a thread "
            "nothing ever reads, so its spend is covered by no delta anywhere — "
            "which is what `dead_leg_unmeasured` marks. Stated unconditionally "
            "it is the negation of that rule's own §4.5 row."
        ),
        instead="Say which leg died: a dead resume leg, on the same thread.",
        quoted_as_history=(
            (
                "plans/codex-cli-harness.md",
                "which is the negation of the row above",
            ),
        ),
    ),
    Retired(
        pattern=r"only the lower-bound subset reaches the templates",
        round=18,
        why_false=(
            "Deleted from build_site.py by the deny-by-default change and "
            "survived as a quotation in a test docstring. It is also the "
            "fail-open spelling: what reaches the templates is every caveat "
            "that is not run-total-neutral."
        ),
        instead="'Only the shortening caveats reach the templates on purpose' (build_site.py).",
    ),
    Retired(
        pattern=r"the real figure is exactly this and needs no adjustment",
        round=16,
        why_false=(
            "The inversion of `_marks.html`'s lb_title that left all 283 tests "
            "green. A lower-bound total's real figure is HIGHER by an unknown amount."
        ),
        instead="'the real figure is higher by an unknown amount'.",
        quoted_as_history=(
            ("tests/test_build_site.py", "left all 283 tests green"),
        ),
    ),
    Retired(
        pattern=r"deliberately unmapped",
        round=18,
        why_false=(
            "REASONING_EFFORTS is an allowlist, not a translation table — the "
            "identity dict was deleted, so 'unmapped' now describes nothing."
        ),
        instead="'`ultra` is deliberately excluded'.",
    ),
)


def test_no_contract_prose_uses_a_retired_spelling():
    """A spelling a review round ruled out by execution cannot come back.

    Round 18: 'this is the eighth round in which restated prose has
    contradicted the code, and the last two rounds' fixes were each certified
    as complete audits that were not.' A phrase sweep by hand is the
    instrument that kept failing; this is the same sweep with a build behind
    it. Every entry in :data:`RETIRED_SPELLINGS` names the round that killed
    it and the executed reason, so a reader who trips this guard is told why
    the sentence is false rather than merely that it is banned.
    """
    hits = []
    for rel in SCANNED:
        for line, sentence in _sentences(rel):
            for retired in RETIRED_SPELLINGS:
                if not re.search(retired.pattern, sentence, re.I | re.S):
                    continue
                if any(
                    rel == where and marker in sentence
                    for where, marker in retired.quoted_as_history
                ):
                    continue  # quoted to record the retirement, not to assert it
                hits.append(
                    f"\n{rel}:{line} uses a spelling retired in review round "
                    f"{retired.round}:\n    {sentence[:200]}\n"
                    f"  WHY IT IS FALSE: {retired.why_false}\n"
                    f"  WRITE INSTEAD:   {retired.instead}"
                )
    assert not hits, "".join(hits)


# --------------------------------------------------------------------------
# Guard 2 — every prose restatement of a canonical rule is registered.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One registered prose span that names a rule's trigger token."""

    rel: str
    #: A verbatim phrase that must still be present. Rewording it fails here,
    #: which is the point: an author changing a registered statement of a rule
    #: has to come back to this register and say what it now says.
    must_contain: str
    #: "restatement" asserts the rule's content and needs a pin;
    #: "reference" points at the canonical statement, or names the token
    #: historically, and does not.
    kind: str
    why: str
    pinned_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rule:
    rule_id: str
    canonical: str
    #: Presence of this pattern in a prose sentence means the rule is being
    #: STATED rather than referenced, so the span must be registered.
    trigger: str
    entries: tuple[Entry, ...] = field(default_factory=tuple)


REGISTER: tuple[Rule, ...] = (
    Rule(
        rule_id="run-total-predicate",
        canonical="plans/codex-cli-harness.md §4.5 — the deny-by-default row",
        trigger=r"RUN_TOTAL_LOWER_BOUND_CAVEATS",
        entries=(
            Entry(
                rel="plans/codex-cli-harness.md",
                must_contain=(
                    "`RUN_TOTAL_LOWER_BOUND_CAVEATS` beside it records which of today's\n"
                    "  values shorten a total and is read by no filter."
                ),
                kind="restatement",
                why=(
                    "§4.5's narrative. Round 17 fixed the table and left this asserting "
                    "the superseded intersection; round 18 found the same "
                    "correct-table/stale-narrative split for a second rule. The narrative "
                    "now states the predicate in the deny-by-default direction and marks "
                    "the lower-bound set as a classification."
                ),
                pinned_by=("test_an_unclassified_caveat_makes_the_run_total_a_lower_bound",),
            ),
            Entry(
                rel="plans/codex-cli-harness.md",
                must_contain=(
                    "since dropping it from `RUN_TOTAL_LOWER_BOUND_CAVEATS` "
                    "leaves the row above green (round 14)"
                ),
                kind="reference",
                why=(
                    "A §4.5 table row explaining why `dead_leg_unmeasured` needs its own "
                    "pin. Names the set as a set, asserts no predicate."
                ),
            ),
            Entry(
                rel="templates/run.html",
                must_contain=(
                    "caveat names it defines are checked against RUN_TOTAL_LOWER_BOUND_CAVEATS /"
                ),
                kind="reference",
                why=(
                    "Describes the vocabulary cross-check that keeps this page's copy and "
                    "the renderer's classification in step. Names both sets as the "
                    "partition they are; asserts no predicate."
                ),
            ),
            Entry(
                rel="tools/build_site.py",
                must_contain=(
                    "# ``explained_caveats`` is the set of caveat names run.html DEFINES in\n"
                    "    # words. It is RUN_TOTAL_LOWER_BOUND_CAVEATS because"
                ),
                kind="reference",
                why=(
                    "Explains why the run page's 'explained_caveats' context equals that "
                    "constant — the page glosses every member of it in words, a property "
                    "the explainer test enforces. Names the set to justify an equality; "
                    "asserts no predicate. Added round 18 (N-8) so the template need not "
                    "hard-code a second copy of the vocabulary."
                ),
            ),
            Entry(
                rel="tests/test_build_site.py",
                must_contain=(
                    "with the\n    filter written as an intersection with "
                    "``RUN_TOTAL_LOWER_BOUND_CAVEATS``:"
                ),
                kind="reference",
                why=(
                    "The executed history of the round-17 fail-open defect. Names the "
                    "RETIRED filter shape explicitly as retired, which is the one context "
                    "in which naming it is not a restatement."
                ),
            ),
        ),
    ),
    Rule(
        rule_id="run-total-predicate-enumerated",
        canonical="plans/codex-cli-harness.md §4.5 — the deny-by-default row",
        # Enumerating the two shortening values in one sentence IS the
        # fail-open spelling of the predicate, whether or not the constant is
        # named — round 18's finding about README's consumer-facing paragraph.
        trigger=r"(?=.*\bmissing\b)(?=.*\bdead_leg_unmeasured\b)",
        entries=(
            Entry(
                rel="README.md",
                must_contain=(
                    "Today `\"absorbed_missing_leg\"` is the only\nneutral value"
                ),
                kind="restatement",
                why=(
                    "The one restatement that must exist: a consumer reading the results "
                    "JSON will not follow a link into a plan, and this is the predicate "
                    "they have to implement. It states deny-by-default FIRST and names "
                    "today's shortening values as an illustration, so a consumer who "
                    "implements the paragraph cannot fail open."
                ),
                pinned_by=(
                    "test_the_readme_states_the_run_total_predicate_the_renderer_implements",
                ),
            ),
        ),
    ),
    Rule(
        rule_id="attempt-granularity-delta",
        canonical=(
            "pagehub_benchmarks/harnesses/codex_cli.py — the :class:`_Usage` docstring, "
            "and plan §4.5's attempt-granularity row"
        ),
        trigger=r"previous_thread_total",
        entries=(
            Entry(
                rel="pagehub_benchmarks/harnesses/codex_cli.py",
                must_contain=(
                    "the ATTEMPT's share, not one leg's: a dead resume leg never reaches"
                ),
                kind="restatement",
                why=(
                    "``_usage_from`` COMPUTES the published figure, so the equation has to "
                    "appear where it is computed. Round 18 found it stated at LEG "
                    "granularity, which :class:`_Usage` exists to deny — the eighth round "
                    "of that class."
                ),
                pinned_by=("test_a_dead_resume_leg_leaves_the_attempt_faithful",),
            ),
        ),
    ),
)


def _defined_test_names() -> set[str]:
    names: set[str] = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        names.update(re.findall(r"^def (test_[A-Za-z0-9_]+)", path.read_text(), re.M))
    return names


def test_every_prose_restatement_of_a_canonical_rule_is_registered():
    """No unregistered prose in the contract set states a registered rule.

    This is the structural half of round 18's ask. The denylist above stops a
    retired spelling coming back; this stops a NEW restatement appearing at
    all. Each rule names a trigger token whose presence in prose means the
    rule is being stated rather than pointed at; every file carrying one must
    be in :data:`REGISTER`, and every registered phrase must still be there
    verbatim.

    So the two ways this class has drifted for eight rounds both fail the
    build: adding an eleventh statement of the run-total rule (unregistered
    file), and quietly rewording one of the registered ones (missing phrase).
    """
    defined = _defined_test_names()
    problems: list[str] = []

    for rule in REGISTER:
        registered_files = {e.rel for e in rule.entries}
        found_files: dict[str, int] = {}
        for rel in SCANNED:
            for line, sentence in _sentences(rel):
                if re.search(rule.trigger, sentence):
                    found_files.setdefault(rel, line)

        for rel, line in sorted(found_files.items()):
            if rel not in registered_files:
                problems.append(
                    f"\n{rel}:{line} states rule '{rule.rule_id}' but is not registered.\n"
                    f"  The canonical statement is: {rule.canonical}\n"
                    "  Point at it instead of restating it. If this prose genuinely must\n"
                    "  restate the rule for a reader who cannot follow the link, add an\n"
                    "  Entry to REGISTER naming the phrase that carries it and the test\n"
                    "  that pins it."
                )
        for rel in sorted(registered_files - set(found_files)):
            problems.append(
                f"\n{rel} is registered for rule '{rule.rule_id}' but no longer states it.\n"
                "  Drop its Entry from REGISTER — a register with dead entries is the\n"
                "  decoration this guard exists to prevent."
            )

        for entry in rule.entries:
            if entry.must_contain not in (ROOT / entry.rel).read_text():
                problems.append(
                    f"\n{entry.rel}: the registered phrase for '{rule.rule_id}' is gone.\n"
                    f"  Expected verbatim: {entry.must_contain!r}\n"
                    f"  Why it is registered: {entry.why}\n"
                    "  If the rewording is deliberate, update the Entry with the new\n"
                    "  phrase — and check it against the code before you do."
                )
            assert entry.kind in ("restatement", "reference"), entry
            if entry.kind == "restatement" and not entry.pinned_by:
                problems.append(
                    f"\n{entry.rel}: a registered RESTATEMENT of '{rule.rule_id}' names no\n"
                    "  pinning test. A restatement no test pins is a restatement that drifts."
                )
            for name in entry.pinned_by:
                if name not in defined:
                    problems.append(f"\n{entry.rel}: pinning test {name} does not exist.")

    assert not problems, "".join(problems)


def test_the_readme_states_the_run_total_predicate_the_renderer_implements(tmp_path):
    """README's consumer-facing paragraph, checked against EXECUTED behaviour.

    The one restatement of the run-total rule that has to exist — a consumer
    reading the results JSON will not follow a link into a plan — and until
    round 18 the only one with no pin of any kind. Round 18: 'the passage that
    tells an external JSON consumer which predicate to implement' stated the
    lower-bound pair as the predicate, which is the same predicate today and
    fails open the day a fourth caveat value ships.

    String-matching the paragraph would pin the words to themselves, so this
    drives the REAL ``load_runs`` over a record carrying a caveat in neither
    classified set and asserts the direction that comes back is the one the
    README tells a consumer to implement. Re-deriving the filter inline here
    instead was tried and rejected: it left the guard blind to the renderer
    reverting to the round-17 intersection, which is the exact defect the
    paragraph now warns about (verifying the two ENDS of a path and inferring
    the middle).
    """
    from tools.build_site import RUN_TOTAL_LOWER_BOUND_CAVEATS, RUN_TOTAL_NEUTRAL_CAVEATS, load_runs

    readme = (ROOT / "README.md").read_text()

    # The direction the paragraph tells a consumer to implement…
    assert "a run total is short unless\nevery caveat in every" in readme
    assert "is run-total-neutral" in readme
    assert "fails open the day a fourth value ships" in readme

    # …is the direction build_site actually implements, executed end to end.
    # `compaction_unmeasured` is in neither classified set, exactly like a
    # caveat written by a harness version the renderer does not import.
    unknown = "compaction_unmeasured"
    assert unknown not in RUN_TOTAL_NEUTRAL_CAVEATS | RUN_TOTAL_LOWER_BOUND_CAVEATS

    results = tmp_path / "results" / "eval-chess-backend"
    results.mkdir(parents=True)
    (results / "run.json").write_text(
        json.dumps(
            {
                "benchmark": "eval-chess-backend",
                "harness": "codex-cli",
                "started_at": "2026-09-12T00:00:00Z",
                "passed": True,
                "attempts": 1,
                "cost_usd": 12.3456,
                "per_attempt": [
                    {"raw": {"usage_faithful": False, "usage_caveats": [unknown]}}
                ],
            }
        )
    )
    runs, _ = load_runs(tmp_path / "results", tmp_path / "benchmarks")
    assert len(runs) == 1
    assert runs[0]["totals_are_lower_bound"] is True, (
        "the renderer cleared an unclassified caveat — it is failing OPEN, which is "
        "the direction README tells consumers not to implement"
    )
    assert runs[0]["totals_lower_bound_caveats"] == [unknown]

    # And the neutral value it DOES clear, so the assertion above is not
    # passing because everything is marked.
    (results / "run.json").write_text(
        (results / "run.json").read_text().replace(unknown, "absorbed_missing_leg")
    )
    runs, _ = load_runs(tmp_path / "results", tmp_path / "benchmarks")
    assert runs[0]["totals_are_lower_bound"] is False
