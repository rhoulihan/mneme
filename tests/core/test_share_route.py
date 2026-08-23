"""Re-routing a staged candidate — fixing a destination without spending a decline.

`/mneme:share` used to tell a user that a mis-routed candidate could not be fixed and to
decline-and-reflag instead. That advice is worse than it sounds: a decline is a human
verdict recorded forever, and for a candidate with NO destination the ledger deliberately
makes it GLOBAL — `staging._applies_to` says guessing a scope for those "would resurrect
knowledge a human has already rejected". So the only sanctioned way to fix a routing
mistake was to silence the knowledge in every repo, permanently.

Three things make the move non-trivial, and each has a test here: the candidate id embeds
the target, the declined ledger is target-scoped, and the boundary check needs the
sensitivity of the context the knowledge came FROM.
"""
import pytest

from mneme_core import registry, staging, units
from mneme_core.errors import MnemeError
from mneme_core.registry import Plugin
from mneme_core.staging import Candidate, candidate_id

BODY = "- [gotcha] The webhook replays for 72 hours #x (verified: 2026-08-23)\n"


def home_with(tmp_path, *plugins):
    home = tmp_path / "home"
    for name, sensitivity in plugins:
        registry.add_plugin(
            home,
            Plugin(name=name, repo=f"git@example.com:acme/{name}.git",
                   path=str(tmp_path / name), sensitivity=sensitivity),
        )
    return home


def stage(home, target, *, body=BODY, status="staged", source_sensitivity="", topic="webhooks"):
    cand = Candidate(
        id=candidate_id("fact", target, body), type="fact", edit="new",
        target=target, body=body, topic=topic, status=status,
        source_sensitivity=source_sensitivity,
        provenance={"source": "demo@s1", "captured": "2026-08-23"},
    )
    staging.write_candidate(home, cand)
    return cand


# --- the id is derived from the target, so it has to move with it ------------


def test_the_id_is_reminted_so_the_distiller_dedupes_against_it(tmp_path):
    """`candidate_id` hashes the target with the body. Keeping the old id would leave an
    id that no longer derives from its inputs, and the next distiller run would stage the
    same knowledge again under the correct one — a duplicate the gate shows twice."""
    home = home_with(tmp_path, ("team-kb", "internal"), ("ops-kb", "internal"))
    before = stage(home, "team-kb")

    after = staging.route(home, before.id, "ops-kb")

    assert after.id == candidate_id("fact", "ops-kb", BODY)
    assert after.id != before.id
    assert after.target == "ops-kb"
    # The old file is gone and the new one is the only copy.
    ids = {c.id for c in staging.load_candidates(home)}
    assert ids == {after.id}


def test_everything_else_about_the_candidate_survives_the_move(tmp_path):
    home = home_with(tmp_path, ("team-kb", "internal"), ("ops-kb", "internal"))
    before = stage(home, "team-kb", topic="chargebacks")

    after = staging.route(home, before.id, "ops-kb")

    assert after.body == before.body
    assert after.topic == "chargebacks"
    assert after.type == before.type and after.edit == before.edit
    assert after.provenance == before.provenance


def test_an_unassigned_candidate_can_be_given_a_home(tmp_path):
    """The case that motivated this: no destination, and declining it would be global."""
    home = home_with(tmp_path, ("ops-kb", "internal"))
    before = stage(home, staging.UNASSIGNED)

    after = staging.route(home, before.id, "ops-kb")

    assert after.target == "ops-kb"
    assert [c.target for c in staging.load_candidates(home)] == ["ops-kb"]


def test_a_candidate_can_be_un_routed_back_to_unassigned(tmp_path):
    """`unassigned` is a legal destination — "I do not know yet" is an honest answer."""
    home = home_with(tmp_path, ("team-kb", "internal"))
    before = stage(home, "team-kb")

    after = staging.route(home, before.id, staging.UNASSIGNED)

    assert after.target == staging.UNASSIGNED


# --- the refusals ------------------------------------------------------------


def test_an_unknown_candidate_is_refused(tmp_path):
    home = home_with(tmp_path, ("team-kb", "internal"))
    with pytest.raises(MnemeError, match="fact-deadbeef"):
        staging.route(home, "fact-deadbeef", "team-kb")


def test_an_unregistered_target_is_refused(tmp_path):
    home = home_with(tmp_path, ("team-kb", "internal"))
    before = stage(home, "team-kb")
    with pytest.raises(MnemeError, match="not registered"):
        staging.route(home, before.id, "ghost-kb")
    assert staging.load_candidates(home)[0].id == before.id, "the candidate was disturbed"


def test_routing_to_where_it_already_points_is_refused_plainly(tmp_path):
    home = home_with(tmp_path, ("team-kb", "internal"))
    before = stage(home, "team-kb")
    with pytest.raises(MnemeError, match="already"):
        staging.route(home, before.id, "team-kb")


def test_a_decline_recorded_for_the_destination_still_blocks_it(tmp_path):
    """"Declined stays declined" is a §7.3 guarantee. Routing must not be a way around it."""
    home = home_with(tmp_path, ("team-kb", "internal"), ("ops-kb", "internal"))
    rejected = stage(home, "ops-kb")
    staging.decline(home, rejected, "not for this repo")

    before = stage(home, "team-kb")
    with pytest.raises(MnemeError, match="declined"):
        staging.route(home, before.id, "ops-kb")


def test_a_decline_for_a_DIFFERENT_repo_does_not_block_the_move(tmp_path):
    """One repo's curation must not silence knowledge for a repo that never saw it."""
    home = home_with(tmp_path, ("team-kb", "internal"), ("ops-kb", "internal"),
                     ("other-kb", "internal"))
    rejected = stage(home, "other-kb")
    staging.decline(home, rejected, "not for other-kb")

    before = stage(home, "team-kb")
    after = staging.route(home, before.id, "ops-kb")
    assert after.target == "ops-kb"


def test_the_same_knowledge_already_headed_there_is_a_duplicate_not_a_move(tmp_path):
    home = home_with(tmp_path, ("team-kb", "internal"), ("ops-kb", "internal"))
    stage(home, "ops-kb")
    before = stage(home, "team-kb")

    with pytest.raises(MnemeError, match="already staged"):
        staging.route(home, before.id, "ops-kb")
    # Neither copy was destroyed by the refusal.
    assert len(staging.load_candidates(home)) == 2


# --- the boundary ------------------------------------------------------------


def test_a_move_into_a_less_restricted_repo_is_refused_without_consent(tmp_path):
    """The exact move the boundary flag exists to catch, now reachable by one command."""
    home = home_with(tmp_path, ("secret-kb", "restricted"), ("open-kb", "public"))
    before = stage(home, "secret-kb", source_sensitivity="restricted")

    with pytest.raises(MnemeError, match="boundary"):
        staging.route(home, before.id, "open-kb")

    assert staging.load_candidates(home)[0].target == "secret-kb"


def test_the_boundary_move_is_allowed_when_the_human_says_so_and_is_recorded(tmp_path):
    home = home_with(tmp_path, ("secret-kb", "restricted"), ("open-kb", "public"))
    before = stage(home, "secret-kb", source_sensitivity="restricted")

    after = staging.route(home, before.id, "open-kb", allow_boundary=True)

    assert after.target == "open-kb"
    assert "restricted" in after.boundary_warning and "open-kb" in after.boundary_warning


def test_a_move_toward_MORE_restriction_needs_no_consent(tmp_path):
    home = home_with(tmp_path, ("open-kb", "public"), ("secret-kb", "restricted"))
    before = stage(home, "open-kb", source_sensitivity="public")
    after = staging.route(home, before.id, "secret-kb")
    assert after.target == "secret-kb"
    assert after.boundary_warning == ""


def test_without_a_recorded_source_the_current_target_stands_in_for_it(tmp_path):
    """Candidates staged before `source_sensitivity` existed must still be protected.

    The knowledge was judged fit for the repo it is pointed at, so that repo's sensitivity
    is the best evidence available — and it is the conservative reading.
    """
    home = home_with(tmp_path, ("secret-kb", "restricted"), ("open-kb", "public"))
    before = stage(home, "secret-kb", source_sensitivity="")

    with pytest.raises(MnemeError, match="boundary"):
        staging.route(home, before.id, "open-kb")


def test_an_unassigned_candidate_with_no_source_says_the_boundary_is_unverified(tmp_path):
    """Nothing is known about where it came from, so nothing is claimed about the move."""
    home = home_with(tmp_path, ("open-kb", "public"))
    before = stage(home, staging.UNASSIGNED, source_sensitivity="")

    after = staging.route(home, before.id, "open-kb")

    assert after.target == "open-kb"
    assert "unverified" in after.boundary_warning.lower(), after.boundary_warning


# --- quarantine --------------------------------------------------------------


def test_routing_does_not_launder_a_secret_scan_hit(tmp_path):
    home = home_with(tmp_path, ("team-kb", "internal"), ("ops-kb", "internal"))
    before = stage(home, "team-kb", status="quarantined")

    after = staging.route(home, before.id, "ops-kb")

    assert after.status == "quarantined"
    assert [c.id for c in staging.load_candidates(home, include_quarantined=True)] == [after.id]
    assert staging.load_candidates(home) == [], "a quarantined candidate stayed out of staging"


# --- the persisted field -----------------------------------------------------


def test_source_sensitivity_round_trips_and_is_optional(tmp_path):
    home = home_with(tmp_path, ("team-kb", "internal"))
    cand = stage(home, "team-kb", source_sensitivity="restricted")
    assert staging.load_candidates(home)[0].source_sensitivity == "restricted"

    # A candidate written before the field existed still loads.
    path = staging._find(home, cand.id)
    text = path.read_text(encoding="utf-8")
    path.write_text(
        "\n".join(l for l in text.splitlines() if not l.startswith("source-sensitivity:")) + "\n",
        encoding="utf-8",
    )
    assert staging.load_candidates(home)[0].source_sensitivity == ""


# --- the CLI surface ---------------------------------------------------------


def run(capsys, *argv):
    from mneme_core.cli import main

    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_cli_reports_both_ids_because_the_id_changed(tmp_path, capsys):
    home = home_with(tmp_path, ("team-kb", "internal"), ("ops-kb", "internal"))
    before = stage(home, "team-kb")

    code, out, _ = run(capsys, "--home", str(home), "share", "route", before.id,
                       "--target", "ops-kb")

    assert code == 0
    assert before.id in out and "ops-kb" in out
    assert candidate_id("fact", "ops-kb", BODY) in out


def test_the_cli_refuses_a_boundary_move_and_takes_the_flag(tmp_path, capsys):
    home = home_with(tmp_path, ("secret-kb", "restricted"), ("open-kb", "public"))
    before = stage(home, "secret-kb", source_sensitivity="restricted")

    code, _out, err = run(capsys, "--home", str(home), "share", "route", before.id,
                          "--target", "open-kb")
    assert code == 1 and "boundary" in err

    code, out, _ = run(capsys, "--home", str(home), "share", "route", before.id,
                       "--target", "open-kb", "--allow-boundary")
    assert code == 0
    assert "boundary:" in out, out


def test_a_routed_candidate_shows_up_under_its_new_target_in_the_queue(tmp_path, capsys):
    home = home_with(tmp_path, ("team-kb", "internal"), ("ops-kb", "internal"))
    before = stage(home, staging.UNASSIGNED)
    run(capsys, "--home", str(home), "share", "route", before.id, "--target", "ops-kb")

    _code, out, _ = run(capsys, "--home", str(home), "share", "list")
    assert "ops-kb:" in out
    assert staging.UNASSIGNED not in out


# --- the laundering hop ------------------------------------------------------
#
# An adversarial review found that `restricted -> unassigned -> public` completed with no
# refusal and no flag, one command after the direct move had been refused. Two lines caused
# it: `unassigned` was treated as a free pass, and `route` never persisted the source
# sensitivity it had just inferred — so the evidence died at the first hop.


def test_a_hop_through_unassigned_cannot_launder_the_sensitivity(tmp_path):
    home = home_with(tmp_path, ("secret-kb", "restricted"), ("open-kb", "public"))
    before = stage(home, "secret-kb")  # no recorded source — the real-world case

    with pytest.raises(MnemeError, match="boundary"):
        staging.route(home, before.id, "open-kb")

    parked = staging.route(home, before.id, staging.UNASSIGNED)
    with pytest.raises(MnemeError, match="boundary"):
        staging.route(home, parked.id, "open-kb")


def test_a_hop_through_another_repo_cannot_launder_it_either(tmp_path):
    home = home_with(tmp_path, ("secret-kb", "restricted"), ("mid-kb", "internal"),
                     ("open-kb", "public"))
    before = stage(home, "secret-kb")

    with pytest.raises(MnemeError, match="boundary"):
        staging.route(home, before.id, "mid-kb")
    mid = staging.route(home, before.id, "mid-kb", allow_boundary=True)
    with pytest.raises(MnemeError, match="boundary"):
        staging.route(home, mid.id, "open-kb")


def test_the_resolved_source_is_persisted_so_it_survives_the_next_hop(tmp_path):
    """The inferred value was computed and thrown away, which is what made the hop work."""
    home = home_with(tmp_path, ("secret-kb", "restricted"), ("secret2-kb", "restricted"))
    before = stage(home, "secret-kb")
    assert before.source_sensitivity == ""

    after = staging.route(home, before.id, "secret2-kb")
    assert after.source_sensitivity == "restricted"
    assert staging.load_candidates(home)[0].source_sensitivity == "restricted"


def test_parking_at_unassigned_keeps_the_sensitivity_it_arrived_with(tmp_path):
    home = home_with(tmp_path, ("secret-kb", "restricted"))
    before = stage(home, "secret-kb")
    parked = staging.route(home, before.id, staging.UNASSIGNED)
    assert parked.source_sensitivity == "restricted"


def test_a_recorded_crossing_is_not_erased_by_a_move(tmp_path):
    """A legacy candidate carries the warning but not the source. Recomputing from its
    CURRENT target would call restricted-sourced knowledge public and drop the flag."""
    home = home_with(tmp_path, ("open-kb", "public"), ("open2-kb", "public"))
    cand = stage(home, "open-kb")
    cand.boundary_warning = "target 'open-kb' is public but the source context is restricted"
    staging.write_candidate(home, cand)

    with pytest.raises(MnemeError, match="boundary"):
        staging.route(home, cand.id, "open2-kb")

    after = staging.route(home, cand.id, "open2-kb", allow_boundary=True)
    assert "restricted" in after.boundary_warning, "the recorded crossing was erased"


# --- an update names a unit in the repo it is leaving ------------------------


def test_a_cross_repo_move_is_refused_for_an_update(tmp_path):
    """`target_unit` points into the OLD repo. Applying it there either aborts the whole
    batch or, when a topic key collides, silently rewrites an unrelated bullet."""
    home = home_with(tmp_path, ("team-kb", "internal"), ("ops-kb", "internal"))
    cand = Candidate(
        id=candidate_id("fact", "team-kb", BODY), type="fact", edit="update",
        target="team-kb", body=BODY, topic="webhooks",
        target_unit="facts/webhooks#the-webhook-replays-for-72-hours",
    )
    staging.write_candidate(home, cand)

    with pytest.raises(MnemeError, match="update"):
        staging.route(home, cand.id, "ops-kb")
    assert staging.load_candidates(home)[0].target == "team-kb"


def test_an_update_can_still_be_parked_at_unassigned(tmp_path):
    """Un-routing takes it out of every repo, so the stale target_unit harms nothing."""
    home = home_with(tmp_path, ("team-kb", "internal"))
    cand = Candidate(
        id=candidate_id("fact", "team-kb", BODY), type="fact", edit="update",
        target="team-kb", body=BODY, topic="webhooks",
        target_unit="facts/webhooks#the-webhook-replays-for-72-hours",
    )
    staging.write_candidate(home, cand)
    assert staging.route(home, cand.id, staging.UNASSIGNED).target == staging.UNASSIGNED


# --- smaller edges the review named -----------------------------------------


def test_a_similarity_hint_pointing_into_the_old_repo_is_dropped(tmp_path):
    home = home_with(tmp_path, ("team-kb", "internal"), ("ops-kb", "internal"))
    cand = stage(home, "team-kb")
    cand.similar_to = "facts/webhooks#something-in-team-kb"
    staging.write_candidate(home, cand)

    after = staging.route(home, cand.id, "ops-kb")
    assert after.similar_to == "", "a hint about the old repo followed the candidate"


def test_an_empty_target_says_so(tmp_path):
    home = home_with(tmp_path, ("team-kb", "internal"))
    before = stage(home, "team-kb")
    with pytest.raises(MnemeError, match="empty|no target"):
        staging.route(home, before.id, "")
