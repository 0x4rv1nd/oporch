from oporch.doctor import DoctorResult, run_doctor


class TestDoctor:
    def test_doctor_result_tracking(self):
        r = DoctorResult()
        r.add_pass("check1", "detail1")
        r.add_fail("check2", "detail2")
        r.add_warning("check3", "detail3")
        assert r.passed == 1
        assert r.failed == 1
        assert r.warnings == 1
        assert len(r.checks) == 3

    def test_doctor_runs_without_error(self):
        result = run_doctor()
        assert isinstance(result, DoctorResult)
        assert result.checks is not None
