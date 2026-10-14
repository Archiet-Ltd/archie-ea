def test_unlisted_failure_must_fail_the_run():
    assert False, "throwaway proof: an unlisted failing test must fail the run"


def test_self_quarantined_failure_must_still_fail_the_run():
    assert False, "throwaway proof: a test this pull request adds to the quarantine list must still fail"
