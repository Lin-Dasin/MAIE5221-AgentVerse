import json
import subprocess
from contextlib import nullcontext
from datetime import date
from http.client import RemoteDisconnected

import pytest

from harness import scenario_software as software

GUIDE = """# Room dashboard

```sh
python booking_app.py --port 8000 --db "room bookings.db"
python usage_dashboard.py --port 8001 --db "room bookings.db"
```

Select an inclusive date range. The dashboard ranks two-hour bands by covered
minutes. It reports cancelled bookings separately.
"""


def _delivery(root, script, guide, text):
    (root / script).parent.mkdir(parents=True, exist_ok=True)
    (root / script).write_text(
        "raise RuntimeError('Do not run this on the host')\n", encoding="utf-8"
    )
    (root / guide).write_text(text, encoding="utf-8")


def test_dashboard_rejects_stale_one_hour_guide_after_runtime_pass(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    original = tmp_path / "booking_app.py"
    original.write_text("# accepted original\n", encoding="utf-8")
    seen = []

    def mock_fixture(mode, mounts, **claims):
        seen.append((mode, mounts))
        return software.SoftwareCheck("passed", evidence=["HTTP passed"])

    monkeypatch.setattr(software, "_run_fixture", mock_fixture)
    guide = GUIDE + "\nThe old table still uses 24-hour bands and 24 band labels.\n"
    _delivery(candidate, software.DASHBOARD_ARTIFACT, software.DASHBOARD_GUIDE_ARTIFACT, guide)
    verdict = software.check_dashboard(candidate, original)

    assert verdict.status == "failed"
    assert verdict.evidence == ["HTTP passed"]
    assert verdict.defects == ["In dashboard_usage.md, describe 12 two-hour bands throughout."]
    assert seen == [
        (
            "dashboard",
            [
                (original.resolve(), "booking.py"),
                ((candidate / software.DASHBOARD_ARTIFACT).resolve(), "dashboard.py"),
            ],
        )
    ]

    (candidate / software.DASHBOARD_GUIDE_ARTIFACT).write_text(GUIDE, encoding="utf-8")
    assert software.check_dashboard(candidate, original).status == "passed"


def test_guide_distinguishes_a_two_hour_meeting_from_two_hour_bands():
    stale_explanation = GUIDE.replace("two-hour bands", "hourly bands") + "\nA 2-hour meeting."
    assert any(
        "two-hour bands and covered minutes" in defect
        for defect in software._guide_defects(stale_explanation, dashboard=True)
    )


def test_guide_points_out_distinct_booking_and_dashboard_databases():
    distinct_databases = GUIDE.replace(
        '--port 8001 --db "room bookings.db"', "--port 8001 --db other.db"
    )
    assert software._guide_defects(distinct_databases, dashboard=True) == [
        "In dashboard_usage.md, show both Python commands with the same --db value."
    ]


def test_documented_launch_resolves_an_omitted_port_from_the_actual_program():
    guide = (
        "python booking_app.py --db booking.db\n"
        "python usage_dashboard.py --port 8000 --db booking.db\n"
    )
    booking = "parser.add_argument('--port', type=int, default=8000)"
    assert software._guide_port(guide, "booking_app.py", booking) == 8000
    assert software._guide_port(guide, "usage_dashboard.py", "") == 8000
    assert (
        software._guide_port(guide.replace("--port 8000", "--port 8100"), "usage_dashboard.py", "")
        == 8100
    )


def test_dashboard_guide_flags_and_default_port_follow_argparse():
    program = """
p.add_argument('--host', default='127.0.0.1')
p.add_argument('--port', type=int, default=8001)
p.add_argument('--db')
"""
    guide = (
        GUIDE
        + "\nDefault port for the dashboard is 8770.\n"
        + ("```sh\npython outputs/usage_dashboard.py --db bookings.db --selftest\n```")
    )
    assert software._dashboard_cli_defects(guide, program) == [
        "In dashboard_usage.md, remove the --selftest example or implement that option.",
        "In dashboard_usage.md, state the dashboard's actual default port 8001.",
    ]
    corrected_program = program + "p.add_argument('--selftest', action='store_true')\n"
    assert software._dashboard_cli_defects(guide.replace("8770", "8001"), corrected_program) == []
    # An explicit port override is valid, regardless of the parser's default.
    override = GUIDE.replace("--port 8001", "--port 8770")
    assert software._dashboard_cli_defects(override, program) == []


def test_dashboard_guide_names_actual_button_and_chart_order():
    html = """
<button><span>Apply</span></button>
<h2>Time bands ranked by occupied minutes</h2>
<script>render(data.bands.by_covered_minutes.map(band => band.value));</script>
"""
    guide = GUIDE + "\nAfter picking dates, press **Refresh**.\n"
    report = {"bands": {"by_covered_minutes": [{"value": 0}, {"value": 105}]}}
    buttons = software._documented_buttons(guide)
    defects = software._dashboard_presentation_defects(html, report, buttons)
    assert len(defects) == 2
    assert "documented button 'Refresh' is absent" in defects[0]
    assert "renders clock order" in defects[1]
    clock_order = html.replace("ranked by occupied minutes", "in clock order")
    assert software._dashboard_presentation_defects(clock_order, report, ("Apply",)) == []
    assert (
        len(software._dashboard_presentation_defects(clock_order, report, ("Apply",), ranked=True))
        == 1
    )


@pytest.mark.parametrize("first_hour", [0, 8])
def test_dashboard_clock_start_uses_first_observed_band_including_valid_wrap(first_hour):
    page = """
<script>
var markup = '';
data.bands.by_covered_minutes.forEach(function(b){markup+=bar(b.band,b.value);});
document.getElementById('bands').innerHTML=markup;
</script>
"""
    hours = list(range(0, 24, 2))
    start = hours.index(first_hour)
    hours = hours[start:] + hours[:start]
    wide = {
        "bands": {
            "by_covered_minutes": [
                {"band": f"{hour:02}:00-{hour + 2:02}:00", "value": 0} for hour in hours
            ]
        }
    }
    clock_start = software._documented_clock_start("Drawn in **clock order starting at 08:00**.")
    assert clock_start == "08:00"
    defects = software._dashboard_presentation_defects(page, wide, clock_start=clock_start)
    if first_hour == 8:
        assert not defects
    else:
        assert "starts at 00:00, not 08:00" in defects[0]


def test_dashboard_clock_start_defers_when_display_reorders_the_response():
    page = """
<script>
var totals = data.bands.by_covered_minutes.map(b => b.band + b.value);
var display = totals.slice(4).concat(totals.slice(0, 4));
document.getElementById('bands').innerHTML = display.join('');
</script>
"""
    wide = {"bands": {"by_covered_minutes": [{"band": "00:00-02:00", "value": 0}]}}
    assert not software._dashboard_presentation_defects(page, wide, clock_start="08:00")
    assert software._documented_clock_start("The meeting is starting at 08:00.") is None


def test_dashboard_guide_validates_an_explicit_quoted_button_description(tmp_path, monkeypatch):
    original = tmp_path / "booking_app.py"
    original.write_text("# accepted original\n", encoding="utf-8")
    candidate = tmp_path / "candidate"
    guide = GUIDE + "\nA **`Load dashboard`** button shows the selected range.\n"
    _delivery(candidate, software.DASHBOARD_ARTIFACT, software.DASHBOARD_GUIDE_ARTIFACT, guide)

    def mock_fixture(mode, mounts, **claims):
        assert claims["buttons"] == ("Load dashboard",)
        defects = software._dashboard_presentation_defects(
            "<button>Refresh dashboard</button>", {}, **claims
        )
        return software.SoftwareCheck("failed", defects, ["HTTP passed"])

    monkeypatch.setattr(software, "_run_fixture", mock_fixture)
    verdict = software.check_dashboard(candidate, original)
    assert "The documented button 'Load dashboard' is absent" in verdict.defects[0]
    assert "Refresh dashboard" in verdict.defects[0]
    assert verdict.evidence == ["HTTP passed"]


@pytest.mark.parametrize("window, valid", [("00:00–23:59", True), ("08:00–18:00", False)])
def test_booking_guide_can_explain_actual_all_day_window(window, valid):
    guide = (
        "python booking_app.py --db bookings.db\n"
        "Check availability, book and cancel.\n"
        f"Booking hours are {window}.\n"
    )
    defects = software._guide_defects(guide, dashboard=False)
    assert (not defects) == valid


@pytest.mark.parametrize(
    "example",
    [
        '| GET | `/api/bookings?date=YYYY-MM-DD` | `{"bookings": []}` |',
        'GET /api/bookings?date=2030-02-01 returns {"bookings": []}.',
        'curl -s "$BASE/api/bookings?date=2030-02-01"\n# -> {"bookings": []}',
    ],
)
def test_booking_guide_only_rejects_explicit_wrapped_history_examples(example):
    assert software._documents_wrapped_history(example)
    assert not software._documents_wrapped_history(
        example.replace('{"bookings": []}', '[{"id": 1, "status": "active"}]')
    )
    assert not software._documents_wrapped_history(
        '| POST | `/api/bookings` | `{"bookings": []}` |\nCheck bookings in the browser.'
    )


def test_booking_guide_can_explain_history_does_not_use_a_wrapper():
    assert not software._documents_wrapped_history(
        'GET /api/bookings?date=2030-02-01 does not return {"bookings": []}. '
        "The response is a bare array."
    )


def test_booking_guide_checks_static_and_literal_dynamic_labels():
    guide = "A `Load bookings` button and a **Cancel** button on active rows."
    documented = software._documented_buttons(guide, descriptions=True)
    assert documented == ("Load bookings", "Cancel")
    page = """
<button>Load bookings</button>
<script>
  const btn = document.createElement('button');
  btn.textContent = 'Cancel';
</script>
"""
    assert software._button_defects(page, documented, "usage.md") == []
    old_page = """
<button>Show bookings</button>
<button>Cancel selected booking</button>
<script>const paragraph = document.createElement('p'); paragraph.textContent = 'Cancel';</script>
"""
    defects = software._button_defects(old_page, documented, "usage.md")
    assert len(defects) == 2
    assert "documented button 'Load bookings' is absent" in defects[0]
    assert "documented button 'Cancel' is absent" in defects[1]


def test_booking_flow_reports_wrong_guide_without_losing_http_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(software, "_serve", lambda *args: nullcontext())
    monkeypatch.setattr(software, "_rooms", lambda: [{"id": 1}])
    monkeypatch.setattr(
        software,
        "_http",
        lambda *args: (
            200,
            "<button>Check availability</button><button>Book</button>"
            "<button>Cancel selected booking</button>",
        ),
    )
    busy = iter((set(), {("09:00", "10:00")}, set()))
    monkeypatch.setattr(software, "_busy", lambda *args: next(busy))
    ids = iter((1, None, 2, 3, 4))
    monkeypatch.setattr(software, "_book", lambda *args, **kwargs: next(ids))
    monkeypatch.setattr(software, "_cancel", lambda _: None)
    monkeypatch.setattr(
        software,
        "_json",
        lambda *args: [{"id": 1, "status": "cancelled"}, {"id": 2, "status": "active"}],
    )
    defects, evidence = [], []
    software._booking_flow(
        tmp_path / "bookings.db",
        defects,
        evidence,
        buttons=("Cancel",),
        wrapped_history=True,
    )
    assert "documented button 'Cancel' is absent" in defects[0]
    assert "top-level JSON array" in defects[1]
    assert "persistent history" in evidence[0]


def test_booking_report_is_unverified_when_docker_cannot_start(tmp_path, monkeypatch):
    guide = "python booking_app.py --db bookings.db\nCheck availability, book and cancel.\n"
    _delivery(tmp_path, software.BOOKING_ARTIFACT, software.BOOKING_GUIDE_ARTIFACT, guide)

    def unavailable(command, **kwargs):
        assert command[:2] == ["docker", "run"]
        raise FileNotFoundError("Docker CLI unavailable")

    monkeypatch.setattr(software.subprocess, "run", unavailable)
    result = software.check_booking(tmp_path)
    assert result.status == "unverified"
    assert result.defects == ["Docker could not start the isolated S1 verifier."]


def test_fixture_mounts_only_its_files_and_runs_with_docker_network_disabled(tmp_path, monkeypatch):
    booking = tmp_path / "booking_app.py"
    monkeypatch.setenv("CODE_EXEC_IMAGE", "agentverse-sandbox:local-test")

    def mock_docker(command, **kwargs):
        assert command[:2] == ["docker", "run"]
        assert command[command.index("--network") + 1] == "none"
        assert command[command.index("--pull") + 1] == "never"
        assert command[command.index("--user") + 1] == "65532:65532"
        assert "--read-only" in command
        mounts = [command[idx + 1] for idx, flag in enumerate(command) if flag == "--mount"]
        assert len(mounts) == 2
        assert mounts[-1] == f"type=bind,src={booking.as_posix()},dst=/fixture/booking.py,readonly"
        assert mounts[0].endswith("dst=/fixture/verifier.py,readonly")
        assert command[-5:] == [
            "agentverse-sandbox:local-test",
            "-I",
            "-B",
            "/fixture/verifier.py",
            "booking",
        ]
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"defects": ["An app defect"], "evidence": []}), ""
        )

    monkeypatch.setattr(software.subprocess, "run", mock_docker)
    assert software._run_fixture("booking", [(booking, "booking.py")]) == software.SoftwareCheck(
        "failed", ["An app defect"]
    )


def test_dashboard_disconnect_names_route_and_bounded_app_exception(tmp_path, monkeypatch):
    class Process:
        closed = False

        def poll(self):
            return 0 if self.closed else None

        def terminate(self):
            self.closed = True

        def wait(self, *, timeout):
            assert timeout > 0

    def mock_start(*args, stderr, **kwargs):
        stderr.write(
            b"Traceback (most recent call last):\n"
            b'  File "/fixture/dashboard.py", line 198, in compute\n'
            b"NameError: name 'bands' is not defined\n"
        )
        return Process()

    def disconnect(port, path, data=None):
        if path == "/":
            return 200, "<html></html>"
        raise RemoteDisconnected("no HTTP response")

    monkeypatch.setattr(software.subprocess, "Popen", mock_start)
    monkeypatch.setattr(software, "_http", disconnect)
    day = date(2030, 2, 1)
    with (
        pytest.raises(
            software._CheckFailed,
            match=r"GET /api/dashboard .*RemoteDisconnected.*NameError: name 'bands'",
        ) as exc_info,
        software._serve("dashboard", software._DASHBOARD_PORT, tmp_path / "booking.db"),
    ):
        software._query_dashboard(day, day)
    assert "/fixture/" not in str(exc_info.value)


def test_dashboard_response_missing_bands_names_the_missing_field():
    with pytest.raises(
        software._CheckFailed, match=r"GET /api/dashboard response is missing bands"
    ):
        software._bands({"totals": {}})


@pytest.mark.parametrize("cross_min", [15, 0])
def test_dashboard_fixture_detects_unsplit_minutes_at_two_hour_boundary(monkeypatch, cross_min):
    first, second, outside = date(2030, 2, 1), date(2030, 2, 2), date(2030, 2, 3)

    def band_rows(covered):
        return [
            {"band": f"{hour:02}:00-{hour + 2:02}:00", "value": covered.get(hour, 0)}
            for hour in range(0, 24, 2)
        ]

    def totals(active, reserved, cancelled, rate):
        return dict(
            active_bookings=active,
            reserved_minutes=reserved,
            cancelled_bookings=cancelled,
            cancellation_rate_pct=rate,
        )

    wide = {
        "range": {"start": first.isoformat(), "end": second.isoformat(), "days": 2},
        "totals": totals(3, 210, 1, 25),
        "rooms": [
            {"name": "Busy", "active": 3, "minutes": 210, "cancelled": 1},
            {"name": "Idle", "active": 0, "minutes": 0, "cancelled": 0},
        ],
        "busiest_days": [
            {"date": first.isoformat(), "active": 2},
            {"date": second.isoformat(), "active": 1},
        ],
        "bands": {
            "width_hours": 2,
            "by_covered_minutes": band_rows(
                {
                    8: 120 - cross_min,
                    10: cross_min,
                    14: 90,
                }
            ),
        },
    }
    reports = {
        (first, second, 200): wide,
        (second, second, 200): {"totals": totals(1, 90, 1, 50)},
        (outside, outside, 200): {
            "bands": {
                "width_hours": 2,
                "by_covered_minutes": band_rows({6: 90, 22: 60}),
            }
        },
        (second, first, 400): {},
    }
    monkeypatch.setattr(
        software, "_http", lambda *args: (200, '<input type="date"><input type="date">')
    )
    monkeypatch.setattr(software, "_query_dashboard", lambda a, b, code=200: reports[a, b, code])
    rooms = [{"name": "Busy"}, {"name": "Idle"}]
    if cross_min == 0:
        with pytest.raises(ValueError, match="15 minutes in each band"):
            software._dashboard_results(first, second, outside, rooms)
    else:
        software._dashboard_results(first, second, outside, rooms)
