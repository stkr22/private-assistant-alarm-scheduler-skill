from datetime import timedelta

import pytest

from private_assistant_alarm_scheduler_skill.tools_time_units import (
    format_time_difference,
)


@pytest.mark.parametrize(
    "time_diff, expected_output",
    [
        (timedelta(seconds=45), "45 seconds"),
        (timedelta(minutes=5, seconds=30), "5 minutes and 30 seconds"),
        (
            timedelta(hours=1, minutes=2, seconds=3),
            "1 hour and 2 minutes and 3 seconds",
        ),
        (timedelta(hours=2), "2 hours"),
    ],
)
def test_format_time_difference(time_diff, expected_output):
    result = format_time_difference(time_diff)
    assert result == expected_output
