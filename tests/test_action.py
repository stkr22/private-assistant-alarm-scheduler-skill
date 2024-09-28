import pytest

from private_assistant_alarm_scheduler_skill.alarm_scheduler_skill import Action


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Please help", Action.HELP),
        ("help me out!", Action.HELP),
        ("how to set alarm", Action.SET),
        ("set an alarm for 7 am", Action.SET),
        ("skip the next alarm", Action.SKIP),
        ("please skip the alarm", Action.SKIP),
        ("break the alarm schedule", Action.BREAK),
        ("continue the alarm schedule", Action.CONTINUE),
        ("what is the current alarm", Action.GET_ACTIVE),
        ("tell me the current alarm", Action.GET_ACTIVE),
        ("this should return none", None),
        ("trigger something else", None),
    ],
)
def test_find_matching_action(text, expected):
    assert Action.find_matching_action(text) == expected
