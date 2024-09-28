from datetime import datetime

import jinja2
import pytest

from private_assistant_alarm_scheduler_skill.alarm_scheduler_skill import Parameters


# Fixture to set up the Jinja environment
@pytest.fixture(scope="module")
def jinja_env():
    return jinja2.Environment(
        loader=jinja2.PackageLoader(
            "private_assistant_alarm_scheduler_skill",
            "templates",
        ),
    )


def get_template_output(template_name, parameters, env):
    template = env.get_template(template_name)
    return template.render(parameters=parameters)


# Test for alarm_set.j2 template (similar to the original timer_set.j2)
@pytest.mark.parametrize(
    "parameters,expected_output",
    [
        (
            Parameters(alarm_time=datetime(2023, 3, 15, 6, 30)),
            "The new alarm is set for 06:30.",
        ),
    ],
)
def test_alarm_set_template(jinja_env, parameters, expected_output):
    assert get_template_output("set.j2", parameters, jinja_env) == expected_output


# Test for get_active.j2 template
@pytest.mark.parametrize(
    "parameters,expected_output",
    [
        (
            Parameters(alarm_time=datetime(2023, 3, 15, 6, 30)),
            "Current active alarm is set for Wednesday, March 15 at 06:30.",
        ),
        (
            Parameters(alarm_time=None),
            "No active alarm is set at the moment.",
        ),
    ],
)
def test_get_active_template(jinja_env, parameters, expected_output):
    assert get_template_output("get_active.j2", parameters, jinja_env) == expected_output


# Test for skip.j2 template
@pytest.mark.parametrize(
    "parameters,expected_output",
    [
        (
            Parameters(alarm_time=datetime(2023, 3, 15, 6, 30)),
            "Skipped the next alarm. The new alarm is set for Wednesday, March 15 at 06:30.",
        ),
    ],
)
def test_skip_template(jinja_env, parameters, expected_output):
    assert get_template_output("skip.j2", parameters, jinja_env) == expected_output


# Test for break.j2 template
@pytest.mark.parametrize(
    "parameters,expected_output",
    [
        (
            Parameters(),
            "The current alarm has been cancelled and future executions stopped.",
        ),
    ],
)
def test_break_template(jinja_env, parameters, expected_output):
    assert get_template_output("break.j2", parameters, jinja_env) == expected_output


# Test for continue.j2 template
@pytest.mark.parametrize(
    "parameters,expected_output",
    [
        (
            Parameters(alarm_time=datetime(2023, 3, 15, 6, 30)),
            "Alarm schedule has been resumed. The next alarm is set for Wednesday, March 15 at 06:30.",
        ),
    ],
)
def test_continue_template(jinja_env, parameters, expected_output):
    assert get_template_output("continue.j2", parameters, jinja_env) == expected_output
